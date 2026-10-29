#!/usr/bin/env python3
# One-time 维护脚本：清理 SQLite messages 表中未被任何来源引用的孤儿消息行。
#
# 引用来源（任一命中即视为“被引用”，予以保留）：
#   1. tree.json 的 MessageNode.message_id 与 thinking_message_id
#   2. recycle_bin.json 中 message 节点的 message_id 与 thinking_message_id
#   3. branch_nodes 表引用的 node_id（分支切换需要）
#   4. fork_nodes 表归档的 node_id 与 thinking_message_id（分支切换需要）
#
# 默认 dry-run：只统计并打印孤儿行，不做任何修改。
# 加 --execute 才真正删除孤儿行。
#
# 用法（在项目根目录运行）：
#   python scripts/sync_db_with_tree.py            # dry-run
#   python scripts/sync_db_with_tree.py --execute  # 真正删除

from __future__ import annotations

import json
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中，使 `import config` / `from app...` 可用
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as app_config
from app.storage.branch_store import BranchStore
from app.storage.database import get_connection
from app.storage.message_repo import MessageRepo


def _collect_tree_references() -> set[str]:
    """从 tree.json 收集被引用的 message_id / thinking_message_id。"""
    refs: set[str] = set()
    tree_path = Path(app_config.TREE_STORE_PATH) / "tree.json"
    if not tree_path.exists():
        return refs
    try:
        data = json.loads(tree_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return refs
    for node in data.get("nodes", []):
        if node.get("node_type") != "message":
            continue
        mid = node.get("message_id") or node.get("id")
        if mid:
            refs.add(mid)
        tid = node.get("thinking_message_id")
        if tid:
            refs.add(tid)
    return refs


def _collect_trash_references() -> set[str]:
    """从 recycle_bin.json 收集被引用的 message_id / thinking_message_id。"""
    refs: set[str] = set()
    trash_path = Path(app_config.TREE_STORE_PATH) / "recycle_bin.json"
    if not trash_path.exists():
        return refs
    try:
        entries = json.loads(trash_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return refs
    if not isinstance(entries, list):
        return refs
    for entry in entries:
        data = entry.get("node_data", {}) if isinstance(entry, dict) else {}
        if data.get("node_type") != "message":
            continue
        mid = data.get("message_id") or data.get("id")
        if mid:
            refs.add(mid)
        tid = data.get("thinking_message_id")
        if tid:
            refs.add(tid)
    return refs


def _table_exists(name: str) -> bool:
    row = get_connection().execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def collect_branch_keep_ids(
    store: BranchStore | None = None,
) -> tuple[set[str], set[str]]:
    """
    分支存储引用的消息行——即使用户当前看不到它们，也必须保留。

    分支切换会把旧链归档进 SQLite（tree.json 只存当前链），这些行一旦被当孤儿
    删掉，切回旧分支就是一片空白。

    Args:
        store: BranchStore（测试注入用；默认新建，读 config 的 DB_PATH）

    Returns:
        (node_ids, thinking_ids)：branch_nodes 引用的 node_id + fork_nodes 归档的
        node_id，以及 fork_nodes 绑定的 thinking_message_id。
    """
    store = store or BranchStore()
    node_ids: set[str] = set()
    thinking_ids: set[str] = set()
    if _table_exists("branch_nodes"):
        node_ids |= store.get_all_referenced_node_ids()
    if _table_exists("fork_nodes"):
        node_ids |= store.get_all_archived_node_ids()
        thinking_ids |= store.get_all_thinking_message_ids()
    return node_ids, thinking_ids


def main() -> int:
    execute = "--execute" in sys.argv[1:]

    if not _table_exists("messages"):
        print("[sync_db_with_tree] messages 表不存在（数据库未初始化），无孤儿行可清理")
        return 0

    branch_store = BranchStore()
    message_repo = MessageRepo()

    referenced: set[str] = set()
    referenced |= _collect_tree_references()
    referenced |= _collect_trash_references()
    node_ids, thinking_ids = collect_branch_keep_ids(branch_store)
    referenced |= node_ids | thinking_ids

    all_messages = message_repo.get_all_messages()
    orphans = [m for m in all_messages if m.id not in referenced]

    print(f"[sync_db_with_tree] 总消息行: {len(all_messages)}")
    print(f"[sync_db_with_tree] 被引用行: {len(referenced)}")
    print(f"[sync_db_with_tree] 孤儿行: {len(orphans)}")

    for m in orphans:
        role = m.role.value if hasattr(m.role, "value") else str(m.role)
        print(
            f"  孤儿  id={m.id}  conv={m.conversation_id}  role={role}  "
            f"thinking={m.is_thinking}  content={m.content[:60]!r}"
        )

    if execute:
        if orphans:
            ids = [m.id for m in orphans]
            deleted = message_repo.delete_messages_by_ids(ids)
            conn = get_connection()
            conn.commit()
            print(f"[sync_db_with_tree] 已删除 {deleted} 条孤儿行")
        else:
            print("[sync_db_with_tree] 无孤儿行，未做修改")
    else:
        print("[sync_db_with_tree] dry-run 模式：未做任何修改（加 --execute 真正删除）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
