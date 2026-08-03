# Layer: Storage
# File: app/storage/branch_store.py
# Responsibility: 分叉分支数据的 SQLite 持久化。
#                 tree.json 是当前视图的工作副本，本存储是历史分支的归档库：
#                 - fork_nodes:   分支节点的元数据归档（title/preview/summary/分叉标记等）
#                 - branch_nodes: 分支列表（路径分解法：列表头部 = 分叉点，遇下一分叉点停止）
# Input:  节点元数据 dict、分支列表
# Output: dict / list[str] / 引用 id 集合
# 禁止: 业务判断、编排逻辑、导入 UI 库

from __future__ import annotations

import sqlite3

from app.storage.database import get_connection

_DT_FMT = "%Y-%m-%dT%H:%M:%S.%f"


def _dt_to_str(dt) -> str:
    """datetime → ISO 字符串（兼容 datetime 或 str）。"""
    if hasattr(dt, "strftime"):
        return dt.strftime(_DT_FMT)
    return str(dt)


# enabled 编码：True→1, False→0, "some"→2
def _enc_enabled(enabled) -> int:
    if enabled is True:
        return 1
    if enabled is False:
        return 0
    return 2  # "some"


def _dec_enabled(v: int):
    return {1: True, 0: False, 2: "some"}.get(v, True)


