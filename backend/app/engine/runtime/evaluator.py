"""AST 解释器。

输入：
- AST 节点
- case 上下文（属性 / 列表 / 标量变量）
- RuleIndex（集合 / 排除表 等内存索引）
- EvidenceNode（挂载证据）

输出：bool

短路求值 + 证据完整性：
- OrNode 左侧 True → 右侧标记 'not_evaluated'
- AndNode 左侧 False → 右侧标记 'skipped'

医保版编码约束：所有从病案系统送来的诊断/手术码都已经过国临版→医保版对照（见
scripts/his_sync/sync.py 的 apply_mapping_list）。分组引擎**全程用医保版精确编码**做
集合成员判定，**不做 X 扩展剥除回退**。

原因：
- 医保版规则集合（set_members）本身已收录 46k 条 X 扩展（约占 44%），规则表是按医保版逐码
  设计的，基础码和扩展码各自归属相应集合（如 I50.900 和 I50.900X007 都落在 DI_F00 /
  DI_FK2 / DI_FP1 / DI_FR2）。
- cc_list / EX_n 也是按医保版逐码收录，X 扩展各自登记各自的 level 和 exclusion_table。
- 如果送来医保版 X 扩展精确码不命中集合，说明这条规则不适用，**不能用基础码兜底强行命中**
  ——否则会把不该命中的命中、该命中的归
"""
from __future__ import annotations

from typing import Any

from app.engine.parser.ast_nodes import (
    AndNode,
    CompareNode,
    ConstTrueNode,
    InCheckNode,
    LengthCompareNode,
    Node,
    NotNode,
    OrNode,
)
from app.engine.runtime.evidence import EvidenceNode


class CaseView:
    """病案的求值视图：把 case 的 8 个字段（标量 / 列表 / 注入变量）统一暴露。

    DSL 语法糖：规则里写 QTZD 等价于「QTZD_LIST 里任一命中」；
               规则里写 ZYSS 等价于「ZYSS 标量」；
               规则里写 {ZYSS, QTSS} 则分别按各自语义处理。
    """

    # 规则里的"裸名" → 实际字段
    _ALIASES = {
        "QTZD": "QTZD_LIST",
        "QTSS": "QTSS_LIST",
    }

    def __init__(self, **fields: Any):
        # 字段名归一为大写（DSL 规则里都用大写）
        norm = {}
        for k, v in fields.items():
            key = k.upper()
            if key.endswith("_LIST"):
                norm[key] = v or []
            else:
                norm[key] = v
        self._fields = norm

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            return super().__getattribute__(name)
        # 先查直接字段
        if name in self._fields:
            return self._fields[name]
        # 别名：QTZD → QTZD_LIST
        alias = self._ALIASES.get(name)
        if alias and alias in self._fields:
            return self._fields[alias]
        return None

# 将任意值转换为数字（int / float），用于比较运算。None 或空字符串视作无法比较。
def _to_number(v: Any) -> int | float | None:
    if v is None or v == "":
        return None
    try:
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, (int, float)):
            return v
        return int(v) if str(v).isdigit() else float(v)
    except (ValueError, TypeError):
        return None

# 比较运算：支持 =, >=, <=, >, <。None 视作不满足。
def _compare(actual: Any, op: str, expected: Any) -> bool:
    """比较运算。None 视作不满足。"""
    a = _to_number(actual)
    e = _to_number(expected)
    if a is None or e is None:
        return False
    if op == "=":
        return a == e
    if op == ">=":
        return a >= e
    if op == "<=":
        return a <= e
    if op == ">":
        return a > e
    if op == "<":
        return a < e
    raise ValueError(f"unknown op: {op!r}")


def evaluate(
    node: Node,
    case: CaseView,
    ctx: Any,            # RuleIndex（鸭子类型，避免循环 import）
    ev: EvidenceNode,
) -> bool:
    # 逻辑 OR：先求左子树；只要左侧为 True，则右侧无需计算，记录为短路跳过。
    if isinstance(node, OrNode):
        # 取左子树证据节点
        left_ev = ev.child("or.left")
        # 递归求值左子树
        left_val = evaluate(node.left, case, ctx, left_ev)
        if left_val:
            # 左侧命中 → 右侧标记未求值（短路证据）
            right_ev = ev.child("or.right")
            # 右侧标记为短路跳过，附上原因
            right_ev.set(skipped_short_circuit=True, reason="or.left was True")
            # 设置当前节点为 OR，结果为 True
            ev.set(node="Or", result=True)
            return True
        right_ev = ev.child("or.right")
        right_val = evaluate(node.right, case, ctx, right_ev)
        # 设置当前节点为 OR，结果为右侧的求值结果
        ev.set(node="Or", result=right_val)
        return right_val

    # 逻辑 AND：先求左子树；只要左侧为 False，则右侧无需计算，记录为短路跳过。
    if isinstance(node, AndNode):
        left_ev = ev.child("and.left")
        left_val = evaluate(node.left, case, ctx, left_ev)
        if not left_val:
            right_ev = ev.child("and.right")
            right_ev.set(skipped_short_circuit=True, reason="and.left was False")
            ev.set(node="And", result=False)
            return False
        right_ev = ev.child("and.right")
        right_val = evaluate(node.right, case, ctx, right_ev)
        ev.set(node="And", result=right_val)
        return right_val

    # 逻辑 NOT：对内部表达式取反，并把证据链继续挂在子节点上。
    if isinstance(node, NotNode):
        inner_ev = ev.child("not")
        v = evaluate(node.operand, case, ctx, inner_ev)
        ev.set(node="Not", result=not v)
        return not v

    # 集合/成员判定：例如变量是否在某个规则集合中，支持单集合/多集合/多字段。
    if isinstance(node, InCheckNode):
        return _eval_in(node, case, ctx, ev)

    # 比较表达式：从 case 中取变量值，并与目标值按操作符比较。
    if isinstance(node, CompareNode):
        actual = getattr(case, node.variable, None)
        result = _compare(actual, node.op, node.value)
        ev.set(
            node="Compare",
            variable=node.variable,
            op=node.op,
            expected=node.value,
            actual=actual,
            result=result,
        )
        return result

    # 长度比较：用于检查列表长度或字符串长度等与阈值的关系。
    if isinstance(node, LengthCompareNode):
        return _eval_length(node, case, ctx, ev)

    # 常量 True：规则中直接写 True 的兜底叶子节点。
    if isinstance(node, ConstTrueNode):
        ev.set(node="True", result=True)
        return True

    # 未知节点类型：说明 AST 与解释器版本不一致，通常应视为实现错误。
    raise TypeError(f"cannot evaluate node: {type(node).__name__}")


