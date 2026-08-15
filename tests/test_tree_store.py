# Layer: tests
# File: tests/test_tree_store.py
# Responsibility: TreeStore 核心 CRUD、enabled 级联、回收站、消息链 的单元测试。

from __future__ import annotations

import uuid

from tests.base import TempConfigTestCase
from app.storage.models import (
    ConversationNode,
    FolderNode,
    MessageNode,
    Role,
)
from app.storage.tree_store import TreeStore


def _conv(parent_id: str | None = "root", title: str = "对话") -> ConversationNode:
    return ConversationNode(id=str(uuid.uuid4()), parent_id=parent_id, title=title)


def _msg(parent_id: str, role: str = Role.USER.value, text: str = "hello") -> MessageNode:
    mid = str(uuid.uuid4())
    return MessageNode(
        id=mid, parent_id=parent_id, message_id=mid, role=role, preview=text[:60],
    )


class TestTreeStore(TempConfigTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = TreeStore()

    def test_default_tree_has_root(self) -> None:
        tree = self.store.get_tree()
        self.assertTrue(any(n.id == "root" for n in tree.nodes))
        root = self.store.get_node("root")
        self.assertIsInstance(root, FolderNode)
        self.assertEqual(root.title, "未分类")

    def test_create_and_get_node(self) -> None:
        conv = _conv()
        self.store.create_node(conv)
        fetched = self.store.get_node(conv.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.title, "对话")
        self.assertEqual(fetched.parent_id, "root")

    def test_update_node(self) -> None:
        conv = _conv()
        self.store.create_node(conv)
        self.store.update_node(conv.id, title="改名", summary="摘要")
        fetched = self.store.get_node(conv.id)
        self.assertEqual(fetched.title, "改名")
        self.assertEqual(fetched.summary, "摘要")

    def test_move_node_cycle_detection(self) -> None:
        folder = FolderNode(id=str(uuid.uuid4()), parent_id="root", title="目录")
        self.store.create_node(folder)
        # 根目录不可移动
        with self.assertRaises(ValueError):
            self.store.move_node("root", folder.id)
        # 不能移动到自身
        with self.assertRaises(ValueError):
            self.store.move_node(folder.id, folder.id)

    def test_soft_delete_restore_trash(self) -> None:
        conv = _conv()
        self.store.create_node(conv)
        self.store.soft_delete_node(conv.id, mode="recursive")
        self.assertIsNone(self.store.get_node(conv.id))
        trash = self.store.list_trash()
        self.assertEqual(len(trash), 1)
        # 恢复
        self.store.restore_from_trash(trash[0].id)
        self.assertIsNotNone(self.store.get_node(conv.id))
        self.assertEqual(self.store.list_trash(), [])

    def test_conversation_chain_replace(self) -> None:
        conv = _conv()
        self.store.create_node(conv)
        m1 = _msg(conv.id, Role.USER.value)
        m2 = _msg(conv.id, Role.ASSISTANT.value)
        self.store.replace_conversation_chain(conv.id, [m1, m2])
        chain = self.store.get_conversation_chain(conv.id)
        self.assertEqual([n.id for n in chain], [m1.id, m2.id])

    def test_enabled_cascade_and_recompute(self) -> None:
        folder_a = FolderNode(id=str(uuid.uuid4()), parent_id="root", title="A")
        folder_b = FolderNode(id=str(uuid.uuid4()), parent_id="root", title="B")
        self.store.create_node(folder_a)
        self.store.create_node(folder_b)
        conv = _conv(parent_id=folder_a.id)
        self.store.create_node(conv)
        affected = self.store.set_node_enabled_cascade_down(folder_a.id, False)
        self.assertIn(conv.id, affected)
        self.assertFalse(self.store.get_node(conv.id).enabled)
        changed = self.store.recompute_ancestors_enabled(conv.id)
        # root 下两个子目录一个禁用一个启用 → 混合态 "some"
        self.assertIn("root", changed)
        self.assertEqual(self.store.get_node("root").enabled, "some")

    def test_incomplete_cleanup(self) -> None:
        conv = _conv()
        self.store.create_node(conv)
        m = _msg(conv.id, Role.USER.value)
        self.store.replace_conversation_chain(conv.id, [m])
        self.store.mark_incomplete(m.id, True)
        self.assertTrue(self.store.get_node(m.id).incomplete)
        removed = self.store.cleanup_incomplete_nodes()
        self.assertEqual(removed, 1)
        self.assertIsNone(self.store.get_node(m.id))


if __name__ == "__main__":
    import unittest

    unittest.main()
