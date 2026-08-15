# Layer: tests
# File: tests/test_branch_service.py
# Responsibility: 分叉基础流程（新建 / 切换 / 删除分支）的单元测试。

from __future__ import annotations

import uuid

from tests.base import TempConfigTestCase
from app.core.branch_service import BranchService
from app.storage.branch_store import BranchStore
from app.storage.message_repo import MessageRepo
from app.storage.models import ConversationNode, MessageNode, Role
from app.storage.tree_store import TreeStore


def _conv(title: str = "c") -> ConversationNode:
    return ConversationNode(id=str(uuid.uuid4()), parent_id="root", title=title)


def _msg(parent_id: str, role: str, text: str = "hello") -> MessageNode:
    mid = str(uuid.uuid4())
    return MessageNode(
        id=mid, parent_id=parent_id, message_id=mid, role=role,
        preview=text[:60], title=f"{role}: {text[:30]}",
    )


class TestBranchService(TempConfigTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tree = TreeStore()
        self.branch = BranchStore()
        self.repo = MessageRepo()
        self.svc = BranchService(
            tree_store=self.tree,
            branch_store=self.branch,
            message_repo=self.repo,
        )

    def _conv_with_chain(self):
        conv = _conv()
        self.tree.create_node(conv)
        m1 = _msg(conv.id, Role.USER.value)
        m2 = _msg(conv.id, Role.ASSISTANT.value)
        self.tree.replace_conversation_chain(conv.id, [m1, m2])
        return conv, m1, m2

    def test_create_branch(self) -> None:
        conv, m1, m2 = self._conv_with_chain()
        m2_new = _msg(conv.id, Role.ASSISTANT.value, text="new answer")
        self.svc.create_branch(conv.id, m1.id, [m1, m2_new])

        chain = self.tree.get_conversation_chain(conv.id)
        self.assertEqual([n.id for n in chain], [m1.id, m2_new.id])

        fp = self.tree.get_node(m1.id)
        self.assertTrue(fp.is_fork_point)
        self.assertEqual(fp.fork_branch_count, 2)
        self.assertEqual(fp.fork_current_index, 1)

    def test_switch_branch(self) -> None:
        conv, m1, m2 = self._conv_with_chain()
        m2_new = _msg(conv.id, Role.ASSISTANT.value, text="new answer")
        self.svc.create_branch(conv.id, m1.id, [m1, m2_new])

        chain = self.svc.switch_branch(conv.id, m1.id, 0)
        self.assertEqual([n.id for n in chain], [m1.id, m2.id])
        fp = self.tree.get_node(m1.id)
        self.assertEqual(fp.fork_current_index, 0)

    def test_delete_branch_degrades(self) -> None:
        conv, m1, m2 = self._conv_with_chain()
        m2_new = _msg(conv.id, Role.ASSISTANT.value, text="new answer")
        self.svc.create_branch(conv.id, m1.id, [m1, m2_new])

        self.svc.delete_branch(conv.id, m1.id, 1)
        fp = self.tree.get_node(m1.id)
        self.assertFalse(fp.is_fork_point)
        self.assertEqual(fp.fork_branch_count, 0)
        # 唯一剩余分支（旧链）成为当前链
        chain = self.tree.get_conversation_chain(conv.id)
        self.assertEqual([n.id for n in chain], [m1.id, m2.id])


if __name__ == "__main__":
    import unittest

    unittest.main()
