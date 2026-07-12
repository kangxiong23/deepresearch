# Layer: Storage
# File: app/storage/message_repo.py
# Responsibility: messages 表的 SQLite 存取。
#                 实现 Core 层定义的 MessageRepoProtocol，
#                 只负责消息行读写与 Row→领域对象映射，不含业务逻辑。
#                 对话元数据已由 TreeStore 接管，不再维护 conversations 表。
# Input:  领域对象（Message）
# Output: 领域对象或 None
# 禁止: 业务判断、编排逻辑、导入 UI 库

from __future__ import annotations

import sqlite3
from datetime import datetime

from app.storage.database import get_connection
from app.storage.models import (
    Message,
    Role,
)


# ISO 8601 格式，SQLite TEXT 列存储
_DT_FMT = "%Y-%m-%dT%H:%M:%S.%f"


def _dt_to_str(dt: datetime) -> str:
    return dt.strftime(_DT_FMT)


def _str_to_dt(s: str) -> datetime:
    try:
        return datetime.strptime(s, _DT_FMT)
    except ValueError:
        # 兼容无微秒格式
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")


class MessageRepo:
    """
    messages 表 SQLite 仓库。

    只负责消息的持久化，不管理对话元数据。
    所有方法同步执行（SQLite 同步驱动），线程安全。
    """

    # ──────────────────────────────────────────
    # Message CRUD
    # ──────────────────────────────────────────

    def save_message(self, message: Message) -> None:
        """保存一条消息（UPSERT）。"""
        conn = get_connection()
        role_val = (
            message.role.value if hasattr(message.role, "value")
            else str(message.role)
        )
        with conn:
            conn.execute(
                """
                INSERT INTO messages
                    (id, conversation_id, role, content, is_thinking, created_at, token_count)
                VALUES
                    (:id, :conv_id, :role, :content, :is_thinking, :created_at, :token_count)
                ON CONFLICT(id) DO UPDATE SET
                    content     = excluded.content,
                    token_count = excluded.token_count
                """,
                {
                    "id":          message.id,
                    "conv_id":     message.conversation_id,
                    "role":        role_val,
                    "content":     message.content,
                    "is_thinking": int(message.is_thinking),
                    "created_at":  _dt_to_str(message.created_at),
                    "token_count": message.token_count,
                },
            )

    def get_messages(self, conversation_id: str) -> list[Message]:
        """获取某对话的全部消息，按 created_at 正序。"""
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT * FROM messages
            WHERE conversation_id = ?
            ORDER BY created_at ASC
            """,
            (conversation_id,),
        ).fetchall()
        return [_row_to_message(r) for r in rows]

    def delete_message(self, message_id: str) -> None:
        """删除单条消息（重新生成时使用）。"""
        conn = get_connection()
        with conn:
            conn.execute(
                "DELETE FROM messages WHERE id = ?", (message_id,)
            )

    def delete_messages_by_conversation(self, conversation_id: str) -> int:
        """
        删除指定对话 ID 下的所有消息（彻底删除时使用）。

        Args:
            conversation_id: 树中对话节点的 ID

        Returns:
            删除的消息行数
        """
        conn = get_connection()
        with conn:
            cursor = conn.execute(
                "DELETE FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            return cursor.rowcount

    # ──────────────────────────────────────────
    # 批量查询（Phase 5 — 多对话上下文）
    # ──────────────────────────────────────────

    def get_messages_by_ids(self, message_ids: list[str]) -> list[Message]:
        """
        按 ID 批量获取消息，按 created_at 升序排列。

        Args:
            message_ids: 消息 ID 列表

        Returns:
            Message 列表（按 created_at 升序）
        """
        if not message_ids:
            return []
        conn = get_connection()
        placeholders = ",".join(["?" for _ in message_ids])
        rows = conn.execute(
            f"""
            SELECT * FROM messages
            WHERE id IN ({placeholders})
            ORDER BY created_at ASC
            """,
            message_ids,
        ).fetchall()
        return [_row_to_message(r) for r in rows]

    def get_all_messages(self) -> list[Message]:
        """返回数据库中所有消息，按 created_at 倒序。"""
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM messages ORDER BY created_at DESC"
        ).fetchall()
        return [_row_to_message(r) for r in rows]

    def get_messages_by_conversation_ids(
        self, conversation_ids: list[str]
    ) -> list[Message]:
        """
        按对话 ID 集合批量获取消息，按 created_at 升序排列。

        Args:
            conversation_ids: 对话 ID 列表

        Returns:
            Message 列表（按 created_at 升序）
        """
        if not conversation_ids:
            return []
        conn = get_connection()
        placeholders = ",".join(["?" for _ in conversation_ids])
        rows = conn.execute(
            f"""
            SELECT * FROM messages
            WHERE conversation_id IN ({placeholders})
            ORDER BY created_at ASC
            """,
            conversation_ids,
        ).fetchall()
        return [_row_to_message(r) for r in rows]


# ──────────────────────────────────────────────
# Row → 领域对象映射（模块私有）
# ──────────────────────────────────────────────

def _row_to_message(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        conversation_id=row["conversation_id"],
        role=Role(row["role"]),
        content=row["content"],
        is_thinking=bool(row["is_thinking"]),
        created_at=_str_to_dt(row["created_at"]),
        token_count=row["token_count"],
    )
