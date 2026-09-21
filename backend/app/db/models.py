"""SQLAlchemy ORM 模型。

与《DRG 3.0 配置信息 xlsx》六张表严格对应：
- mdc_rules    → MDC
- adrg_rules   → ADRG
- drg_rules    → DRG
- set_members  → 集合
- cc_list      → CC
- exclusion_tables → 排除表

外加：
- cases          病案
- case_results   分组结果
- batch_jobs     批量任务
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ──────────────── 业务规则表 ────────────────


class MdcRule(Base):
    __tablename__ = "mdc_rules"

    mdc_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    mdc_name: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_expr: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    is_pre_mdc: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parsed_ast: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (Index("idx_mdc_priority", "priority"),)


class AdrgRule(Base):
    __tablename__ = "adrg_rules"

    adrg_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    adrg_name: Mapped[str] = mapped_column(String(128), nullable=False)
    mdc_code: Mapped[str] = mapped_column(String(16), nullable=False)
    rule_expr: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    adrg_type: Mapped[str] = mapped_column(String(16), nullable=False, default="medical")
    parsed_ast: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (Index("idx_adrg_mdc_priority", "mdc_code", "priority"),)


class DrgRule(Base):
    __tablename__ = "drg_rules"

    drg_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    drg_name: Mapped[str] = mapped_column(String(128), nullable=False)
    adrg_code: Mapped[str] = mapped_column(String(16), nullable=False)
    rule_expr: Mapped[str] = mapped_column(Text, nullable=False, default="")
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    parsed_ast: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (Index("idx_drg_adrg_priority", "adrg_code", "priority"),)


class SetMember(Base):
    __tablename__ = "set_members"

    set_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    code_name: Mapped[str] = mapped_column(String(255), default="")
    code_type: Mapped[str] = mapped_column(String(16), default="")  # DI / OP

    __table_args__ = (Index("idx_set_code", "code"),)


class CcList(Base):
    __tablename__ = "cc_list"

    diagnosis_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    level: Mapped[str] = mapped_column(String(8), primary_key=True)  # MCC / CC
    exclusion_table: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (Index("idx_cc_level", "level"),)


class ExclusionTable(Base):
    __tablename__ = "exclusion_tables"

    table_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), primary_key=True)


# ──────────────── 业务表：病案 / 结果 / 任务 ────────────────


class Case(Base):
    __tablename__ = "cases"

    case_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    zyzd: Mapped[str] = mapped_column(String(32), nullable=False)
    zyss: Mapped[str | None] = mapped_column(String(32))
    qtzd_list: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    qtss_list: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    nl: Mapped[int | None] = mapped_column(Integer)
    xb: Mapped[int | None] = mapped_column(Integer)
    xsrtl: Mapped[int | None] = mapped_column(Integer)
    xsrtz: Mapped[int | None] = mapped_column(Integer)
    # 病案管理信息：日期按日（病案按日归档，精确时分秒意义不大）；出院科室用文本
    admission_date: Mapped[date | None] = mapped_column(Date)
    discharge_date: Mapped[date | None] = mapped_column(Date)
    discharge_department: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )

    results: Mapped[list["CaseResult"]] = relationship(back_populates="case")

    __table_args__ = (
        Index("idx_case_discharge_date", "discharge_date"),
        Index("idx_case_discharge_dept", "discharge_department"),
    )


class CaseResult(Base):
    __tablename__ = "case_results"

    result_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("cases.case_id"), nullable=False
    )
    mdc_code: Mapped[str | None] = mapped_column(String(16))
    adrg_code: Mapped[str | None] = mapped_column(String(16))
    drg_code: Mapped[str | None] = mapped_column(String(16))
    cc_level: Mapped[str | None] = mapped_column(String(8))   # MCC / CC / NONE
    error_type: Mapped[str | None] = mapped_column(String(16))
    error_msg: Mapped[str | None] = mapped_column(Text)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )

    case: Mapped["Case"] = relationship(back_populates="results")

    __table_args__ = (
        Index("idx_result_case", "case_id"),
        Index("idx_result_drg", "drg_code"),
    )


class BatchJob(Base):
    __tablename__ = "batch_jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fallback: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_path: Mapped[str | None] = mapped_column(Text)
    error_log: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class HisSyncLog(Base):
    """HIS 同步日志：88.3 桥接机每次同步一行。"""
    __tablename__ = "his_sync_logs"

    log_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sync_type: Mapped[str] = mapped_column(String(16), default="cron")  # 'cron' / 'manual'
    source: Mapped[str | None] = mapped_column(String(128))  # 88.3 主机名或 IP
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    received: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    fallback: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="success")  # 'success' / 'partial' / 'failed'
    error_msg: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (Index("idx_sync_log_started_at", "started_at"),)


def init_schema(engine) -> None:
    """在 engine 上创建所有表（幂等）。

    对已存在的 cases 表执行 ALTER TABLE ADD COLUMN 补齐新增字段。
    SQLite 不支持 IF NOT EXISTS 列，用 PRAGMA table_info 探测。
    """
    from sqlalchemy import inspect, text

    Base.metadata.create_all(engine)

    insp = inspect(engine)
    if "cases" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("cases")}
    with engine.begin() as conn:
        if "admission_date" not in existing:
            conn.execute(text("ALTER TABLE cases ADD COLUMN admission_date DATE"))
        if "discharge_date" not in existing:
            conn.execute(text("ALTER TABLE cases ADD COLUMN discharge_date DATE"))
        if "discharge_department" not in existing:
            conn.execute(text("ALTER TABLE cases ADD COLUMN discharge_department VARCHAR(64)"))
        # 索引（SQLite ALTER TABLE ADD INDEX 用 CREATE INDEX 即可）
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_case_discharge_date ON cases(discharge_date)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_case_discharge_dept ON cases(discharge_department)"
        ))
        # his_sync_logs 表由 Base.metadata.create_all 自动创建
