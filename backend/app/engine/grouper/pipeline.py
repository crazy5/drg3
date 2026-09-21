"""三层漏斗分组引擎主流程。

MDC → ADRG → DRG，每层"按 priority 顺序找首个命中"。

兜底：
- 任何一层找不到命中 → drg_code='0000'，error_type='fallback'
- 任何层抛异常 → error_type='error'

证据链：所有尝试过的规则（含未命中、含短路）都记录到 EvidenceCollector。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from app.engine.grouper.cc import classify_cc
from app.engine.index import RuleIndex, RuleRecord
from app.engine.runtime.evidence import EvidenceCollector, EvidenceNode, StageTrace
from app.engine.runtime.evaluator import CaseView, evaluate

log = logging.getLogger(__name__)

FALLBACK_CODE = "0000"


@dataclass
class Case:
    """业务侧的病案。"""
    zyzd: str
    zyss: str | None = None
    qtzd_list: list[str] | None = None
    qtss_list: list[str] | None = None
    nl: int | None = None
    xb: int | None = None
    xsrtl: int | None = None
    xsrtz: int | None = None

    def to_view(self, cc_level: str | None = None) -> CaseView:
        return CaseView(
            ZYZD=self.zyzd or "",
            ZYSS=self.zyss,
            QTZD_LIST=self.qtzd_list or [],
            QTSS_LIST=self.qtss_list or [],
            NL=self.nl,
            XB=self.xb,
            XSRTL=self.xsrtl,
            XSRTZ=self.xsrtz,
            CC_LEVEL=cc_level,
        )


@dataclass
class GroupResult:
    mdc_code: str | None
    adrg_code: str | None
    drg_code: str | None
    cc_level: str
    error_type: str       # 'success' / 'fallback' / 'error'
    error_msg: str | None
    evidence: dict
    duration_ms: int


def group_case(case: Case, index: RuleIndex) -> GroupResult:
    """对单个病案执行完整分组。"""
    t0 = time.perf_counter()
    case_view = case.to_view()
    collector = EvidenceCollector(case_summary={
        "zyzd": case.zyzd,
        "zyss": case.zyss,
        "qtzd_list": case.qtzd_list or [],
        "qtss_list": case.qtss_list or [],
        "nl": case.nl, "xb": case.xb,
    })

    try:
        # ── MDC ──
        mdc_stage = collector.new_stage("MDC")
        mdc_rec = _match_first(index.mdc_rules, case_view, index, mdc_stage)
        if mdc_rec is None:
            return _fallback(collector, "no MDC matched", t0)
        collector.record_match("MDC", mdc_rec.code)

        # ── ADRG ──
        adrg_stage = collector.new_stage("ADRG")
        adrg_list = index.adrg_rules.get(mdc_rec.code, [])
        adrg_rec = _match_first(adrg_list, case_view, index, adrg_stage)
        if adrg_rec is None:
            return _fallback(collector, f"no ADRG matched in {mdc_rec.code}", t0)
        collector.record_match("ADRG", adrg_rec.code)

        # ── CC 预分级 ──
        cc_stage = collector.new_stage("CC")
        cc_level = classify_cc(case_view, index, EvidenceNode(label="CC"))  # 单独一份证据给 CC stage
        # 把 classify_cc 的证据挂到 cc_stage
        _attach_cc_evidence(cc_stage, case_view, index, cc_level)

        # DRG 规则可能引用 CC_LEVEL 变量；注入 case_view
        enriched_view = case.to_view(cc_level=cc_level)

        # ── DRG ──
        drg_stage = collector.new_stage("DRG")
        drg_list = index.drg_rules.get(adrg_rec.code, [])
        drg_rec = _match_first(drg_list, enriched_view, index, drg_stage, empty_rule_treat="match")
        if drg_rec is None:
            return _fallback(collector, f"no DRG matched in {adrg_rec.code}", t0)
        collector.record_match("DRG", drg_rec.code)

        duration_ms = int((time.perf_counter() - t0) * 1000)
        return GroupResult(
            mdc_code=mdc_rec.code,
            adrg_code=adrg_rec.code,
            drg_code=drg_rec.code,
            cc_level=cc_level,
            error_type="success",
            error_msg=None,
            evidence=collector.snapshot(),
            duration_ms=duration_ms,
        )
    except Exception as e:        # noqa: BLE001
        log.exception("group_case failed")
        duration_ms = int((time.perf_counter() - t0) * 1000)
        return GroupResult(
            mdc_code=None,
            adrg_code=None,
            drg_code=FALLBACK_CODE,
            cc_level="NONE",
            error_type="error",
            error_msg=str(e),
            evidence=collector.snapshot(),
            duration_ms=duration_ms,
        )


def _match_first(
    rules: list[RuleRecord],
    case_view: CaseView,
    index: RuleIndex,
    stage: StageTrace,
    empty_rule_treat: str = "skip",
) -> RuleRecord | None:
    """
    按 priority 顺序遍历，第一个命中即返回。
    empty_rule_treat:
      - "skip"  MDC/ADRG 层：空规则视为占位，跳过
      - "match" DRG 层：空规则视为该 ADRG 的默认子组，直接命中
    """
    for rule in rules:
        # parse 失败的规则（DSL 不支持）直接跳过，绝不能 silent 命中
        if rule.is_invalid or rule.ast is None:
            ev_node = EvidenceNode(label=f"rule.{rule.code}")
            ev_node.set(kind="invalid", note="DSL 解析失败，规则被跳过（防 silent 命中）")
            stage.add_tried(
                rule_code=rule.code,
                raw_expr=rule.raw_expr,
                matched=False,
                children=ev_node,
                skip_reason="rule AST is invalid (DSL parse failed at import time)",
            )
            continue

        if not rule.raw_expr or not rule.raw_expr.strip():
            if empty_rule_treat == "match":
                # DRG 层：空规则直接命中（继承 ADRG），记录恒真证据
                ev_node = EvidenceNode(label=f"rule.{rule.code}")
                ev_node.set(kind="const_true", note="继承 ADRG 规则，无独立条件")
                stage.add_tried(
                    rule_code=rule.code,
                    raw_expr="(继承 ADRG)",
                    matched=True,
                    children=ev_node,
                )
                return rule
            # skip：占位，跳过
            continue

        ev_node = EvidenceNode(label=f"rule.{rule.code}")
        try:
            matched = evaluate(rule.ast, case_view, index, ev_node)
        except Exception as e:        # noqa: BLE001
            stage.add_tried(
                rule_code=rule.code,
                raw_expr=rule.raw_expr,
                matched=False,
                children=ev_node,
                skip_reason=f"evaluation error: {e}",
            )
            continue

        if matched:
            stage.add_tried(
                rule_code=rule.code,
                raw_expr=rule.raw_expr,
                matched=True,
                children=ev_node,
            )
            return rule

        stage.add_tried(
            rule_code=rule.code,
            raw_expr=rule.raw_expr,
            matched=False,
            children=ev_node,
            skip_reason="rule evaluated False",
        )
    return None


def _attach_cc_evidence(
    stage: StageTrace,
    case_view: CaseView,
    index: RuleIndex,
    cc_level: str,
) -> None:
    """把 classify_cc 的结果作为一条"伪规则"挂到 stage 里（便于前端展示）。"""
    # 直接重新跑一遍拿证据（开销可忽略）
    ev = EvidenceNode(label="CC.classify")
    classify_cc(case_view, index, ev)
    stage.add_tried(
        rule_code="__CC_CLASSIFY__",
        raw_expr=f"QTZD in MCC/CC with exclusion-table filter → {cc_level}",
        matched=(cc_level != "NONE"),
        children=ev,
        skip_reason=None if cc_level != "NONE" else "no qualifying complication",
    )


def _fallback(collector: EvidenceCollector, reason: str, t0: float) -> GroupResult:
    duration_ms = int((time.perf_counter() - t0) * 1000)
    return GroupResult(
        mdc_code=None,
        adrg_code=None,
        drg_code=FALLBACK_CODE,
        cc_level="NONE",
        error_type="fallback",
        error_msg=reason,
        evidence=collector.snapshot(),
        duration_ms=duration_ms,
    )


__all__ = ["Case", "GroupResult", "group_case", "FALLBACK_CODE"]
