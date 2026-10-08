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
    """内存中一条规则的完整信息。

    分组时只读这个对象，不再触碰 DB。
    """
    code: str                     # 规则编码（MDC_CODE / ADRG_CODE / DRG_CODE）
    name: str                     # 规则名称（用于展示和证据收集）
    raw_expr: str                 # 原始 DSL 表达式文本（用于展示和调试）
    priority: int                 # 优先级，数值越小越先匹配
    ast: object                   # AST 节点（来自 deserialize），分组时直接求值
    parent: str = ""              # 父级编码：ADRG 规则存 MDC_CODE，DRG 规则存 ADRG_CODE
    adrg_type: str = ""           # ADRG 类型（如 手术/非手术），仅 ADRG 规则有值
    is_invalid: bool = False      # 解析失败的规则标记；pipeline 应 skip 而不是 silent 命中


@dataclass
class RuleIndex:
    """启动期一次性构建的内存索引，分组时只读它。

    所有字段在 build_index 中填充完毕，之后不再修改。
    """
    # ── 集合（自定义代码集合，如"恶性肿瘤"集合）──
    # set_id → codes（frozenset 保证 O(1) 成员判定）
    sets: dict[str, frozenset[str]] = field(default_factory=dict)
    # code → set_ids（反查，用于证据收集：某 code 命中了哪些集合）
    set_index: dict[str, set[str]] = field(default_factory=dict)

    # ── CC / MCC 并发症合并症表 ──
    # MCC（主要并发症合并症）代码集合
    mcc_set: set[str] = field(default_factory=set)
    # CC（并发症合并症）代码集合
    cc_set: set[str] = field(default_factory=set)
    # (diagnosis_code, level) → exclusion_table_id
    # 用于查找某诊断对应的排除表
    code_to_excl: dict[tuple[str, str], str] = field(default_factory=dict)

    # ── 排除表 ──
    # table_id → codes（该排除表包含的所有代码）
    exclusion: dict[str, set[str]] = field(default_factory=dict)

    # ── 规则（均按 priority 升序排列，遍历时直接命中）──
    # MDC 规则：扁平列表，按 priority 升序
    mdc_rules: list[RuleRecord] = field(default_factory=list)
    # ADRG 规则：按所属 MDC 分组，每组内按 priority 升序
    adrg_rules: dict[str, list[RuleRecord]] = field(default_factory=dict)
    # DRG 规则：按所属 ADRG 分组，每组内按 priority 升序
    drg_rules: dict[str, list[RuleRecord]] = field(default_factory=dict)
    # 按 code 快速查找（含 name 等元信息，用于展示和证据收集）
    mdc_by_code: dict[str, RuleRecord] = field(default_factory=dict)
    adrg_by_code: dict[str, RuleRecord] = field(default_factory=dict)
    drg_by_code: dict[str, RuleRecord] = field(default_factory=dict)


def build_index(session: Session) -> RuleIndex:
    """从 DB 加载全部数据构建内存索引。

    启动期调用一次，之后分组全程只读内存，不再查 DB。
    加载顺序：集合 → CC/MCC → 排除表 → MDC 规则 → ADRG 规则 → DRG 规则。
    """
    idx = RuleIndex()

    # ── 集合 ──
    # 双向索引：set_id → codes（正向，用于规则求值时的成员判定）
    #            code → set_ids（反向，用于证据收集：某 code 属于哪些集合）
    set_group: dict[str, set[str]] = {}
    set_index: dict[str, set[str]] = {}
    for row in session.query(SetMember).all():
        sid = row.set_id
        code = row.code
        set_group.setdefault(sid, set()).add(code)
        set_index.setdefault(code, set()).add(sid)
    # 转 frozenset：不可变 + O(1) 成员判定，分组热路径上更安全
    idx.sets = {sid: frozenset(codes) for sid, codes in set_group.items()}
    idx.set_index = set_index
    log.info("loaded %d sets (%d unique codes)",
             len(idx.sets), len(idx.set_index))

    # ── CC / MCC ──
    # 同时构建 code_to_excl：(诊断代码, 级别) → 排除表 ID，
    # 分组时据此判断某 CC/MCC 诊断是否被排除表排除
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
    # table_id → codes：某诊断命中排除表后，表内代码不再参与 CC/MCC 判定
    excl_group: dict[str, set[str]] = {}
    for row in session.query(ExclusionTable).all():
        excl_group.setdefault(row.table_id, set()).add(row.code)
    idx.exclusion = excl_group
    log.info("loaded %d exclusion tables", len(excl_group))

    # ── MDC 规则 ──
    # 按 priority 升序加载，遍历时第一条命中的即最终 MDC
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
    # 按所属 MDC 分组（分组时只在当前 MDC 的 ADRG 规则里找），组内按 priority 升序
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
    # 按所属 ADRG 分组（分组时只在当前 ADRG 的 DRG 规则里找），组内按 priority 升序
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
