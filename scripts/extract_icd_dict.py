"""从 DIP xlsx 提取 ICD 编码-中文名映射，作为简易字典。"""
import json
import re
import sys
from pathlib import Path
from openpyxl import load_workbook

XLSX = Path(__file__).parent.parent / "按病种分值（DIP）付费3.0版分组方案.xlsx"
OUT = Path(__file__).parent.parent / "backend" / "data" / "icd_dict.json"

# 形如 "37.5100"、"37.5100x001"、"Z00.000"、"I50.900" 的编码
CODE_RE = re.compile(r"^[A-Z]\d+(\.[A-Za-z0-9]+)?$|^\d+(\.\d+)?[a-z]?\d*(x\d+)?$")

# 哪些 sheet 哪些列对是 编码-名称
SHEET_COLS = {
    "二、并项规则下的核心病种": [
        (2, 3),  # 主要诊断编码/名称
        (4, 5),  # 主要手术编码/名称
        (6, 7),  # 相关手术编码/名称
    ],
    "三、诊断辅助细分-肿瘤类病种": [
        (2, 3),  # 主要诊断
        (4, 5),  # 其他诊断
        (6, 7),  # 手术
    ],
    "三、诊断辅助细分-结核类病种": [
        (2, 3),  # 主要诊断
        (4, 5),  # 主要手术
    ],
    "附表—结核耐药诊断": [
        (0, 1),  # 诊断编码/名称（0-based）
    ],
    "四、基础规则下的核心病种": [
        (2, 3),  # 主要诊断
        (4, 5),  # 主要手术
    ],
    "五、不纳入分组的主要手术操作": [
        (0, 1),  # 手术编码/名称
    ],
    "六、基层病种": [
        (1, 2),  # 主要诊断
        (3, 4),  # 主要手术
    ],
}


def split_codes(cell: str):
    """DIP 里有些 cell 用 | 或 ; 分隔多个编码"""
    if not cell:
        return []
    s = str(cell).strip()
    for sep in ["|", ";", "；", ","]:
        if sep in s:
            return [c.strip() for c in s.split(sep) if c.strip()]
    return [s]


def main():
    wb = load_workbook(XLSX, read_only=True)
    icd_map: dict[str, str] = {}
    stats: dict[str, int] = {}

    for sheet_name, col_pairs in SHEET_COLS.items():
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        added = 0
        for row in ws.iter_rows(min_row=3, values_only=True):
            for code_col, name_col in col_pairs:
                if code_col >= len(row) or name_col >= len(row):
                    continue
                codes = split_codes(row[code_col])
                # 名称只取第一个（多编码共享同一名称）
                name = str(row[name_col] or "").strip()
                if not name or "所有" in name:
                    continue
                for code in codes:
                    code_up = code.strip().upper().rstrip(".")
                    if not CODE_RE.match(code_up):
                        continue
                    if code_up not in icd_map:  # 首次出现保留
                        icd_map[code_up] = name
                        added += 1
        stats[sheet_name] = added

    # 也扫一遍「五、不纳入分组的主要诊断」用启发式（无名称列，只拿编码列表）
    # 该 sheet 没有名称，跳过

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(icd_map, f, ensure_ascii=False, indent=0, sort_keys=True)

    print(f"提取完成，共 {len(icd_map)} 条编码-名称映射")
    print(f"输出文件: {OUT}")
    print("各 sheet 贡献:")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()