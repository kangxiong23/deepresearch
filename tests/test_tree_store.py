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
    MessageNode,
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


class TestThinkingBinding(unittest.TestCase):
    """Phase 6: thinking 绑定迁移与 thinking_message_id 序列化。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._base = Path(self._tmpdir.name)
        self.store = TreeStore(base_path=self._base)
        # 构造旧树（v2.0）：root → conv → user / thinking / assistant
        conv = ConversationNode(id="conv", parent_id="root", title="对话")
        self.store.create_node(conv)
        u = MessageNode(
            id="u1", parent_id="conv", message_id="u1", role="user",
            preview="问", title="User: 问",
        )
        t = MessageNode(
            id="t1", parent_id="conv", message_id="t1", role="thinking",
            preview="想", title="Think: 想",
        )
        a = MessageNode(
            id="a1", parent_id="conv", message_id="a1", role="assistant",
            preview="答", title="Asst: 答",
        )
        self.store.create_node(u)
        self.store.create_node(t)
        self.store.create_node(a)
        self.store._root.version = "2.0"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_migrate_binds_thinking_to_next_assistant(self) -> None:
        """thinking 节点应绑定到后随 assistant 并从树中移除，版本升 3.0。"""
        self.store._migrate_thinking_binding()
        asst = self.store.get_node("a1")
        self.assertIsNotNone(asst)
        self.assertEqual(asst.thinking_message_id, "t1")
        # thinking 节点不再存在于树中
        self.assertIsNone(self.store.get_node("t1"))
        self.assertEqual(self.store._root.version, "3.0")

    def test_migrate_keeps_user_and_assistant_order(self) -> None:
        """迁移后 user 与 assistant 顺序不变，且 sort_order 重编号。"""
        self.store._migrate_thinking_binding()
        children = self.store.get_children("conv")
        ids = [n.id for n in children]
        self.assertEqual(ids, ["u1", "a1"])
        self.assertEqual(children[0].sort_order, 0)
        self.assertEqual(children[1].sort_order, 1)

    def test_migrate_orphan_thinking_goes_to_trash(self) -> None:
        """后无 assistant 的孤儿 thinking 节点应软删到回收站。"""
        t2 = MessageNode(
            id="t2", parent_id="conv", message_id="t2", role="thinking",
            preview="孤儿", title="Think: 孤儿",
        )
        self.store.create_node(t2)
        self.store._root.version = "2.0"
        self.store._migrate_thinking_binding()
        # t1 绑定 a1；t2 是孤儿 → 回收站
        self.assertIsNone(self.store.get_node("t2"))
        trash = self.store.list_trash()
        self.assertEqual(len(trash), 1)
        self.assertEqual(trash[0].node_data.get("id"), "t2")

    def test_thinking_message_id_serialization_roundtrip(self) -> None:
        """thinking_message_id 应持久化到 tree.json 并能往返读取。"""
        import json
        a9 = MessageNode(
            id="a9", parent_id="conv", message_id="a9", role="assistant",
            preview="答", title="Asst: 答", thinking_message_id="t9",
        )
        self.store.create_node(a9)
        self.store._save_tree(self.store._root)

        data = json.loads(
            (self._base / "tree.json").read_text(encoding="utf-8")
        )
        node_dict = next(n for n in data["nodes"] if n["id"] == "a9")
        self.assertEqual(node_dict.get("thinking_message_id"), "t9")

        got = self.store.get_node("a9")
        self.assertEqual(got.thinking_message_id, "t9")

    def test_update_node_thinking_message_id(self) -> None:
        """update_node 应支持更新 thinking_message_id。"""
        self.store.update_node("a1", thinking_message_id="t1")
        self.assertEqual(self.store.get_node("a1").thinking_message_id, "t1")


class TestForkFieldsAndDirtyMarking(unittest.TestCase):
    """分叉字段序列化、脏标记与链路辅助方法测试。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._base = Path(self._tmpdir.name)
        self.store = TreeStore(base_path=self._base)
        # 构造: conv 下 M1(分叉点) → M2(被修改节点)
        self.store.create_node(ConversationNode(
            id="conv", parent_id="root", title="对话",
        ))
        self.store.create_node(MessageNode(
            id="m1", parent_id="conv", message_id="m1", role="user",
            title="User: 问题1", preview="问题1",
        ))
        self.store.create_node(MessageNode(
            id="m2", parent_id="conv", message_id="m2", role="assistant",
            title="Asst: 回答1", preview="回答1",
        ))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    # ── 分叉字段序列化 ────────────────────────

    def test_fork_fields_serialization_roundtrip(self) -> None:
        """分叉字段应持久化到 tree.json 并能往返读取。"""
        import json
        self.store.update_node(
            "m1", is_fork_point=True, fork_branch_count=2, fork_current_index=1,
        )
        self.store.update_node(
            "conv", is_fork_point=True, fork_branch_count=2, fork_current_index=0,
        )

        data = json.loads(
            (self._base / "tree.json").read_text(encoding="utf-8")
        )
        m1_dict = next(n for n in data["nodes"] if n["id"] == "m1")
        conv_dict = next(n for n in data["nodes"] if n["id"] == "conv")
        self.assertTrue(m1_dict["is_fork_point"])
        self.assertEqual(m1_dict["fork_branch_count"], 2)
        self.assertEqual(m1_dict["fork_current_index"], 1)
        self.assertTrue(conv_dict["is_fork_point"])

        got_m1 = self.store.get_node("m1")
        self.assertTrue(got_m1.is_fork_point)
        self.assertEqual(got_m1.fork_branch_count, 2)
        self.assertEqual(got_m1.fork_current_index, 1)

    def test_fork_fields_default_for_old_data(self) -> None:
        """旧 tree.json（无分叉字段）应回退到默认值。"""
        import json
        # 手工写一个不带分叉字段的 tree.json
        data = {
            "version": "3.0",
            "nodes": [
                {"id": "root", "parent_id": None, "sort_order": 0, "enabled": True,
                 "title": "未分类", "created_at": "2026-01-01T00:00:00",
                 "updated_at": "2026-01-01T00:00:00", "node_type": "folder",
                 "context_block_ids": [], "attachment_paths": []},
                {"id": "c", "parent_id": "root", "sort_order": 0, "enabled": True,
                 "title": "旧对话", "created_at": "2026-01-01T00:00:00",
                 "updated_at": "2026-01-01T00:00:00", "node_type": "conversation",
                 "summary": "", "message_count": 0, "context_block_ids": [],
                 "attachment_paths": []},
                {"id": "x", "parent_id": "c", "sort_order": 0, "enabled": True,
                 "title": "User: 旧消息", "created_at": "2026-01-01T00:00:00",
                 "updated_at": "2026-01-01T00:00:00", "node_type": "message",
                 "message_id": "x", "role": "user", "preview": "旧消息",
                 "incomplete": False, "thinking_message_id": None},
            ],
        }
        (self._base / "tree.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        store = TreeStore(base_path=self._base)
        self.assertFalse(store.get_node("c").is_fork_point)
        self.assertEqual(store.get_node("c").fork_branch_count, 0)
        self.assertFalse(store.get_node("x").is_fork_point)
        self.assertEqual(store.get_node("x").fork_current_index, 0)

    # ── 脏标记 ────────────────────────────────

    def test_dirty_marked_on_tree_ops(self) -> None:
        """create/update/move/delete/mark_incomplete/toggle 都应标记对话为脏。"""
        self.store.create_node(MessageNode(
            id="m3", parent_id="conv", message_id="m3", role="user",
            title="User: 新问题",
        ))
        self.assertIn("conv", self.store.dirty_conversations)

        self.store.clear_dirty()
        self.store.update_node("m3", title="User: 改名")
        self.assertIn("conv", self.store.dirty_conversations)

        self.store.clear_dirty()
        self.store.mark_incomplete("m3")
        self.assertIn("conv", self.store.dirty_conversations)

        self.store.clear_dirty()
        self.store.set_node_enabled_cascade_down("m3", False)
        self.assertIn("conv", self.store.dirty_conversations)

        self.store.clear_dirty()
        self.store.soft_delete_node("m3")
        self.assertIn("conv", self.store.dirty_conversations)

    def test_dirty_marked_on_move_across_conversations(self) -> None:
        """跨对话拖拽应同时标记两个对话为脏。"""
        self.store.create_node(ConversationNode(
            id="conv2", parent_id="root", title="对话2",
        ))
        self.store.create_node(MessageNode(
            id="m4", parent_id="conv2", message_id="m4", role="user",
            title="User: 对话2消息",
        ))
        self.store.clear_dirty()
        self.store.move_node("m4", new_parent_id="conv", position=0)
        self.assertIn("conv", self.store.dirty_conversations)
        self.assertIn("conv2", self.store.dirty_conversations)

    def test_dirty_marked_on_conversation_creation(self) -> None:
        """新建对话节点应标记自身为脏。"""
        self.store.create_node(ConversationNode(
            id="conv3", parent_id="root", title="对话3",
        ))
        self.assertIn("conv3", self.store.dirty_conversations)

    def test_clear_dirty(self) -> None:
        """clear_dirty 应支持清除指定或全部对话。"""
        self.store.update_node("m1", title="改标题")
        self.assertIn("conv", self.store.dirty_conversations)
        self.store.clear_dirty("conv")
        self.assertNotIn("conv", self.store.dirty_conversations)
        self.store.update_node("m2", title="再改")
        self.store.clear_dirty()
        self.assertEqual(self.store.dirty_conversations, set())

    # ── 链路辅助 ──────────────────────────────

    def test_get_conversation_chain(self) -> None:
        """get_conversation_chain 应返回按 sort_order 排列的消息链副本。"""
        chain = self.store.get_conversation_chain("conv")
        self.assertEqual([n.id for n in chain], ["m1", "m2"])
        # 修改返回副本不影响内部状态
        chain[0].title = "hack"
        self.assertEqual(self.store.get_node("m1").title, "User: 问题1")

    def test_replace_conversation_chain(self) -> None:
        """replace_conversation_chain 应整体替换对话的消息链并重排 sort_order。"""
        new_m2 = MessageNode(
            id="m2b", parent_id="conv", message_id="m2b", role="assistant",
            title="Asst: 回答1改",
        )
        self.store.clear_dirty()  # 排除 setUp 创建节点的脏标记
        self.store.replace_conversation_chain("conv", [
            self.store.get_node("m1"),
            new_m2,
        ])
        chain = self.store.get_conversation_chain("conv")
        self.assertEqual([n.id for n in chain], ["m1", "m2b"])
        self.assertEqual(chain[0].sort_order, 0)
        self.assertEqual(chain[1].sort_order, 1)
        # 旧链节点已从树中移除
        self.assertIsNone(self.store.get_node("m2"))
        # 该操作不产生脏标记（本身就是同步时机）
        self.assertNotIn("conv", self.store.dirty_conversations)

    def test_replace_conversation_chain_rejects_non_conversation(self) -> None:
        """对非对话节点调用 replace_conversation_chain 应抛错。"""
        with self.assertRaises(ValueError):
            self.store.replace_conversation_chain("m1", [])

    def test_get_predecessor(self) -> None:
        """get_predecessor 应返回链中前一个节点，首节点返回 None。"""
        self.assertEqual(self.store.get_predecessor("conv", "m2").id, "m1")
        self.assertIsNone(self.store.get_predecessor("conv", "m1"))

    def test_find_fork_point_of(self) -> None:
        """find_fork_point_of 应识别被修改节点（分叉点后继/对话根分叉）。"""
        # 无分叉点 → 不是被修改节点
        self.assertIsNone(self.store.find_fork_point_of(self.store.get_node("m2")))

        # m1 成为分叉点 → m2 是被修改节点
        self.store.update_node("m1", is_fork_point=True, fork_branch_count=2)
        fp = self.store.find_fork_point_of(self.store.get_node("m2"))
        self.assertEqual(fp, ("m1", "message"))

        # 对话节点成为分叉点 → 第一条消息是被修改节点
        self.store.update_node("conv", is_fork_point=True, fork_branch_count=2)
        self.store.update_node("m1", is_fork_point=False, fork_branch_count=0)
        fp2 = self.store.find_fork_point_of(self.store.get_node("m1"))
        self.assertEqual(fp2, ("conv", "conversation"))

        # 对话节点自身不是被修改节点（仅消息节点可被修改）
        self.assertIsNone(self.store.find_fork_point_of(self.store.get_node("conv")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
