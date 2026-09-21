"""AST 节点定义。

DSL 形态（来自 xlsx 规则反推）：
- 成员判定：  <var> in <set_id>  /  <var> not in <set_id>
- 集合字面量：{<var1>, <var2>} in <set_id>    # 多字段任一命中
- 逻辑运算：  and / or / not（支持括号、跨行）
- 比较：      =, >=, <=, >, <
- 恒真：      1

每个节点自带 evidence_id（解释执行时挂证据用）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Node:
    """AST 基类（仅作类型标记）。"""
    evidence_id: str = field(default_factory=lambda: "")  # 解释器填入


@dataclass
class OrNode(Node):
    left: Node = None  # type: ignore
    right: Node = None  # type: ignore


@dataclass
class AndNode(Node):
    left: Node = None  # type: ignore
    right: Node = None  # type: ignore


@dataclass
class NotNode(Node):
    operand: Node = None  # type: ignore


@dataclass
class InCheckNode(Node):
    """`var in set_id` / `var not in set_id` / `{a,b} in set_id`

    两种右侧形态：
    - set_id 单集合（最常见）
    - set_list 多集合列表（语义：变量属于任一集合就算命中，如 MDCZ 多发创伤）
    """
    variables: list[str] = field(default_factory=list)
    set_id: str = ""
    set_list: list[str] = field(default_factory=list)
    negated: bool = False


@dataclass
class CompareNode(Node):
    """`NL >= 70` / `XB = 1` 等"""
    variable: str = ""
    op: str = ""       # '=' | '>=' | '<=' | '>' | '<'
    value: Any = None  # int | float | str


@dataclass
class LengthCompareNode(Node):
    """`length(<set_id> ∩ {<vars>}) OP NUMBER`

    唯一已知用法：DRG 3.0 IC29（ADRG IC2）规则：
        `length(OP_IC2 ∩ {ZYSS, QTSS})>=2`
    语义：在病例所有出现过的手术码（ZYSS 标量 + QTSS_LIST 列表）中，
    属于指定集合的不同编码个数，与阈值用 OP 比较（>= / > / <= / <）。

    - left_set_id  : 左侧集合（必须是已加载的 set_id）
    - right_kind   : 右侧形态，'vars' = 字段列表字面量 {ZYSS, QTSS}
    - right_vars   : 字段名列表（仅 right_kind=='vars' 时使用）
    - op / value   : 比较运算和阈值
    """
    left_set_id: str = ""
    right_kind: str = "vars"     # 当前只支持 'vars'
    right_vars: list[str] = field(default_factory=list)
    op: str = ""                 # '>=' | '>' | '<=' | '<'
    value: int = 0


@dataclass
class ConstTrueNode(Node):
    """恒真节点（单独 '1' 或纯真）。"""
    pass


# ─────────── 序列化（用于 AST 缓存进 SQLite） ───────────


def serialize(node: Node) -> dict:
    """AST → dict（JSON 可序列化）。"""
    if isinstance(node, OrNode):
        return {"type": "Or", "left": serialize(node.left), "right": serialize(node.right)}
    if isinstance(node, AndNode):
        return {"type": "And", "left": serialize(node.left), "right": serialize(node.right)}
    if isinstance(node, NotNode):
        return {"type": "Not", "operand": serialize(node.operand)}
    if isinstance(node, InCheckNode):
        return {
            "type": "In",
            "variables": list(node.variables),
            "set_id": node.set_id,
            "set_list": list(node.set_list),
            "negated": node.negated,
        }
    if isinstance(node, CompareNode):
        return {
            "type": "Compare",
            "variable": node.variable,
            "op": node.op,
            "value": node.value,
        }
    if isinstance(node, LengthCompareNode):
        return {
            "type": "LengthCompare",
            "left_set_id": node.left_set_id,
            "right_kind": node.right_kind,
            "right_vars": list(node.right_vars),
            "op": node.op,
            "value": node.value,
        }
    if isinstance(node, ConstTrueNode):
        return {"type": "True"}
    raise TypeError(f"unknown node: {type(node).__name__}")


def deserialize(d: dict) -> Node:
    """dict → AST。"""
    t = d.get("type")
    if t == "Or":
        return OrNode(left=deserialize(d["left"]), right=deserialize(d["right"]))
    if t == "And":
        return AndNode(left=deserialize(d["left"]), right=deserialize(d["right"]))
    if t == "Not":
        return NotNode(operand=deserialize(d["operand"]))
    if t == "In":
        return InCheckNode(
            variables=list(d["variables"]),
            set_id=d["set_id"],
            set_list=list(d.get("set_list", [])),
            negated=bool(d.get("negated", False)),
        )
    if t == "Compare":
        return CompareNode(variable=d["variable"], op=d["op"], value=d["value"])
    if t == "LengthCompare":
        return LengthCompareNode(
            left_set_id=d["left_set_id"],
            right_kind=d.get("right_kind", "vars"),
            right_vars=list(d.get("right_vars", [])),
            op=d["op"],
            value=int(d["value"]),
        )
    if t == "True":
        return ConstTrueNode()
    raise ValueError(f"unknown node type: {t!r}")
