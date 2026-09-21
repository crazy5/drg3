"""词法分析器。

DSL token 类型：
  IDENT     标识符（ZYZD / OP_AA1 / MCC / EX_1 等）
  NUMBER    整数 / 浮点
  LBRACE / RBRACE / COMMA / LPAREN / RPAREN
  OP        = >= <= > <
  KW_IN / KW_NOT / KW_AND / KW_OR
  EOF

调用方负责预处理（折叠反斜杠换行等）后再传入 tokenize。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterator


class TokType(str, Enum):
    IDENT = "IDENT"
    NUMBER = "NUMBER"
    LBRACE = "LBRACE"
    RBRACE = "RBRACE"
    COMMA = "COMMA"
    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    OP = "OP"
    KW_IN = "KW_IN"
    KW_NOT = "KW_NOT"
    KW_AND = "KW_AND"
    KW_OR = "KW_OR"
    KW_LENGTH = "KW_LENGTH"   # length(...) 内置函数
    INTERSECT = "INTERSECT"   # ∩ 集合交集（IC29 规则专用）
    EOF = "EOF"


@dataclass
class Token:
    type: TokType
    value: str
    pos: int  # 在原文中的字符偏移


# 关键字 → token 类型映射
KEYWORDS = {
    "in": TokType.KW_IN,
    "not": TokType.KW_NOT,
    "and": TokType.KW_AND,
    "or": TokType.KW_OR,
    "length": TokType.KW_LENGTH,
}


# 比较运算符（按长度降序，贪婪匹配）
_COMPARE_OPS = [">=", "<=", ">", "<", "="]

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def tokenize(text: str) -> list[Token]:
    """把规则字符串切成 token 流。"""
    tokens: list[Token] = []
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        # 空白
        if ch.isspace():
            i += 1
            continue

        # 括号 / 大括号 / 逗号
        if ch == "(":
            tokens.append(Token(TokType.LPAREN, ch, i)); i += 1; continue
        if ch == ")":
            tokens.append(Token(TokType.RPAREN, ch, i)); i += 1; continue
        if ch == "{":
            tokens.append(Token(TokType.LBRACE, ch, i)); i += 1; continue
        if ch == "}":
            tokens.append(Token(TokType.RBRACE, ch, i)); i += 1; continue
        if ch == ",":
            tokens.append(Token(TokType.COMMA, ch, i)); i += 1; continue

        # 比较运算符
        matched_op = None
        for op in _COMPARE_OPS:
            if text.startswith(op, i):
                matched_op = op
                break
        if matched_op:
            tokens.append(Token(TokType.OP, matched_op, i))
            i += len(matched_op)
            continue

        # 集合交集 ∩（IC29 规则专用，U+2229 INTERSECTION）
        if text.startswith("∩", i):
            tokens.append(Token(TokType.INTERSECT, "∩", i))
            i += 1
            continue

        # 数字
        m = _NUMBER_RE.match(text, i)
        if m:
            tokens.append(Token(TokType.NUMBER, m.group(), i))
            i = m.end()
            continue

        # 标识符 / 关键字
        m = _IDENT_RE.match(text, i)
        if m:
            word = m.group()
            tok_type = KEYWORDS.get(word.lower(), TokType.IDENT)
            # 关键字保留原大小写（in / AND / And 都能识别，但统一存原词以便报错）
            tokens.append(Token(tok_type, word, i))
            i = m.end()
            continue

        raise SyntaxError(f"非法字符 {ch!r} at position {i}")

    tokens.append(Token(TokType.EOF, "", n))
    return tokens


def preprocess(rule_text: str | None) -> str:
    """把 xlsx 规则文本归一为单行表达式。

    关键点：
    1. xlsx 多行规则用反斜杠换行（如 MDCZ 多发创伤的 9 条 OR），需折叠
    2. 真实换行也要折叠
    3. xlsx 还有 _x000D_ 这种 unicode 转义（CR），也要折叠
    4. 单独 '1' 视为恒真
    """
    if rule_text is None:
        return ""
    # Excel 的 unicode 转义换行符
    s = rule_text.replace("_x000D_", " ")
    # 反斜杠 + 换行（xlsx 单元格里的强制换行）
    s = s.replace("\\\n", " ").replace("\\\r\n", " ")
    # 真实换行 / tab / 多空格 → 单空格
    s = re.sub(r"\s+", " ", s)
    s = s.strip()
    return s


__all__ = ["Token", "TokType", "tokenize", "preprocess"]
