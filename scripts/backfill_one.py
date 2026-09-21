"""对单个 case 做字段回填（不依赖 HIS 重新同步）。

适用场景：
- 老数据某字段缺失或解析错（如 xsrtl / xsrtz / nl）
- HIS 端 FWORKRQ 已超出常规同步窗口，重跑 sync 拉不到
- 想精确修一条，不动其他记录

用法：
  python backfill_one.py --case 220172-20260225 --nl 0 --xsrtl 16 --xsrtz 3200
  python backfill_one.py --case 079012-20260228 --nl 74 --xb 1

只填你给的字段；不传的字段不动。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.db.models import Case                # noqa: E402
from app.db.session import SessionLocal       # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("backfill")

NULLABLE_FIELDS = ("zyzd", "zyss", "nl", "xb", "xsrtl", "xsrtz",
                   "admission_date", "discharge_date", "discharge_department")


def main() -> int:
    p = argparse.ArgumentParser(description="单条 case 字段回填")
    p.add_argument("--case", required=True, help="case_id（FPRN-yyyyMMdd）")
    p.add_argument("--nl", type=int, help="年龄（岁）")
    p.add_argument("--xb", type=int, choices=(1, 2, 9), help="性别（1男 2女 9未知）")
    p.add_argument("--xsrtl", type=int, help="新生儿日龄")
    p.add_argument("--xsrtz", type=int, help="新生儿入院体重（克）")
    p.add_argument("--zyzd", help="主诊断（医保版编码）")
    p.add_argument("--zyss", help="主手术")
    p.add_argument("--dept", help="出院科室")
    args = p.parse_args()

    fields = {k: v for k, v in vars(args).items()
              if k in NULLABLE_FIELDS and v is not None}
    if not fields:
        log.error("至少要传一个字段（--nl / --xb / --xsrtl / --xsrtz / ...）")
        return 1

    session = SessionLocal()
    try:
        c = session.query(Case).filter_by(case_id=args.case).one_or_none()
        if not c:
            log.error("未找到 case_id=%s", args.case)
            return 1

        log.info("修改前 %s：%s", c.case_id,
                 {k: getattr(c, k) for k in fields})
        for k, v in fields.items():
            setattr(c, k, v)
        session.commit()

        log.info("修改后 %s：%s", c.case_id,
                 {k: getattr(c, k) for k in fields})
        log.info("回填完成；如需重新分组，跑 python scripts/regroup_all.py")
        return 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
