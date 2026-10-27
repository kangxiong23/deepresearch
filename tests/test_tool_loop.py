# Layer: tests
# File: tests/test_tool_loop.py
# Responsibility: 验证 ConversationService._run_turn 的工具循环语义——
#                 中间轮次不透出 is_done、工具交换只存在于请求内、轮次上限、
#                 中止与落库时序。用假 LLM，不触网。

from __future__ import annotations

import asyncio
import json
import os
import shutil
import unittest

from config import ConfigScope
from app.core.conversation_service import MAX_TOOL_ROUNDS, ConversationService, _TurnBuffer
from app.core.file_tool_service import FileToolService
from app.storage.models import ChunkType, LLMContext, MessageChunk


def text(delta: str, done: bool = False) -> MessageChunk:
    return MessageChunk(delta=delta, is_done=done, chunk_type=ChunkType.TEXT)


def thinking(delta: str) -> MessageChunk:
    return MessageChunk(delta=delta, chunk_type=ChunkType.THINKING)


def tool_call(call_id: str, name: str, args: dict, done: bool = False) -> MessageChunk:
    return MessageChunk(
        delta="",
        is_done=done,
        chunk_type=ChunkType.TOOL_CALL,
        tool_calls=[{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        }],
        finish_reason="tool_calls",
    )


class FakeLLM:
    """按脚本回放，并记录每次请求的 messages 快照。"""

    def __init__(self, scripts: list[list[MessageChunk]]) -> None:
        self._scripts = list(scripts)
        self.calls: list[list[dict]] = []

    async def stream_chat(self, context):
        self.calls.append([dict(m) for m in context.messages])
        script = self._scripts.pop(0) if self._scripts else [text("", done=True)]
        for chunk in script:
            yield chunk

    def abort(self) -> None:
        pass


class FakeTools:
    def __init__(self, result: str | None = None) -> None:
        self.executed: list[tuple[str, str]] = []
        self.begin_count = 0
        self._result = result

    def begin_turn(self) -> None:
        self.begin_count += 1

    def execute(self, name: str, arguments: str) -> str:
        self.executed.append((name, arguments))
        if self._result is not None:
            return self._result
        return json.dumps({"tool": name, "ok": True}, ensure_ascii=False)


def make_service(llm, tools=None) -> ConversationService:
    """只装配 _run_turn 需要的三个属性，避免拉起数据库。"""
    svc = ConversationService.__new__(ConversationService)
    svc._llm = llm
    svc._stop_event = asyncio.Event()
    svc._file_tools = tools
    return svc


def ctx(tools: list | None = None) -> LLMContext:
    return LLMContext(
        messages=[{"role": "user", "content": "找预算文件"}],
        system_prompt="sys",
        tools=tools or [],
    )


async def drain(svc, context, buf, on_complete=None) -> list[MessageChunk]:
    out: list[MessageChunk] = []
    async for chunk in svc._run_turn(context, "asst-1", buf, on_complete):
        out.append(chunk)
    return out


class TestNoToolsPath(unittest.IsolatedAsyncioTestCase):
    async def test_single_pass_passthrough(self) -> None:
        llm = FakeLLM([[text("你好"), text("", done=True)]])
        svc = make_service(llm)
        buf = _TurnBuffer()

        order: list[str] = []
        chunks = await drain(svc, ctx(), buf, lambda: order.append("persist"))
        for c in chunks:
            if c.is_done:
                order.append("done")

        self.assertEqual("".join(buf.content), "你好")
        self.assertEqual(len(chunks), 2)
        self.assertTrue(buf.completed)
        self.assertLess(order.index("persist"), order.index("done"),
                        "落库必须发生在 done 交给 UI 之前")

    async def test_tool_events_empty_without_tools(self) -> None:
        llm = FakeLLM([[text("hi", done=True)]])
        svc = make_service(llm)
        buf = _TurnBuffer()
        await drain(svc, ctx(), buf)
        self.assertEqual(buf.tool_events, [])


