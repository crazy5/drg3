"""全局配置。

约定：
- 仓库根 = backend 的父目录（drg3/）
- data/ 目录与 SQLite 文件路径都相对仓库根
"""
from __future__ import annotations

from pathlib import Path

# backend/ 的父目录 = 仓库根
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
REPO_ROOT = BACKEND_DIR.parent

DATA_DIR = REPO_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "drg.db"
DB_URL = f"sqlite:///{DB_PATH.as_posix()}"

# 默认规则文件位置
DEFAULT_RULES_XLSX = DATA_DIR / "drg_rules_3.0.xlsx"

# 病案批量上传临时目录
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
