"""启动期内存索引。

把 SQLite 里的规则 / 集合 / CC / 排除表 一次性加载到内存，
分组时只读这个索引，单病案分组 < 5ms。

关键设计：
- 集合用 frozenset（O(1) 成员判定）
- 规则按 priority 升序排好，遍历直接命中
- AST 已从 DB 反序列化，分组时不需再 parse
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.db.models import (
    AdrgRule,
    CcList,
    DrgRule,
    ExclusionTable,
    MdcRule,
    SetMember,
)
from app.engine.parser.ast_nodes import deserialize

log = logging.getLogger(__name__)


@dataclass
class RuleRecord:
    """内存中一条规则的完整信息。"""
    code: str
    name: str
    raw_expr: str
    priority: int
    ast: object                     # AST 节点（来自 deserialize）
    parent: str = ""                # MDC_CODE / ADRG_CODE
    adrg_type: str = ""
    is_invalid: bool = False        # 解析失败的规则标记；pipeline 应 skip 而不是 silent 命中


@dataclass
class RuleIndex:
    # set_id → codes
    sets: dict[str, frozenset[str]] = field(default_factory=dict)
    # code → set_ids（反查，用于证据收集）
    set_index: dict[str, set[str]] = field(default_factory=dict)

    # CC 表
    mcc_set: set[str] = field(default_factory=set)
    cc_set: set[str] = field(default_factory=set)
    # (diagnosis, level) → exclusion_table
    code_to_excl: dict[tuple[str, str], str] = field(default_factory=dict)

    # 排除表 table_id → codes
    exclusion: dict[str, set[str]] = field(default_factory=dict)

    # 规则
    mdc_rules: list[RuleRecord] = field(default_factory=list)            # 按 priority 升序
    adrg_rules: dict[str, list[RuleRecord]] = field(default_factory=dict)  # mdc → sorted
    drg_rules: dict[str, list[RuleRecord]] = field(default_factory=dict)   # adrg → sorted
    mdc_by_code: dict[str, RuleRecord] = field(default_factory=dict)       # code → record（含 name）
    adrg_by_code: dict[str, RuleRecord] = field(default_factory=dict)
    drg_by_code: dict[str, RuleRecord] = field(default_factory=dict)


def build_index(session: Session) -> RuleIndex:
    """从 DB 加载全部数据构建内存索引。"""
    idx = RuleIndex()

    # ── 集合 ──
    set_group: dict[str, set[str]] = {}
    set_index: dict[str, set[str]] = {}
    for row in session.query(SetMember).all():
        sid = row.set_id
        code = row.code
        set_group.setdefault(sid, set()).add(code)
        set_index.setdefault(code, set()).add(sid)
    idx.sets = {sid: frozenset(codes) for sid, codes in set_group.items()}
    idx.set_index = set_index
    log.info("loaded %d sets (%d unique codes)",
             len(idx.sets), len(idx.set_index))

    # ── CC ──
    mcc: set[str] = set()
    cc: set[str] = set()
    code_to_excl: dict[tuple[str, str], str] = {}
    for row in session.query(CcList).all():
        if row.level == "MCC":
            mcc.add(row.diagnosis_code)
            code_to_excl[(row.diagnosis_code, "MCC")] = row.exclusion_table
        elif row.level == "CC":
            cc.add(row.diagnosis_code)
            code_to_excl[(row.diagnosis_code, "CC")] = row.exclusion_table
    idx.mcc_set = mcc
    idx.cc_set = cc
    idx.code_to_excl = code_to_excl
    log.info("loaded CC: MCC=%d, CC=%d", len(mcc), len(cc))

    # ── 排除表 ──
    excl_group: dict[str, set[str]] = {}
    for row in session.query(ExclusionTable).all():
        excl_group.setdefault(row.table_id, set()).add(row.code)
    idx.exclusion = excl_group
    log.info("loaded %d exclusion tables", len(excl_group))

    # ── MDC 规则 ──
    mdc_rules: list[RuleRecord] = []
    mdc_by: dict[str, RuleRecord] = {}
    for row in session.query(MdcRule).order_by(MdcRule.priority).all():
        ast, is_invalid = _safe_deserialize(row.parsed_ast)
        rec = RuleRecord(
            code=row.mdc_code, name=row.mdc_name,
            raw_expr=row.rule_expr, priority=row.priority, ast=ast,
            is_invalid=is_invalid,
        )
        mdc_rules.append(rec)
        mdc_by[rec.code] = rec
    idx.mdc_rules = mdc_rules
    idx.mdc_by_code = mdc_by

    # ── ADRG 规则 ──
    adrg_grouped: dict[str, list[RuleRecord]] = {}
    adrg_by: dict[str, RuleRecord] = {}
    for row in session.query(AdrgRule).order_by(AdrgRule.priority).all():
        ast, is_invalid = _safe_deserialize(row.parsed_ast)
        rec = RuleRecord(
            code=row.adrg_code, name=row.adrg_name,
            raw_expr=row.rule_expr, priority=row.priority, ast=ast,
            parent=row.mdc_code, adrg_type=row.adrg_type,
            is_invalid=is_invalid,
        )
        adrg_grouped.setdefault(row.mdc_code, []).append(rec)
        adrg_by[rec.code] = rec
    idx.adrg_rules = adrg_grouped
    idx.adrg_by_code = adrg_by

    # ── DRG 规则 ──
    drg_grouped: dict[str, list[RuleRecord]] = {}
    drg_by: dict[str, RuleRecord] = {}
    for row in session.query(DrgRule).order_by(DrgRule.priority).all():
        ast, is_invalid = _safe_deserialize(row.parsed_ast)
        rec = RuleRecord(
            code=row.drg_code, name=row.drg_name,
            raw_expr=row.rule_expr, priority=row.priority, ast=ast,
            parent=row.adrg_code,
            is_invalid=is_invalid,
        )
        drg_grouped.setdefault(row.adrg_code, []).append(rec)
        drg_by[rec.code] = rec
    idx.drg_rules = drg_grouped
    idx.drg_by_code = drg_by

    log.info(
        "loaded rules: mdc=%d, adrg=%d (across %d MDC), drg=%d (across %d ADRG)",
        len(mdc_rules), sum(len(v) for v in adrg_grouped.values()),
        len(adrg_grouped), sum(len(v) for v in drg_grouped.values()),
        len(drg_grouped),
    )
    return idx


def _safe_deserialize(blob: str) -> tuple[object, bool]:
    """反序列化 AST JSON。

    返回 (ast_node, is_invalid)：
    - 成功：返回正常 AST + is_invalid=False
    - parsed_ast 含 "error"（import 时 DSL 不支持的语法）：返回 (None, True)，
      pipeline 应当**跳过**这条规则而不是 silent 命中。
      历史背景：早期版本曾返回 ConstTrueNode 兜底，导致 parse 失败规则被当成"永真"，
      把所有进入该层的病案都吸进去（典型表现：某 ADRG 占比异常高到不合常理）。
    - json 解析异常：同上 (None, True)
    """
    try:
        d = json.loads(blob) if blob else {"type": "True"}
        if isinstance(d, dict) and "error" in d:
            return None, True
        return deserialize(d), False
    except Exception:        # noqa: BLE001
        return None, True
