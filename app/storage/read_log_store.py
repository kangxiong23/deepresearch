# Layer: Storage
# File: app/storage/read_log_store.py
# Responsibility: file_reads 表的 SQLite 存取——记录"工具读过哪些文件"。
#                 只追加、按对话裁剪；不碰 tree.json（tree 每次改动全量重写，
#                 而这是一条只追加的日志，形状不对）。
# Input:  conversation_id / path / 行范围
# Output: 记录行（dict）
# 禁止: 业务判断、文件 IO、UI 导入

from __future__ import annotations

from datetime import datetime

from app.storage.database import get_connection
from app.storage.models import utcnow


# ISO 8601 格式，与 messages 表保持一致
_DT_FMT = "%Y-%m-%dT%H:%M:%S.%f"

# 每个对话保留的读取记录条数上限。太短会让"上次读到哪"迅速失真，
# 太长则面包屑里全是噪声；50 条覆盖一次深挖足够。
MAX_ENTRIES_PER_CONVERSATION = 50


def _dt_to_str(dt: datetime) -> str:
    return dt.strftime(_DT_FMT)


class ReadLogStore:
    """
    工具读取记录仓库（D3）。

    工具读取是**实时**的：内容不进消息、不产生挂载，只在读成功时追加一条
    (conversation_id, path, start_line, end_line) 记录。下一轮的面包屑
    由这份持久化记录推导——重启后仍在，不再是内存里的假象。
    """

    def __init__(self, keep: int = MAX_ENTRIES_PER_CONVERSATION) -> None:
        self._keep = max(1, int(keep))

    # ──────────────────────────────────────────
    # 写入
    # ──────────────────────────────────────────

    def append(
        self,
        conversation_id: str,
        path: str,
        start_line: int = 0,
        end_line: int = 0,
    ) -> None:
        """追加一条读取记录，并裁剪该对话的历史记录（超出保留条数的删除）。"""
        if not conversation_id or not path:
            return
        conn = get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO file_reads
                    (conversation_id, path, start_line, end_line, at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    path,
                    int(start_line or 0),
                    int(end_line or 0),
                    _dt_to_str(utcnow()),
                ),
            )
            conn.execute(
                """
                DELETE FROM file_reads
                WHERE conversation_id = ?
                  AND id NOT IN (
                      SELECT id FROM file_reads
                      WHERE conversation_id = ?
                      ORDER BY id DESC
                      LIMIT ?
                  )
                """,
                (conversation_id, conversation_id, self._keep),
            )

    # ──────────────────────────────────────────
    # 读取
    # ──────────────────────────────────────────

    def recent(self, conversation_id: str, limit: int = 0) -> list[dict]:
        """
        返回最近读取记录，**新的在前**。

        Args:
            limit: 0 表示取全部（仍受保留条数约束）
        """
        if not conversation_id:
            return []
        count = int(limit) if limit and limit > 0 else self._keep
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT conversation_id, path, start_line, end_line, at
            FROM file_reads
            WHERE conversation_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (conversation_id, count),
        ).fetchall()
        return [
            {
                "conversation_id": r["conversation_id"],
                "path": r["path"],
                "start_line": r["start_line"],
                "end_line": r["end_line"],
                "at": r["at"],
            }
            for r in rows
        ]

    # ──────────────────────────────────────────
    # 清理
    # ──────────────────────────────────────────

    def delete_by_conversation(self, conversation_id: str) -> int:
        """删除某对话的全部读取记录（彻底删除对话时调用）。"""
        if not conversation_id:
            return 0
        conn = get_connection()
        with conn:
            cur = conn.execute(
                "DELETE FROM file_reads WHERE conversation_id = ?",
                (conversation_id,),
            )
            return cur.rowcount

    def clear(self) -> int:
        """清空全部读取记录（维护用）。"""
        conn = get_connection()
        with conn:
            cur = conn.execute("DELETE FROM file_reads")
            return cur.rowcount

    def count(self, conversation_id: str) -> int:
        """某对话的记录条数（测试/诊断用）。"""
        conn = get_connection()
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM file_reads WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        return int(row["n"]) if row else 0
