"""病案录入 / 查询 API。"""
from __future__ import annotations

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_rule_index
from app.core import icd_dict
from app.db.models import Case, CaseResult
from app.db.session import get_session
from app.engine.grouper.pipeline import Case as EngineCase, group_case
from app.engine.index import RuleIndex
from app.schemas import CaseIn, CaseSummaryOut, CodeOut, GroupResultOut

router = APIRouter(prefix="/api/cases", tags=["cases"])


def _norm(code: str | None) -> str | None:
    """ICD 编码规范化：去空白 + 大写 + 去末尾点号（xlsx 集合全是大写）。"""
    if not code:
        return code
    return code.strip().upper().rstrip(".")


def _summary(
    zyzd: str, zyss: str | None,
    qtzd_list: list[str], qtss_list: list[str],
    nl: int | None, xb: int | None,
    xsrtl: int | None, xsrtz: int | None,
    admission_date=None, discharge_date=None, discharge_department=None,
) -> CaseSummaryOut:
    return CaseSummaryOut(
        zyzd=CodeOut(code=zyzd, name=icd_dict.lookup(zyzd)),
        zyss=CodeOut(code=zyss, name=icd_dict.lookup(zyss)) if zyss else None,
        qtzd_list=[CodeOut(code=c, name=icd_dict.lookup(c)) for c in qtzd_list],
        qtss_list=[CodeOut(code=c, name=icd_dict.lookup(c)) for c in qtss_list],
        nl=nl, xb=xb, xsrtl=xsrtl, xsrtz=xsrtz,
        admission_date=admission_date,
        discharge_date=discharge_date,
        discharge_department=discharge_department,
    )


def _rule_names(index: RuleIndex, mdc_code: str | None, adrg_code: str | None, drg_code: str | None):
    """查 MDC/ADRG/DRG 的中文名（来自规则表）。"""
    mdc_name = adrg_name = drg_name = None
    if mdc_code and mdc_code in index.mdc_by_code:
        mdc_name = index.mdc_by_code[mdc_code].name
    if adrg_code and adrg_code in index.adrg_by_code:
        adrg_name = index.adrg_by_code[adrg_code].name
    if drg_code and drg_code in index.drg_by_code:
        drg_name = index.drg_by_code[drg_code].name
    return mdc_name, adrg_name, drg_name


@router.post("", response_model=GroupResultOut)
def create_and_group_case(
    body: CaseIn,
    session: Annotated[Session, Depends(get_session)],
    index: Annotated[RuleIndex, Depends(get_rule_index)],
):
    """新建病案 + 立即分组，返回结果（含证据链）。"""
    # ICD 编码统一规范化：去空白 + 大写 + 去末尾点号
    zyzd = _norm(body.zyzd)
    zyss = _norm(body.zyss)
    qtzd_list = [_norm(c) for c in (body.qtzd_list or []) if c.strip()]
    qtss_list = [_norm(c) for c in (body.qtss_list or []) if c.strip()]

    case_id = body.case_id or f"C{uuid.uuid4().hex[:10].upper()}"
    case_row = Case(
        case_id=case_id,
        zyzd=zyzd,
        zyss=zyss,
        qtzd_list=json.dumps(qtzd_list, ensure_ascii=False),
        qtss_list=json.dumps(qtss_list, ensure_ascii=False),
        nl=body.nl, xb=body.xb, xsrtl=body.xsrtl, xsrtz=body.xsrtz,
        admission_date=body.admission_date,
        discharge_date=body.discharge_date,
        discharge_department=(body.discharge_department or "").strip() or None,
        source=body.source or "manual",
    )
    session.add(case_row)
    session.flush()

    engine_case = EngineCase(
        zyzd=zyzd, zyss=zyss,
        qtzd_list=qtzd_list, qtss_list=qtss_list,
        nl=body.nl, xb=body.xb, xsrtl=body.xsrtl, xsrtz=body.xsrtz,
    )
    result = group_case(engine_case, index)

    result_row = CaseResult(
        case_id=case_id,
        mdc_code=result.mdc_code,
        adrg_code=result.adrg_code,
        drg_code=result.drg_code,
        cc_level=result.cc_level,
        error_type=result.error_type,
        error_msg=result.error_msg,
        evidence_json=json.dumps(result.evidence, ensure_ascii=False),
        duration_ms=result.duration_ms,
    )
    session.add(result_row)
    session.commit()

    mdc_name, adrg_name, drg_name = _rule_names(index, result.mdc_code, result.adrg_code, result.drg_code)
    return GroupResultOut(
        mdc_code=result.mdc_code,
        mdc_name=mdc_name,
        adrg_code=result.adrg_code,
        adrg_name=adrg_name,
        drg_code=result.drg_code or "0000",
        drg_name=drg_name,
        cc_level=result.cc_level,
        error_type=result.error_type,
        error_msg=result.error_msg,
        evidence=result.evidence,
        case_summary=_summary(
            case_row.zyzd, case_row.zyss,
            json.loads(case_row.qtzd_list or "[]"),
            json.loads(case_row.qtss_list or "[]"),
            case_row.nl, case_row.xb, case_row.xsrtl, case_row.xsrtz,
            case_row.admission_date, case_row.discharge_date, case_row.discharge_department,
        ),
        duration_ms=result.duration_ms,
        case_id=case_id,
        result_id=result_row.result_id,
    )


