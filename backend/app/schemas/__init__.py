"""Pydantic DTO。"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


# ────────── 病案 ──────────


class CaseIn(BaseModel):
    case_id: str | None = None
    zyzd: str
    zyss: str | None = None
    qtzd_list: list[str] = []
    qtss_list: list[str] = []
    nl: int | None = None
    xb: int | None = None
    xsrtl: int | None = None
    xsrtz: int | None = None
    admission_date: date | None = None
    discharge_date: date | None = None
    discharge_department: str | None = None
    source: str | None = "manual"


class CodeOut(BaseModel):
    """带中文名的 ICD 编码对。"""
    code: str
    name: str | None = None


class CaseSummaryOut(BaseModel):
    """病案摘要：编码 + 中文名（从 ICD 字典查询）。"""
    zyzd: CodeOut
    zyss: CodeOut | None = None
    qtzd_list: list[CodeOut] = []
    qtss_list: list[CodeOut] = []
    nl: int | None = None
    xb: int | None = None
    xsrtl: int | None = None
    xsrtz: int | None = None
    admission_date: date | None = None
    discharge_date: date | None = None
    discharge_department: str | None = None


class GroupResultOut(BaseModel):
    mdc_code: str | None = None
    mdc_name: str | None = None
    adrg_code: str | None = None
    adrg_name: str | None = None
    drg_code: str
    drg_name: str | None = None
    cc_level: Literal["MCC", "CC", "NONE"]
    error_type: str
    error_msg: str | None = None
    evidence: dict
    case_summary: CaseSummaryOut | None = None
    duration_ms: int
    case_id: str | None = None
    result_id: int | None = None


# ────────── 规则 ──────────


class SetSummary(BaseModel):
    set_id: str
    size: int


class SetMemberOut(BaseModel):
    code: str
    code_name: str | None = None
    code_type: str


class RuleOut(BaseModel):
    code: str
    name: str
    rule_expr: str
    priority: int
    parent: str | None = None
    adrg_type: str | None = None


class RuleDetailOut(RuleOut):
    parsed_ast: dict | None = None


# ────────── 导入 / 任务 ──────────


class ImportSummary(BaseModel):
    mdc: int
    adrg: int
    drg: int
    sets: int
    cc: int
    exclusion: int
    parse_errors: list[dict]


class BatchStartOut(BaseModel):
    job_id: str
    total: int
    status: str = "pending"


class BatchStatusOut(BaseModel):
    job_id: str
    status: str
    total: int
    processed: int
    succeeded: int
    fallback: int
    failed: int
    started_at: str | None
    finished_at: str | None


# ────────── 错误 ──────────


class ApiError(BaseModel):
    detail: str