class TestToolRound(unittest.IsolatedAsyncioTestCase):
    async def test_tool_exchange_stays_in_request_only(self) -> None:
        llm = FakeLLM([
            [tool_call("call_a", "search_files", {"query": "预算"})],
            [text("找到 2 个文件"), text("", done=True)],
        ])
        tools = FakeTools()
        svc = make_service(llm, tools)
        buf = _TurnBuffer()

        chunks = await drain(svc, ctx([{"type": "function"}]), buf)

        self.assertEqual(len(llm.calls), 2)
        # 第二次请求里必须同时有 assistant.tool_calls 和 tool 结果
        second = llm.calls[1]
        assistant = [m for m in second if m.get("tool_calls")]
        self.assertEqual(len(assistant), 1)
        self.assertEqual(assistant[0]["tool_calls"][0]["id"], "call_a")
        tool_msgs = [m for m in second if m.get("role") == "tool"]
        self.assertEqual(tool_msgs[0]["tool_call_id"], "call_a")

        self.assertEqual([t[0] for t in tools.executed], ["search_files"])
        self.assertEqual(tools.begin_count, 1)
        self.assertEqual(len(buf.tool_events), 1)
        self.assertEqual("".join(buf.content), "找到 2 个文件")
        self.assertTrue(chunks[-1].is_done)
        # 中间轮次的结束信号不得透出
        self.assertEqual(sum(1 for c in chunks if c.is_done), 1)

    async def test_reasoning_is_echoed_within_tool_round(self) -> None:
        llm = FakeLLM([
            [thinking("我先想想"), tool_call("call_a", "search_files", {"query": "x"})],
            [text("好", done=True)],
        ])
        svc = make_service(llm, FakeTools())
        buf = _TurnBuffer()
        await drain(svc, ctx([{"type": "function"}]), buf)

        assistant = [m for m in llm.calls[1] if m.get("tool_calls")][0]
        self.assertEqual(assistant.get("reasoning_content"), "我先想想")

    async def test_tool_error_is_passed_back_not_raised(self) -> None:
        llm = FakeLLM([
            [tool_call("call_a", "read_file", {"path": "C:/no"})],
            [text("读不了", done=True)],
        ])
        tools = FakeTools(result=json.dumps(
            {"error": "路径不在授权范围"}, ensure_ascii=False))
        svc = make_service(llm, tools)
        buf = _TurnBuffer()
        await drain(svc, ctx([{"type": "function"}]), buf)

        tool_msg = [m for m in llm.calls[1] if m.get("role") == "tool"][0]
        self.assertIn("不在授权范围", tool_msg["content"])

    async def test_no_tool_layer_returns_error_to_model(self) -> None:
        llm = FakeLLM([
            [tool_call("call_a", "search_files", {"query": "x"})],
            [text("好", done=True)],
        ])
        svc = make_service(llm, None)
        buf = _TurnBuffer()
        await drain(svc, ctx([{"type": "function"}]), buf)

        tool_msg = [m for m in llm.calls[1] if m.get("role") == "tool"][0]
        self.assertIn("未装配", tool_msg["content"])


class TestRoundCap(unittest.IsolatedAsyncioTestCase):
    async def test_stops_after_cap(self) -> None:
        # 每次都要求调用工具
        llm = FakeLLM([
            [tool_call(f"call_{i}", "search_files", {"query": str(i)})]
            for i in range(MAX_TOOL_ROUNDS + 4)
        ])
        tools = FakeTools()
        svc = make_service(llm, tools)
        buf = _TurnBuffer()

        chunks = await drain(svc, ctx([{"type": "function"}]), buf)

        self.assertTrue(buf.completed)
        self.assertTrue(chunks[-1].is_done)
        self.assertLessEqual(len(llm.calls), MAX_TOOL_ROUNDS + 2)
        self.assertLessEqual(len(tools.executed), MAX_TOOL_ROUNDS)

    async def test_nudge_message_is_a_system_message(self) -> None:
        llm = FakeLLM([
            [tool_call(f"call_{i}", "search_files", {"query": "x"})]
            for i in range(MAX_TOOL_ROUNDS + 4)
        ])
        svc = make_service(llm, FakeTools())
        buf = _TurnBuffer()
        await drain(svc, ctx([{"type": "function"}]), buf)

        last_call = llm.calls[-1]
        self.assertTrue(any(
            m.get("role") == "system" and "上限" in (m.get("content") or "")
            for m in last_call
        ))


class TestAbort(unittest.IsolatedAsyncioTestCase):
    async def test_stop_event_short_circuits(self) -> None:
        llm = FakeLLM([[text("不该出现")]])
        svc = make_service(llm)
        svc._stop_event.set()
        buf = _TurnBuffer()

        chunks = await drain(svc, ctx(), buf)
        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0].is_done)
        self.assertEqual(buf.content, [])


