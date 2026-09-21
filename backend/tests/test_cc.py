"""CC 分级判定测试。

手工构造 4 类回归用例（同系统排除 / 跨系统命中 / 主诊断未命中 EX / 空 QTZD）。
"""
from __future__ import annotations

from app.engine.grouper.cc import classify_cc, find_main_exclusion_tables
from app.engine.index import RuleIndex
from app.engine.runtime.evidence import EvidenceNode
from app.engine.runtime.evaluator import CaseView


def _mk_index(
    mcc_codes: set[str], cc_codes: set[str],
    code_to_excl: dict[tuple[str, str], str],
    exclusion: dict[str, set[str]],
) -> RuleIndex:
    return RuleIndex(
        mcc_set=mcc_codes,
        cc_set=cc_codes,
        code_to_excl=code_to_excl,
        exclusion=exclusion,
    )


def test_same_exclusion_table_excluded():
    """主霍乱 + 副霍乱 → 同 EX_1 排除 → NONE。"""
    idx = _mk_index(
        mcc_codes={"A00.900"},
        cc_codes=set(),
        code_to_excl={("A00.900", "MCC"): "EX_1"},
        exclusion={"EX_1": {"A00.900"}},
    )
    case = CaseView(ZYZD="A00.900", QTZD_LIST=["A00.900"])
    assert classify_cc(case, idx, EvidenceNode()) == "NONE"


def test_cross_exclusion_table_hits_mcc():
    """主霍乱 + 副 MCC（不同 EX_n）→ MCC。"""
    idx = _mk_index(
        mcc_codes={"B99.001"},
        cc_codes=set(),
        code_to_excl={("B99.001", "MCC"): "EX_42"},
        exclusion={"EX_1": {"A00.900"}},     # 主诊断霍乱命中 EX_1
    )
    case = CaseView(ZYZD="A00.900", QTZD_LIST=["B99.001"])
    assert classify_cc(case, idx, EvidenceNode()) == "MCC"


def test_main_diagnosis_not_in_any_exclusion():
    """主诊断 Z99.x 不在任何 EX_n → 所有 MCC 都算。"""
    idx = _mk_index(
        mcc_codes={"A01.001"},
        cc_codes=set(),
        code_to_excl={("A01.001", "MCC"): "EX_5"},
        exclusion={"EX_5": {"A01.001"}},     # 排除表只装 A01.001，不含 Z99
    )
    case = CaseView(ZYZD="Z99.001", QTZD_LIST=["A01.001"])
    assert classify_cc(case, idx, EvidenceNode()) == "MCC"


def test_empty_qtzd_returns_none():
    idx = _mk_index(mcc_codes={"A01.001"}, cc_codes={"A02.001"},
                    code_to_excl={}, exclusion={})
    case = CaseView(ZYZD="A00.001", QTZD_LIST=[])
    assert classify_cc(case, idx, EvidenceNode()) == "NONE"


def test_mcc_priority_over_cc():
    """同一诊断在 MCC 和 CC 表都有，但 MCC 先判中。"""
    idx = _mk_index(
        mcc_codes={"D50.000"},
        cc_codes={"D50.000"},
        code_to_excl={("D50.000", "MCC"): "EX_10", ("D50.000", "CC"): "EX_10"},
        exclusion={"EX_10": {"D50.000"}},
    )
    # 主诊断不在 EX_10 → 都能命中，按规则 MCC 优先
    case = CaseView(ZYZD="Z99.001", QTZD_LIST=["D50.000"])
    assert classify_cc(case, idx, EvidenceNode()) == "MCC"


def test_cc_only_when_no_mcc():
    """只有 CC 命中 → 返回 CC。"""
    idx = _mk_index(
        mcc_codes=set(),
        cc_codes={"D50.000"},
        code_to_excl={("D50.000", "CC"): "EX_10"},
        exclusion={"EX_10": {"D50.000"}},
    )
    case = CaseView(ZYZD="Z99.001", QTZD_LIST=["D50.000"])
    assert classify_cc(case, idx, EvidenceNode()) == "CC"


def test_find_main_exclusion_tables():
    idx = _mk_index(
        mcc_codes=set(), cc_codes=set(), code_to_excl={},
        exclusion={
            "EX_1": {"A00.900"},
            "EX_2": {"B99.001"},
            "EX_3": {"A00.900", "C11.001"},
        },
    )
    tables = find_main_exclusion_tables("A00.900", idx)
    assert tables == {"EX_1", "EX_3"}