def _eval_in(node: InCheckNode, case: CaseView, ctx: Any, ev: EvidenceNode) -> bool:
    """集合成员判定。

    支持：
    - var in <set>           单集合
    - var in {set1, set2}    多集合列表（变量属于任一就算命中）
    - {var1, var2} in <set>  多字段任一命中

    特殊集合名：
    - MCC / CC  → ctx.mcc_set / ctx.cc_set（DRG 层规则专用）
    - 其他      → ctx.sets[set_id]

    **不做 X 扩展剥除兜底**——所有诊断/手术码都是医保版精确码，未命中即未命中。
    """
    target_sets: list[str] = []
    if node.set_list:
        target_sets = list(node.set_list)
    elif node.set_id:
        target_sets = [node.set_id]
    else:
        ev.set(node="InCheck", variables=list(node.variables),
               target_sets=[], matched=False, hit_codes=[])
        return False

    # 变量取值：标量 or 列表
    hit_codes: list[str] = []
    matched = False
    for var in node.variables:
        candidate = getattr(case, var, None)
        if isinstance(candidate, list):
            for c in candidate:
                if c is None:
                    continue
                hit = _match_exact(c, target_sets, ctx)
                if hit:
                    hit_codes.append(c)
                    matched = True
        else:
            hit = _match_exact(candidate, target_sets, ctx)
            if hit:
                hit_codes.append(candidate)
                matched = True

    if node.negated:
        matched = not matched
        hit_codes = []  # 取反时无可视化"命中码"

    ev.set(
        node="InCheck",
        variables=list(node.variables),
        target_sets=target_sets,
        negated=node.negated,
        matched=matched,
        hit_codes=hit_codes,
    )
    return matched


def _resolve_set(ctx: Any, set_id: str):
    """根据集合名取成员集合；MCC / CC 走专用索引，其他走 ctx.sets。"""
    if set_id == "MCC":
        return getattr(ctx, "mcc_set", None)
    if set_id == "CC":
        return getattr(ctx, "cc_set", None)
    sets = getattr(ctx, "sets", None)
    if sets is None:
        return None
    return sets.get(set_id)


def _match_exact(code: Any, target_sets: list[str], ctx: Any) -> bool:
    """医保版精确码集合成员检查（不做 X 扩展兜底）。"""
    if not code:
        return False
    cs = str(code)
    for sid in target_sets:
        members = _resolve_set(ctx, sid)
        if members and cs in members:
            return True
    return False


def _eval_length(
    node: LengthCompareNode,
    case: CaseView,
    ctx: Any,
    ev: EvidenceNode,
) -> bool:
    """`length(<set_id> ∩ {<vars>}) OP NUMBER` 求值。

    唯一已知用法：IC29 (ADRG IC2) 规则：
        length(OP_IC2 ∩ {ZYSS, QTSS})>=2
    语义：病例所有出现过的手术码（ZYSS 标量 + QTSS_LIST 列表拼接去重）中，
    属于 left_set_id 的不同编码个数，与 value 用 op 比较。

    集合不存在（None）→ 命中数 = 0，length 表达式返回 False（threshold > 0 时）。
    """
    members = _resolve_set(ctx, node.left_set_id)
    if not members:
        ev.set(
            node="LengthCompare",
            left_set_id=node.left_set_id,
            right_vars=list(node.right_vars),
            op=node.op,
            expected=node.value,
            count=0,
            hit_codes=[],
            result=False,
            note=f"set {node.left_set_id} not loaded",
        )
        return False

    # 收集所有候选码（标量 + 列表拼接，去 None 去重）
    candidates: set[str] = set()
    raw_codes: list[str] = []    # 保留顺序便于证据展示
    for var in node.right_vars:
        v = getattr(case, var, None)
        if isinstance(v, list):
            for c in v:
                if c is None or c == "":
                    continue
                cs = str(c)
                if cs not in candidates:
                    candidates.add(cs)
                    raw_codes.append(cs)
        else:
            if v is not None and v != "":
                cs = str(v)
                if cs not in candidates:
                    candidates.add(cs)
                    raw_codes.append(cs)

    # 命中：属于 set 的不同码
    hit_codes = [c for c in raw_codes if c in members]
    count = len(hit_codes)

    result = _compare(count, node.op, node.value)
    ev.set(
        node="LengthCompare",
        left_set_id=node.left_set_id,
        right_vars=list(node.right_vars),
        op=node.op,
        expected=node.value,
        count=count,
        hit_codes=hit_codes,
        result=result,
    )
    return result


__all__ = ["CaseView", "evaluate", "_compare"]
