#!/usr/bin/env python
# File: sync_db_with_tree.py
# Responsibility: 数据一致性维护工具 — 以 tree.json / recycle_bin.json 为唯一数据源，
#                 清理 SQLite messages 表中的孤立消息行。
#
# 背景：删除操作只从 tree.json 移除 MessageNode，不会删除 messages 表中的行。
#       长期运行后 DB 会积累孤儿数据（不影响功能，但占用空间、拖慢查询）。
#       本脚本根据 tree.json 的内容删除数据库中不再被引用的消息行。
#
# 孤儿判定（保守策略，满足全部条件才删除）：
#   1. 消息 id 不在任何 tree.json MessageNode.message_id 中
#   2. 消息 id 不在任何 recycle_bin.json MessageNode.message_id 中（保持可恢复）
#   3. 消息 conversation_id 不属于"没有任何 MessageNode 子节点的存活 ConversationNode"
#      （即 Phase 5 迁移前的旧对话，其消息只能通过 conversation_id 查询，必须保留）。
#      已有 MessageNode 的对话：被删除的消息不再有树引用，属于孤儿，可删除。
#   4. 分叉功能（Phase 7）：消息 id 不在分支存储（branch_nodes / fork_nodes）中，
#      且不在归档 assistant 的 thinking_message_id 绑定中 —— 历史分支内容
#      只存在于分支存储，切换分支时按需重建，必须保留。
#
# 用法：
#   python scripts/sync_db_with_tree.py            # dry-run，仅打印统计与将删除的消息
#   python scripts/sync_db_with_tree.py --execute  # 实际执行删除
#   python scripts/sync_db_with_tree.py --json     # 输出 JSON 统计（便于脚本化调用）

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 脚本位于 scripts/ 子目录：把项目根加入 sys.path，保证 import config / app 可用
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as app_config


# ──────────────────────────────────────────────
# JSON 加载
# ──────────────────────────────────────────────

def load_tree_nodes(tree_path: Path) -> list[dict]:
    """读取 tree.json 的所有节点（dict 列表）。"""
    if not tree_path.exists():
        return []
    try:
        with tree_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("nodes", [])
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[SYNC] 警告：无法读取 {tree_path}: {exc}")
        return []


def load_trash_node_data(trash_path: Path) -> list[dict]:
    """读取 recycle_bin.json 中所有条目的 node_data。"""
    if not trash_path.exists():
        return []
    try:
        with trash_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return [e.get("node_data", {}) for e in data if isinstance(e, dict)]
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[SYNC] 警告：无法读取 {trash_path}: {exc}")
        return []


def collect_message_ids(nodes: list[dict]) -> set[str]:
    """收集所有 MessageNode 的 message_id。"""
    return {
        n.get("message_id", n.get("id", ""))
        for n in nodes
        if n.get("node_type") == "message" and n.get("message_id", n.get("id"))
    }


def collect_thinking_ids(nodes: list[dict]) -> set[str]:
    """
    Phase 6: 收集所有 assistant 节点绑定的 thinking_message_id。

    thinking 内容行（role=thinking）不再有独立 MessageNode，只通过
    assistant 节点的 thinking_message_id 引用 —— 这些行不是孤儿，必须保留。
    """
    return {
        n.get("thinking_message_id")
        for n in nodes
        if n.get("thinking_message_id")
    }


def collect_branch_keep_ids() -> tuple[set[str], set[str]]:
    """
    Phase 7: 收集分支存储引用的消息行 id 与 thinking 行 id。

    历史分支的节点不在 tree.json（tree.json 只保存当前链路），其消息行
    只被 branch_nodes（分支列表）与 fork_nodes（元数据归档）引用——
    这些行不是孤儿，切换分支时需要重建，必须保留。

    Returns:
        (node_ids, thinking_ids) — 分支引用节点 id 集合与绑定 thinking 行 id 集合
    """
    from app.storage.branch_store import BranchStore
    store = BranchStore()
    referenced = store.get_all_referenced_node_ids()   # branch_nodes.node_id
    archived = store.get_all_archived_node_ids()       # fork_nodes.node_id
    thinking = store.get_all_thinking_message_ids()    # fork_nodes.thinking_message_id
    return (referenced | archived, thinking)


def collect_conversations_without_message_nodes(nodes: list[dict]) -> set[str]:
    """
    返回树中没有任何 MessageNode 子节点的存活 ConversationNode id。

    这些是 Phase 5 迁移前的旧对话，其消息只能通过 conversation_id 查询
    （get_messages_for_node 的回退路径），因此其消息行必须保留。
    已有 MessageNode 子节点的对话：被删除的消息不再有树引用，属于孤儿。
    """
    conv_ids: set[str] = set()
    conv_with_msg_child: set[str] = set()
    for n in nodes:
        if n.get("node_type") == "conversation" and n.get("id"):
            conv_ids.add(n["id"])
        elif n.get("node_type") == "message" and n.get("parent_id"):
            conv_with_msg_child.add(n["parent_id"])
    return conv_ids - conv_with_msg_child


