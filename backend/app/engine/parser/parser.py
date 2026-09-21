"""递归下降 parser。

Grammar：
  expr       := or_expr
  or_expr    := and_expr ( KW_OR and_expr )*
  and_expr   := not_expr ( KW_AND not_expr )*
  not_expr   := KW_NOT? primary
  primary    := '(' expr ')' | '{' vars '}' KW_IN IDENT   # 集合字面量 in
              | IDENT ( KW_NOT ) KW_IN IDENT             # var [not] in set
              | IDENT OP NUMBER                          # 比较
              | NUMBER                                    # 恒真（单独数字）
"""
from __future__ import annotations

import uuid

from app.engine.parser.ast_nodes import (
    AndNode,
    CompareNode,
    ConstTrueNode,
    InCheckNode,
    LengthCompareNode,
    Node,
    NotNode,
    OrNode,
)
from app.engine.parser.lexer import Token, TokType, preprocess, tokenize


class ParseError(Exception):
    """DSL 解析错误（带位置信息）。"""


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    # ─── low-level cursor ───

    def peek(self, offset: int = 0) -> Token:
        return self.tokens[self.pos + offset]

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect(self, *types: TokType) -> Token:
        tok = self.peek()
        if tok.type not in types:
            raise ParseError(
                f"位置 {tok.pos}: 期望 {types}, 实际 {tok.type.name}({tok.value!r})"
            )
        return self.advance()

    def _new_evidence_id(self) -> str:
        return uuid.uuid4().hex[:8]

    # ─── grammar rules ───

    def parse(self) -> Node:
        node = self._or()
        if self.peek().type != TokType.EOF:
            t = self.peek()
            raise ParseError(f"位置 {t.pos}: 未消费完的 token {t.type.name}({t.value!r})")
        return node

    def _or(self) -> Node:
        left = self._and()
        while self.peek().type == TokType.KW_OR:
            self.advance()
            right = self._and()
            left = OrNode(left=left, right=right, evidence_id=self._new_evidence_id())
        return left

    def _and(self) -> Node:
        left = self._not()
        while self.peek().type == TokType.KW_AND:
            self.advance()
            right = self._not()
            left = AndNode(left=left, right=right, evidence_id=self._new_evidence_id())
        return left

    def _not(self) -> Node:
        if self.peek().type == TokType.KW_NOT:
            self.advance()
            operand = self._primary()
            return NotNode(operand=operand, evidence_id=self._new_evidence_id())
        return self._primary()

    def _primary(self) -> Node:
        tok = self.peek()

        # (expr)
        if tok.type == TokType.LPAREN:
            self.advance()
            node = self._or()
            self.expect(TokType.RPAREN)
            return node

        # {var1, var2} in set_id    集合字面量（左侧）
        if tok.type == TokType.LBRACE:
            return self._parse_set_literal_in()

        # length(<set_id> ∩ {<vars>}) OP NUMBER    （IC29/ADRG IC2 专用）
        if tok.type == TokType.KW_LENGTH:
            return self._parse_length_compare()

        # 单独数字 → 恒真（兜底 '1'）
        if tok.type == TokType.NUMBER:
            self.advance()
            return ConstTrueNode(evidence_id=self._new_evidence_id())

        # IDENT 开头的若干分支：var in/not in set、var op number、var（罕见）
        if tok.type == TokType.IDENT:
            return self._parse_ident_leading()

        raise ParseError(
            f"位置 {tok.pos}: 非预期的 token {tok.type.name}({tok.value!r})"
        )

    def _parse_length_compare(self) -> Node:
        """`length(<set_id> ∩ {<var1, var2,...>}) OP NUMBER`

        唯一已知用例：DRG 3.0 ADRG IC2（→ DRG IC29「多部位髋、肩、膝、肘和踝关节置换术」）
        规则：`length(OP_IC2 ∩ {ZYSS, QTSS})>=2 and ZYSS in OP_IC2`
        语义：在病例所有出现过的手术码中属于指定集合的不同码个数与阈值比较。

        右侧字段列表字面量（{ZYSS, QTSS}）必须至少含 2 个字段；当前 IC2 是 {ZYSS, QTSS}。
        """
        self.expect(TokType.KW_LENGTH)
        self.expect(TokType.LPAREN)
        left_set_id = self.expect(TokType.IDENT).value
        self.expect(TokType.INTERSECT)
        self.expect(TokType.LBRACE)
        right_vars = [self.expect(TokType.IDENT).value]
        while self.peek().type == TokType.COMMA:
            self.advance()
            right_vars.append(self.expect(TokType.IDENT).value)
        self.expect(TokType.RBRACE)
        self.expect(TokType.RPAREN)
        op = self.expect(TokType.OP).value
        lit_tok = self.advance()
        if lit_tok.type != TokType.NUMBER:
            raise ParseError(
                f"位置 {lit_tok.pos}: length(...) 比较右侧必须是数字, 实际 {lit_tok.type.name}({lit_tok.value!r})"
            )
        value = int(lit_tok.value)
        return LengthCompareNode(
            left_set_id=left_set_id,
            right_kind="vars",
            right_vars=right_vars,
            op=op,
            value=value,
            evidence_id=self._new_evidence_id(),
        )

    def _parse_set_literal_in(self) -> Node:
        """{var1, var2} in set_id    左侧变量列表（罕见）。"""
        self.expect(TokType.LBRACE)
        variables = [self.expect(TokType.IDENT).value]
        while self.peek().type == TokType.COMMA:
            self.advance()
            variables.append(self.expect(TokType.IDENT).value)
        self.expect(TokType.RBRACE)
        self.expect(TokType.KW_IN)
        set_id = self.expect(TokType.IDENT).value
        return InCheckNode(
            variables=variables, set_id=set_id, negated=False,
            evidence_id=self._new_evidence_id(),
        )

    def _parse_set_id_or_list(self) -> tuple[str, list[str] | None]:
        """解析 in 右侧的 set 引用。

        - IDENT                 → 单集合
        - {IDENT, IDENT, ...}   → 多集合列表（语义：变量属于任一集合就算命中）
        """
        tok = self.peek()
        if tok.type == TokType.IDENT:
            self.advance()
            return tok.value, None
        if tok.type == TokType.LBRACE:
            self.advance()
            ids = [self.expect(TokType.IDENT).value]
            while self.peek().type == TokType.COMMA:
                self.advance()
                ids.append(self.expect(TokType.IDENT).value)
            self.expect(TokType.RBRACE)
            return "", ids  # set_id 留空，list 携带实际值
        raise ParseError(
            f"位置 {tok.pos}: in/not in 右侧必须是集合编号或 {{...}}, 实际 {tok.type.name}({tok.value!r})"
        )

    def _parse_ident_leading(self) -> Node:
        var_name = self.expect(TokType.IDENT).value
        nxt = self.peek()

        # var in set_id  /  var in {set1, set2}  /  var not in ...
        if nxt.type in (TokType.KW_IN, TokType.KW_NOT):
            negated = (nxt.type == TokType.KW_NOT)
            self.advance()                       # not
            if negated:
                self.expect(TokType.KW_IN)
            set_id, set_list = self._parse_set_id_or_list()
            return InCheckNode(
                variables=[var_name],
                set_id=set_id,
                set_list=set_list or [],
                negated=negated,
                evidence_id=self._new_evidence_id(),
            )

        # var op number
        if nxt.type == TokType.OP:
            op = self.advance().value
            lit_tok = self.advance()
            if lit_tok.type != TokType.NUMBER:
                raise ParseError(
                    f"位置 {lit_tok.pos}: 比较右侧必须是数字, 实际 {lit_tok.type.name}({lit_tok.value!r})"
                )
            value = _to_number(lit_tok.value)
            return CompareNode(
                variable=var_name, op=op, value=value,
                evidence_id=self._new_evidence_id(),
            )

        # var 单独出现 → 视为真（兜底；实际规则里很少见）
        return ConstTrueNode(evidence_id=self._new_evidence_id())


def _to_number(s: str) -> int | float:
    """把字符串数字转 int / float（保持类型以便比较）。"""
    if "." in s:
        return float(s)
    try:
        return int(s)
    except ValueError:
        return float(s)


def parse_rule(rule_text: str | None) -> Node:
    """入口：从原始规则文本到 AST。

    - 自动预处理（折叠换行、归一恒真）
    - 空规则返回 ConstTrueNode（继承 ADRG 名的 DRG 兜底）
    """
    s = preprocess(rule_text)
    if not s:
        return ConstTrueNode()
    tokens = tokenize(s)
    parser = Parser(tokens)
    return parser.parse()


__all__ = ["parse_rule", "Parser", "ParseError"]
