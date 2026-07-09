# Layer: Storage / Infrastructure
# File: app/storage/database.py
# Responsibility: SQLite 连接管理、建表 DDL、迁移执行。
#                 只处理数据库技术细节，不含任何业务逻辑。
# Input:  config.DB_PATH（数据库文件路径）
# Output: sqlite3.Connection，供 Repo 类使用

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import config as app_config


# 每个线程持有独立连接（sqlite3 连接非线程安全）
_local = threading.local()


def get_connection() -> sqlite3.Connection:
    """
    获取当前线程的 SQLite 连接（懒初始化）。
    连接开启 WAL 模式与外键约束，Row 工厂返回字典式访问。
    """
    if not hasattr(_local, "conn") or _local.conn is None:
        db_path = Path(app_config.DB_PATH)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn

    return _local.conn


def initialize_database() -> None:
    """
    执行建表 DDL。幂等：表已存在时不报错。
    应在应用启动时调用一次。
    """
    conn = get_connection()
    with conn:
        conn.executescript(_DDL)


# ──────────────────────────────────────────────
# DDL
# ──────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS conversations (
    id                   TEXT PRIMARY KEY,
    title                TEXT NOT NULL DEFAULT '新对话',
    last_message_preview TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL DEFAULT '',
    is_thinking     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    token_count     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages (conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_conversations_updated
    ON conversations (updated_at DESC);
"""
