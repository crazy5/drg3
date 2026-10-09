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
    """AST 求值时的子节点证据。

    每个节点代表一次表达式/判断的中间证据，形成一棵树。
    - label: 当前节点的名称，如 "InCheck"、"And"、"Variable"。
    - children: 子节点，用于递归记录嵌套求值过程。
    - attrs: 节点附加属性，例如命中代码、变量列表、匹配状态等。
    """

    def __init__(self, label: str = ""):
        # 节点标签，通常用于标识当前 AST 子节点的类型或名称。
        self.label = label
        # 子节点列表，按求值顺序或树结构保存下一级证据。
        self.children: list[EvidenceNode] = []
        # 额外元数据，例如 matched、hit_codes、set_id、variables 等。
        self.attrs: dict[str, Any] = {}

    def child(self, label: str) -> "EvidenceNode":
        # 为当前节点创建一个子证据节点，便于表达式的递归求值链路展开。
        n = EvidenceNode(label=label)
        self.children.append(n)
        return n

    def set(self, **kw: Any) -> None:
        # 合并节点属性，常用于记录命中状态、变量名、集合标签等信息。
        self.attrs.update(kw)

    def to_dict(self) -> dict:
        # 将树状证据结构转换为 JSON 可序列化对象，便于输出到 case_results。
        d: dict[str, Any] = {"label": self.label, **self.attrs}
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d


class StageTrace:
    """一个 stage（MDC/ADRG/DRG/CC）的完整尝试轨迹。

    记录某个阶段中所有规则的尝试顺序、匹配情况和最终命中结果，
    用于后续回放或复盘为什么某条规则被选中或被跳过。
    """

    def __init__(self, stage: str):
        # 阶段名，如 "MDC"、"ADRG"、"DRG"、"CC"。
        self.stage = stage
        # 记录每一条规则的调用结果列表。
        self.tried_rules: list[dict] = []
        # 当前阶段命中的首个规则，通常用于快速定位最终命中点。
        self.matched_rule: str | None = None

    def add_tried(self, rule_code: str, raw_expr: str, matched: bool,
                  children: EvidenceNode, skip_reason: str | None = None) -> None:
        # 构造一条规则尝试记录，保存规则代码、原始表达式和匹配结果。
        rec: dict[str, Any] = {
            "rule_code": rule_code,
            "raw_expr": raw_expr,
            "matched": matched,
        }
        if matched:
            # 只在命中时保留子树证据，避免无效的树结构膨胀。
            rec["children"] = children.to_dict().get("children", [])
        if skip_reason:
            # 被跳过的原因，如 "first rule matched" 或 "guard failed"。
            rec["skip_reason"] = skip_reason
        self.tried_rules.append(rec)
        if matched and self.matched_rule is None:
            # 记录第一次命中的规则，供后续 quick lookup 使用。
            self.matched_rule = rule_code

    def to_dict(self) -> dict:
        # 输出该阶段的完整调试记录，供前端或日志序列化展示。
        return {
            "stage": self.stage,
            "tried_rules": self.tried_rules,
            "matched_rule": self.matched_rule,
        }


class EvidenceCollector:
    """总收集器。

    汇总所有 stage 的轨迹，并记录最终命中路径，便于输出完整证据链。
    """
    def __init__(self, case_summary: dict | None = None):
        # 案例摘要信息，例如 case_id、project、症状描述等元数据。
        self.case_summary = case_summary or {}
        # 各阶段的追踪记录。
        self.stages: list[StageTrace] = []
        # 最终命中的规则路径，形如 ["MDC.MDCA", "ADRG.AA1"]。
        self.matched_path: list[str] = []

    def new_stage(self, stage: str) -> StageTrace:
        # 创建并注册一个新的 stage 轨迹采集器。
        s = StageTrace(stage)
        self.stages.append(s)
        return s

    def record_match(self, stage: str, rule_code: str) -> None:
        # 记录一条已命中的规则位置，形成最终证据链路径。
        self.matched_path.append(f"{stage}.{rule_code}")

    def snapshot(self) -> dict:
        # 生成最终的证据快照，供调试或结果持久化使用。
        return {
            "case_summary": self.case_summary,
            "stages": [s.to_dict() for s in self.stages],
            "matched_path": self.matched_path,
        }