# ──────────────────────────────────────────────
# 主逻辑
# ──────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="根据 tree.json 清理 SQLite messages 表中的孤立消息行。",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="实际执行删除；默认仅打印（dry-run）。",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="以 JSON 格式输出统计结果。",
    )
    args = parser.parse_args()

    # 定位数据文件
    tree_dir = Path(app_config.TREE_STORE_PATH)
    tree_path = tree_dir / "tree.json"
    trash_path = tree_dir / "recycle_bin.json"

    # 初始化数据库（确保 messages 表存在）
    from app.storage.database import initialize_database
    initialize_database()

    from app.storage.message_repo import MessageRepo
    repo = MessageRepo()

    all_messages = repo.get_all_messages()

    # 收集 tree.json / recycle_bin.json 中的引用
    tree_nodes = load_tree_nodes(tree_path)
    trash_node_data = load_trash_node_data(trash_path)

    tree_ids = collect_message_ids(tree_nodes)
    trash_ids = collect_message_ids(trash_node_data)
    # Phase 6: 绑定 thinking 行（无独立 MessageNode，通过 thinking_message_id 引用）
    tree_thinking_ids = collect_thinking_ids(tree_nodes)
    trash_thinking_ids = collect_thinking_ids(trash_node_data)
    # 仅旧数据回退对话按 conversation_id 保留（见函数 docstring）
    conv_ids = collect_conversations_without_message_nodes(tree_nodes)
    # Phase 7: 分支存储引用的行（历史分支内容，切换时重建）
    branch_node_ids, branch_thinking_ids = collect_branch_keep_ids()

    # 判定孤儿行
    orphan_messages = [
        m for m in all_messages
        if m.id not in tree_ids
        and m.id not in trash_ids
        and m.id not in tree_thinking_ids
        and m.id not in trash_thinking_ids
        and m.id not in branch_node_ids
        and m.id not in branch_thinking_ids
        and m.conversation_id not in conv_ids
    ]

    # 保留原因统计（优先 tree → trash → thinking 绑定 → 分支存储 → live conversation）
    kept = [m for m in all_messages if m not in orphan_messages]
    n_tree = sum(1 for m in kept if m.id in tree_ids)
    n_trash = sum(1 for m in kept if m.id in trash_ids)
    n_thinking = sum(
        1 for m in kept
        if m.id not in tree_ids and m.id not in trash_ids
        and (m.id in tree_thinking_ids or m.id in trash_thinking_ids)
    )
    n_branch = sum(
        1 for m in kept
        if m.id not in tree_ids and m.id not in trash_ids
        and m.id not in tree_thinking_ids and m.id not in trash_thinking_ids
        and (m.id in branch_node_ids or m.id in branch_thinking_ids)
    )
    n_conv = sum(1 for m in kept
                 if m.id not in tree_ids and m.id not in trash_ids
                 and m.id not in tree_thinking_ids and m.id not in trash_thinking_ids
                 and m.id not in branch_node_ids and m.id not in branch_thinking_ids)

    stats = {
        "total": len(all_messages),
        "keeping": len(kept),
        "keep_by_tree": n_tree,
        "keep_by_trash": n_trash,
        "keep_by_thinking_binding": n_thinking,
        "keep_by_branch": n_branch,
        "keep_by_legacy_conversation": n_conv,
        "orphan": len(orphan_messages),
        "tree_path": str(tree_path),
        "db_path": app_config.DB_PATH,
    }

    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print(f"[SYNC] DB: {app_config.DB_PATH}")
        print(f"[SYNC] tree.json: {tree_path}")
        print(f"[SYNC] Total: {stats['total']} | "
              f"Keeping: {stats['keeping']} "
              f"(tree: {stats['keep_by_tree']}, "
              f"trash: {stats['keep_by_trash']}, "
              f"thinking_binding: {stats['keep_by_thinking_binding']}, "
              f"branch: {stats['keep_by_branch']}, "
              f"legacy_conv: {stats['keep_by_legacy_conversation']}) | "
              f"Orphan: {stats['orphan']}")

    # 打印将删除的消息预览（限前 20 条）
    if orphan_messages and not args.json:
        preview = orphan_messages[:20]
        print(f"[SYNC] 将删除的孤立消息（显示前 {len(preview)} 条 / 共 "
              f"{len(orphan_messages)} 条）:")
        for m in preview:
            content = m.content.replace("\n", " ")[:60]
            role = m.role.value if hasattr(m.role, "value") else str(m.role)
            print(f"  - id={m.id[:12]}… conv={m.conversation_id[:12]}… "
                  f"role={role} content={content!r}")

    if not args.execute:
        if not args.json:
            print("[SYNC] dry-run 完成，未删除任何数据。"
                  "加 --execute 实际执行删除。")
        return 0

    # 实际执行删除
    for m in orphan_messages:
        repo.delete_message(m.id)

    if not args.json:
        print(f"[SYNC] 已删除 {len(orphan_messages)} 条孤立消息。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
