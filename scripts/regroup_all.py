"""重新跑所有病例的分组。

适用场景：
- 改了 ICD 字典（scripts/build_icd_dict.py）
- 改了 ICD 国临→医保映射（scripts/build_icd_mapping.py）
- 修了 engine / evaluator 的 bug（如 X 扩展回退）
- 改了规则或集合导入

做法：
- 从 SQLite 读所有 Case
- 用最新的 RuleIndex（重新构建一次）跑 group_case
- 删旧 CaseResult，按 case_id 写新结果
- 打印汇总

注意：DB 路径写死 data/drg.db（项目根），与服务进程一致。
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.db.models import Case, CaseResult     # noqa: E402
from app.db.session import SessionLocal        # noqa: E402
from app.engine.grouper.pipeline import Case as EngineCase, group_case  # noqa: E402
from app.engine.index import build_index       # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
log = logging.getLogger("regroup")


def main() -> int:
    session = SessionLocal()
    try:
        # 1. 加载最新规则索引（直接走 SQL，不依赖运行中的 FastAPI）
        log.info("构建规则索引…")
        t0 = time.perf_counter()
        index = build_index(session)
        log.info("索引构建完成：MDC %d / ADRG %d / DRG %d / 集合 %d，耗时 %.2fs",
                 len(index.mdc_rules), sum(len(v) for v in index.adrg_rules.values()),
                 sum(len(v) for v in index.drg_rules.values()), len(index.sets),
                 time.perf_counter() - t0)

        # 2. 读所有病案
        cases = session.query(Case).all()
        total = len(cases)
        log.info("共有 %d 条病案，开始重新分组…", total)
        if total == 0:
            return 0

        # 3. 删旧 results（整表清，按 case_id 重建）
        deleted = session.query(CaseResult).delete()
        log.info("已删除 %d 条旧 result", deleted)
        session.commit()

        succeeded = fallback = failed = 0
        fallback_case_ids: list[tuple[str, str]] = []   # (case_id, zyzd) for inspection

        for i, c in enumerate(cases, 1):
            try:
                # 解析 JSON 字段
                qtzd = json.loads(c.qtzd_list) if c.qtzd_list else []
                qtss = json.loads(c.qtss_list) if c.qtss_list else []

                ec = EngineCase(
                    zyzd=c.zyzd or "",
                    zyss=c.zyss,
                    qtzd_list=qtzd,
                    qtss_list=qtss,
                    nl=c.nl, xb=c.xb,
                    xsrtl=c.xsrtl, xsrtz=c.xsrtz,
                )
                result = group_case(ec, index)

                row = CaseResult(
                    case_id=c.case_id,
                    mdc_code=result.mdc_code,
                    adrg_code=result.adrg_code,
                    drg_code=result.drg_code,
                    cc_level=result.cc_level,
                    error_type=result.error_type,
                    error_msg=result.error_msg,
                    evidence_json=json.dumps(result.evidence, ensure_ascii=False),
                    duration_ms=result.duration_ms,
                )
                session.add(row)

                if result.error_type == "success":
                    succeeded += 1
                elif result.error_type == "fallback":
                    fallback += 1
                    fallback_case_ids.append((c.case_id, c.zyzd))
                else:
                    failed += 1
            except Exception as e:        # noqa: BLE001
                log.exception("case_id=%s 重跑失败", c.case_id)
                failed += 1

            # 每 100 条提交一次
            if i % 100 == 0 or i == total:
                session.commit()
                log.info("进度 %d/%d", i, total)

        log.info("=" * 60)
        log.info("重跑完成（%s）", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        log.info("总数 %d | 成功 %d (%.1f%%) | 兜底 %d (%.1f%%) | 失败 %d",
                 total, succeeded, succeeded / total * 100,
                 fallback, fallback / total * 100, failed)

        if fallback_case_ids:
            log.info("\n兜底 %d 条：", len(fallback_case_ids))
            for cid, zyzd in fallback_case_ids:
                log.info("  %s  zyzd=%s", cid, zyzd)

        return 0 if failed == 0 else 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
