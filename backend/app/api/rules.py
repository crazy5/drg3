"""规则导入 / 浏览 API。"""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_rule_index, rebuild_rule_index
from app.core.config import DEFAULT_RULES_XLSX
from app.db.models import AdrgRule, DrgRule, MdcRule, SetMember
from app.db.session import get_session
from app.engine.index import RuleIndex
from app.importers.xlsx_importer import import_rules_xlsx
from app.schemas import (
    ImportSummary,
    RuleDetailOut,
    RuleOut,
    SetMemberOut,
    SetSummary,
)

router = APIRouter(prefix="/api/rules", tags=["rules"])


# ────────── 导入 ──────────


@router.post("/import", response_model=ImportSummary)
async def import_rules(
    request: Request,
    file: Annotated[UploadFile, File(description="xlsx 文件")],
    session: Annotated[Session, Depends(get_session)],
):
    """上传 xlsx → 写入 SQLite → 重建内存索引。"""
    content = await file.read()
    tmp_path = DEFAULT_RULES_XLSX.with_suffix(".uploaded.xlsx")
    tmp_path.write_bytes(content)
    summary = import_rules_xlsx(tmp_path, session)
    rebuild_rule_index(request, session)
    return ImportSummary(**summary)


# ────────── 集合浏览 ──────────


@router.get("/sets", response_model=list[SetSummary])
def list_sets(
    index: Annotated[RuleIndex, Depends(get_rule_index)],
    q: str | None = Query(None, description="按 set_id 前缀过滤"),
    limit: int = Query(50, ge=1, le=500),
):
    items = [
        SetSummary(set_id=sid, size=len(members))
        for sid, members in sorted(index.sets.items())
        if not q or sid.startswith(q.upper())
    ]
    return items[:limit]


@router.get("/sets/{set_id}/members", response_model=list[SetMemberOut])
def get_set_members(
    set_id: str,
    session: Annotated[Session, Depends(get_session)],
    limit: int = Query(200, ge=1, le=2000),
    offset: int = 0,
):
    from app.core import icd_dict
    rows = (
        session.query(SetMember)
        .filter(SetMember.set_id == set_id.upper())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        SetMemberOut(
            code=r.code,
            code_name=r.code_name or icd_dict.lookup(r.code),
            code_type=r.code_type,
        )
        for r in rows
    ]


# ────────── 三层规则浏览 ──────────


@router.get("/mdc", response_model=list[RuleOut])
def list_mdc(session: Annotated[Session, Depends(get_session)]):
    rows = session.query(MdcRule).order_by(MdcRule.priority).all()
    return [RuleOut(code=r.mdc_code, name=r.mdc_name, rule_expr=r.rule_expr,
                    priority=r.priority) for r in rows]


@router.get("/adrg", response_model=list[RuleOut])
def list_adrg(
    session: Annotated[Session, Depends(get_session)],
    mdc_code: str | None = None,
):
    q = session.query(AdrgRule).order_by(AdrgRule.mdc_code, AdrgRule.priority)
    if mdc_code:
        q = q.filter(AdrgRule.mdc_code == mdc_code.upper())
    rows = q.all()
    return [RuleOut(code=r.adrg_code, name=r.adrg_name, rule_expr=r.rule_expr,
                    priority=r.priority, parent=r.mdc_code, adrg_type=r.adrg_type)
            for r in rows]


@router.get("/drg", response_model=list[RuleOut])
def list_drg(
    session: Annotated[Session, Depends(get_session)],
    adrg_code: str | None = None,
):
    q = session.query(DrgRule).order_by(DrgRule.adrg_code, DrgRule.priority)
    if adrg_code:
        q = q.filter(DrgRule.adrg_code == adrg_code.upper())
    rows = q.all()
    return [RuleOut(code=r.drg_code, name=r.drg_name, rule_expr=r.rule_expr,
                    priority=r.priority, parent=r.adrg_code) for r in rows]


@router.get("/drg/{code}/ast", response_model=RuleDetailOut)
def get_drg_ast(code: str, session: Annotated[Session, Depends(get_session)]):
    row = session.query(DrgRule).filter(DrgRule.drg_code == code.upper()).one_or_none()
    if not row:
        raise HTTPException(404, "DRG not found")
    ast = json.loads(row.parsed_ast) if row.parsed_ast else None
    return RuleDetailOut(
        code=row.drg_code, name=row.drg_name, rule_expr=row.rule_expr,
        priority=row.priority, parent=row.adrg_code, parsed_ast=ast,
    )


@router.get("/drg-codes")
def list_all_drg_codes(session: Annotated[Session, Depends(get_session)]):
    """一次性返回所有 DRG 编码（带名称），避免前端 N+1 调用。"""
    rows = session.query(DrgRule.drg_code, DrgRule.drg_name).order_by(DrgRule.drg_code).all()
    return [{"code": r.drg_code, "name": r.drg_name} for r in rows]
