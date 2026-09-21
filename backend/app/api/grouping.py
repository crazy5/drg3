"""批量分组 + 任务管理 API。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_rule_index
from app.core.config import UPLOAD_DIR
from app.db.models import BatchJob
from app.db.session import get_session
from app.engine.index import RuleIndex
from app.schemas import BatchStartOut, BatchStatusOut
from app.tasks.batch_grouping import (
    MAX_BATCH,
    create_batch_job,
    parse_uploaded_cases,
    run_batch_job,
)

router = APIRouter(prefix="/api/grouping", tags=["grouping"])


@router.post("/batch", response_model=BatchStartOut)
async def batch_grouping(
    bg: BackgroundTasks,
    file: Annotated[UploadFile, File(description="xlsx 或 csv")],
    index: Annotated[RuleIndex, Depends(get_rule_index)],
):
    """上传 xlsx/csv → 创建任务 → 后台跑分组。"""
    # 落盘
    suffix = Path(file.filename or "").suffix.lower() or ".xlsx"
    save_path = UPLOAD_DIR / f"upload_{file.filename or 'batch'}"
    save_path.write_bytes(await file.read())

    cases = parse_uploaded_cases(save_path)
    if not cases:
        raise HTTPException(400, "empty file or no valid rows")
    if len(cases) > MAX_BATCH:
        raise HTTPException(413, f"too many rows: {len(cases)} > {MAX_BATCH}")

    job_id = create_batch_job(save_path, total=len(cases))

    # 后台执行
    bg.add_task(run_batch_job, job_id, cases, index)

    return BatchStartOut(job_id=job_id, total=len(cases), status="pending")


@router.get("/jobs/{job_id}", response_model=BatchStatusOut)
def get_job(job_id: str, session: Annotated[Session, Depends(get_session)]):
    row = session.query(BatchJob).filter_by(job_id=job_id).one_or_none()
    if not row:
        raise HTTPException(404, "job not found")
    return BatchStatusOut(
        job_id=row.job_id,
        status=row.status,
        total=row.total,
        processed=row.processed,
        succeeded=row.succeeded,
        fallback=row.fallback,
        failed=row.failed,
        started_at=row.started_at.isoformat() if row.started_at else None,
        finished_at=row.finished_at.isoformat() if row.finished_at else None,
    )
