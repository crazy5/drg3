"""DSL parser 测试。

覆盖规则所有形态：
1. 最简单 in
2. 集合字面量 in
3. not in
4. 恒真 '1'
5. 空规则
6. 比较运算
7. 跨字段 OR
8. 反斜杠换行（xlsx 多行规则）
9. parse 失败
10. AST 序列化往返
"""
from __future__ import annotations

import pytest

from app.engine.parser.ast_nodes import (
    AndNode,
    CompareNode,
    ConstTrueNode,
    InCheckNode,
    NotNode,
    OrNode,
    deserialize,
    serialize,
)
from app.engine.parser.lexer import preprocess
from app.engine.parser.parser import ParseError, parse_rule


# ────────── 形态测试 ──────────


def test_simple_in():
    n = parse_rule("ZYZD in DI_B00")
    assert isinstance(n, InCheckNode)
    assert n.variables == ["ZYZD"]
    assert n.set_id == "DI_B00"
    assert n.negated is False


def test_set_literal_in():
    n = parse_rule("{ZYSS, QTSS} in OP2_AC1")
    assert isinstance(n, InCheckNode)
    assert n.variables == ["ZYSS", "QTSS"]
    assert n.set_id == "OP2_AC1"
    assert n.negated is False


def test_not_in():
    n = parse_rule("ZYSS not in OP_ALL")
    assert isinstance(n, InCheckNode)
    assert n.variables == ["ZYSS"]
    assert n.set_id == "OP_ALL"
    assert n.negated is True


def test_const_true_one():
    n = parse_rule("1")
    assert isinstance(n, ConstTrueNode)


def test_empty_rule():
    n = parse_rule("")
    assert isinstance(n, ConstTrueNode)
    n = parse_rule(None)
    assert isinstance(n, ConstTrueNode)


def test_compare_eq():
    n = parse_rule("XB = 1")
    assert isinstance(n, CompareNode)
    assert n.variable == "XB"
    assert n.op == "="
    assert n.value == 1
    assert isinstance(n.value, int)


def test_compare_ge_float():
    n = parse_rule("NL >= 70")
    assert isinstance(n, CompareNode)
    assert n.variable == "NL"
    assert n.op == ">="
    assert n.value == 70


def test_compare_lt():
    n = parse_rule("XSRTL < 29")
    assert isinstance(n, CompareNode)
    assert n.op == "<"


def test_newborn_rule():
    """新生儿那条跨字段 OR 规则。"""
    raw = (
        "((NL=0) and (XSRTL>=29) and (XSRTL<366) and "
        "(ZYZD in DI_PV1) and (ZYSS not in OP_ALL)) or "
        "((NL=0) and (XSRTL<29))"
    )
    n = parse_rule(raw)
    assert isinstance(n, OrNode)
    assert isinstance(n.left, AndNode)
    assert isinstance(n.right, AndNode)


def test_backslash_newline_folding():
    """xlsx 多行规则（MDCZ 多发创伤 9 条 OR）。

    关键：xlsx 单元格里的强制换行是「反斜杠 + 换行符」，
    preprocess 必须把它们折叠成空格，否则 parser 会在跨行处报错。
    """
    raw = (
        "((ZYZD in DI_Z01) and (QTZD in {DI_Z02, DI_Z03, DI_Z04, DI_Z05, DI_Z06, DI_Z07, DI_Z08, DI_Z09})) or \\\n"
        "((ZYZD in DI_Z02) and (QTZD in {DI_Z01, DI_Z03, DI_Z04, DI_Z05, DI_Z06, DI_Z07, DI_Z08, DI_Z09})) or \\\n"
        "((ZYZD in DI_Z03) and (QTZD in {DI_Z01, DI_Z02, DI_Z04, DI_Z05, DI_Z06, DI_Z07, DI_Z08, DI_Z09}))"
    )
    n = parse_rule(raw)
    assert isinstance(n, OrNode)
    # 三段 OR → Or(Or(A, B), C)
    assert isinstance(n.left, OrNode)
    assert isinstance(n.right, AndNode)


def test_real_xlsx_multiline():
    """模拟直接从 xlsx 读到的原始字符串（含字面 \\n）。"""
    # repr 看到的就是真实内容
    raw = "ZYZD in DI_B00 or \\\n ZYSS in OP_AA1"
    n = parse_rule(raw)
    assert isinstance(n, OrNode)


def test_set_list_on_right_side():
    """`QTZD in {DI_Z02, DI_Z03}` — 右侧多集合列表，语义：QTZD 属于任一集合。"""
    n = parse_rule("QTZD in {DI_Z02, DI_Z03}")
    assert isinstance(n, InCheckNode)
    assert n.variables == ["QTZD"]
    assert n.set_id == ""
    assert n.set_list == ["DI_Z02", "DI_Z03"]


def test_set_list_in_complex():
    """真实 xlsx 9 条 OR 简化版。"""
    n = parse_rule("(ZYZD in DI_Z01) and (QTZD in {DI_Z02, DI_Z03})")
    assert isinstance(n, AndNode)
    assert isinstance(n.right, InCheckNode)
    assert n.right.set_list == ["DI_Z02", "DI_Z03"]


def test_qtzd_in_mcc():
    n = parse_rule("QTZD in MCC")
    assert isinstance(n, InCheckNode)
    assert n.set_id == "MCC"


def test_nested_precedence():
    """a and b or c  →  (a and b) or c （and 优先于 or）。"""
    n = parse_rule("a and b or c")
    assert isinstance(n, OrNode)
    assert isinstance(n.left, AndNode)
    assert isinstance(n.right, InCheckNode) or isinstance(n.right, CompareNode) \
        or isinstance(n.right, ConstTrueNode)


def test_parentheses_override():
    n = parse_rule("a and (b or c)")
    assert isinstance(n, AndNode)
    assert isinstance(n.right, OrNode)


def test_not_prefix():
    n = parse_rule("not (ZYZD in DI_B00)")
    assert isinstance(n, NotNode)
    assert isinstance(n.operand, InCheckNode)


# ────────── 错误处理 ──────────


def test_invalid_character():
    """非预期字符既可能在 lexer 阶段被拒绝（SyntaxError），
    也可能在 parser 阶段被拒绝（ParseError），两种都算解析失败。"""
    with pytest.raises((ParseError, SyntaxError)):
        parse_rule("ZYZD @ DI_B00")


def test_unclosed_paren():
    with pytest.raises(ParseError):
        parse_rule("(ZYZD in DI_B00")


def test_missing_set_after_in():
    with pytest.raises(ParseError):
        parse_rule("ZYZD in")


# ────────── AST 序列化往返 ──────────


def test_serialize_roundtrip():
    """复杂 AST 序列化 → 反序列化 后结构一致。"""
    raw = "((NL=0) and (XSRTL>=29)) or (ZYZD in DI_PV1)"
    n1 = parse_rule(raw)
    blob = serialize(n1)
    n2 = deserialize(blob)

    # 结构比对
    assert type(n1) is type(n2)
    if isinstance(n1, OrNode):
        assert type(n1.left) is type(n2.left)
        assert type(n1.right) is type(n2.right)


def test_preprocess_keeps_clean_text():
    assert preprocess("  ZYZD  in   DI_B00  ") == "ZYZD in DI_B00"
    assert preprocess(None) == ""
    assert preprocess("") == ""
