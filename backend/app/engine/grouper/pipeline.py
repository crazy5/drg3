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
    """单病案分组结果。

    这是最终对外暴露的分组产物：包含 MDC/ADRG/DRG 代码、CC 预分级、
    运行状态（success / fallback / error）以及完整证据链，便于前端展示和排查。
    """
    mdc_code: str | None          # 选中的 MDC 编码；未命中时为 None
    adrg_code: str | None          # 选中的 ADRG 编码；未命中时为 None
    drg_code: str | None          # 最终 DRG 编码，兜底时为 0000
    cc_level: str                  # CC/MCC 预分级结果：CC / MCC / NONE
    error_type: str               # 'success' / 'fallback' / 'error'
    error_msg: str | None          # fallback 或 error 的原因说明
    evidence: dict                 # 证据快照，包含每层规则尝试细节与命中信息
    duration_ms: int               # 单病案分组耗时（毫秒）


def group_case(case: Case, index: RuleIndex) -> GroupResult:
    """对单个病案执行完整分组。

    流程遵循三层漏斗：MDC → ADRG → DRG。
    每层都按 priority 升序尝试规则，命中第一条即返回；未命中则走 fallback。
    同时会计算 CC 分级，并将所有尝试过的规则、短路、解释原因记录到证据链中。
    """
    t0 = time.perf_counter()
    # 先把病案抽象成评估视图，后续规则求值全部依赖这个统一视图，不再直接读原始对象属性。
    case_view = case.to_view()
    collector = EvidenceCollector(case_summary={
        "zyzd": case.zyzd,
        "zyss": case.zyss,
        "qtzd_list": case.qtzd_list or [],
        "qtss_list": case.qtss_list or [],
        "nl": case.nl, "xb": case.xb,
    })

    try:
        # ── MDC：筛选主诊断分组（病案入口） ──
        mdc_stage = collector.new_stage("MDC")
        # 按优先级顺序找首个命中的 MDC 规则；任何一层未命中都会触发 fallback
        mdc_rec = _match_first(index.mdc_rules, case_view, index, mdc_stage)
        if mdc_rec is None:
            return _fallback(collector, "no MDC matched", t0)
        collector.record_match("MDC", mdc_rec.code)

        # ── ADRG：在当前 MDC 范围内继续细分 ──
        adrg_stage = collector.new_stage("ADRG")
        adrg_list = index.adrg_rules.get(mdc_rec.code, [])
        adrg_rec = _match_first(adrg_list, case_view, index, adrg_stage)
        if adrg_rec is None:
            return _fallback(collector, f"no ADRG matched in {mdc_rec.code}", t0)
        collector.record_match("ADRG", adrg_rec.code)

        # ── CC 预分级：DRG 规则评估时可能依赖 CC_LEVEL，因此需要先计算并注入到视图 ──
        cc_stage = collector.new_stage("CC")
        # classify_cc 会返回 "MCC" / "CC" / "NONE"，并把中间证据挂在一个独立 EvidenceNode 上。
        cc_level = classify_cc(case_view, index, EvidenceNode(label="CC"))
        # 把 CC 结果包装成 stage 的一条伪规则，保证前端展示时看到完整的 CC 证据链
        _attach_cc_evidence(cc_stage, case_view, index, cc_level)

        # DRG 规则可能引用 CC_LEVEL 变量；注入 case_view 以保证评估时能拿到该分级
        enriched_view = case.to_view(cc_level=cc_level)

        # ── DRG：最终分组，可能继承 ADRG 默认规则 ──
        drg_stage = collector.new_stage("DRG")
        drg_list = index.drg_rules.get(adrg_rec.code, [])
        # 对 DRG 层使用 empty_rule_treat="match"：空规则视作继承 ADRG 的默认命中，避免无条件规则被当作无效
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
        # 任何未捕获异常都记录为 error，保持调用方可诊断且不吞异常
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
    """按 priority 顺序遍历，返回第一个命中的规则。

    这是三层漏斗的核心：MDC/ADRG/DRG 每层都复用同一逻辑，
    保证较低 priority 的规则在相同层级中优先级更高，且能保留完整证据树。

    empty_rule_treat:
      - "skip"  MDC/ADRG 层：空规则视为占位，跳过，不作为命中
      - "match" DRG 层：空规则视为该 ADRG 的默认子组，直接命中
    """
    for rule in rules:
        # parse 失败的规则（DSL 不支持）直接跳过，绝不能 silent 命中；
        # 否则历史上曾出现“规则解析失败但被当成恒真”，导致大量病案被误吸入错误分组。
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

        # 空规则代表“无独立条件”，通常用于继承父节点逻辑；
        # 但不同层级的语义不同：MDC/ADRG 允许跳过，而 DRG 可作为默认命中。
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
            # skip：占位，跳过，继续找下一个更具体的条件规则
            continue

        ev_node = EvidenceNode(label=f"rule.{rule.code}")
        try:
            # 真正的规则求值在这里：把 AST 与病案视图一起计算，
            # 产生布尔值并附带中间节点证据（如条件分支、集合判定、字段访问）。
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
    """把 classify_cc 的结果作为一条“伪规则”挂到 stage 里（便于前端展示）。

    这是为了让 CC 分级也像真实规则一样参与证据链：
    前端能在同一页里看到“CC 规则尝试了什么、命中/未命中、排除表过滤细节”。
    """
    # 直接重新跑一遍拿证据，成本很低；这样可以在 stage 内保留完整的中间过程。
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
    """兜底返回值，表示某一层没有命中规则。

    规则：
    - MDC/ADRG/DRG 任意一层未命中 → drg_code='0000'
    - 统一返回 fallback 状态，以便上层感知并记录问题原因
    """
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