@router.post("/{case_id}/group", response_model=GroupResultOut)
def regroup_case(
    case_id: str,
    session: Annotated[Session, Depends(get_session)],
    index: Annotated[RuleIndex, Depends(get_rule_index)],
):
    """对已存在的病案重新分组。"""
    case_row = session.query(Case).filter_by(case_id=case_id).one_or_none()
    if not case_row:
        raise HTTPException(404, "case not found")

    qtzd_list = json.loads(case_row.qtzd_list or "[]")
    qtss_list = json.loads(case_row.qtss_list or "[]")
    engine_case = EngineCase(
        zyzd=case_row.zyzd,
        zyss=case_row.zyss,
        qtzd_list=qtzd_list,
        qtss_list=qtss_list,
        nl=case_row.nl, xb=case_row.xb,
        xsrtl=case_row.xsrtl, xsrtz=case_row.xsrtz,
    )
    result = group_case(engine_case, index)
    result_row = CaseResult(
        case_id=case_id,
        mdc_code=result.mdc_code,
        adrg_code=result.adrg_code,
        drg_code=result.drg_code,
        cc_level=result.cc_level,
        error_type=result.error_type,
        error_msg=result.error_msg,
        evidence_json=json.dumps(result.evidence, ensure_ascii=False),
        duration_ms=result.duration_ms,
    )
    session.add(result_row)
    session.commit()

    mdc_name, adrg_name, drg_name = _rule_names(index, result.mdc_code, result.adrg_code, result.drg_code)
    return GroupResultOut(
        mdc_code=result.mdc_code,
        mdc_name=mdc_name,
        adrg_code=result.adrg_code,
        adrg_name=adrg_name,
        drg_code=result.drg_code or "0000",
        drg_name=drg_name,
        cc_level=result.cc_level,
        error_type=result.error_type,
        error_msg=result.error_msg,
        evidence=result.evidence,
        case_summary=_summary(
            case_row.zyzd, case_row.zyss,
            qtzd_list, qtss_list,
            case_row.nl, case_row.xb, case_row.xsrtl, case_row.xsrtz,
            case_row.admission_date, case_row.discharge_date, case_row.discharge_department,
        ),
        duration_ms=result.duration_ms,
        case_id=case_id,
        result_id=result_row.result_id,
    )


@router.get("/departments", response_model=list[str])
def list_departments(
    session: Annotated[Session, Depends(get_session)],
):
    """返回所有已出现的出院科室（去重，按字母排序），供前端下拉用。"""
    rows = (
        session.query(Case.discharge_department)
        .filter(Case.discharge_department.isnot(None), Case.discharge_department != "")
        .distinct()
        .order_by(Case.discharge_department)
        .all()
    )
    return [r[0] for r in rows]
