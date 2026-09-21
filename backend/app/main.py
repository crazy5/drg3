"""FastAPI 应用入口。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.cases import router as cases_router
from app.api.codes import router as codes_router
from app.api.grouping import router as grouping_router
from app.api.results import router as results_router
from app.api.rules import router as rules_router
from app.api.sync import router as sync_router
from app.db.session import SessionLocal, get_session, init_db
from app.engine.index import build_index

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动期建表 + 加载规则索引；关闭期释放。"""
    log.info("starting up: init db + build rule index")
    init_db()
    # 启动期直接用 SessionLocal，不用 yield 模式的 get_session
    session = SessionLocal()
    try:
        idx = build_index(session)
        app.state.rule_index = idx
        log.info("rule index ready: %d MDC / %d ADRG / %d DRG / %d sets",
                 len(idx.mdc_rules),
                 sum(len(v) for v in idx.adrg_rules.values()),
                 sum(len(v) for v in idx.drg_rules.values()),
                 len(idx.sets))
    finally:
        session.close()
    yield
    log.info("shutting down")


app = FastAPI(
    title="DRG 3.0 分组器",
    description="基于国家医保局 DRG 3.0 分组方案的分组引擎 + REST API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 前端开发期放开
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rules_router)
app.include_router(cases_router)
app.include_router(grouping_router)
app.include_router(results_router)
app.include_router(codes_router)
app.include_router(sync_router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
