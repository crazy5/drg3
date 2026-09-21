"""重跑指定月份的病案分组（覆盖 case_results）。

用途：规则引擎修复后，让受影响月份的 case_results 重新走漏斗，写新结果。
旧的 case_results 保留（审计用），但前端的 stats / list 都按 created_at DESC 取最新。

参数：
- --month YYYY-MM    要重跑的出院月份（默认 2026-08）
- --all              重跑所有（注意会覆盖几万行）

写法：单线程顺序跑（重跑是一次性脚本，不需要并发）。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# 让脚本能 import app.*
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.db.session import SessionLocal, init_db  # noqa: E402
from app.db.models import Case, CaseResult  # noqa: E402
from app.engine.grouper.pipeline import Case as EngineCase, group_case  # noqa: E402
from app.engine.index import build_index  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("regroup")


def regroup_month(month_prefix: str) -> None:
    """重跑某个月份的所有 case，写新 case_results。"""
    session = SessionLocal()
    try:
        n_cases = session.query(Case).filter(
            Case.discharge_date.like(f"{month_prefix}%")
        ).count()
        log.info("出院的 %s 病案共 %d 条，开始重跑", month_prefix, n_cases)

        # 启动期构建内存索引
        idx = build_index(session)
        log.info("索引构建完成：%d MDC / %d ADRG / %d DRG",
                 len(idx.mdc_rules), len(idx.adrg_rules), len(idx.drg_rules))

        cases = session.query(Case).filter(
            Case.discharge_date.like(f"{month_prefix}%")
        ).all()

        n_success = n_fallback = n_error = 0
        t0 = datetime.utcnow()
        for i, c in enumerate(cases, 1):
            try:
                qtzd_list = json.loads(c.qtzd_list or "[]")
                qtss_list = json.loads(c.qtss_list or "[]")
                ec = EngineCase(
                    zyzd=c.zyzd or "",
                    zyss=c.zyss,
                    qtzd_list=qtzd_list,
                    qtss_list=qtss_list,
                    nl=c.nl, xb=c.xb,
                    xsrtl=c.xsrtl, xsrtz=c.xsrtz,
                )
                r = group_case(ec, idx)
                session.add(CaseResult(
                    case_id=c.case_id,
                    mdc_code=r.mdc_code,
                    adrg_code=r.adrg_code,
                    drg_code=r.drg_code,
                    error_type=r.error_type,
                    error_msg=r.error_msg,
                    evidence_json=json.dumps(r.evidence, ensure_ascii=False),
                    duration_ms=r.duration_ms,
                ))
                if r.error_type == "success":
                    n_success += 1
                elif r.error_type == "fallback":
                    n_fallback += 1
                else:
                    n_error += 1
                if i % 100 == 0:
                    session.commit()
                    elapsed = (datetime.utcnow() - t0).total_seconds()
                    rate = i / elapsed if elapsed else 0
                    log.info("[%d/%d] success=%d fallback=%d error=%d 速率=%.1f/s",
                             i, n_cases, n_success, n_fallback, n_error, rate)
            except Exception as e:        # noqa: BLE001
                log.warning("case_id=%s 重跑失败: %s", c.case_id, e)
                n_error += 1
        session.commit()
        log.info("完成：total=%d success=%d fallback=%d error=%d  用时=%.1fs",
                 n_cases, n_success, n_fallback, n_error,
                 (datetime.utcnow() - t0).total_seconds())
    finally:
        session.close()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--month", default=None, help="出院月份前缀 YYYY-MM；不传则跑全库")
    args = p.parse_args()
    if args.month:
        regroup_month(args.month)
        return 0
    # 全库重跑：按月份顺序跑（每 100 条 commit 一次）
    session = SessionLocal()
    try:
        months = [r[0] for r in session.query(Case.discharge_date).distinct()
                  .filter(Case.discharge_date.isnot(None))
                  .all() if r[0]]
        # 取 YYYY-MM 前缀
        prefixes = sorted({m.strftime("%Y-%m") for m in months})
        for prefix in prefixes:
            regroup_month(prefix)
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())