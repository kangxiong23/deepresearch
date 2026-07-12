# Layer: Storage / Infrastructure
# File: app/storage/database.py
# Responsibility: SQLite 连接管理、建表 DDL、迁移执行。
#                 只处理数据库技术细节，不含任何业务逻辑。
# Input:  config.DB_PATH（数据库文件路径）
# Output: sqlite3.Connection，供 Repo 类使用

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime
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
    执行建表 DDL 与数据迁移。幂等：表已存在时不报错。
    应在应用启动时调用一次。

    迁移逻辑：
    1. 检查是否存在旧 conversations 表
    2. 若存在且 tree.json 尚未创建 → 将对话数据迁移为树结构
    3. 迁移后重命名旧表为 conversations_bak（保留可回滚）
    """
    conn = get_connection()

    # 先执行当前 DDL（确保 messages 表存在）
    with conn:
        conn.executescript(_DDL)

    # 检测是否需要迁移
    _migrate_if_needed(conn)

    # Phase 5: MessageNode 迁移
    _migrate_message_nodes_if_needed()


# ──────────────────────────────────────────────
# DDL（仅 messages 表 — conversations 已废弃）
# ──────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL DEFAULT '',
    is_thinking     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    token_count     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages (conversation_id, created_at);
"""


# ──────────────────────────────────────────────
# 迁移：conversations 表 → tree.json
# ──────────────────────────────────────────────

def _migrate_if_needed(conn: sqlite3.Connection) -> None:
    """
    检测旧 conversations 表是否存在，若存在且 tree.json 未创建则执行完整迁移。

    迁移步骤：
        1. 读取所有旧对话记录
        2. 为每个对话生成 ConversationNode
        3. 所有节点放入"未分类"根目录下
        4. 写入 tree.json
        5. 重建 messages 表（去除 conversations 外键约束）
        6. 重命名 conversations → conversations_bak

    修复半完成迁移：若 tree.json 已存在但 conversations 表仍存在
    （之前因 tree.json 检测过早退出导致 _finalize_migration 未执行），
    仅执行 messages_old → messages 数据复制 + 旧表清理。
    """
    # 检查旧表是否存在
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'"
    ).fetchone()
    if row is None:
        return  # 无旧表，已完全迁移

    tree_dir = Path(app_config.TREE_STORE_PATH)
    tree_path = tree_dir / "tree.json"

    # ── 检测半完成迁移：tree.json 存在但 conversations 表也存在 ──
    if tree_path.exists():
        # 检查 messages_old 是否还有数据需要复制
        msg_old_row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages_old'"
        ).fetchone()
        if msg_old_row is not None:
            msg_count = conn.execute(
                "SELECT COUNT(*) FROM messages_old"
            ).fetchone()[0]
            if msg_count > 0:
                print(
                    f"[MIGRATE] 检测到半完成迁移：tree.json 已存在但 "
                    f"messages_old 中仍有 {msg_count} 条消息未复制，执行数据修复..."
                )
                _finalize_migration(conn)
                return
        # messages_old 不存在或为空，仅清理 conversations 表
        print("[MIGRATE] tree.json 已存在，清理残留的旧 conversations 表")
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("ALTER TABLE conversations RENAME TO conversations_bak")
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")
        return

    # ── 完整迁移：tree.json 不存在，执行全流程 ──

    # ── 1. 读取旧对话 ─────────────────────────
    try:
        old_rows = conn.execute(
            "SELECT * FROM conversations ORDER BY created_at ASC"
        ).fetchall()
    except sqlite3.OperationalError:
        return  # 表存在但无法读取（异常情况），跳过

    if not old_rows:
        # 空表，直接清理
        _finalize_migration(conn)
        return

    # ── 2. 为每个对话生成 ConversationNode ────
    nodes: list[dict] = []
    for r in old_rows:
        conv_id = r["id"]
        title = r["title"] if r["title"] else "未命名对话"
        created_at = r["created_at"]
        updated_at = r["updated_at"]

        # 获取消息数量（从 messages 或 messages_old 查询）
        msg_count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
            (conv_id,),
        ).fetchone()[0]

        node = {
            "id": conv_id,
            "parent_id": "root",
            "sort_order": len(nodes),
            "enabled": True,
            "title": title,
            "created_at": created_at,
            "updated_at": updated_at,
            "node_type": "conversation",
            "summary": (r["last_message_preview"] or "")[:200],
            "message_count": msg_count,
        }
        nodes.append(node)

    # ── 3. 创建树结构 ─────────────────────────
    now_iso = datetime.utcnow().isoformat()
    root_folder = {
        "id": "root",
        "parent_id": None,
        "sort_order": 0,
        "enabled": True,
        "title": "未分类",
        "created_at": now_iso,
        "updated_at": now_iso,
        "node_type": "folder",
        "context_block_ids": [],
        "attachment_paths": [],
    }

    tree_data = {
        "version": "1.0",
        "nodes": [root_folder] + nodes,
    }

    # ── 4. 写入 tree.json ─────────────────────
    tree_dir.mkdir(parents=True, exist_ok=True)
    with tree_path.open("w", encoding="utf-8") as f:
        json.dump(tree_data, f, ensure_ascii=False, indent=2)

    # 初始化空的回收站
    trash_path = tree_dir / "recycle_bin.json"
    with trash_path.open("w", encoding="utf-8") as f:
        json.dump([], f, ensure_ascii=False, indent=2)

    # ── 5 & 6. 重建 messages 表 + 重命名旧表 ──
    _finalize_migration(conn)


