"""evaluator 测试。

用一个最小的 RuleIndex stub（只暴露 .sets）覆盖：
- InCheck 命中 / 未命中 / 取反 / 多集合列表 / 集合字面量（左侧多变量）
- Compare 各种运算符
- ConstTrue
- And/Or/Not + 短路证据
"""
from __future__ import annotations

from app.engine.parser.parser import parse_rule
from app.engine.runtime.evidence import EvidenceNode
from app.engine.runtime.evaluator import CaseView, evaluate


class StubIndex:
    def __init__(self, sets: dict[str, set[str]]):
        self.sets = sets


def _ctx(sets: dict[str, set[str]] | None = None):
    return StubIndex(sets or {})


# ────────── InCheck ──────────


def test_in_match():
    idx = _ctx({"DI_B00": {"A01.001", "A01.002"}})
    n = parse_rule("ZYZD in DI_B00")
    case = CaseView(ZYZD="A01.001", QTZD_LIST=[], QTSS_LIST=[])
    ev = EvidenceNode()
    assert evaluate(n, case, idx, ev) is True
    # InCheck 直接 set 在 ev 自身
    assert ev.attrs["matched"] is True
    assert ev.attrs["hit_codes"] == ["A01.001"]


def test_in_no_match():
    idx = _ctx({"DI_B00": {"A01.001"}})
    n = parse_rule("ZYZD in DI_B00")
    case = CaseView(ZYZD="B99.999")
    ev = EvidenceNode()
    assert evaluate(n, case, idx, ev) is False


def test_in_negated():
    """ZYSS not in OP_ALL：变量不在集合时命中。"""
    idx = _ctx({"OP_ALL": {"00.0000"}})
    n = parse_rule("ZYSS not in OP_ALL")
    case = CaseView(ZYSS="33.5100")
    ev = EvidenceNode()
    assert evaluate(n, case, idx, ev) is True


def test_in_set_list_right():
    """QTZD in {DI_A, DI_B}  — 任一集合命中即算。"""
    idx = _ctx({
        "DI_A": {"X1"}, "DI_B": {"X2"}, "DI_C": {"X3"},
    })
    n = parse_rule("QTZD in {DI_A, DI_B}")
    case = CaseView(QTZD_LIST=["X3"], QTSS_LIST=[])  # X3 在 DI_C，不在 DI_A/B
    ev = EvidenceNode()
    assert evaluate(n, case, idx, ev) is False

    case = CaseView(QTZD_LIST=["X2"], QTSS_LIST=[])
    ev = EvidenceNode()
    assert evaluate(n, case, idx, ev) is True


def test_in_left_set_literal():
    """{ZYSS, QTSS} in OP2_AC1 — 任一变量命中就算。"""
    idx = _ctx({"OP2_AC1": {"Y1", "Y2"}})
    n = parse_rule("{ZYSS, QTSS} in OP2_AC1")
    # ZYSS 命中
    case = CaseView(ZYSS="Y1", QTSS_LIST=["Z9"])
    ev = EvidenceNode()
    assert evaluate(n, case, idx, ev) is True


# ────────── Compare ──────────


def test_compare_ge():
    n = parse_rule("NL >= 70")
    case = CaseView(NL=70)
    assert evaluate(n, case, _ctx(), EvidenceNode()) is True

    case = CaseView(NL=69)
    assert evaluate(n, case, _ctx(), EvidenceNode()) is False


def test_compare_eq():
    n = parse_rule("XB = 1")
    case = CaseView(XB=1)
    assert evaluate(n, case, _ctx(), EvidenceNode()) is True

    case = CaseView(XB=2)
    assert evaluate(n, case, _ctx(), EvidenceNode()) is False


def test_compare_none():
    """缺失字段视为不满足。"""
    n = parse_rule("NL >= 70")
    case = CaseView(NL=None)
    assert evaluate(n, case, _ctx(), EvidenceNode()) is False


# ────────── ConstTrue ──────────


def test_const_true():
    n = parse_rule("1")
    assert evaluate(n, CaseView(), _ctx(), EvidenceNode()) is True


# ────────── 短路求值 + 证据完整性 ──────────


def test_or_short_circuit():
    """a or b — a 命中后 b 不该被求值。"""
    idx = _ctx({"A": {"hit"}, "B": {"hit"}})
    n = parse_rule("(ZYZD in A) or (ZYSS in B)")
    case = CaseView(ZYZD="hit", ZYSS="hit")
    ev = EvidenceNode()
    assert evaluate(n, case, idx, ev) is True
    # children[0]=or.left, children[1]=or.right
    assert ev.children[1].attrs.get("skipped_short_circuit") is True


def test_and_short_circuit():
    """a and b — a 未命中后 b 不该被求值。"""
    idx = _ctx({"A": {"x"}, "B": {"y"}})
    n = parse_rule("(ZYZD in A) and (ZYSS in B)")
    # a=True, b=False
    case = CaseView(ZYZD="x", ZYSS="miss")
    ev = EvidenceNode()
    assert evaluate(n, case, idx, ev) is False
    # 两边都求值了，没有短路
    assert len(ev.children) == 2

    # a=False → b 短路
    case = CaseView(ZYZD="z", ZYSS="y")
    ev = EvidenceNode()
    assert evaluate(n, case, idx, ev) is False
    assert ev.children[1].attrs.get("skipped_short_circuit") is True


def test_not():
    n = parse_rule("not (ZYZD in A)")
    idx = _ctx({"A": {"hit"}})
    case = CaseView(ZYZD="hit")
    assert evaluate(n, case, idx, EvidenceNode()) is False

    case = CaseView(ZYZD="miss")
    assert evaluate(n, case, idx, EvidenceNode()) is True
