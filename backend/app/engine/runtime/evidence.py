"""证据链收集器。

设计：
- 树形结构，对应 AST 的求值轨迹
- 每个 stage（MDC / ADRG / DRG / CC）独立一棵子树
- 短路求值时未执行的分支标记为 'not_evaluated'
- 输出 JSON 可直接存 case_results.evidence_json

数据结构（snapshot 输出）：
{
  "case_summary": {...},
  "stages": [
    {
      "stage": "MDC",
      "tried_rules": [
        {
          "rule_code": "MDCA",
          "raw_expr": "ZYZD in DI_B00",
          "matched": true,
          "children": [
            {"node": "InCheck", "variables": ["ZYZD"], "set_id": "DI_B00",
             "matched": true, "hit_codes": ["A01.001"]}
          ]
        },
        {"rule_code": "MDCB", "matched": false, "skip_reason": "first rule matched"}
      ],
      "matched_rule": "MDCA"
    },
    ...
  ],
  "matched_path": ["MDC.MDCA", "ADRG.AA1", "DRG.AA19"]
}
"""
from __future__ import annotations

from typing import Any


class EvidenceNode:
    """AST 求值时的子节点证据。"""

    def __init__(self, label: str = ""):
        self.label = label
        self.children: list[EvidenceNode] = []
        self.attrs: dict[str, Any] = {}

    def child(self, label: str) -> "EvidenceNode":
        n = EvidenceNode(label=label)
        self.children.append(n)
        return n

    def set(self, **kw: Any) -> None:
        self.attrs.update(kw)

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"label": self.label, **self.attrs}
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d


class StageTrace:
    """一个 stage（MDC/ADRG/DRG/CC）的完整尝试轨迹。"""

    def __init__(self, stage: str):
        self.stage = stage
        self.tried_rules: list[dict] = []
        self.matched_rule: str | None = None

    def add_tried(self, rule_code: str, raw_expr: str, matched: bool,
                  children: EvidenceNode, skip_reason: str | None = None) -> None:
        rec: dict[str, Any] = {
            "rule_code": rule_code,
            "raw_expr": raw_expr,
            "matched": matched,
        }
        if matched:
            rec["children"] = children.to_dict().get("children", [])
        if skip_reason:
            rec["skip_reason"] = skip_reason
        self.tried_rules.append(rec)
        if matched and self.matched_rule is None:
            self.matched_rule = rule_code

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "tried_rules": self.tried_rules,
            "matched_rule": self.matched_rule,
        }


class EvidenceCollector:
    """总收集器。"""
    def __init__(self, case_summary: dict | None = None):
        self.case_summary = case_summary or {}
        self.stages: list[StageTrace] = []
        self.matched_path: list[str] = []

    def new_stage(self, stage: str) -> StageTrace:
        s = StageTrace(stage)
        self.stages.append(s)
        return s

    def record_match(self, stage: str, rule_code: str) -> None:
        self.matched_path.append(f"{stage}.{rule_code}")

    def snapshot(self) -> dict:
        return {
            "case_summary": self.case_summary,
            "stages": [s.to_dict() for s in self.stages],
            "matched_path": self.matched_path,
        }
