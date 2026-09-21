"""HIS 同步接收 API。

88.3 桥接机上的同步脚本每天凌晨从 HIS 拉前一天数据，POST 到这里。
- `POST /api/sync/from-his` 接收批量病案 + 立即分组 + 写同步日志
- `GET  /api/sync/logs`      查看同步历史
- `GET  /api/sync/logs/{id}` 查看单次同步详情
"""
from __future__ import annotations

import json
import logging
import os
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.cases import _norm, _rule_names, _summary
from app.api.deps import get_rule_index
from app.db.models import Case, CaseResult, HisSyncLog
from app.db.session import SessionLocal, get_session
from app.engine.grouper.pipeline import Case as EngineCase, group_case
from app.engine.index import RuleIndex
from app.schemas import GroupResultOut

log = logging.getLogger(__name__)

# 并发处理批量内每条病案的 worker 数。
# 88.2 后端串行处理 100 条 ~10s（SQLite 锁 + DRG 分组），并发后 ~1.5s。
# 数值要 ≤ SQLite WAL 模式能容忍的并发写（实测 ≤16 安全），又要 ≤ pool_size + max_overflow（15）。
_SYNC_MAX_WORKERS = int(os.environ.get("HIS_SYNC_MAX_WORKERS", "8"))


router = APIRouter(prefix="/api/sync", tags=["sync"])


# ────────── DTO ──────────


class HisSyncCaseIn(BaseModel):
    """88.3 推过来的单条病案（字段名已在 HIS 端 SQL 里 alias 好）。"""
    case_id: str
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
    update_time: datetime | None = None  # HIS 工作日期，用于排查


class HisSyncBatchIn(BaseModel):
    source: str | None = None           # 88.3 主机标识，便于排查
    start_date: date | None = None      # HIS 查询窗口起
    end_date: date | None = None        # HIS 查询窗口止（含）
    cases: list[HisSyncCaseIn] = Field(default_factory=list)


class HisSyncCaseResultOut(BaseModel):
    case_id: str
    drg_code: str
    error_type: str
    error_msg: str | None = None
    result_id: int | None = None
    is_updated: bool = False            # True=已存在的 case 被覆盖；False=新插入


class HisSyncBatchOut(BaseModel):
    log_id: int
    received: int
    inserted: int
    updated: int
    succeeded: int
    fallback: int
    failed: int
    status: Literal["success", "partial", "failed"]
    detail: list[HisSyncCaseResultOut]


class HisSyncLogOut(BaseModel):
    log_id: int
    sync_type: str
    source: str | None
    start_date: date | None
    end_date: date | None
    received: int
    inserted: int
    updated: int
    succeeded: int
    fallback: int
    failed: int
    status: str
    error_msg: str | None
    started_at: datetime
    finished_at: datetime | None


# ────────── 端点 ──────────


