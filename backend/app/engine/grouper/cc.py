"""CC 分级判定。

最容易出错的地方，独立成章。

关键语义：
- ZYZD（主诊断）命中了哪些 EX_n 的明细码？→ 收集成 main_tables
- QTZD（其他诊断）中存在属于 MCC 集、且其所属 EX_n 不在 main_tables 中的诊断 → 返回 MCC
- 同样思路判 CC
- MCC 优先于 CC

口径约束（与 evaluator 统一）：**所有诊断/手术码都是医保版精确编码，全程不做 X 扩展剥除
兜底**。医保版 cc_list / EX_n 都是按医保版逐码收录（约 40% 是 X 扩展），规则表设计就是
基础码 + 各 X 扩展各自归属，未命中即未命中。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.engine.runtime.evidence import EvidenceNode

if TYPE_CHECKING:
    from app.engine.index import RuleIndex
    from app.engine.runtime.evaluator import CaseView


def find_main_exclusion_tables(zyzd: str | None, index: "RuleIndex") -> set[str]:
    """主诊断 ZYZD 命中了哪些 EX_n 表？医保版精确码匹配，不剥 X。"""
    if not zyzd:
        return set()
    zyzd = zyzd.upper().strip()
    return {tid for tid, codes in index.exclusion.items() if zyzd in codes}


def _mcc_hit(qtzd: str, index: "RuleIndex") -> tuple[str, str] | None:
    """返回 (匹配的 qtzd 原码, exclusion_table)；未命中返回 None。

    医保版精确码匹配，不剥 X。
    """
    if qtzd in index.mcc_set:
        excl = index.code_to_excl.get((qtzd, "MCC"))
        if excl is not None:
            return (qtzd, excl)
    return None


def _cc_hit(qtzd: str, index: "RuleIndex") -> tuple[str, str] | None:
    """CC 集合成员 + 排除表查找（医保版精确码匹配）。"""
    if qtzd in index.cc_set:
        excl = index.code_to_excl.get((qtzd, "CC"))
        if excl is not None:
            return (qtzd, excl)
    return None


def classify_cc(
    case: "CaseView",
    index: "RuleIndex",
    ev: EvidenceNode,
) -> str:
    """判定 CC 等级：MCC / CC / NONE。"""
    main_tables = find_main_exclusion_tables(getattr(case, "ZYZD", None), index)
    ev.set(
        node="CCPrecheck",
        main_diagnosis=getattr(case, "ZYZD", None),
        main_exclusion_tables=sorted(main_tables),
    )

    qtzd_list = getattr(case, "QTZD_LIST", None) or []

    # ─── 先判 MCC ───
    mcc_ev = ev.child("MCC")
    for qtzd in qtzd_list:
        if not qtzd:
            continue
        hit = _mcc_hit(qtzd, index)
        if not hit:
            continue
        matched_code, excl = hit
        if excl in main_tables:
            mcc_ev.set(
                skipped=True,
                diagnosis=qtzd,
                reason=f"diagnosis={qtzd} same exclusion table {excl} as main",
            )
            continue
        mcc_ev.set(
            hit=True,
            diagnosis=qtzd,
            matched_code=matched_code,
            exclusion_table=excl,
            reason=f"diagnosis={qtzd} matched, excl={excl} differs from main={sorted(main_tables)}",
        )
        ev.set(final_level="MCC")
        return "MCC"

    # ─── 再判 CC ───
    cc_ev = ev.child("CC")
    for qtzd in qtzd_list:
        if not qtzd:
            continue
        hit = _cc_hit(qtzd, index)
        if not hit:
            continue
        matched_code, excl = hit
        if excl in main_tables:
            cc_ev.set(
                skipped=True,
                diagnosis=qtzd,
                reason=f"diagnosis={qtzd} same exclusion table {excl} as main",
            )
            continue
        cc_ev.set(
            hit=True,
            diagnosis=qtzd,
            matched_code=matched_code,
            exclusion_table=excl,
            reason=f"diagnosis={qtzd} matched, excl={excl} differs from main={sorted(main_tables)}",
        )
        ev.set(final_level="CC")
        return "CC"

    ev.set(final_level="NONE")
    return "NONE"


__all__ = ["classify_cc", "find_main_exclusion_tables"]
