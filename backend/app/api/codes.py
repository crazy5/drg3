"""ICD 编码-中文名查询 API。"""
from __future__ import annotations

from fastapi import APIRouter

from app.core import icd_dict

router = APIRouter(prefix="/api/codes", tags=["codes"])


@router.get("/names")
def get_names(codes: str):
    """批量查：?codes=A01.001,I50.900,33.5100 → {code: name, ...}"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    return icd_dict.lookup_many(code_list)


@router.get("/{code}/name")
def get_name(code: str):
    """单条查：/api/codes/A01.001/name → {"code":"A01.001","name":"霍乱"}"""
    name = icd_dict.lookup(code)
    return {"code": code.upper(), "name": name}


@router.get("/stats")
def get_stats():
    return icd_dict.stats()