@router.post("/from-his", response_model=HisSyncBatchOut)
def sync_from_his(
    body: HisSyncBatchIn,
    session: Annotated[Session, Depends(get_session)],
    index: Annotated[RuleIndex, Depends(get_rule_index)],
):
    """88.3 同步脚本 POST 入口。

    行为：
    - 按 case_id 逐条 upsert（已存在则覆盖 ICD/日期/科室，并保留原 created_at）
    - 立即分组，写 case_results（每次分组产生一条新结果）
    - 整批完成后写一行 his_sync_logs
    - **并发处理**：batch 内每条病案用独立 Session + ThreadPoolExecutor 并发跑
      （DB 已切 WAL 模式，SQLite 并发写不互锁；DRG 分组是纯 CPU，可真并发）
    """
    if not body.cases:
        raise HTTPException(400, "cases is empty")

    started_at = datetime.utcnow()
    log_row = HisSyncLog(
        sync_type="manual",   # 88.3 调用方不管类型，统一记 manual
        source=body.source,
        start_date=body.start_date,
        end_date=body.end_date,
        received=len(body.cases),
    )
    session.add(log_row)
    session.commit()
    log_id = log_row.log_id

    # ── 把 raw 提前 split + normalize，避免线程里重复干 ──
    prepared: list[tuple[str, dict]] = []
    for raw in body.cases:
        case_id = raw.case_id.strip() if raw.case_id else ""
        if not case_id:
            prepared.append(("", {}))
            continue
        prepared.append((case_id, {
            "zyzd": _norm(raw.zyzd),
            "zyss": _norm(raw.zyss),
            "qtzd_list": [_norm(c) for c in (raw.qtzd_list or []) if c and c.strip()],
            "qtss_list": [_norm(c) for c in (raw.qtss_list or []) if c and c.strip()],
            "nl": raw.nl,
            "xb": raw.xb,
            "xsrtl": raw.xsrtl,
            "xsrtz": raw.xsrtz,
            "admission_date": raw.admission_date,
            "discharge_date": raw.discharge_date,
            "discharge_department": (raw.discharge_department or "").strip() or None,
        }))

    detail: list[HisSyncCaseResultOut] = []

    # ── 并发处理每条病案 ──
    # max_workers 控制并发度：≤ pool_size + max_overflow（15），避免连接池耗尽
    max_workers = min(_SYNC_MAX_WORKERS, len(prepared))
    if max_workers <= 1:
        # 小批量直接串行（避免线程池 overhead）
        results_iter = (_process_one_case(case_id, payload, index) for case_id, payload in prepared)
    else:
        executor = ThreadPoolExecutor(max_workers=max_workers)
        try:
            futures = [
                executor.submit(_process_one_case, case_id, payload, index)
                for case_id, payload in prepared
            ]
            results_iter = (f.result() for f in as_completed(futures))
        finally:
            executor.shutdown(wait=True)

    inserted = updated = succeeded = fallback = failed = 0
    for entry in results_iter:
        detail.append(entry)
        if entry.error_type == "success":
            succeeded += 1
        elif entry.error_type == "fallback":
            fallback += 1
        elif entry.error_type == "error":
            failed += 1
        if entry.is_updated:
            updated += 1
        elif entry.error_type != "error" and entry.case_id and entry.case_id != "(empty)":
            inserted += 1

    # ── 更新 his_sync_logs 行（用主线程 session，expire_on_commit=False 保 identity） ──
    log_row.inserted = inserted
    log_row.updated = updated
    log_row.succeeded = succeeded
    log_row.fallback = fallback
    log_row.failed = failed
    log_row.status = "success" if failed == 0 else "partial"
    log_row.finished_at = datetime.utcnow()
    session.commit()

    log.info(
        "HIS sync: log_id=%s 收=%d 插=%d 更=%d 成功=%d 兜=%d 败=%d",
        log_id, len(body.cases), inserted, updated, succeeded, fallback, failed,
    )
    return HisSyncBatchOut(
        log_id=log_id,
        received=len(body.cases),
        inserted=inserted,
        updated=updated,
        succeeded=succeeded,
        fallback=fallback,
        failed=failed,
        status="success" if failed == 0 else "partial",
        detail=detail,
    )


