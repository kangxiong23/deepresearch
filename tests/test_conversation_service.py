# Layer: tests
# File: tests/test_conversation_service.py
# Responsibility: 对话生命周期 + send_message（含异常标记 incomplete）的单元测试。

from __future__ import annotations

import asyncio
import unittest

from tests.base import TempConfigTestCase
from app.core.branch_service import BranchService
from app.core.context_service import ContextService
from app.core.conversation_service import ConversationService
from app.core.file_service import FileService
from app.core.search_service import SearchService
from app.storage.branch_store import BranchStore
from app.storage.context_store import ContextStore
from app.storage.message_repo import MessageRepo
from app.storage.models import MessageChunk
from app.storage.tree_store import TreeStore


class _FakeLLM:
    """内存 LLM 桩：正常流式返回或抛异常。"""

    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    async def stream_chat(self, context):
        if self._fail:
            raise RuntimeError("boom")
        yield MessageChunk(delta="hello", is_done=False)
        yield MessageChunk(delta="", is_done=True)

    async def stream_prefix_continue(
        self, context, partial_content: str, partial_thinking: str = ""
    ):
        yield MessageChunk(delta="", is_done=True)

    def abort(self) -> None:
        pass


def _make_service(llm: _FakeLLM):
    tree = TreeStore()
    repo = MessageRepo()
    ctx_store = ContextStore()
    branch = BranchStore()
    context_svc = ContextService(
        message_repo=repo, context_store=ctx_store, tree_store=tree
    )
    branch_svc = BranchService(
        tree_store=tree, branch_store=branch, message_repo=repo
    )
    service = ConversationService(
        message_repo=repo,
        tree_store=tree,
        llm_client=llm,
        context_service=context_svc,
        search_service=SearchService([]),
        file_service=FileService([]),
        branch_service=branch_svc,
    )
    return service, tree, repo


async def _collect(gen):
    out = []
    async for chunk in gen:
        out.append(chunk)
    return out


class TestConversationService(TempConfigTestCase):
    def test_create_and_list(self) -> None:
        service, _tree, _repo = _make_service(_FakeLLM())
        conv_id = service.create_conversation(title="测试对话")
        conversations = service.list_conversations()
        self.assertTrue(any(c.id == conv_id for c in conversations))

    def test_switch_empty_conversation(self) -> None:
        service, _tree, _repo = _make_service(_FakeLLM())
        conv_id = service.create_conversation()
        detail = service.switch_conversation(conv_id)
        self.assertEqual(detail.id, conv_id)
        self.assertEqual(detail.messages, [])

    def test_send_message_persists_user_and_assistant(self) -> None:
        service, tree, repo = _make_service(_FakeLLM())
        conv_id = service.create_conversation()
        chunks = asyncio.run(_collect(service.send_message(conv_id, "hi", [])))
        self.assertTrue(any(c.is_done for c in chunks))

        roles = {m.role.value for m in repo.get_messages(conv_id)}
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)

        node_roles = {n.role for n in tree.get_conversation_chain(conv_id)}
        self.assertIn("user", node_roles)
        self.assertIn("assistant", node_roles)

    def test_send_message_error_marks_incomplete(self) -> None:
        service, tree, _repo = _make_service(_FakeLLM(fail=True))
        conv_id = service.create_conversation()
        with self.assertRaises(RuntimeError):
            asyncio.run(_collect(service.send_message(conv_id, "hi", [])))

        nodes = tree.get_all_message_nodes()
        self.assertTrue(nodes)
        self.assertTrue(any(n.incomplete for n in nodes))


if __name__ == "__main__":
    unittest.main()
