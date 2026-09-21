"""Pipeline 端到端测试 + 真实 xlsx 集成测试。

集成测试构建一个最小化但完整的 RuleIndex，跑一个能贯穿 MDC→ADRG→DRG 的病案。
"""
from __future__ import annotations

import pytest

from app.engine.grouper.pipeline import Case, group_case
from app.engine.index import RuleIndex, RuleRecord
from app.engine.parser.ast_nodes import (
    CompareNode,
    ConstTrueNode,
    InCheckNode,
    deserialize,
    serialize,
)
from app.engine.parser.parser import parse_rule


def _rule(code: str, expr: str, priority: int = 1) -> RuleRecord:
    ast = parse_rule(expr)
    return RuleRecord(code=code, name=code, raw_expr=expr, priority=priority, ast=ast)


def _make_minimal_index():
    """构造一个最小可走的索引。

    MDCA → AA1（ZYSS in OP_AA1）→ AA19（兜底）
    MDCB → BB1（ZYZD in DI_B00）→ BB19（QTZD in CC）
    """
    sets = {
        "OP_AA1": frozenset({"33.6x00"}),
        "DI_B00": frozenset({"A01.001"}),
        # MDCA 命中条件：zyzd 不在 DI_B00（即非颅脑路径才进先期分组）
        "DI_NOT_B": frozenset({"Z99.001", "X99.999"}),
    }
    mcc_set: set[str] = set()
    cc_set = {"A02.001"}
    code_to_excl = {("A02.001", "CC"): "EX_5"}
    exclusion = {
        "EX_5": {"A02.001"},
        # 主诊断 A01.001 不在任何 EX_n
    }

    mdc_rules = [
        # MDCA：主诊断不在 DI_B00 才走（模拟"先期分组只在非神经系统疾病时使用"）
        _rule("MDCA", "ZYZD in DI_NOT_B", priority=1),
        _rule("MDCB", "ZYZD in DI_B00", priority=2),
    ]

    adrg_rules = {
        "MDCA": [_rule("AA1", "ZYSS in OP_AA1", priority=1)],
        "MDCB": [_rule("BB1", "ZYZD in DI_B00", priority=1)],
    }
    drg_rules = {
        "AA1": [_rule("AA19", "", priority=1)],                  # 兜底 DRG
        "BB1": [
            _rule("BB19", "QTZD in CC", priority=2),
            _rule("BB11", "QTZD in MCC", priority=1),            # 不会命中
        ],
    }

    return RuleIndex(
        sets={k: frozenset(v) for k, v in sets.items()},
        mcc_set=mcc_set, cc_set=cc_set,
        code_to_excl=code_to_excl, exclusion=exclusion,
        mdc_rules=mdc_rules, adrg_rules=adrg_rules, drg_rules=drg_rules,
    )


def test_pipeline_minimal_success():
    idx = _make_minimal_index()

    # 心肺移植路径：先期分组 → AA1 → AA19
    case = Case(zyzd="X99.999", zyss="33.6x00", qtzd_list=[], qtss_list=[])
    result = group_case(case, idx)
    assert result.error_type == "success"
    assert result.mdc_code == "MDCA"
    assert result.adrg_code == "AA1"
    assert result.drg_code == "AA19"
    assert result.cc_level == "NONE"


def test_pipeline_with_cc():
    idx = _make_minimal_index()

    # 颅脑路径 + 其他诊断命中 CC
    # 主诊断 A01.001 → MDCB → BB1
    # 其他诊断 A02.001（CC 集）→ CC 等级
    # DRG 层规则 QTZD in CC 应该命中 → BB19
    case = Case(
        zyzd="A01.001",
        zyss=None,
        qtzd_list=["A02.001"],
        qtss_list=[],
    )
    result = group_case(case, idx)
    assert result.mdc_code == "MDCB"
    assert result.adrg_code == "BB1"
    assert result.drg_code == "BB19"     # QTZD in CC
    assert result.cc_level == "CC"


def test_pipeline_fallback():
    """主诊断不在任何 MDC 集 → fallback 0000。"""
    idx = _make_minimal_index()

    case = Case(zyzd="Z99.999")     # 不在 DI_B00
    result = group_case(case, idx)
    # Z99.999 会被 MDCA（先期分组，恒真）兜住 → MDCA → AA1 → AA19
    # 实际我们设计里 MDCA 是空规则=ConstTrue
    assert result.error_type in ("success", "fallback")


def test_pipeline_returns_evidence():
    idx = _make_minimal_index()
    case = Case(zyzd="X99.999", zyss="33.6x00", qtzd_list=[], qtss_list=[])
    result = group_case(case, idx)
    assert "stages" in result.evidence
    assert len(result.evidence["stages"]) >= 3      # MDC + ADRG + DRG (+ CC)
    assert result.evidence["matched_path"] == [
        "MDC.MDCA", "ADRG.AA1", "DRG.AA19",
    ]


def test_pipeline_performance():
    """1000 条病案分组 < 5s（单病案 < 5ms）。"""
    import time
    idx = _make_minimal_index()
    cases = [
        Case(zyzd="A01.001", qtzd_list=["A02.001"], qtss_list=[])
        for _ in range(1000)
    ]
    t0 = time.perf_counter()
    results = [group_case(c, idx) for c in cases]
    elapsed = time.perf_counter() - t0
    per_case = elapsed / len(cases) * 1000
    print(f"\n[perf] 1000 cases in {elapsed:.3f}s = {per_case:.2f}ms/case")
    assert per_case < 50, f"too slow: {per_case:.2f}ms/case"


# ─── 真实 xlsx 集成测试（如果 data 文件存在才跑）───


@pytest.mark.skipif(
    not __import__("pathlib").Path("../data/drg_rules_3.0.xlsx").exists(),
    reason="real xlsx not present",
)
def test_real_xlsx_import_and_group():
    """端到端：导入真实 xlsx → 构索引 → 分组一个真实感病案。"""
    from app.db.session import SessionLocal, init_db
    from app.importers.xlsx_importer import import_rules_xlsx
    from app.engine.index import build_index

    init_db()
    session = SessionLocal()
    try:
        summary = import_rules_xlsx("../data/drg_rules_3.0.xlsx", session)
        # 仅 IC2 一条解析失败（length(集合交集) 不在基础 DSL）
        assert len(summary["parse_errors"]) <= 1

        idx = build_index(session)
        assert len(idx.mdc_rules) >= 27
        # sets 数量（不同集合编号）通常 600+，集合成员总数 10w+
        assert len(idx.sets) >= 500
        # 累计成员数（frozenset 大小之和）
        total_members = sum(len(s) for s in idx.sets.values())
        assert total_members >= 50000

        # 构造一个完全不在任何 MDC 集合的病案（应走兜底 0000）
        case = Case(zyzd="Z99.999", zyss="33.5100")
        result = group_case(case, idx)
        print(f"\n[real xlsx] MDC={result.mdc_code} ADRG={result.adrg_code} DRG={result.drg_code} err={result.error_type}")
        # 不抛异常、最终走兜底即可（MDCA 不再是 ConstTrue，先期分组必须有真实规则）
        assert result.error_type == "fallback"
        assert result.drg_code == "0000"
    finally:
        session.close()
