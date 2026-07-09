# File: tests/test_tree_store.py
# Phase 5 — TreeStore 单元测试
# 运行方式: python -m pytest tests/test_tree_store.py -v
#         或 python tests/test_tree_store.py

import os
import sys
import tempfile
import unittest
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.storage.models import (
    ConversationNode,
    FolderNode,
    TreeNode,
    TreeRoot,
)
from app.storage.tree_store import TreeStore


class TestTreeStoreCRUD(unittest.TestCase):
    """测试 TreeStore 的基础 CRUD 操作。"""

    def setUp(self) -> None:
        """每个测试前创建临时目录和 TreeStore 实例。"""
        self._tmpdir = tempfile.TemporaryDirectory()
        self._base = Path(self._tmpdir.name)
        self.store = TreeStore(base_path=self._base)

    def tearDown(self) -> None:
        """清理临时目录。"""
        self._tmpdir.cleanup()

    # ── 默认树 ──────────────────────────────

    def test_default_tree_has_root(self) -> None:
        """新建 TreeStore 应包含一个"未分类"根目录。"""
        tree = self.store.get_tree()
        self.assertEqual(len(tree.nodes), 1)
        root = tree.nodes[0]
        self.assertEqual(root.id, "root")
        self.assertEqual(root.title, "未分类")
        self.assertIsInstance(root, FolderNode)

    # ── create_node + get_node ──────────────

    def test_create_node_and_get_node(self) -> None:
        """创建节点后应能通过 get_node 查找到。"""
        folder = FolderNode(
            id="folder-1",
            parent_id="root",
            title="测试目录",
        )
        self.store.create_node(folder)

        found = self.store.get_node("folder-1")
        self.assertIsNotNone(found)
        self.assertEqual(found.title, "测试目录")
        self.assertIsInstance(found, FolderNode)

    def test_create_conversation_node(self) -> None:
        """创建 ConversationNode 应正确存储。"""
        conv = ConversationNode(
            id="conv-1",
            parent_id="root",
            title="测试对话",
            summary="摘要",
            message_count=5,
        )
        self.store.create_node(conv)

        found = self.store.get_node("conv-1")
        self.assertIsNotNone(found)
        self.assertEqual(found.title, "测试对话")
        self.assertEqual(found.summary, "摘要")
        self.assertEqual(found.message_count, 5)
        self.assertIsInstance(found, ConversationNode)

    def test_get_nonexistent_node(self) -> None:
        """查询不存在的节点应返回 None。"""
        self.assertIsNone(self.store.get_node("nonexistent"))

    def test_create_node_auto_sets_sort_order(self) -> None:
        """创建节点应自动分配 sort_order。"""
        f1 = FolderNode(id="f1", parent_id="root", title="A")
        f2 = FolderNode(id="f2", parent_id="root", title="B")
        self.store.create_node(f1)
        self.store.create_node(f2)

        found1 = self.store.get_node("f1")
        found2 = self.store.get_node("f2")
        self.assertEqual(found1.sort_order, 0)
        self.assertEqual(found2.sort_order, 1)

    # ── get_children ───────────────────────

    def test_get_children(self) -> None:
        """get_children 应返回按 sort_order 排序的子节点列表。"""
        # create_node 自动分配 sort_order（按创建顺序：0, 1, ...）
        f1 = FolderNode(id="f1", parent_id="root", title="第一个")
        f2 = ConversationNode(id="c1", parent_id="root", title="第二个")
        self.store.create_node(f1)
        self.store.create_node(f2)

        children = self.store.get_children("root")
        self.assertEqual(len(children), 2)
        # f1 先创建 → sort_order=0, c1 后创建 → sort_order=1
        self.assertEqual(children[0].id, "f1")
        self.assertEqual(children[1].id, "c1")

    def test_get_children_sorted(self) -> None:
        """子节点应按 sort_order 排序。"""
        # 清理默认树，从空开始
        self.store._root = TreeRoot(version="1.0", nodes=[])
        self.store._rebuild_cache()

        f1 = FolderNode(id="f1", parent_id=None, title="B")
        f2 = ConversationNode(id="c1", parent_id=None, title="A")
        self.store.create_node(f1)
        self.store.create_node(f2)

        children = self.store.get_children(None)
        self.assertEqual(len(children), 2)
        self.assertEqual(children[0].id, "f1")
        self.assertEqual(children[1].id, "c1")

    # ── update_node ────────────────────────

    def test_update_node_title(self) -> None:
        """update_node 应更新标题。"""
        conv = ConversationNode(id="c1", parent_id="root", title="旧标题")
        self.store.create_node(conv)
        self.store.update_node("c1", title="新标题")

        updated = self.store.get_node("c1")
        self.assertEqual(updated.title, "新标题")

    def test_update_node_enabled(self) -> None:
        """update_node 应更新 enabled 状态。"""
        conv = ConversationNode(id="c1", parent_id="root", enabled=True)
        self.store.create_node(conv)
        self.store.update_node("c1", enabled=False)

        updated = self.store.get_node("c1")
        self.assertEqual(updated.enabled, False)

    def test_update_nonexistent_node(self) -> None:
        """更新不存在的节点应抛出 ValueError。"""
        with self.assertRaises(ValueError):
            self.store.update_node("nonexistent", title="test")

    # ── move_node ─────────────────────────

    def test_move_node(self) -> None:
        """move_node 应将节点移到新父级下。"""
        f1 = FolderNode(id="f1", parent_id="root", title="目标目录")
        conv = ConversationNode(id="c1", parent_id="root", title="移动我")
        self.store.create_node(f1)
        self.store.create_node(conv)

        self.store.move_node("c1", new_parent_id="f1")

        moved = self.store.get_node("c1")
        self.assertEqual(moved.parent_id, "f1")

    def test_move_node_renumbers_siblings(self) -> None:
        """移动后应重新编号新旧父级的 sort_order。"""
        # 清理
        self.store._root = TreeRoot(version="1.0", nodes=[])
        self.store._rebuild_cache()

        f1 = FolderNode(id="f1", parent_id=None, title="目录")
        c1 = ConversationNode(id="c1", parent_id=None, title="对话1")
        c2 = ConversationNode(id="c2", parent_id=None, title="对话2")
        self.store.create_node(f1)
        self.store.create_node(c1)
        self.store.create_node(c2)

        # 移动 c2 到 f1 下
        self.store.move_node("c2", new_parent_id="f1")

        # 旧父级（None）下的 c1 应重新编号为 sort_order=1
        c1_node = self.store.get_node("c1")
        self.assertEqual(c1_node.sort_order, 1)

        # 新父级（f1）下的 c2 应为 sort_order=0
        c2_node = self.store.get_node("c2")
        self.assertEqual(c2_node.sort_order, 0)

    # ── delete_node (recursive) ───────────

    def test_delete_node_recursive(self) -> None:
        """递归删除应移除节点及其子树。"""
        f1 = FolderNode(id="f1", parent_id="root", title="目录")
        c1 = ConversationNode(id="c1", parent_id="f1", title="子对话")
        self.store.create_node(f1)
        self.store.create_node(c1)

        removed, trash = self.store.delete_node("f1", mode="recursive")

        self.assertIsNone(self.store.get_node("f1"))
        self.assertIsNone(self.store.get_node("c1"))
        self.assertEqual(len(removed), 2)
        self.assertEqual(len(trash), 2)

    def test_delete_node_raise(self) -> None:
        """提升模式删除应移除节点但保留子节点。"""
        f1 = FolderNode(id="f1", parent_id="root", title="目录")
        c1 = ConversationNode(id="c1", parent_id="f1", title="子对话")
        self.store.create_node(f1)
        self.store.create_node(c1)

        removed, trash = self.store.delete_node("f1", mode="raise")

        self.assertIsNone(self.store.get_node("f1"))
        # 子节点应提升到父级
        child = self.store.get_node("c1")
        self.assertIsNotNone(child)
        self.assertEqual(child.parent_id, "root")
        self.assertEqual(len(removed), 2)
        self.assertEqual(len(trash), 1)  # 只有 f1 进回收站

    def test_cannot_delete_root(self) -> None:
        """不能删除根目录。"""
        with self.assertRaises(ValueError):
            self.store.delete_node("root", mode="recursive")

    # ── soft_delete + trash ───────────────

    def test_soft_delete_and_list_trash(self) -> None:
        """软删除后节点应出现在回收站中。"""
        conv = ConversationNode(id="c1", parent_id="root", title="测试")
        self.store.create_node(conv)

        self.store.soft_delete_node("c1")
        self.assertIsNone(self.store.get_node("c1"))

        trash = self.store.list_trash()
        self.assertEqual(len(trash), 1)
        self.assertEqual(trash[0].node_data["title"], "测试")

    def test_restore_from_trash(self) -> None:
        """从回收站恢复应重建节点。"""
        conv = ConversationNode(id="c1", parent_id="root", title="恢复测试")
        self.store.create_node(conv)
        self.store.soft_delete_node("c1")

        trash = self.store.list_trash()
        self.store.restore_from_trash(trash[0].id)

        restored = self.store.get_node("c1")
        self.assertIsNotNone(restored)
        self.assertEqual(restored.title, "恢复测试")
        self.assertEqual(len(self.store.list_trash()), 0)

    def test_permanently_delete_from_trash(self) -> None:
        """彻底删除应从回收站移除条目。"""
        conv = ConversationNode(id="c1", parent_id="root", title="永久删除")
        self.store.create_node(conv)
        self.store.soft_delete_node("c1")

        trash = self.store.list_trash()
        self.store.permanently_delete_from_trash(trash[0].id)
        self.assertEqual(len(self.store.list_trash()), 0)

    # ── get_path ──────────────────────────

    def test_get_path(self) -> None:
        """get_path 应返回正确的层级路径。"""
        f1 = FolderNode(id="f1", parent_id="root", title="子目录")
        conv = ConversationNode(id="c1", parent_id="f1", title="我的对话")
        self.store.create_node(f1)
        self.store.create_node(conv)

        path = self.store.get_path("c1")
        self.assertIn("子目录", path)
        self.assertIn("我的对话", path)

    def test_get_path_root(self) -> None:
        """根目录路径应为 'root'。"""
        path = self.store.get_path("root")
        self.assertEqual(path, "root")

    def test_get_path_nonexistent(self) -> None:
        """查询不存在节点的路径应返回空字符串。"""
        self.assertEqual(self.store.get_path("nonexistent"), "")

    # ── 节点缓存（Phase 5）─────────────────

    def test_node_cache_consistency(self) -> None:
        """写入操作后缓存应与实际数据一致。"""
        conv = ConversationNode(id="c1", parent_id="root", title="缓存测试")
        self.store.create_node(conv)

        # 缓存中的引用应与 _find_node 结果相同
        cached = self.store._node_cache.get("c1")
        found = self.store._find_node("c1")
        self.assertIs(cached, found)  # 应是同一个对象引用

    def test_cache_after_delete(self) -> None:
        """删除节点后缓存应同步清除。"""
        conv = ConversationNode(id="c1", parent_id="root", title="删除缓存")
        self.store.create_node(conv)
        self.store.delete_node("c1", mode="recursive")

        self.assertIsNone(self.store._node_cache.get("c1"))

    # ── 边界情况 ──────────────────────────

    def test_create_duplicate_id(self) -> None:
        """创建相同 ID 的节点应覆盖旧节点（当前行为）。"""
        c1 = ConversationNode(id="dup", parent_id="root", title="第一次")
        c2 = ConversationNode(id="dup", parent_id="root", title="第二次")
        self.store.create_node(c1)
        self.store.create_node(c2)

        found = self.store.get_node("dup")
        self.assertEqual(found.title, "第二次")

    def test_move_to_same_parent(self) -> None:
        """移到相同父级不应出错。"""
        conv = ConversationNode(id="c1", parent_id="root", title="不变")
        self.store.create_node(conv)
        self.store.move_node("c1", new_parent_id="root")
        self.assertEqual(self.store.get_node("c1").parent_id, "root")


if __name__ == "__main__":
    unittest.main(verbosity=2)