def _process_one_case(case_id: str, payload: dict, index: RuleIndex) -> HisSyncCaseResultOut:
    """处理单条病案：upsert + group + 写 case_results。

    用独立 SessionLocal()，配合 WAL 模式可并发跑。
    RuleIndex 是只读内存对象，多线程同时持有没问题。
    """
    if not case_id:
        return HisSyncCaseResultOut(
            case_id="(empty)", drg_code="0000",
            error_type="error", error_msg="case_id 为空",
        )

    s = SessionLocal()
    try:
        zyzd = payload["zyzd"]
        zyss = payload["zyss"]
        qtzd_list = payload["qtzd_list"]
        qtss_list = payload["qtss_list"]

        existing = s.query(Case).filter_by(case_id=case_id).one_or_none()
        if existing:
            existing.zyzd = zyzd or ""
            existing.zyss = zyss
            existing.qtzd_list = json.dumps(qtzd_list, ensure_ascii=False)
            existing.qtss_list = json.dumps(qtss_list, ensure_ascii=False)
            existing.nl = payload["nl"]
            existing.xb = payload["xb"]
            existing.xsrtl = payload["xsrtl"]
            existing.xsrtz = payload["xsrtz"]
            existing.admission_date = payload["admission_date"]
            existing.discharge_date = payload["discharge_date"]
            existing.discharge_department = payload["discharge_department"]
            is_updated = True
        else:
            s.add(Case(
                case_id=case_id,
                zyzd=zyzd or "",
                zyss=zyss,
                qtzd_list=json.dumps(qtzd_list, ensure_ascii=False),
                qtss_list=json.dumps(qtss_list, ensure_ascii=False),
                nl=payload["nl"], xb=payload["xb"],
                xsrtl=payload["xsrtl"], xsrtz=payload["xsrtz"],
                admission_date=payload["admission_date"],
                discharge_date=payload["discharge_date"],
                discharge_department=payload["discharge_department"],
                source="his_sync",
            ))
            is_updated = False
        s.flush()

        engine_case = EngineCase(
            zyzd=zyzd, zyss=zyss,
            qtzd_list=qtzd_list, qtss_list=qtss_list,
            nl=payload["nl"], xb=payload["xb"],
            xsrtl=payload["xsrtl"], xsrtz=payload["xsrtz"],
        )
        result = group_case(engine_case, index)

        result_row = CaseResult(
            case_id=case_id,
            mdc_code=result.mdc_code,
            adrg_code=result.adrg_code,
            drg_code=result.drg_code,
            error_type=result.error_type,
            error_msg=result.error_msg,
            evidence_json=json.dumps(result.evidence, ensure_ascii=False),
            duration_ms=result.duration_ms,
        )
        s.add(result_row)
        s.commit()

        return HisSyncCaseResultOut(
            case_id=case_id,
            drg_code=result.drg_code or "0000",
            error_type=result.error_type,
            error_msg=result.error_msg,
            result_id=result_row.result_id,
            is_updated=is_updated,
        )
    except Exception as e:        # noqa: BLE001
        s.rollback()
        log.warning("case_id=%s 处理失败: %s", case_id, e)
        return HisSyncCaseResultOut(
            case_id=case_id, drg_code="0000",
            error_type="error", error_msg=str(e)[:200],
            is_updated=False,
        )
    finally:
        s.close()


@router.get("/logs", response_model=list[HisSyncLogOut])
def list_sync_logs(
    session: Annotated[Session, Depends(get_session)],
    limit: int = Query(50, ge=1, le=500),
):
    """同步日志列表，按时间倒序。"""
    rows = session.query(HisSyncLog).order_by(HisSyncLog.log_id.desc()).limit(limit).all()
    return [_log_to_out(r) for r in rows]


@router.get("/logs/{log_id}", response_model=HisSyncLogOut)
def get_sync_log(
    log_id: int,
    session: Annotated[Session, Depends(get_session)],
):
    row = session.query(HisSyncLog).filter_by(log_id=log_id).one_or_none()
    if not row:
        raise HTTPException(404, "log not found")
    return _log_to_out(row)


def _log_to_out(row: HisSyncLog) -> HisSyncLogOut:
    return HisSyncLogOut(
        log_id=row.log_id,
        sync_type=row.sync_type or "manual",
        source=row.source,
        start_date=row.start_date,
        end_date=row.end_date,
        received=row.received or 0,
        inserted=row.inserted or 0,
        updated=row.updated or 0,
        succeeded=row.succeeded or 0,
        fallback=row.fallback or 0,
        failed=row.failed or 0,
        status=row.status or "success",
        error_msg=row.error_msg,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )