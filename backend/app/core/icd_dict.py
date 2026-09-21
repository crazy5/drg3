"""ICD-10 / ICD-9-CM-3 编码-中文名字典（国家临床版，启动期加载）。

源：
- 国家临床版 2.0 疾病编码 2022 修订（37,294 条，含 ~1100 个星剑号双重编码）
- 国家临床版 3.0 ICD-9-CM-3 手术编码 2025 修订（13,740 条）
合计 51,034 条，键统一大写（含 X 扩展码）。

二级兜底（医保版医保名）：
- icd_medical_name.json — 国临→医保对照表的「医保码 → 医保名」（~32k 条）
- icd_op_medical_name.json — ICD9 国临→医保对照表的「医保码 → 医保名」（~13.5k 条）
主字典没收录的医保版新码（如 I67.200X003 / K64.811 等）由这两份表兜底。
实测：cases 表实际用到的 7350 个编码里，主字典覆盖 96%；加上二级表后接近 100%。

注意：
- 字典 key 形态分两类：
 - **单码**：`E11.500`、`N80.100X003`、`45.4301`
 - **星剑号双重编码（合并码）**：`E11.501+I79.2*`、`A01.000X003+G01*` 等 ~1100 条，
 HIS 字段里这种是临床医生写的"病因+临床表现"合并写法，字典源 xls 也按合并条目收录。
- 因此 lookup 时**必须先按原码（含 +X* 后缀）查**，未命中再退到基础码 fallback，再未命中
 查二级兜底（仅用于同步显示中文名，**不影响 DRG 引擎判定**，引擎始终按医保版精确码判定）。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DICT_PATH = Path(__file__).parent.parent.parent / "data" / "icd_dict.json"
MEDICAL_NAME_PATH = Path(__file__).parent.parent.parent / "data" / "icd_medical_name.json"
OP_MEDICAL_NAME_PATH = Path(__file__).parent.parent.parent / "data" / "icd_op_medical_name.json"


def normalize_icd(code: str | None) -> str:
    """ICD 编码归一化（大小写、去末尾点），**保留 X 扩展和 +X* 复合后缀**。

    复合编码（星剑号双重编码）字典里就是按完整 key 收录的（如
    `E11.501+I79.2* → 2型糖尿病性周围血管病变`），lookup 时优先按原码查。
    本函数只用于把同一码的不同写法归一（去大小写、去末尾点），不做剥后缀。
    """
    if not code:
        return ""
    s = str(code).strip().upper()
    return s.rstrip(".")


def _strip_to_base(code: str) -> str:
    """兜底基础码：剥 + 复合编码和末尾 *。仅在原码未命中时尝试。"""
    s = code.split("+", 1)[0].rstrip("*")
    return s.rstrip(".")


@lru_cache(maxsize=1)
def _load() -> dict[str, str]:
    """主字典（国家临床版），key 是 ICD 编码（含 X 扩展和 +X* 复合码）。"""
    if not DICT_PATH.exists():
        return {}
    with open(DICT_PATH, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _load_medical_fallback() -> dict[str, str]:
    """二级兜底：医保版医保名（key 是医保码）。

    主字典没收录的医保版新码靠这里查。注意诊断表和手术表两份都要合并。
    之所以分两份：build_icd_mapping / build_icd_op_mapping 是两套独立源 xlsx。
    """
    out: dict[str, str] = {}
    for p in (MEDICAL_NAME_PATH, OP_MEDICAL_NAME_PATH):
        if p.exists():
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
                # 医保版比国临版优先：医保表覆盖到的话用医保表的；国临版表是补充
                for k, v in d.items():
                    out.setdefault(k, v)
    return out


# 手工兜底：医保名映射表里也没有的少数医保版编码（一般是 xlsx 源数据漏了
# 但临床上频繁出现），按 E11.501+I79.2* 复合码的中文名借用，让界面有名字可显
_MANUAL_OVERRIDES: dict[str, str] = {
    "E11.501": "2型糖尿病性周围血管病变",   # 复合码 E11.501+I79.2* 的中文名借用
}


def lookup(code: str | None) -> str | None:
    """查单个编码的中文名。优先级：

    1. 主字典原码（含 +X*）
    2. 主字典基础码（剥 +X*）
    3. 二级兜底：医保版医保名表
    4. 手工 overrides（极少数 xlsx 源漏录的医保码）
    """
    if not code:
        return None
    norm = normalize_icd(code)
    if not norm:
        return None
    d = _load()
    if norm in d:
        return d[norm]
    base = _strip_to_base(norm)
    name = d.get(base)
    if name is not None:
        return name
    fb = _load_medical_fallback()
    name = fb.get(norm) or fb.get(base)
    if name is not None:
        return name
    return _MANUAL_OVERRIDES.get(norm) or _MANUAL_OVERRIDES.get(base)


def lookup_many(codes: list[str]) -> dict[str, str]:
    """批量查，返回 code→name 子集（只包含命中）。key 用原始传入值（便于前端定位）。"""
    if not codes:
        return {}
    d = _load()
    fb = _load_medical_fallback()
    out: dict[str, str] = {}
    for c in codes:
        if not c:
            continue
        norm = normalize_icd(c)
        if not norm:
            continue
        name = d.get(norm)
        if name is None:
            base = _strip_to_base(norm)
            name = d.get(base)
            if name is None:
                name = fb.get(norm) or fb.get(base)
                if name is None:
                    name = _MANUAL_OVERRIDES.get(norm) or _MANUAL_OVERRIDES.get(base)
        if name is not None:
            out[c] = name
    return out


def stats() -> dict:
    return {
        "size": len(_load()),
        "fallback_size": len(_load_medical_fallback()),
        "manual_overrides": len(_MANUAL_OVERRIDES),
        "path": str(DICT_PATH),
        "fallback_path": [str(MEDICAL_NAME_PATH), str(OP_MEDICAL_NAME_PATH)],
    }