class BranchStore:
    """
    分叉数据存储（SQLite）。

    线程安全：与 MessageRepo 一致，使用同步 sqlite3 驱动 + WAL。
    fork_nodes 与 branch_nodes 的 DDL 由 database.initialize_database() 创建。
    """

    # ──────────────────────────────────────────
    # fork_nodes：节点元数据归档
    # ──────────────────────────────────────────

    def upsert_node(self, *, conversation_id: str, node_id: str,
                    role: str = "", title: str = "", preview: str = "",
                    summary: str = "", parent_id: str | None = None,
                    sort_order: int = 0, enabled: bool | str = True,
                    incomplete: bool = False,
                    thinking_message_id: str | None = None,
                    is_fork_point: bool = False, fork_branch_count: int = 0,
                    fork_current_index: int = 0, updated_at=None) -> None:
        """写入/更新一条节点元数据归档（UPSERT）。"""
        conn = get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO fork_nodes (
                    node_id, conversation_id, role, title, preview, summary,
                    parent_id, sort_order, enabled, incomplete, thinking_message_id,
                    is_fork_point, fork_branch_count, fork_current_index, updated_at
                ) VALUES (
                    :node_id, :conversation_id, :role, :title, :preview, :summary,
                    :parent_id, :sort_order, :enabled, :incomplete, :thinking_message_id,
                    :is_fork_point, :fork_branch_count, :fork_current_index, :updated_at
                )
                ON CONFLICT(node_id) DO UPDATE SET
                    role               = excluded.role,
                    title              = excluded.title,
                    preview            = excluded.preview,
                    summary            = excluded.summary,
                    parent_id          = excluded.parent_id,
                    sort_order         = excluded.sort_order,
                    enabled            = excluded.enabled,
                    incomplete         = excluded.incomplete,
                    thinking_message_id = excluded.thinking_message_id,
                    is_fork_point      = excluded.is_fork_point,
                    fork_branch_count  = excluded.fork_branch_count,
                    fork_current_index = excluded.fork_current_index,
                    updated_at         = excluded.updated_at
                """,
                {
                    "node_id": node_id,
                    "conversation_id": conversation_id,
                    "role": role,
                    "title": title,
                    "preview": preview,
                    "summary": summary,
                    "parent_id": parent_id,
                    "sort_order": sort_order,
                    "enabled": _enc_enabled(enabled),
                    "incomplete": int(bool(incomplete)),
                    "thinking_message_id": thinking_message_id,
                    "is_fork_point": int(bool(is_fork_point)),
                    "fork_branch_count": fork_branch_count,
                    "fork_current_index": fork_current_index,
                    "updated_at": _dt_to_str(updated_at),
                },
            )

    def get_node(self, node_id: str) -> dict | None:
        """按 ID 读取节点元数据归档。"""
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM fork_nodes WHERE node_id = ?", (node_id,)
        ).fetchone()
        return _row_to_node_dict(row) if row is not None else None

    def get_nodes(self, conversation_id: str) -> list[dict]:
        """读取某对话的全部节点元数据归档。"""
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM fork_nodes WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchall()
        return [_row_to_node_dict(r) for r in rows]

    def delete_nodes(self, node_ids: list[str]) -> None:
        """按 ID 批量删除节点元数据归档。"""
        if not node_ids:
            return
        conn = get_connection()
        with conn:
            conn.executemany(
                "DELETE FROM fork_nodes WHERE node_id = ?",
                [(nid,) for nid in node_ids],
            )

    # ──────────────────────────────────────────
    # branch_nodes：分支列表（路径分解法）
    # ──────────────────────────────────────────

    def get_branch_list(self, fork_point_id: str, branch_index: int) -> list[str]:
        """读取某分叉点下指定分支的节点 ID 列表（按 position 正序）。"""
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT node_id FROM branch_nodes
            WHERE fork_point_id = ? AND branch_index = ?
            ORDER BY position ASC
            """,
            (fork_point_id, branch_index),
        ).fetchall()
        return [r["node_id"] for r in rows]

    def get_all_branches(self, fork_point_id: str) -> dict[int, list[str]]:
        """读取某分叉点下的全部分支列表，返回 {branch_index: [node_ids]}。"""
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT branch_index, node_id FROM branch_nodes
            WHERE fork_point_id = ?
            ORDER BY branch_index ASC, position ASC
            """,
            (fork_point_id,),
        ).fetchall()
        branches: dict[int, list[str]] = {}
        for r in rows:
            branches.setdefault(r["branch_index"], []).append(r["node_id"])
        return branches

    def get_branch_indexes(self, fork_point_id: str) -> list[int]:
        """返回某分叉点下存在的分支编号列表（升序）。"""
        conn = get_connection()
        rows = conn.execute(
            "SELECT DISTINCT branch_index FROM branch_nodes WHERE fork_point_id = ?",
            (fork_point_id,),
        ).fetchall()
        return sorted(r["branch_index"] for r in rows)

    def set_branch(self, conversation_id: str, fork_point_id: str,
                   branch_index: int, node_ids: list[str]) -> None:
        """
        整体覆写一个分支列表（先删后插）。
        用于同步：tree.json 当前链路的分支记录需要与 tree.json 保持一致。
        """
        conn = get_connection()
        with conn:
            conn.execute(
                "DELETE FROM branch_nodes WHERE fork_point_id = ? AND branch_index = ?",
                (fork_point_id, branch_index),
            )
            self._insert_branch(conn, conversation_id, fork_point_id,
                                branch_index, node_ids)

    def add_branch(self, conversation_id: str, fork_point_id: str,
                   branch_index: int, node_ids: list[str]) -> None:
        """新增一个分支列表（编号须为分叉点下当前最大编号 + 1）。"""
        conn = get_connection()
        with conn:
            self._insert_branch(conn, conversation_id, fork_point_id,
                                branch_index, node_ids)

    @staticmethod
    def _insert_branch(conn: sqlite3.Connection, conversation_id: str,
                       fork_point_id: str, branch_index: int,
                       node_ids: list[str]) -> None:
        conn.executemany(
            """
            INSERT INTO branch_nodes
                (conversation_id, fork_point_id, branch_index, node_id, position)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (conversation_id, fork_point_id, branch_index, nid, i)
                for i, nid in enumerate(node_ids)
            ],
        )

    def delete_branch(self, fork_point_id: str, branch_index: int) -> None:
        """删除一个分支列表。"""
        conn = get_connection()
        with conn:
            conn.execute(
                "DELETE FROM branch_nodes WHERE fork_point_id = ? AND branch_index = ?",
                (fork_point_id, branch_index),
            )

    def delete_all_for_fork_point(self, fork_point_id: str) -> None:
        """删除某分叉点名下的全部分支列表。"""
        conn = get_connection()
        with conn:
            conn.execute(
                "DELETE FROM branch_nodes WHERE fork_point_id = ?",
                (fork_point_id,),
            )

    def delete_all_for_conversation(self, conversation_id: str) -> None:
        """删除某对话的全部分支数据（branch_nodes + fork_nodes）。"""
        conn = get_connection()
        with conn:
            conn.execute(
                "DELETE FROM branch_nodes WHERE conversation_id = ?",
                (conversation_id,),
            )
            conn.execute(
                "DELETE FROM fork_nodes WHERE conversation_id = ?",
                (conversation_id,),
            )

    def get_all_referenced_node_ids(self, conversation_id: str | None = None) -> set[str]:
        """
        返回 branch_nodes 中引用的全部节点 ID（孤儿行清理用）。

        Args:
            conversation_id: 限定某对话（None 表示全部）
        """
        conn = get_connection()
        if conversation_id is not None:
            rows = conn.execute(
                "SELECT node_id FROM branch_nodes WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT node_id FROM branch_nodes").fetchall()
        return {r["node_id"] for r in rows}

    def node_id_in_any_branch(self, node_id: str) -> bool:
        """节点是否出现在任一分支列表中。"""
        conn = get_connection()
        row = conn.execute(
            "SELECT 1 FROM branch_nodes WHERE node_id = ? LIMIT 1",
            (node_id,),
        ).fetchone()
        return row is not None


def _row_to_node_dict(row: sqlite3.Row) -> dict:
    return {
        "node_id": row["node_id"],
        "conversation_id": row["conversation_id"],
        "role": row["role"],
        "title": row["title"],
        "preview": row["preview"],
        "summary": row["summary"],
        "parent_id": row["parent_id"],
        "sort_order": row["sort_order"],
        "enabled": _dec_enabled(row["enabled"]),
        "incomplete": bool(row["incomplete"]),
        "thinking_message_id": row["thinking_message_id"],
        "is_fork_point": bool(row["is_fork_point"]),
        "fork_branch_count": row["fork_branch_count"],
        "fork_current_index": row["fork_current_index"],
        "updated_at": row["updated_at"],
    }
