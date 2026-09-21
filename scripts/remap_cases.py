"""把 cases 表里现有的 ICD 编码也过一遍国临→医保映射。

背景：
- 之前 sync.py 没做映射，DB 里 cases.zyzd 是国临版（I84.201）
- 改了 sync.py 加映射后，新同步进来的数据是医保版
- 但老数据（已在 DB 的）还是国临版，分组兜底

注意：
- 诊断（zyzd / qtzd_list）走 ICD10 国临版 2.0 → 医保版 2.0 映射
- 手术（zyss / qtss_list）走 ICD9 国临版 3.0 → 医保版 2.0 映射（**单独的表**）

做法：
- 加载两份映射表
- 对 cases 表每行的 zyzd / zyss / qtzd_list / qtss_list 分别过对应映射
- 改动落库
- 打印改动条数

⚠️ 改前请备份 DB！脚本会直接 UPDATE。
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.db.models import Case               # noqa: E402
from app.db.session import SessionLocal      # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("remap")

DX_MAPPING_PATH = REPO_ROOT / "backend" / "data" / "icd_mapping.json"
OP_MAPPING_PATH = REPO_ROOT / "backend" / "data" / "icd_op_mapping.json"


def _norm(code: str | None) -> str:
    if not code:
        return ""
    return str(code).strip().upper().rstrip(".")


def main() -> int:
    if not DX_MAPPING_PATH.exists():
        log.error("诊断映射表不存在：%s（先跑 scripts/build_icd_mapping.py）", DX_MAPPING_PATH)
        return 1
    if not OP_MAPPING_PATH.exists():
        log.error("手术映射表不存在：%s（先跑 scripts/build_icd_op_mapping.py）", OP_MAPPING_PATH)
        return 1
    with open(DX_MAPPING_PATH, encoding="utf-8") as f:
        dx_map = json.load(f)
    with open(OP_MAPPING_PATH, encoding="utf-8") as f:
        op_map = json.load(f)
    log.info("加载诊断映射 %d 条；手术映射 %d 条", len(dx_map), len(op_map))

    def map_dx(code: str | None) -> str | None:
        if not code:
            return code
        return dx_map.get(_norm(code), code)

    def map_op(code: str | None) -> str | None:
        if not code:
            return code
        return op_map.get(_norm(code), code)

    def map_dx_list(codes_json: str | None) -> tuple[str, int]:
        if not codes_json:
            return codes_json, 0
        try:
            arr = json.loads(codes_json)
        except json.JSONDecodeError:
            return codes_json, 0
        n = 0
        out = []
        for c in arr:
            new = dx_map.get(_norm(c), c)
            if new != c:
                n += 1
            out.append(new)
        return json.dumps(out, ensure_ascii=False), n

    def map_op_list(codes_json: str | None) -> tuple[str, int]:
        if not codes_json:
            return codes_json, 0
        try:
            arr = json.loads(codes_json)
        except json.JSONDecodeError:
            return codes_json, 0
        n = 0
        out = []
        for c in arr:
            new = op_map.get(_norm(c), c)
            if new != c:
                n += 1
            out.append(new)
        return json.dumps(out, ensure_ascii=False), n

    session = SessionLocal()
    try:
        cases = session.query(Case).all()
        log.info("扫描 %d 条 case", len(cases))

        n_zyzd = n_zyss = n_qtzd = n_qtss = 0
        affected = 0
        for c in cases:
            old_zyzd = c.zyzd or ""
            new_zyzd = map_dx(old_zyzd) or ""
            old_zyss = c.zyss
            new_zyss = map_op(old_zyss)
            new_qtzd_json, n1 = map_dx_list(c.qtzd_list)
            new_qtss_json, n2 = map_op_list(c.qtss_list)

            row_changed = (new_zyzd != old_zyzd or new_zyss != old_zyss or n1 or n2)
            if row_changed:
                affected += 1
                if new_zyzd != old_zyzd:
                    n_zyzd += 1
                    log.info("  %s zyzd: %s → %s", c.case_id, old_zyzd, new_zyzd)
                if new_zyss != old_zyss:
                    n_zyss += 1
                    log.info("  %s zyss: %s → %s", c.case_id, old_zyss, new_zyss)
                if n1:
                    n_qtzd += n1
                if n2:
                    n_qtss += n2
                c.zyzd = new_zyzd
                c.zyss = new_zyss
                c.qtzd_list = new_qtzd_json
                c.qtss_list = new_qtss_json

        session.commit()
        log.info("=" * 60)
        log.info("完成：%d/%d 条 case 编码被改写", affected, len(cases))
        log.info("  zyzd: %d 处, zyss: %d 处, qtzd_list: %d 处, qtss_list: %d 处",
                 n_zyzd, n_zyss, n_qtzd, n_qtss)

        return 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
