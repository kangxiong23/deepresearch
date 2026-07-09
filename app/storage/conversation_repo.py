# Layer: Storage
# File: app/storage/conversation_repo.py
# Responsibility: 对话历史的 SQLite 存取。
#                 实现 Core 层定义的 ConversationRepoProtocol，
#                 只负责数据读写与 Row→领域对象映射，不含业务逻辑。
# Input:  领域对象（Conversation, Message）
# Output: 领域对象或 None
# 禁止: 业务判断、编排逻辑、导入 UI 库

from __future__ import annotations

from datetime import datetime

from app.storage.database import get_connection
from app.storage.models import (
    Conversation,
    ConversationDetail,
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


class ConversationRepo:
    """
    对话历史 SQLite 仓库。

    所有方法都是同步的（SQLite 同步驱动）。
    连接通过 database.get_connection() 获取，线程安全。
    """

    # ──────────────────────────────────────────
    # Conversation CRUD
    # ──────────────────────────────────────────

    def save_conversation(self, conversation: Conversation) -> None:
        """新建或更新对话元数据（UPSERT）。"""
        conn = get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO conversations (id, title, last_message_preview, created_at, updated_at)
                VALUES (:id, :title, :preview, :created_at, :updated_at)
                ON CONFLICT(id) DO UPDATE SET
                    title                = excluded.title,
                    last_message_preview = excluded.last_message_preview,
                    updated_at           = excluded.updated_at
                """,
                {
                    "id":         conversation.id,
                    "title":      conversation.title,
                    "preview":    conversation.last_message_preview,
                    "created_at": _dt_to_str(conversation.created_at),
                    "updated_at": _dt_to_str(conversation.updated_at),
                },
            )

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        """按 ID 获取对话摘要，不存在返回 None。"""
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        return _row_to_conversation(row) if row else None

    def get_conversation_detail(
        self, conversation_id: str
    ) -> ConversationDetail | None:
        """按 ID 获取完整对话（含消息列表），不存在返回 None。"""
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            return None

        messages = self.get_messages(conversation_id)
        return ConversationDetail(
            id=conversation.id,
            title=conversation.title,
            messages=messages,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )

    def list_conversations(self) -> list[Conversation]:
        """获取所有对话摘要，按 updated_at 倒序。"""
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
        return [_row_to_conversation(r) for r in rows]

    def delete_conversation(self, conversation_id: str) -> None:
        """删除对话及其所有消息（CASCADE 由外键约束处理）。"""
        conn = get_connection()
        with conn:
            conn.execute(
                "DELETE FROM conversations WHERE id = ?", (conversation_id,)
            )

    def update_conversation_meta(
        self,
        conversation_id: str,
        title: str | None = None,
        last_message_preview: str | None = None,
    ) -> None:
        """
        局部更新对话的标题或摘要预览。
        传 None 的字段不更新，updated_at 始终刷新。
        """
        conn = get_connection()
        fields: list[str] = ["updated_at = :updated_at"]
        params: dict = {"id": conversation_id, "updated_at": _dt_to_str(datetime.utcnow())}

        if title is not None:
            fields.append("title = :title")
            params["title"] = title
        if last_message_preview is not None:
            fields.append("last_message_preview = :preview")
            params["preview"] = last_message_preview

        with conn:
            conn.execute(
                f"UPDATE conversations SET {', '.join(fields)} WHERE id = :id",
                params,
            )

    # ──────────────────────────────────────────
    # Message CRUD
    # ──────────────────────────────────────────

    def save_message(self, message: Message) -> None:
        """保存一条消息（UPSERT）。"""
        conn = get_connection()
        role_val = message.role.value if hasattr(message.role, "value") else str(message.role)
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

    def append_message(self, session_id: str, message: Message) -> None:
        """
        向指定对话追加一条消息。
        conversation_service 调用此方法；内部委托给 save_message。

        Args:
            session_id: 对话 ID（写入 message.conversation_id）
            message:    待追加的 Message 对象
        """
        message.conversation_id = session_id
        self.save_message(message)

    def delete_message(self, message_id: str) -> None:
        """删除单条消息（重新生成时使用）。"""
        conn = get_connection()
        with conn:
            conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))


# ──────────────────────────────────────────────
# Row → 领域对象映射（模块私有）
# ──────────────────────────────────────────────

def _row_to_conversation(row: sqlite3.Row) -> Conversation:
    return Conversation(
        id=row["id"],
        title=row["title"],
        last_message_preview=row["last_message_preview"],
        created_at=_str_to_dt(row["created_at"]),
        updated_at=_str_to_dt(row["updated_at"]),
    )


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


# sqlite3.Row 需要在此处引入用于类型提示
import sqlite3  # noqa: E402
