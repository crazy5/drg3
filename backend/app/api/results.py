"""分组结果查询 API。"""
from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_rule_index
from app.api.cases import _rule_names, _summary
from app.db.models import Case, CaseResult
from app.db.session import get_session
from app.engine.index import RuleIndex
from app.schemas import GroupResultOut

router = APIRouter(prefix="/api/results", tags=["results"])


@router.get("/{result_id}", response_model=GroupResultOut)
def get_result(
    result_id: int,
    session: Annotated[Session, Depends(get_session)],
    index: Annotated[RuleIndex, Depends(get_rule_index)],
):
    row = session.query(CaseResult).filter_by(result_id=result_id).one_or_none()
    if not row:
        raise HTTPException(404, "result not found")
    case_row = session.query(Case).filter_by(case_id=row.case_id).one_or_none()
    return _to_out(row, case_row, index)


@router.get("", response_model=list[GroupResultOut])
def list_results(
    session: Annotated[Session, Depends(get_session)],
    index: Annotated[RuleIndex, Depends(get_rule_index)],
    drg_code: str | None = None,
    mdc_code: str | None = None,
    adrg_code: str | None = None,
    discharge_date_from: date | None = None,
    discharge_date_to: date | None = None,
    discharge_department: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = 0,
):
    """结果列表。

    支持筛选：
    - drg_code / mdc_code / adrg_code: 按层级组号
    - discharge_date_from / discharge_date_to: 按出院日期范围（含两端）
    - discharge_department: 按出院科室（精确匹配）
    """
    q = session.query(CaseResult)
    # 每个 case_id 只取最新一条（与 stats 接口一致），避免历史 sync 重复列出
    latest = _latest_result_subquery(session)
    q = q.join(latest, CaseResult.result_id == latest.c.max_result_id)
    q = q.order_by(CaseResult.result_id.desc())
    if drg_code:
        q = q.filter(CaseResult.drg_code == drg_code)
    if mdc_code:
        q = q.filter(CaseResult.mdc_code == mdc_code)
    if adrg_code:
        q = q.filter(CaseResult.adrg_code == adrg_code)
    if discharge_date_from or discharge_date_to or discharge_department:
        q = q.join(Case, Case.case_id == CaseResult.case_id)
        if discharge_date_from:
            q = q.filter(Case.discharge_date >= discharge_date_from)
        if discharge_date_to:
            q = q.filter(Case.discharge_date <= discharge_date_to)
        if discharge_department:
            q = q.filter(Case.discharge_department == discharge_department)
    rows = q.offset(offset).limit(limit).all()
    case_ids = {r.case_id for r in rows}
    case_map = {c.case_id: c for c in session.query(Case).filter(Case.case_id.in_(case_ids)).all()}
    return [_to_out(r, case_map.get(r.case_id), index) for r in rows]


def _to_out(row: CaseResult, case_row: Case | None, index: RuleIndex) -> GroupResultOut:
    try:
        evidence = json.loads(row.evidence_json) if row.evidence_json else {}
    except json.JSONDecodeError:
        evidence = {}

    mdc_name, adrg_name, drg_name = _rule_names(index, row.mdc_code, row.adrg_code, row.drg_code)

    summary = None
    if case_row:
        summary = _summary(
            case_row.zyzd, case_row.zyss,
            json.loads(case_row.qtzd_list or "[]"),
            json.loads(case_row.qtss_list or "[]"),
            case_row.nl, case_row.xb, case_row.xsrtl, case_row.xsrtz,
            case_row.admission_date, case_row.discharge_date, case_row.discharge_department,
        )

    return GroupResultOut(
        mdc_code=row.mdc_code,
        mdc_name=mdc_name,
        adrg_code=row.adrg_code,
        adrg_name=adrg_name,
        drg_code=row.drg_code or "0000",
        drg_name=drg_name,
        cc_level=row.cc_level or "NONE",
        error_type=row.error_type or "error",
        error_msg=row.error_msg,
        evidence=evidence,
        case_summary=summary,
        duration_ms=row.duration_ms or 0,
        case_id=row.case_id,
        result_id=row.result_id,
    )


# ────────── 统计聚合接口（不走明细列表，按 SQL 聚合，永远准确）──────────


def _latest_result_subquery(session: Session):
    """每个 case_id 取最新一条 case_results（按 result_id DESC）。

    解决历史 sync 多次跑导致同一 case_id 有多条 result 行（旧版本引擎的结果也保留），
    不去重会让 stats 把历史失败/兜底也算进去，造成 dashboard 显示的总数/兜底率虚高。
    """
    return (
        session.query(
            CaseResult.case_id.label("case_id"),
            func.max(CaseResult.result_id).label("max_result_id"),
        )
        .group_by(CaseResult.case_id)
        .subquery()
    )


