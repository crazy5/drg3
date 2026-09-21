"""从国家临床版 2.0 官方库重建 ICD 字典。

源文件（项目根目录）：
- 国家临床版2.0疾病编码2022修订.xls
- 国家临床3.0手术操作编码（ICD-9-CM3）2025修订.xls（12.4省）.xls

输出：
- backend/data/icd_dict.json — {code: name}，key 转大写
"""
from __future__ import annotations

import json
from pathlib import Path

import xlrd

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "backend" / "data" / "icd_dict.json"

SOURCES = [
    {
        "label": "疾病",
        "path": REPO_ROOT / "国家临床版2.0疾病编码2022修订.xls",
        "code_col": 0,   # ficdm
        "name_col": 1,   # fjbname
    },
    {
        "label": "手术",
        "path": REPO_ROOT / "国家临床3.0手术操作编码（ICD-9-CM3）2025修订.xls（12.4省）.xls",
        "code_col": 0,   # fopcode
        "name_col": 1,   # fopname
    },
]


def main() -> None:
    out: dict[str, str] = {}

    for src in SOURCES:
        path = src["path"]
        print(f"[{src['label']}] {path.name}")
        wb = xlrd.open_workbook(str(path))
        sh = wb.sheet_by_index(0)
        n_rows = sh.nrows
        n_added = n_dup = n_empty = 0
        for i in range(1, n_rows):                # 跳过表头
            code = sh.cell_value(i, src["code_col"])
            name = sh.cell_value(i, src["name_col"])
            if not code or not name:
                n_empty += 1
                continue
            # 转大写（A00.000x001 → A00.000X001），与 DRG 规则集合一致
            code_u = str(code).strip().upper()
            name_s = str(name).strip()
            if not code_u or not name_s:
                n_empty += 1
                continue
            if code_u in out:
                # 同一码出现多次：保留先入的（一般是基础码更优先）
                n_dup += 1
                continue
            out[code_u] = name_s
            n_added += 1
        print(f"  行 {n_rows}, 新增 {n_added}, 重复跳过 {n_dup}, 空行 {n_empty}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\n写入 {OUT_PATH}：{len(out)} 条")

    # 验证
    print("\n=== 验证 ===")
    samples = [
        ("N80.100", "卵巢的子宫内膜异位症（基础码）"),
        ("N80.100X003", "深在性子宫内膜异位症（X 扩展码）"),
        ("A00.000X001", "古典生物型霍乱（小写 x → 大写 X）"),
        ("65.2501", "手术码测试"),
    ]
    with open(OUT_PATH, encoding="utf-8") as f:
        d = json.load(f)
    for code, expected_desc in samples:
        got = d.get(code)
        print(f"  {code}: {got!r}  ({expected_desc})")


if __name__ == "__main__":
    main()