def _finalize_migration(conn: sqlite3.Connection) -> None:
    """
    完成迁移收尾（幂等）：
    - 若 messages_old 不存在：重命名 messages → messages_old
    - 复制 messages_old → messages（跳过已存在行）
    - 删除 messages_old
    - 重命名 conversations → conversations_bak
    """
    # 关闭外键检查以安全重建表
    conn.execute("PRAGMA foreign_keys=OFF")

    # 检查 messages_old 是否已存在（半完成迁移的残留）
    msg_old_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages_old'"
    ).fetchone()

    if not msg_old_exists:
        # 首次迁移：重命名 messages → messages_old
        conn.execute("ALTER TABLE messages RENAME TO messages_old")
    else:
        print("[MIGRATE] messages_old 已存在，跳过重命名，直接复制数据")

    # 确保 messages 表存在（可能已存在但为空，或刚被重命名掉）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id              TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role            TEXT NOT NULL,
            content         TEXT NOT NULL DEFAULT '',
            is_thinking     INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL,
            token_count     INTEGER NOT NULL DEFAULT 0
        )
    """)

    # 复制数据：使用 INSERT OR IGNORE 跳过已存在行
    result = conn.execute(
        "SELECT COUNT(*) FROM messages_old"
    ).fetchone()[0]
    if result > 0:
        conn.execute("""
            INSERT OR IGNORE INTO messages
                (id, conversation_id, role, content, is_thinking, created_at, token_count)
            SELECT id, conversation_id, role, content, is_thinking, created_at, token_count
            FROM messages_old
        """)
        conn.commit()  # 确保 INSERT 持久化
        copied = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        print(f"[MIGRATE] 从 messages_old 复制了 {result} 条 → messages 表现在有 {copied} 行")

    conn.execute("DROP TABLE IF EXISTS messages_old")
    conn.commit()  # 确保 DROP 持久化

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_conversation "
        "ON messages (conversation_id, created_at)"
    )

    # 重命名旧 conversations 表（保留可回滚）
    conv_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'"
    ).fetchone()
    if conv_exists:
        conn.execute("ALTER TABLE conversations RENAME TO conversations_bak")
        conn.commit()  # 确保 RENAME 持久化
        print("[MIGRATE] conversations → conversations_bak")

    conn.execute("PRAGMA foreign_keys=ON")


# ──────────────────────────────────────────────
# Phase 5 迁移：messages → MessageNode
# ──────────────────────────────────────────────

def _migrate_message_nodes_if_needed() -> None:
    """
    Phase 5 迁移：为现有 messages 表中的每条记录创建 MessageNode。

    检测逻辑：
    - tree.json 中存在 ConversationNode 但无任何 MessageNode → 执行迁移
    - 迁移后 tree.json 版本号更新为 "2.0"

    幂等：已存在 MessageNode 时直接跳过。
    """
    from app.storage.tree_store import TreeStore
    from app.storage.message_repo import MessageRepo
    from app.storage.models import MessageNode as MN, ConversationNode as CN

    tree_dir = Path(app_config.TREE_STORE_PATH)
    tree_path = tree_dir / "tree.json"
    if not tree_path.exists():
        return

    tree_store = TreeStore()
    tree = tree_store.get_tree()
    nodes = tree.nodes

    has_conversations = any(isinstance(n, CN) for n in nodes)
    has_message_nodes = any(isinstance(n, MN) for n in nodes)

    if not has_conversations:
        print("[MIGRATE] 无 ConversationNode，跳过 MessageNode 迁移")
        return

    if has_message_nodes:
        print("[MIGRATE] MessageNodes 已存在，跳过迁移")
        return

    print("[MIGRATE] 检测到需要创建 MessageNodes...")
    message_repo = MessageRepo()

    created_count = 0
    for node in nodes:
        if not isinstance(node, CN):
            continue

        # 跳过已有消息子节点的对话
        existing = [n for n in nodes if isinstance(n, MN) and n.parent_id == node.id]
        if existing:
            continue

        messages = message_repo.get_messages(node.id)
        if not messages:
            continue

        for i, msg in enumerate(messages):
            role_val = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
            msg_node = MN(
                id=msg.id,
                parent_id=node.id,
                message_id=msg.id,
                role=role_val,
                preview=msg.content[:60] if msg.content else "",
                title=f"{role_val}: {msg.content[:30]}" if msg.content else role_val,
                enabled=True,
                sort_order=i,
                created_at=msg.created_at,
                updated_at=msg.created_at,
            )
            tree_store.create_node(msg_node)
            created_count += 1

    # 更新版本号
    if created_count > 0:
        tree_store._root.version = "2.0"
        tree_store._save_tree(tree_store._root)
        print(f"[MIGRATE] 成功创建 {created_count} 个 MessageNode，树版本 -> 2.0")
    else:
        print("[MIGRATE] 无消息需要迁移（数据库为空），树版本不变")
