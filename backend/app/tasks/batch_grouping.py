"""批量分组后台任务。

策略：
- 用 FastAPI BackgroundTasks（在请求线程外跑）
- 进度写入 batch_jobs 表，前端轮询查询
- 单批上限 10000 条
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import UPLOAD_DIR
from app.db.models import BatchJob, Case, CaseResult
from app.db.session import SessionLocal
from app.engine.grouper.pipeline import Case as EngineCase, group_case
from app.engine.index import RuleIndex

log = logging.getLogger(__name__)

MAX_BATCH = 10000


def create_batch_job(file_path: Path, total: int) -> str:
    """创建 batch_jobs 行，返回 job_id。"""
    session = SessionLocal()
    try:
        job_id = f"J{uuid.uuid4().hex[:10].upper()}"
        job = BatchJob(
            job_id=job_id,
            status="pending",
            total=total,
            file_path=str(file_path),
            started_at=datetime.utcnow(),
        )
        session.add(job)
        session.commit()
        return job_id
    finally:
        session.close()


def run_batch_job(job_id: str, cases: list[dict], index: RuleIndex) -> None:
    """实际执行批量分组，更新进度。"""
    session = SessionLocal()
    try:
        job = session.query(BatchJob).filter_by(job_id=job_id).one()
        job.status = "running"
        session.commit()

        succeeded = fallback = failed = 0

        for i, raw in enumerate(cases, start=1):
            try:
                engine_case = EngineCase(
                    zyzd=raw.get("zyzd", ""),
                    zyss=raw.get("zyss"),
                    qtzd_list=raw.get("qtzd_list") or [],
                    qtss_list=raw.get("qtss_list") or [],
                    nl=raw.get("nl"), xb=raw.get("xb"),
                    xsrtl=raw.get("xsrtl"), xsrtz=raw.get("xsrtz"),
                )
                result = group_case(engine_case, index)

                # 写入病案 + 结果
                case_id = raw.get("case_id") or f"C{uuid.uuid4().hex[:10].upper()}"
                case_row = Case(
                    case_id=case_id,
                    zyzd=raw.get("zyzd", ""),
                    zyss=raw.get("zyss"),
                    qtzd_list=json.dumps(raw.get("qtzd_list") or [], ensure_ascii=False),
                    qtss_list=json.dumps(raw.get("qtss_list") or [], ensure_ascii=False),
                    nl=raw.get("nl"), xb=raw.get("xb"),
                    xsrtl=raw.get("xsrtl"), xsrtz=raw.get("xsrtz"),
                    admission_date=raw.get("admission_date"),
                    discharge_date=raw.get("discharge_date"),
                    discharge_department=raw.get("discharge_department"),
                    source="batch",
                )
                session.merge(case_row)
                session.flush()

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
                session.add(result_row)

                if result.error_type == "success":
                    succeeded += 1
                elif result.error_type == "fallback":
                    fallback += 1
                else:
                    failed += 1
            except Exception as e:        # noqa: BLE001
                log.exception("batch case failed at row %d", i)
                failed += 1

            # 每 50 条更新进度（避免每条 commit）
            if i % 50 == 0 or i == len(cases):
                job.processed = i
                job.succeeded = succeeded
                job.fallback = fallback
                job.failed = failed
                session.commit()

        job.status = "done"
        job.processed = len(cases)
        job.succeeded = succeeded
        job.fallback = fallback
        job.failed = failed
        job.finished_at = datetime.utcnow()
        session.commit()
    except Exception as e:        # noqa: BLE001
        log.exception("batch job %s failed", job_id)
        try:
            job = session.query(BatchJob).filter_by(job_id=job_id).one()
            job.status = "failed"
            job.error_log = str(e)
            job.finished_at = datetime.utcnow()
            session.commit()
        except Exception:        # noqa: BLE001
            session.rollback()
    finally:
        session.close()


def parse_uploaded_cases(file_path: Path) -> list[dict]:
    """从 xlsx/csv 解析病案列表。

    支持列名（中文）：
      病案号 / case_id / 住院号
      主要诊断 / ZYZD
      主要手术 / ZYSS
      其他诊断 / qtzd_list   （多值用 ; 或 ,）
      其他手术 / qtss_list   （多值用 ; 或 ,）
      年龄 / nl   性别 / xb   新生儿日龄 / xsrtl   新生儿体重 / xsrtz
      入院日期 / admission_date        YYYYMMDD 或 YYYY-MM-DD 或 Excel 日期
      出院日期 / discharge_date       YYYYMMDD 或 YYYY-MM-DD 或 Excel 日期
      出院科室 / discharge_department 文本
    """
    suffix = file_path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        return _parse_xlsx(file_path)
    if suffix == ".csv":
        return _parse_csv(file_path)
    raise ValueError(f"unsupported file type: {suffix}")


def _parse_xlsx(path: Path) -> list[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    return _rows_to_cases(rows)


def _parse_csv(path: Path) -> list[dict]:
    import csv
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rows = [tuple(r) for r in csv.reader(f)]
    if not rows:
        return []
    return _rows_to_cases(rows)


def _rows_to_cases(rows: list[tuple]) -> list[dict]:
    header = [str(h or "").strip() for h in rows[0]]
    col: dict[str, int] = {}
    for i, h in enumerate(header):
        col[h] = i

    def get(row, key):
        idx = col.get(key)
        if idx is None:
            return None
        v = row[idx] if idx < len(row) else None
        return v

    def split_multi(v):
        if not v:
            return []
        return [x.strip().upper().rstrip(".") for x in str(v).replace(";", ",").split(",") if x.strip()]

    def try_int(v):
        if v is None or v == "":
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            return None

    def try_date(v):
        """兼容 YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD / Excel datetime/date。"""
        if v is None or v == "":
            return None
        if hasattr(v, "year"):  # datetime / date
            return v.date() if hasattr(v, "hour") else v
        s = str(v).strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y.%m.%d"):
            try:
                from datetime import datetime as _dt
                return _dt.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    def try_text(v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    cases = []
    for row in rows[1:]:
        if not any(row):
            continue
        cases.append({
            "case_id": str(get(row, "病案号") or get(row, "case_id") or get(row, "住院号") or "").strip() or None,
            "zyzd": str(get(row, "主要诊断") or get(row, "ZYZD") or "").strip().upper().rstrip("."),
            "zyss": str(get(row, "主要手术") or get(row, "ZYSS") or "").strip().upper().rstrip(".") or None,
            "qtzd_list": split_multi(get(row, "其他诊断") or get(row, "qtzd_list")),
            "qtss_list": split_multi(get(row, "其他手术") or get(row, "qtss_list")),
            "nl": try_int(get(row, "年龄") or get(row, "nl")),
            "xb": try_int(get(row, "性别") or get(row, "xb")),
            "xsrtl": try_int(get(row, "新生儿日龄") or get(row, "xsrtl")),
            "xsrtz": try_int(get(row, "新生儿体重") or get(row, "xsrtz")),
            "admission_date": try_date(get(row, "入院日期") or get(row, "admission_date")),
            "discharge_date": try_date(get(row, "出院日期") or get(row, "discharge_date")),
            "discharge_department": try_text(get(row, "出院科室") or get(row, "discharge_department")),
        })
    return cases
