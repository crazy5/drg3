"""从 国临版 2.0 ↔ 医保版 2.0 对照表构建映射字典。

源文件（项目根目录）：
- 1_ICD10国临版2.0对照医保版2.0_0125(1)(1)(1).xlsx
  列：国临码 | 国临名 | 医保码 | 医保名

HIS 送来的是国临版编码，DRG 3.0 规则集合用医保版编码（set_members）。
sync.py 在 POST 前通过 icd_mapping.json 把国临转医保版。

输出：
- backend/data/icd_mapping.json — {"<国临码大写去点>": "<医保码大写去点>"}
- backend/data/icd_medical_name.json — {"<医保码大写去点>": "<医保名>"}
  icd_dict.py 把这个表作为「二级兜底」：原码（医保码）查 icd_dict.json 未命中时来查它，
  用于解决医保版新编码在字典源 xlsx 里没收录的问题。
"""
from __future__ import annotations

import json
from pathlib import Path

import openpyxl

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = REPO_ROOT / "1_ICD10国临版2.0对照医保版2.0_0125(1)(1)(1).xlsx"
OUT_PATH = REPO_ROOT / "backend" / "data" / "icd_mapping.json"
OUT_MEDICAL = REPO_ROOT / "backend" / "data" / "icd_medical_name.json"


def _norm(code: str | None) -> str:
    """国临/医保码归一：大写 + 去末尾点（与 icd_dict.normalize_icd 一致）。"""
    if not code:
        return ""
    return str(code).strip().upper().rstrip(".")


def main() -> None:
    if not SRC_PATH.exists():
        raise SystemExit(f"未找到对照表: {SRC_PATH}")

    wb = openpyxl.load_workbook(SRC_PATH, read_only=True)
    ws = wb.active

    mapping: dict[str, str] = {}
    medical: dict[str, str] = {}        # 医保码 → 医保名（icd_dict 二级兜底用）
    n_rows = n_added = n_same = n_skip = 0
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue                    # 跳过表头
        if not row or row[0] is None:
            continue
        n_rows += 1
        src = _norm(row[0])
        dst = _norm(row[2])
        if not src or not dst:
            n_skip += 1
            continue
        if src == dst:
            n_same += 1
            # 也保留一份（国临 = 医保 时也记录，下游统一走映射逻辑）
        if src in mapping and mapping[src] != dst:
            # 同一国临码映射到不同医保码（理论上不应该发生）：保留先入的 + WARN
            print(f"  [WARN] {src} 重复映射：先={mapping[src]} 新={dst}，保留先入")
            continue
        mapping[src] = dst
        n_added += 1

        # 医保名（行 4）：可能有缺失
        if row[3]:
            name = str(row[3]).strip()
            # 后入优先（避免被空名覆盖），但 xlsx 里同一医保码只出现一次，所以后入等同覆盖
            if dst not in medical and name:
                medical[dst] = name

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, separators=(",", ":"))
    with open(OUT_MEDICAL, "w", encoding="utf-8") as f:
        json.dump(medical, f, ensure_ascii=False, separators=(",", ":"))
    print(f"读 {n_rows} 行，写 {n_added} 条映射（自映射 {n_same}，空行 {n_skip}）→ {OUT_PATH}")
    print(f"医保名二级表 {len(medical)} 条 → {OUT_MEDICAL}")

    # 验证：I84 痔系列
    print("\n=== 验证：I84 痔系列 ===")
    samples = [
        ("I84.000", "血栓性内痔"),
        ("I84.201", "混合痔"),
        ("I84.200x002", "内痔"),
        ("I84.201", "重复确认"),
    ]
    for code, desc in samples:
        key = _norm(code)
        print(f"  {key:18s} ({desc}) → 医保码={mapping.get(key)}  医保名={medical.get(mapping.get(key, ''), '-')}")


if __name__ == "__main__":
    main()
