"""API 依赖：RuleIndex 单例 + 重建工具。"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from app.engine.index import RuleIndex, build_index


def get_rule_index(request: Request) -> RuleIndex:
    """从 app.state 取单例索引（启动期已建好）。"""
    return request.app.state.rule_index


def rebuild_rule_index(request: Request | None, session: Session) -> RuleIndex:
    """重建内存索引。request=None 时只能用于初始化场景。

    用法：
    - 启动钩子里：调用 `build_index(session)` 后赋值给 app.state
    - 导入规则后：从 Request 对象拿到 app.state 替换
    """
    idx = build_index(session)
    if request is not None:
        request.app.state.rule_index = idx
    return idx