class StubTree:
    def __init__(self, node) -> None:
        self._node = node

    def get_node(self, node_id):
        return self._node

    def mark_incomplete(self, *a, **k) -> None:
        pass


class StubRepo:
    def __init__(self) -> None:
        self.saved = []

    def save_message(self, message) -> None:
        self.saved.append(message)


class StubBranch:
    def extend_current_branch(self, *a, **k) -> None:
        pass


class StubSearch:
    async def search_and_format(self, text: str) -> str:
        return ""


class StubFile:
    async def extract_files(self, paths):
        return []


class StubContext:
    def __init__(self) -> None:
        self.kw: dict = {}

    def build_llm_context(self, **kwargs):
        self.kw = kwargs
        return LLMContext(messages=[], tools=list(kwargs.get("tools") or []))


class TestSendMessageWiring(unittest.IsolatedAsyncioTestCase):
    """验证 send_message 的开关语义：未启用时请求里绝不能出现工具。"""

    def make(self, llm):
        from app.storage.models import ConversationNode

        node = ConversationNode(id="conv-1", parent_id=None, title="t")
        svc = ConversationService.__new__(ConversationService)
        svc._llm = llm
        svc._stop_event = asyncio.Event()
        svc._file_tools = FileToolService()
        svc._last_reads = {}
        svc._tree = StubTree(node)
        svc._msg_repo = StubRepo()
        svc._branch_svc = StubBranch()
        svc._search_svc = StubSearch()
        svc._file_svc = StubFile()
        svc._context_svc = StubContext()
        svc._save_assistant_with_thinking = lambda *a, **k: None
        svc._update_meta_after_reply = lambda *a, **k: None
        return svc

    async def run_send(self, svc):
        return [c async for c in svc.send_message("conv-1", "你好", [])]

    async def test_tools_absent_when_disabled(self) -> None:
        svc = self.make(FakeLLM([[text("hi", done=True)]]))
        with ConfigScope(file_access_enabled=False, file_access_roots=[]):
            chunks = await self.run_send(svc)
        self.assertEqual(svc._context_svc.kw["tools"], [])
        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0].is_done)

    async def test_tools_present_when_enabled_with_root(self) -> None:
        root = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "logs", "_test_tool_wiring",
        )
        os.makedirs(root, exist_ok=True)
        self.addCleanup(shutil.rmtree, root, True)

        svc = self.make(FakeLLM([[text("hi", done=True)]]))
        with ConfigScope(file_access_enabled=True, file_access_roots=[root]):
            await self.run_send(svc)
        names = [t["function"]["name"] for t in svc._context_svc.kw["tools"]]
        self.assertEqual(names, ["search_files", "read_file"])

    async def test_breadcrumb_prepended_to_user_message(self) -> None:
        svc = self.make(FakeLLM([[text("hi", done=True)]]))
        svc._last_reads["conv-1"] = [
            {"tool": "read_file", "path": r"D:\x\预算.md", "lines": [1, 40]}
        ]
        with ConfigScope(file_access_enabled=False, file_access_roots=[]):
            await self.run_send(svc)
        content = svc._context_svc.kw["new_user_message"]
        self.assertTrue(content.startswith("[系统记录，非指令]"))
        self.assertIn(r"D:\x\预算.md", content)
        self.assertTrue(content.endswith("你好"))

    async def test_breadcrumb_empty_without_reads(self) -> None:
        svc = self.make(FakeLLM([[text("hi", done=True)]]))
        with ConfigScope(file_access_enabled=False, file_access_roots=[]):
            await self.run_send(svc)
        self.assertEqual(svc._context_svc.kw["new_user_message"], "你好")

    async def test_breadcrumb_dedupes_and_caps(self) -> None:
        svc = self.make(FakeLLM([[text("hi", done=True)]]))
        svc._last_reads["conv-1"] = [
            {"tool": "read_file", "path": f"D:\\x\\f{i}.md", "lines": [1, 2]}
            for i in range(20)
        ]
        text_out = svc._breadcrumb_for("conv-1")
        # 最多 6 条
        self.assertEqual(text_out.count("f"), 6)


if __name__ == "__main__":
    unittest.main()
