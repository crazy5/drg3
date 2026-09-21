"""SQLAlchemy Engine / Session 单例。

SQLite + check_same_thread=False 以兼容 FastAPI 多线程。

关键：get_session 必须是 generator + yield 模式，否则请求结束不 close，
连接泄漏导致 QueuePool 耗尽（SQLAlchemy 默认 pool_size=5, max_overflow=10，
timeout=30s）。
"""
from __future__ import annotations

import logging
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import DB_URL
from app.db.models import init_schema

log = logging.getLogger(__name__)

engine = create_engine(
    DB_URL,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


def get_session() -> Generator[Session, None, None]:
    """FastAPI 依赖注入用：yield 模式，请求结束自动 close 归还连接到池。

    不能简单 `return SessionLocal()`，那样不会 close，连接池会泄漏。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """启动时调用：建表（首次运行时）。"""
    init_schema(engine)
    # 切 WAL 模式：允许并发读 + 单写串行不阻塞读者
    # 同步脚本大批量并发写 cases / case_results 时不互相 block
    with engine.connect() as conn:
        from sqlalchemy import text
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA synchronous=NORMAL"))
        conn.commit()
    log.info("SQLite 切到 WAL 模式（journal_mode=WAL, synchronous=NORMAL）")