def _apply_result_filters(
    q,
    session: Session,
    *,
    drg_code: str | None,
    mdc_code: str | None,
    adrg_code: str | None,
    discharge_date_from: date | None,
    discharge_date_to: date | None,
    discharge_department: str | None,
):
    """给 query 套上与 list_results 同款的筛选条件。

    关键：每个 case_id 只取最新一条 case_results（result_id 最大）。这样：
    - stats 接口不会把历史多次 sync 的旧失败/兜底记录算进去
    - list_results 不会重复列出同一病案的多条历史结果
    """
    latest = _latest_result_subquery(session)
    q = q.join(latest, CaseResult.result_id == latest.c.max_result_id)
    if drg_code:
        q = q.filter(CaseResult.drg_code == drg_code)
    if mdc_code:
        q = q.filter(CaseResult.mdc_code == mdc_code)
    if adrg_code:
        q = q.filter(CaseResult.adrg_code == adrg_code)
    if discharge_date_from or discharge_date_to or discharge_department:
        q = q.join(Case, Case.case_id == CaseResult.case_id)
    if discharge_date_from:
        q = q.filter(Case.discharge_date >= discharge_date_from)
    if discharge_date_to:
        q = q.filter(Case.discharge_date <= discharge_date_to)
    if discharge_department:
        q = q.filter(Case.discharge_department == discharge_department)
    return q


@router.get("/stats/count")
def stats_count(
    session: Annotated[Session, Depends(get_session)],
    drg_code: str | None = None,
    mdc_code: str | None = None,
    adrg_code: str | None = None,
    discharge_date_from: date | None = None,
    discharge_date_to: date | None = None,
    discharge_department: str | None = None,
):
    """按筛选条件返回 case_results 精确总条数（走 SQL COUNT，不依赖 limit）。

    用于 Dashboard / 报表的「总病案数」——永远准确，跟 list_results 的 limit 无关。
    """
    q = session.query(func.count(CaseResult.result_id))
    q = _apply_result_filters(
        q, session,
        drg_code=drg_code, mdc_code=mdc_code, adrg_code=adrg_code,
        discharge_date_from=discharge_date_from,
        discharge_date_to=discharge_date_to,
        discharge_department=discharge_department,
    )
    return {"total": q.scalar() or 0}


@router.get("/stats/distribution")
def stats_distribution(
    session: Annotated[Session, Depends(get_session)],
    index: Annotated[RuleIndex, Depends(get_rule_index)],
    by: Literal["drg", "mdc"],
    discharge_date_from: date | None = None,
    discharge_date_to: date | None = None,
    discharge_department: str | None = None,
):
    """按 DRG 或 MDC 聚合分布（GROUP BY），用于 Dashboard 分布表。

    返回：[{code, name, count}, ...] 按 count 降序。
    """
    if by == "drg":
        col = CaseResult.drg_code
        name_col = CaseResult.drg_code  # 名称靠 RuleIndex 反查
    else:
        col = CaseResult.mdc_code
        name_col = CaseResult.mdc_code

    q = session.query(col.label("code"), func.count(CaseResult.result_id).label("cnt"))
    q = _apply_result_filters(
        q, session,
        drg_code=None, mdc_code=None, adrg_code=None,
        discharge_date_from=discharge_date_from,
        discharge_date_to=discharge_date_to,
        discharge_department=discharge_department,
    )
    rows = q.group_by(col).order_by(func.count(CaseResult.result_id).desc()).all()

    out = []
    for r in rows:
        if not r.code:
            continue
        # RuleIndex 反查名字（_rule_names 入参是 (index, mdc_code, adrg_code, drg_code)）
        if by == "drg":
            _, _, name = _rule_names(index, None, None, r.code)
        else:
            name, _, _ = _rule_names(index, r.code, None, None)
        out.append({"code": r.code, "name": name, "count": r.cnt})
    return out


@router.get("/stats/error-rate")
def stats_error_rate(
    session: Annotated[Session, Depends(get_session)],
    discharge_date_from: date | None = None,
    discharge_date_to: date | None = None,
    discharge_department: str | None = None,
):
    """按 error_type 分桶统计（success / fallback / error），用于 Dashboard 顶部卡片。"""
    q = session.query(CaseResult.error_type, func.count(CaseResult.result_id))
    q = _apply_result_filters(
        q, session,
        drg_code=None, mdc_code=None, adrg_code=None,
        discharge_date_from=discharge_date_from,
        discharge_date_to=discharge_date_to,
        discharge_department=discharge_department,
    )
    rows = q.group_by(CaseResult.error_type).all()
    out = {"success": 0, "fallback": 0, "error": 0, "total": 0}
    for et, cnt in rows:
        key = (et or "error").lower()
        if key not in out:
            key = "error"
        out[key] = cnt
        out["total"] += cnt
    return out