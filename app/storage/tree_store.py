# Layer: Storage
# File: app/storage/tree_store.py
# Responsibility: 树形结构的 JSON 文件持久化与操作。
#                 管理 tree.json（节点层级）和 recycle_bin.json（软删除回收站）。
#                 线程安全：写入操作由 threading.Lock 保护。
#                 只负责读写 JSON 文件与对象序列化，不含业务逻辑。
# Input:  AnyTreeNode 及其子类（FolderNode, ConversationNode）
# Output: TreeRoot / AnyTreeNode / TrashEntry 领域对象
# 禁止: 业务判断、编排逻辑、导入 UI 库

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Literal

import config as app_config
from app.storage.models import (
    AnyTreeNode,
    ConversationNode,
    FolderNode,
    MessageNode,
    NodeType,
    Role,
    TrashEntry,
    TreeNode,
    TreeRoot,
)


def _atomic_replace(src: Path, dst: Path, retries: int = 5, delay: float = 0.05) -> None:
    """
    原子替换文件（os.replace），Windows 上带重试。

    Windows 上 os.replace 可能因杀毒/索引器对源或目标文件的瞬时锁定而报
    PermissionError（WinError 5）。重试几次通常即可通过（瞬态锁自动释放）。
    """
    for attempt in range(retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(delay)


class TreeStore:
    """
    树形结构 JSON 文件存储。

    文件结构：
        {TREE_STORE_PATH}/
            tree.json          ← 完整树结构（TreeRoot 序列化）
            recycle_bin.json   ← 软删除节点列表（TrashEntry 序列化）

    默认行为：
        - 首次启动自动创建默认树（含一个"未分类"根 FolderNode）
        - 所有写入操作通过 threading.Lock 串行化
        - 读取返回副本，外部修改不影响内部状态
    """

    def __init__(self, base_path: Path | None = None) -> None:
        if base_path is None:
            base_path = Path(app_config.TREE_STORE_PATH)
        self._base = base_path
        self._base.mkdir(parents=True, exist_ok=True)
        self._tree_path = self._base / "tree.json"
        self._trash_path = self._base / "recycle_bin.json"
        self._lock = threading.RLock()

        # Phase 5: O(1) 节点查找缓存
        self._node_cache: dict[str, AnyTreeNode] = {}

        # 分叉功能: 脏对话标记（tree.json 已改、尚未同步到分支存储的对话 ID 集合）
        self._dirty_conversations: set[str] = set()

        # 加载或创建默认树
        self._root = self._load_tree()
        # __init__ 结束后重建缓存（_load_tree 内部调用 _create_default_tree
        # 时会触发 _save_tree，此时 _root 尚未赋值，缓存由此处统一重建）
        self._rebuild_cache()
        # Phase 6: 旧版 thinking 节点 → assistant.thinking_message_id 绑定迁移
        self._migrate_thinking_binding()

    # ──────────────────────────────────────────
    # 内部 I/O
    # ──────────────────────────────────────────

    def _load_tree(self) -> TreeRoot:
        """从 tree.json 加载树，若无文件则创建默认树（含"未分类"根目录）。"""
        if not self._tree_path.exists():
            return self._create_default_tree()
        try:
            with self._tree_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            version = data.get("version", "1.0")
            nodes = [_dict_to_node(n) for n in data.get("nodes", [])]
            return TreeRoot(version=version, nodes=nodes)
        except (json.JSONDecodeError, OSError, KeyError):
            return self._create_default_tree()

    def _save_tree(self, root: TreeRoot) -> None:
        """写入 tree.json 并重建缓存，由线程锁保护（原子写入）。"""
        data = {
            "version": root.version,
            "nodes": [_node_to_dict(n) for n in root.nodes],
        }
        with self._lock:
            self._tree_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._tree_path.with_suffix(".tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            _atomic_replace(tmp_path, self._tree_path)
        # 写入后重建缓存（仅在 _root 已初始化后；__init__ 期间暂不重建）
        if hasattr(self, "_root") and self._root is not None:
            self._rebuild_cache()

    def _load_trash(self) -> list[TrashEntry]:
        """从 recycle_bin.json 加载回收站列表。"""
        if not self._trash_path.exists():
            return []
        try:
            with self._trash_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            entries: list[TrashEntry] = []
            for d in data:
                entries.append(TrashEntry(
                    id=d.get("id", ""),
                    json_path=d.get("json_path", ""),
                    node_data=d.get("node_data", {}),
                    deleted_at=(
                        datetime.fromisoformat(d["deleted_at"])
                        if d.get("deleted_at") else datetime.utcnow()
                    ),
                ))
            return entries
        except (json.JSONDecodeError, OSError):
            return []

    def _save_trash(self, entries: list[TrashEntry]) -> None:
        """写入 recycle_bin.json，由线程锁保护（原子写入）。"""
        data: list[dict] = []
        for e in entries:
            data.append({
                "id": e.id,
                "json_path": e.json_path,
                "node_data": e.node_data,
                "deleted_at": e.deleted_at.isoformat(),
            })
        with self._lock:
            self._trash_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._trash_path.with_suffix(".tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            _atomic_replace(tmp_path, self._trash_path)

    def _create_default_tree(self) -> TreeRoot:
        """创建默认树结构：仅包含一个"未分类"根目录。"""
        now = datetime.utcnow()
        root_folder = FolderNode(
            id="root",
            parent_id=None,
            sort_order=0,
            enabled=True,
            title="未分类",
            created_at=now,
            updated_at=now,
        )
        tree = TreeRoot(version="3.0", nodes=[root_folder])
        self._save_tree(tree)
        # 初始化空回收站
        self._save_trash([])
        return tree

    # ──────────────────────────────────────────
    # 内部迁移（Phase 6 — thinking 绑定）
    # ──────────────────────────────────────────

    def _migrate_thinking_binding(self) -> None:
        """
        [Phase 6] 旧版 thinking MessageNode → assistant.thinking_message_id 绑定迁移。

        旧格式: user → thinking → assistant（三个并列 MessageNode，v2.0 及以下）
        新格式: user → assistant(thinking_message_id=<thinking 行 id>)（v3.0）

        规则：
        - 对每个 thinking 节点，绑定到紧随其后的 assistant 兄弟节点
        - 孤儿 thinking（同父下无后随 assistant）→ 移入回收站（可恢复）
        - 所有 thinking 节点从树中移除（其内容行仍保留在 messages 表）

        幂等：tree.json 版本号 >= 3.0 直接跳过。
        """
        try:
            cur_version = float(self._root.version)
        except (TypeError, ValueError):
            cur_version = 1.0
        if cur_version >= 3.0:
            return

        thinking_nodes = [
            n for n in self._root.nodes
            if isinstance(n, MessageNode) and n.role == Role.THINKING.value
        ]
        if not thinking_nodes:
            # 无 thinking 节点也升级版本，避免每次启动重复扫描
            self._root.version = "3.0"
            self._save_tree(self._root)
            return

        # 按父节点分组，子节点按 sort_order 排序（保持原始树序）
        from collections import defaultdict
        children_by_parent: dict[str | None, list[AnyTreeNode]] = defaultdict(list)
        for n in self._root.nodes:
            children_by_parent[n.parent_id].append(n)
        for group in children_by_parent.values():
            group.sort(key=lambda n: n.sort_order)

        orphan_nodes: list[MessageNode] = []
        remove_ids: set[str] = set()

        for tn in thinking_nodes:
            siblings = children_by_parent.get(tn.parent_id, [])
            target: AnyTreeNode | None = None
            seen_tn = False
            for s in siblings:
                if s.id == tn.id:
                    seen_tn = True
                    continue
                if seen_tn and isinstance(s, MessageNode) and s.role == Role.ASSISTANT.value:
                    target = s
                    break
            if target is not None:
                target.thinking_message_id = tn.message_id
                target.updated_at = datetime.utcnow()
            else:
                orphan_nodes.append(tn)
            remove_ids.add(tn.id)

        # 孤儿 thinking 节点 → 回收站（保持可恢复）
        trash_entries: list[TrashEntry] = []
        now = datetime.utcnow()
        for tn in orphan_nodes:
            trash_entries.append(TrashEntry(
                id=str(uuid.uuid4()),
                json_path=self._build_path_for_node(tn),
                node_data=_node_to_dict(tn),
                deleted_at=now,
            ))
        if trash_entries:
            all_trash = self._load_trash()
            all_trash.extend(trash_entries)
            self._save_trash(all_trash)

        # 移除 thinking 节点并重新编号受影响父级的子节点顺序
        self._root.nodes = [n for n in self._root.nodes if n.id not in remove_ids]
        self._rebuild_cache()
        for pid in {tn.parent_id for tn in thinking_nodes}:
            self._renumber_children(pid)

        self._root.version = "3.0"
        self._save_tree(self._root)

    # ──────────────────────────────────────────
    # 内部查找辅助
    # ──────────────────────────────────────────

    def _rebuild_cache(self) -> None:
        """从 _root.nodes 重建 _node_cache 字典，实现 O(1) 节点查找。"""
        self._node_cache = {n.id: n for n in self._root.nodes}

    # ──────────────────────────────────────────
    # 分叉功能: 脏标记（spec 3.4.5）
    # ──────────────────────────────────────────

    @property
    def dirty_conversations(self) -> set[str]:
        """
        返回所有已修改但尚未同步到分支存储的对话 ID 集合（副本）。

        所有修改 tree.json 的操作都会标脏对应对话；分支切换/删除/新建时，
        BranchService 会先同步脏对话到数据库再执行操作，随后清除标记。
        """
        return set(self._dirty_conversations)

    def mark_conversation_dirty(self, conversation_id: str | None) -> None:
        """标记对话为脏（tree.json 已修改，待同步到分支存储）。"""
        if conversation_id:
            self._dirty_conversations.add(conversation_id)

    def clear_dirty(self, conversation_id: str | None = None) -> None:
        """清除脏标记。conversation_id 为空时清空全部。"""
        if conversation_id is None:
            self._dirty_conversations.clear()
        else:
            self._dirty_conversations.discard(conversation_id)

    @staticmethod
    def _conv_of(node: AnyTreeNode) -> str | None:
        """返回节点所属的对话 ID（对话节点→自身，消息节点→parent_id，目录→None）。"""
        if isinstance(node, ConversationNode):
            return node.id
        if isinstance(node, MessageNode):
            return node.parent_id
        return None

    def _mark_dirty(self, node: AnyTreeNode) -> None:
        self.mark_conversation_dirty(self._conv_of(node))

    def _mark_subtree_dirty(self, node_id: str) -> None:
        """将节点及其子树涉及的所有对话标记为脏。"""
        for n in self._collect_subtree(node_id):
            self._mark_dirty(n)

    def _find_node(self, node_id: str) -> AnyTreeNode | None:
        """在树中按 ID 查找节点，O(1)（使用 _node_cache）。"""
        return self._node_cache.get(node_id)

    def _find_children(self, parent_id: str | None) -> list[AnyTreeNode]:
        """返回 parent_id 的直接子节点，按 sort_order 排序。"""
        return sorted(
            [n for n in self._root.nodes if n.parent_id == parent_id],
            key=lambda n: n.sort_order,
        )

    def _renumber_children(self, parent_id: str | None) -> None:
        """将 parent_id 的子节点按 sort_order 重新编号为 0..N-1。"""
        children = self._find_children(parent_id)
        for i, child in enumerate(children):
            child.sort_order = i

    def _collect_subtree(self, node_id: str) -> list[AnyTreeNode]:
        """收集节点及其所有后代（广度优先），用于递归删除。"""
        result: list[AnyTreeNode] = []
        node = self._find_node(node_id)
        if node is None:
            return result
        result.append(node)
        queue = [node_id]
        while queue:
            current_id = queue.pop(0)
            for child in self._find_children(current_id):
                result.append(child)
                queue.append(child.id)
        return result

    def _build_path_for_node(self, node: AnyTreeNode) -> str:
        """根据节点当前的 parent_id 在树中构建路径字符串。"""
        if node.parent_id is None:
            return "root"
        parts: list[str] = []
        current_id: str | None = node.parent_id
        while current_id is not None:
            parent = self._find_node(current_id)
            if parent is None:
                break
            parts.insert(0, parent.title)
            current_id = parent.parent_id
        parts.append(node.title)
        return "root/" + "/".join(parts)

    # ──────────────────────────────────────────
    # 公共查询 API
    # ──────────────────────────────────────────

    def get_tree(self) -> TreeRoot:
        """
        返回当前树根的深拷贝（只读安全，外部修改不影响内部状态）。

        Returns:
            TreeRoot — 包含 version 和 nodes 列表的完整拷贝
        """
        data = {
            "version": self._root.version,
            "nodes": [_node_to_dict(n) for n in self._root.nodes],
        }
        return TreeRoot(
            version=data["version"],
            nodes=[_dict_to_node(n) for n in data["nodes"]],
        )

    def get_node(self, node_id: str) -> AnyTreeNode | None:
        """
        按 ID 查找节点（返回副本）。

        Args:
            node_id: 节点唯一 ID

        Returns:
            AnyTreeNode | None — 不存在时返回 None
        """
        node = self._find_node(node_id)
        if node is None:
            return None
        return _dict_to_node(_node_to_dict(node))

    def get_children(self, parent_id: str | None) -> list[AnyTreeNode]:
        """
        返回某节点的直接子节点，按 sort_order 升序。

        Args:
            parent_id: 父节点 ID，None 表示查找根级节点

        Returns:
            list[AnyTreeNode] — 副本列表
        """
        children = self._find_children(parent_id)
        return [_dict_to_node(_node_to_dict(c)) for c in children]

    def get_path(self, node_id: str) -> str:
        """
        返回从根到该节点的路径字符串。

        Args:
            node_id: 节点唯一 ID

        Returns:
            str — 格式如 "root/子目录/对话"，根目录自身返回 "root"
        """
        node = self._find_node(node_id)
        if node is None:
            return ""
        return self._build_path_for_node(node)

    # ──────────────────────────────────────────
    # MessageNode 查询 & enabled 级联（Phase 5）
    # ──────────────────────────────────────────

    def get_all_message_nodes(self) -> list[MessageNode]:
        """返回树中所有 MessageNode 实例。"""
        return [n for n in self._root.nodes if isinstance(n, MessageNode)]

    def mark_incomplete(self, node_id: str, value: bool = True) -> None:
        """标记/清除消息节点的未完成状态。"""
        node = self._find_node(node_id)
        if node is None or not isinstance(node, MessageNode):
            return
        if node.incomplete != value:
            node.incomplete = value
            node.updated_at = datetime.utcnow()
            self._save_tree(self._root)
            self._mark_dirty(node)

    def cleanup_incomplete_nodes(self) -> int:
        """
        软删除所有标记为未完成的 MessageNode（移至回收站，可恢复）。

        Returns:
            被软删除的节点数量
        """
        incomplete = [
            n for n in self._root.nodes
            if isinstance(n, MessageNode) and n.incomplete
        ]
        count = len(incomplete)
        for n in incomplete:
            self.soft_delete_node(n.id, mode="recursive")
        if count:
            print(f"[STORAGE] 清理 {count} 个未完成消息节点（软删除至回收站）")
        return count

    def get_message_ids_in_tree_order(self, root_id: str | None = None) -> list[str]:
        """
        以 DFS 前序遍历收集树中的 MessageNode.message_id，按树结构排序。

        DFS 前序遍历：
        1. 访问当前节点（若是 MessageNode，收集 message_id）
        2. 按 sort_order 依次遍历子节点及子树

        Args:
            root_id: 起始节点 ID，None 表示从根级开始

        Returns:
            list[str] — 按 DFS 前序排列的 message_id 列表
        """
        result: list[str] = []

        def dfs(node_id: str) -> None:
            node = self._find_node(node_id)
            if node is None:
                return
            if isinstance(node, MessageNode):
                result.append(node.message_id)
            children = self._find_children(node_id)
            for child in children:
                dfs(child.id)

        if root_id is not None:
            dfs(root_id)
        else:
            for root_node in self._find_children(None):
                dfs(root_node.id)

        return result

    def get_all_node_ids_in_tree_order(self) -> list[str]:
        """
        以 DFS 前序遍历收集所有节点 id（目录/对话/消息），按树结构排序。

        Returns:
            list[str] — 按 DFS 前序排列的所有节点 id
        """
        result: list[str] = []

        def dfs(node_id: str) -> None:
            result.append(node_id)
            for child in self._find_children(node_id):
                dfs(child.id)

        for root_node in self._find_children(None):
            dfs(root_node.id)
        return result

    def get_descendants(self, node_id: str) -> list[AnyTreeNode]:
        """
        收集节点的所有后代（BFS，不包含节点自身）。

        Args:
            node_id: 起始节点 ID

        Returns:
            后代节点列表（BFS 顺序）
        """
        result: list[AnyTreeNode] = []
        queue = [node_id]
        while queue:
            current = queue.pop(0)
            for child in self._find_children(current):
                result.append(child)
                queue.append(child.id)
        return result

    # ──────────────────────────────────────────
    # 分叉功能: 对话链路辅助
    # ──────────────────────────────────────────

    def get_conversation_chain(self, conversation_id: str) -> list[MessageNode]:
        """
        返回对话节点下的完整消息链（MessageNode 副本，按 sort_order 正序）。

        分叉功能中，tree.json 只保存当前时刻所处的一条链路——
        切换/新建/删除分支都会整体替换这条链（replace_conversation_chain）。
        """
        children = self._find_children(conversation_id)
        chain = [c for c in children if isinstance(c, MessageNode)]
        return [_dict_to_node(_node_to_dict(c)) for c in chain]

    def replace_conversation_chain(
        self, conversation_id: str, nodes: list[MessageNode]
    ) -> None:
        """
        整体替换对话节点的消息链（分叉切换/新建/删除专用）。

        - 移除对话下所有现有 MessageNode 子节点
        - 按给定顺序插入新节点并重排 sort_order
        - 不触发脏标记（此操作本身就是分支同步时机之一）

        Args:
            conversation_id: 对话节点 ID
            nodes:          新的消息链（MessageNode 列表，顺序即最终顺序）
        """
        conv = self._find_node(conversation_id)
        if conv is None or not isinstance(conv, ConversationNode):
            raise ValueError(f"Not a conversation: {conversation_id}")

        existing = {
            n.id for n in self._find_children(conversation_id)
            if isinstance(n, MessageNode)
        }
        self._root.nodes = [n for n in self._root.nodes if n.id not in existing]

        now = datetime.utcnow()
        for i, n in enumerate(nodes):
            n.parent_id = conversation_id
            n.sort_order = i
            n.updated_at = now
        self._root.nodes.extend(nodes)

        self._rebuild_cache()
        self._save_tree(self._root)

    def is_fork_point_node(self, node: AnyTreeNode | None) -> bool:
        """节点是否为分叉点（branch_count >= 2 表示存在多个分支）。"""
        if node is None:
            return False
        return bool(getattr(node, "is_fork_point", False))

    def get_predecessor(
        self, conversation_id: str, node_id: str
    ) -> MessageNode | None:
        """返回节点在同一对话消息链中的前一个节点（无则 None）。"""
        chain = self.get_conversation_chain(conversation_id)
        for i, n in enumerate(chain):
            if n.id == node_id:
                return chain[i - 1] if i > 0 else None
        return None

    def find_fork_point_of(self, node: AnyTreeNode) -> tuple[str, str] | None:
        """
        返回节点所属的分叉点 (fork_point_id, type)。

        被修改节点无独立标记，通过"分叉点的后继节点"定位：
        - 分叉点是其前驱节点 → type="message"
        - 节点是对话中第一条消息且对话节点是分叉点 → type="conversation"
        - 否则返回 None（该节点不是被修改节点）
        """
        if not isinstance(node, MessageNode):
            return None
        predecessor = self.get_predecessor(node.parent_id, node.id)
        if predecessor is not None:
            if self.is_fork_point_node(predecessor):
                return (predecessor.id, "message")
            return None
        conv = self._find_node(node.parent_id)
        if conv is not None and self.is_fork_point_node(conv):
            return (conv.id, "conversation")
        return None

    def set_node_enabled_cascade_down(
        self, node_id: str, enabled: bool
    ) -> list[str]:
        """
        设置节点 enabled 状态并递归级联到所有后代（前序遍历）。

        Args:
            node_id: 目标节点 ID
            enabled: True 或 False（不支持 "some"）

        Returns:
            受影响的节点 ID 列表
        """
        node = self._find_node(node_id)
        if node is None:
            raise ValueError(f"Node not found: {node_id}")

        affected_ids: list[str] = []
        node.enabled = enabled
        node.updated_at = datetime.utcnow()
        affected_ids.append(node_id)

        # BFS 级联到所有后代
        queue = [node_id]
        while queue:
            current = queue.pop(0)
            for child in self._find_children(current):
                child.enabled = enabled
                child.updated_at = datetime.utcnow()
                affected_ids.append(child.id)
                queue.append(child.id)

        self._save_tree(self._root)
        self._mark_subtree_dirty(node_id)
        return affected_ids

    def recompute_ancestors_enabled(self, node_id: str) -> list[str]:
        """
        自底向上重新计算祖先节点的 enabled 状态。

        规则：
            - 所有子节点均为 True  → ancestor = True
            - 所有子节点均为 False → ancestor = False
            - 混合状态              → ancestor = "some"

        Args:
            node_id: 变更来源节点 ID（从此节点的父级开始向上）

        Returns:
            状态发生变化的祖先节点 ID 列表
        """
        node = self._find_node(node_id)
        if node is None:
            return []

        changed: list[str] = []
        current_id = node.parent_id

        while current_id is not None:
            ancestor = self._find_node(current_id)
            if ancestor is None:
                break
            children = self._find_children(current_id)
            if not children:
                current_id = ancestor.parent_id
                continue

            all_true = all(c.enabled is True for c in children)
            all_false = all(c.enabled is False for c in children)

            if all_true:
                new_state = True
            elif all_false:
                new_state = False
            else:
                new_state = "some"

            if ancestor.enabled != new_state:
                ancestor.enabled = new_state
                ancestor.updated_at = datetime.utcnow()
                changed.append(current_id)

            current_id = ancestor.parent_id

        if changed:
            self._save_tree(self._root)
            # 祖先 enabled 变化同样影响分支数据（enabled 会同步到 fork_nodes）
            self._mark_dirty(node)
            for cid in changed:
                cnode = self._find_node(cid)
                if cnode is not None:
                    self._mark_dirty(cnode)
        return changed

    # ──────────────────────────────────────────
    # 节点 CRUD
    # ──────────────────────────────────────────

    def create_node(self, node: AnyTreeNode) -> None:
        """
        插入新节点到树中。

        自动分配 sort_order（同级末尾），填充 created_at 和 updated_at
        为当前时间。最后持久化到 tree.json。

        Args:
            node: 要插入的节点（FolderNode 或 ConversationNode）
        """
        now = datetime.utcnow()
        node.created_at = now
        node.updated_at = now

        siblings = self._find_children(node.parent_id)
        node.sort_order = len(siblings)

        self._root.nodes.append(node)
        self._save_tree(self._root)
        self._mark_dirty(node)

    def update_node(self, node_id: str, **updates) -> None:
        """
        更新节点字段，自动刷新 updated_at。

        可更新字段：
            FolderNode: title, enabled, context_block_ids, attachment_paths
            ConversationNode: title, summary, enabled, message_count
            MessageNode: title, enabled, preview, role

        Args:
            node_id: 节点唯一 ID
            **updates: 要更新的字段键值对

        Raises:
            ValueError: 节点不存在时抛出
        """
        node = self._find_node(node_id)
        if node is None:
            raise ValueError(f"Node not found: {node_id}")

        allowed = {
            "title", "enabled", "summary", "message_count",
            "context_block_ids", "attachment_paths", "preview", "role",
            "incomplete", "thinking_message_id",
            # 分叉字段（分叉点计数/当前索引，由 BranchService 维护）
            "is_fork_point", "fork_branch_count", "fork_current_index",
        }
        for key, value in updates.items():
            if key in allowed and hasattr(node, key):
                setattr(node, key, value)

        node.updated_at = datetime.utcnow()
        self._save_tree(self._root)
        self._mark_dirty(node)

    def move_node(
        self,
        node_id: str,
        new_parent_id: str | None = None,
        position: int | None = None,
    ) -> None:
        """
        移动节点到新父级下，可选指定位置。

        移动后自动重新编号新旧父级的 sort_order（0..N-1）。

        Args:
            node_id:       要移动的节点 ID
            new_parent_id: 新父节点 ID，None 或空串表示移到根级
            position:      插入位置索引（0-based），None 表示末尾

        Raises:
            ValueError: 节点不存在、尝试移动根目录、或移动会产生循环时抛出
        """
        # 规范化：空串 → None（根级）
        if new_parent_id == "":
            new_parent_id = None

        node = self._find_node(node_id)
        if node is None:
            raise ValueError(f"Node not found: {node_id}")

        # ── 根目录保护 ──
        if node.parent_id is None and isinstance(node, FolderNode) and node.id == "root":
            raise ValueError("Cannot move the root folder")

        # ── 类型验证：目标父节点必须能接受此类型的子节点 ──
        # None / "" → 根级，接受任何类型
        if new_parent_id:
            new_parent = self._find_node(new_parent_id)
            if new_parent is not None:
                if isinstance(new_parent, ConversationNode) and not isinstance(node, MessageNode):
                    raise ValueError(
                        f"Cannot move a {type(node).__name__} under a ConversationNode: "
                        f"only MessageNode allowed"
                    )
                if isinstance(new_parent, MessageNode):
                    raise ValueError(
                        f"Cannot move a node under a MessageNode: messages are leaves"
                    )

        # 循环检测：新父级不能是 node_id 自身或其子孙
        if new_parent_id is not None:
            if new_parent_id == node_id:
                raise ValueError(f"Cannot move a node into itself: {node_id}")
            # 从新父级向上遍历，若遇到 node_id 则说明在尝试将节点移入其子孙
            ancestor_id: str | None = new_parent_id
            while ancestor_id is not None:
                ancestor = self._find_node(ancestor_id)
                if ancestor is None:
                    break
                if ancestor.id == node_id:
                    raise ValueError(
                        f"Cannot move node into its own descendant: "
                        f"{node_id} -> {new_parent_id}"
                    )
                ancestor_id = ancestor.parent_id

        old_parent_id = node.parent_id
        node.parent_id = new_parent_id
        node.updated_at = datetime.utcnow()

        # 重新排列新父级下的子节点顺序
        new_siblings = self._find_children(new_parent_id)
        if position is not None:
            new_siblings.remove(node)
            new_siblings.insert(min(position, len(new_siblings)), node)
        for i, n in enumerate(new_siblings):
            n.sort_order = i

        # 重新排列旧父级下的子节点顺序
        if old_parent_id != new_parent_id:
            old_siblings = self._find_children(old_parent_id)
            for i, n in enumerate(old_siblings):
                n.sort_order = i

        self._save_tree(self._root)
        # 拖拽重排序会影响分支数据（sort_order 会同步到 fork_nodes / branch_nodes.position）。
        # 源对话与目标对话都需标记（被移节点的子树涉及哪些对话也一并标记）。
        self._mark_subtree_dirty(node_id)
        self._mark_dirty(node)
        self.mark_conversation_dirty(old_parent_id)
        self.mark_conversation_dirty(new_parent_id)

    # ──────────────────────────────────────────
    # 删除与软删除
    # ──────────────────────────────────────────

    def delete_node(
        self,
        node_id: str,
        mode: Literal["recursive", "raise"] = "recursive",
    ) -> tuple[list[AnyTreeNode], list[TrashEntry]]:
        """
        从树中删除节点（不自动保存到回收站）。

        Args:
            node_id: 要删除的节点 ID
            mode:
                "recursive" — 删除整个子树，所有节点生成 TrashEntry
                "raise"     — 仅删除该节点，其子节点提升到父级

        Returns:
            (被删除或被提升的节点列表, 进入回收站的条目列表)
            调用者可根据需要将 TrashEntry 存入 recycle_bin.json。

        Raises:
            ValueError: 节点不存在或尝试删除根目录时抛出
        """
        node = self._find_node(node_id)
        if node is None:
            raise ValueError(f"Node not found: {node_id}")
        if node.parent_id is None and node.id == "root":
            raise ValueError("Cannot delete the root folder")

        old_parent_id = node.parent_id
        removed_or_promoted: list[AnyTreeNode] = []
        trash_entries: list[TrashEntry] = []
        now = datetime.utcnow()

        if mode == "recursive":
            # 收集并移除整个子树
            removed_or_promoted = self._collect_subtree(node_id)
            removed_ids = {n.id for n in removed_or_promoted}
            self._root.nodes = [
                n for n in self._root.nodes if n.id not in removed_ids
            ]

            # 子树中所有节点进入回收站
            for n in removed_or_promoted:
                path = self._build_path_for_node(n)
                trash_entries.append(TrashEntry(
                    id=str(uuid.uuid4()),
                    json_path=path,
                    node_data=_node_to_dict(n),
                    deleted_at=now,
                ))

        elif mode == "raise":
            # 查找子节点
            children = self._find_children(node_id)

            # 子节点提升到被删节点的父级
            existing_siblings = self._find_children(old_parent_id)
            start_order = len(existing_siblings)
            for i, child in enumerate(children):
                child.parent_id = old_parent_id
                child.sort_order = start_order + i

            # 移除被删节点
            self._root.nodes = [n for n in self._root.nodes if n.id != node_id]

            removed_or_promoted = [node] + children

            # 仅被删除节点进入回收站
            path = self._build_path_for_node(node)
            trash_entries.append(TrashEntry(
                id=str(uuid.uuid4()),
                json_path=path,
                node_data=_node_to_dict(node),
                deleted_at=now,
            ))

        # 重新编号
        self._renumber_children(old_parent_id)

        self._save_tree(self._root)
        # 节点已从树中移除，需基于被删节点集合标记脏（_collect_subtree 已查不到）
        for n in removed_or_promoted:
            self._mark_dirty(n)
        return (removed_or_promoted, trash_entries)

    def soft_delete_node(
        self,
        node_id: str,
        mode: Literal["recursive", "raise"] = "recursive",
    ) -> None:
        """
        软删除：从树中移除节点并存入回收站。

        等价于 delete_node() + 将 TrashEntry 列表持久化到 recycle_bin.json。

        Args:
            node_id: 要删除的节点 ID
            mode:    删除模式（同 delete_node）

        Raises:
            ValueError: 节点不存在或尝试删除根目录时抛出
        """
        with self._lock:
            _removed, trash_entries = self.delete_node(node_id, mode)
            if trash_entries:
                all_trash = self._load_trash()
                all_trash.extend(trash_entries)
                self._save_trash(all_trash)

    # ──────────────────────────────────────────
    # 回收站管理
    # ──────────────────────────────────────────

    def restore_from_trash(
        self,
        entry_id: str,
        new_parent_id: str | None = None,
        new_position: int | None = None,
    ) -> None:
        """
        从回收站恢复节点到树中。

        恢复后节点将从回收站移除。可指定新的父级和位置，
        不指定则尝试使用原始 parent_id。

        Args:
            entry_id:      回收站条目 ID
            new_parent_id: 新父节点 ID，None 则使用原始 parent_id
            new_position:  插入位置，None 则追加到末尾

        Raises:
            ValueError: 条目不存在时抛出
        """
        all_trash = self._load_trash()
        entry: TrashEntry | None = None
        for e in all_trash:
            if e.id == entry_id:
                entry = e
                break

        if entry is None:
            raise ValueError(f"Trash entry not found: {entry_id}")

        # 从数据重建节点
        node = _dict_to_node(entry.node_data)

        # 决定父级
        if new_parent_id is not None:
            node.parent_id = new_parent_id

        # 检查目标父级是否存在
        if node.parent_id is not None:
            parent = self._find_node(node.parent_id)
            if parent is None:
                # 父级不存在（可能也被删除了），降级到根目录
                print(
                    f"[STORAGE] 恢复节点时父级缺失 {node.parent_id}，"
                    f"节点 {node.title} 将移至根目录"
                )
                node.parent_id = None

        # 插入到树中
        siblings = self._find_children(node.parent_id)
        if new_position is not None:
            siblings.insert(min(new_position, len(siblings)), node)
        else:
            siblings.append(node)
        for i, n in enumerate(siblings):
            n.sort_order = i

        node.updated_at = datetime.utcnow()
        self._root.nodes.append(node)

        # 从回收站移除
        all_trash = [e for e in all_trash if e.id != entry_id]
        self._save_trash(all_trash)
        self._save_tree(self._root)
        self._mark_dirty(node)

    def permanently_delete_from_trash(self, entry_id: str) -> None:
        """
        从回收站彻底移除条目（不恢复节点）。

        Args:
            entry_id: 回收站条目 ID
        """
        all_trash = self._load_trash()
        all_trash = [e for e in all_trash if e.id != entry_id]
        self._save_trash(all_trash)

    def list_trash(self) -> list[TrashEntry]:
        """返回回收站中所有条目（副本）。"""
        return self._load_trash()

    def clear_trash(self) -> int:
        """
        清空回收站（永久删除所有条目）。

        Returns:
            int — 被清除的条目数量
        """
        with self._lock:
            all_trash = self._load_trash()
            count = len(all_trash)
            self._save_trash([])
        print(f"[STORAGE] 清空回收站: {count} 个条目")
        return count


# ──────────────────────────────────────────────
# 序列化 / 反序列化（模块私有）
# ──────────────────────────────────────────────

def _node_to_dict(node: AnyTreeNode) -> dict:
    """将树节点序列化为 dict（datetime → ISO 格式字符串，NodeType → value）。"""
    d: dict = {
        "id": node.id,
        "parent_id": node.parent_id,
        "sort_order": node.sort_order,
        "enabled": node.enabled,
        "title": node.title,
        "created_at": node.created_at.isoformat(),
        "updated_at": node.updated_at.isoformat(),
        "node_type": node.node_type.value if hasattr(node, "node_type") else NodeType.CONVERSATION.value,
    }
    if isinstance(node, FolderNode):
        d["context_block_ids"] = node.context_block_ids
        d["attachment_paths"] = node.attachment_paths
    elif isinstance(node, MessageNode):
        d["message_id"] = node.message_id
        d["role"] = node.role
        d["preview"] = node.preview
        d["incomplete"] = node.incomplete
        d["thinking_message_id"] = node.thinking_message_id
        d["is_fork_point"] = node.is_fork_point
        d["fork_branch_count"] = node.fork_branch_count
        d["fork_current_index"] = node.fork_current_index
    else:
        d["summary"] = node.summary
        d["message_count"] = node.message_count
        d["context_block_ids"] = node.context_block_ids
        d["attachment_paths"] = node.attachment_paths
        d["is_fork_point"] = node.is_fork_point
        d["fork_branch_count"] = node.fork_branch_count
        d["fork_current_index"] = node.fork_current_index
    return d


def _dict_to_node(d: dict) -> AnyTreeNode:
    """从 dict 反序列化为具体树节点（根据 node_type 字段分发）。"""
    node_type = d.get("node_type", "conversation")
    common = {
        "id": d["id"],
        "parent_id": d.get("parent_id"),
        "sort_order": d.get("sort_order", 0),
        "enabled": d.get("enabled", True),
        "title": d.get("title", ""),
        "created_at": (
            datetime.fromisoformat(d["created_at"])
            if d.get("created_at") else datetime.utcnow()
        ),
        "updated_at": (
            datetime.fromisoformat(d["updated_at"])
            if d.get("updated_at") else datetime.utcnow()
        ),
    }
    if node_type == "folder":
        return FolderNode(
            **common,
            context_block_ids=d.get("context_block_ids", []),
            attachment_paths=d.get("attachment_paths", []),
        )
    elif node_type == "message":
        return MessageNode(
            **common,
            message_id=d.get("message_id", d["id"]),
            role=d.get("role", ""),
            preview=d.get("preview", ""),
            incomplete=d.get("incomplete", False),
            thinking_message_id=d.get("thinking_message_id"),
            is_fork_point=d.get("is_fork_point", False),
            fork_branch_count=d.get("fork_branch_count", 0),
            fork_current_index=d.get("fork_current_index", 0),
        )
    else:
        return ConversationNode(
            **common,
            summary=d.get("summary", ""),
            message_count=d.get("message_count", 0),
            context_block_ids=d.get("context_block_ids", []),
            attachment_paths=d.get("attachment_paths", []),
            is_fork_point=d.get("is_fork_point", False),
            fork_branch_count=d.get("fork_branch_count", 0),
            fork_current_index=d.get("fork_current_index", 0),
        )
