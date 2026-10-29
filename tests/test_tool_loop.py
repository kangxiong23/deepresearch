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

    def execute(self, name: str, arguments: str, should_stop=None) -> str:
        self.executed.append((name, arguments))
        if self._result is not None:
            return self._result
        return json.dumps({"tool": name, "ok": True}, ensure_ascii=False)


class FakeWebTools:
    """联网工具：execute 是 async 的（要发 HTTP），与文件工具的同步接口不同。"""

    def __init__(self) -> None:
        self.executed: list[tuple[str, str]] = []
        self.begin_count = 0

    def begin_turn(self) -> None:
        self.begin_count += 1

    def supports(self, name: str) -> bool:
        return name == "fetch_url"

    async def execute(self, name: str, arguments: str, should_stop=None) -> str:
        self.executed.append((name, arguments))
        return json.dumps(
            {"url": "https://example.com/a", "title": "示例", "content": "网页正文"},
            ensure_ascii=False,
        )


def make_service(llm, tools=None, web=None) -> ConversationService:
    """只装配 _run_turn 需要的三个属性，避免拉起数据库。"""
    svc = ConversationService.__new__(ConversationService)
    svc._llm = llm
    svc._stop_event = asyncio.Event()
    svc._file_tools = tools
    svc._web_tools = web
    svc._read_log = None
    svc._memory_reads = {}
    svc._blobs = None
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
        svc._read_log = None
        svc._memory_reads = {}
        svc._blobs = None
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

    async def test_fetch_tool_listed_when_enabled(self) -> None:
        from app.core.web_tool_service import WebToolService

        svc = self.make(FakeLLM([[text("hi", done=True)]]))
        svc._web_tools = WebToolService(fetcher=object())   # 只要装配了就会下发
        with ConfigScope(file_access_enabled=False, file_access_roots=[],
                         web_fetch_enabled=True):
            await self.run_send(svc)

        names = [t["function"]["name"] for t in svc._context_svc.kw["tools"]]
        self.assertEqual(names, ["fetch_url"])

    async def test_fetch_tool_absent_when_disabled(self) -> None:
        from app.core.web_tool_service import WebToolService

        svc = self.make(FakeLLM([[text("hi", done=True)]]))
        svc._web_tools = WebToolService(fetcher=object())
        with ConfigScope(file_access_enabled=False, file_access_roots=[],
                         web_fetch_enabled=False):
            await self.run_send(svc)

        self.assertEqual(svc._context_svc.kw["tools"], [])

    async def test_file_and_web_tools_coexist(self) -> None:
        from app.core.web_tool_service import WebToolService

        root = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "logs", "_test_tool_wiring_web",
        )
        os.makedirs(root, exist_ok=True)
        self.addCleanup(shutil.rmtree, root, True)

        svc = self.make(FakeLLM([[text("hi", done=True)]]))
        svc._web_tools = WebToolService(fetcher=object())
        with ConfigScope(file_access_enabled=True, file_access_roots=[root],
                         web_fetch_enabled=True):
            await self.run_send(svc)

        names = [t["function"]["name"] for t in svc._context_svc.kw["tools"]]
        self.assertEqual(names, ["search_files", "read_file", "fetch_url"])

    async def test_breadcrumb_prepended_to_user_message(self) -> None:
        svc = self.make(FakeLLM([[text("hi", done=True)]]))
        svc._memory_reads["conv-1"] = [
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
        svc._memory_reads["conv-1"] = [
            {"tool": "read_file", "path": f"D:\\x\\f{i}.md", "lines": [1, 2]}
            for i in range(20)
        ]
        text_out = svc._breadcrumb_for("conv-1")
        # 最多 6 条
        self.assertEqual(text_out.count("f"), 6)


class TestWebToolRound(unittest.IsolatedAsyncioTestCase):
    """联网抓取走 async 工具服务：按名字路由，不能被交给同步文件工具。"""

    async def test_fetch_url_routed_to_web_tools(self) -> None:
        llm = FakeLLM([
            [tool_call("call_a", "fetch_url", {"url": "https://example.com/a"})],
            [text("读完了", done=True)],
        ])
        files = FakeTools()
        web = FakeWebTools()
        svc = make_service(llm, files, web=web)
        buf = _TurnBuffer()

        await drain(svc, ctx([{"type": "function"}]), buf)

        self.assertEqual([c[0] for c in web.executed], ["fetch_url"])
        self.assertEqual(files.executed, [], "联网工具不该落到文件工具上")
        self.assertEqual(web.begin_count, 1)

        tool_msg = [m for m in llm.calls[1] if m.get("role") == "tool"][0]
        self.assertIn("网页正文", tool_msg["content"])
        self.assertEqual(len(buf.tool_events), 1)
        self.assertEqual(buf.tool_events[0]["name"], "fetch_url")

    async def test_file_tool_still_goes_to_file_tools(self) -> None:
        llm = FakeLLM([
            [tool_call("call_a", "search_files", {"query": "预算"})],
            [text("好", done=True)],
        ])
        files = FakeTools()
        web = FakeWebTools()
        svc = make_service(llm, files, web=web)

        await drain(svc, ctx([{"type": "function"}]), _TurnBuffer())

        self.assertEqual([c[0] for c in files.executed], ["search_files"])
        self.assertEqual(web.executed, [])

    async def test_both_services_begin_turn(self) -> None:
        llm = FakeLLM([[text("hi", done=True)]])
        files = FakeTools()
        web = FakeWebTools()
        svc = make_service(llm, files, web=web)

        await drain(svc, ctx([{"type": "function"}]), _TurnBuffer())

        self.assertEqual(files.begin_count, 1)
        self.assertEqual(web.begin_count, 1)

    async def test_fetch_status_line_hides_full_url(self) -> None:
        llm = FakeLLM([
            [tool_call("call_a", "fetch_url", {"url": "https://secret.example/dir/page"})],
            [text("好了", done=True)],
        ])
        svc = make_service(llm, FakeTools(), web=FakeWebTools())

        chunks = await drain(svc, ctx([{"type": "function"}]), _TurnBuffer())
        status = [c for c in chunks if c.chunk_type == ChunkType.TOOL_CALL][0]

        self.assertIn("抓取", status.delta)
        self.assertIn("secret.example", status.delta)
        self.assertNotIn("dir/page", status.delta, "状态行只报域名")


class TestToolStatusChunks(unittest.IsolatedAsyncioTestCase):
    """工具状态行：先告诉用户"要做什么"，再执行。"""

    async def test_search_status_emitted(self) -> None:
        llm = FakeLLM([
            [tool_call("call_a", "search_files", {"query": "预算"})],
            [text("好了", done=True)],
        ])
        svc = make_service(llm, FakeTools())
        buf = _TurnBuffer()
        chunks = await drain(svc, ctx([{"type": "function"}]), buf)

        status = [c for c in chunks if c.chunk_type == ChunkType.TOOL_CALL]
        self.assertEqual(len(status), 1)
        self.assertIn("预算", status[0].delta)
        self.assertFalse(status[0].is_done)

    async def test_read_status_uses_basename_only(self) -> None:
        llm = FakeLLM([
            [tool_call("call_a", "read_file",
                       {"path": "D:\\secret\\dir\\2026预算.md"})],
            [text("好了", done=True)],
        ])
        svc = make_service(llm, FakeTools())
        buf = _TurnBuffer()
        chunks = await drain(svc, ctx([{"type": "function"}]), buf)

        status = [c for c in chunks if c.chunk_type == ChunkType.TOOL_CALL][0]
        self.assertIn("2026预算.md", status.delta)
        self.assertNotIn("secret", status.delta, "状态行不得暴露完整目录")

    async def test_status_not_emitted_for_dropped_calls(self) -> None:
        """超过轮次上限、调用被丢弃时，不应再显示状态。"""
        llm = FakeLLM([
            [tool_call(f"c{i}", "search_files", {"query": "x"})]
            for i in range(MAX_TOOL_ROUNDS + 4)
        ])
        svc = make_service(llm, FakeTools())
        buf = _TurnBuffer()
        chunks = await drain(svc, ctx([{"type": "function"}]), buf)
        status = [c for c in chunks if c.chunk_type == ChunkType.TOOL_CALL]
        self.assertEqual(len(status), MAX_TOOL_ROUNDS)


class StubTreeMap:
    """按 id 查节点，记录 mark_incomplete 调用。"""

    def __init__(self, nodes: dict) -> None:
        self.nodes = nodes
        self.marked: list[tuple[str, bool]] = []

    def get_node(self, node_id):
        return self.nodes.get(node_id)

    def get_predecessor(self, session_id, node_id):
        return None

    def get_conversation_chain(self, session_id):
        return [n for n in self.nodes.values() if n.parent_id == session_id
                or n.id == session_id]

    def mark_incomplete(self, node_id, value=True) -> None:
        self.marked.append((node_id, value))


class StubBranchLog:
    def __init__(self) -> None:
        self.created: list = []
        self.extended: list = []

    def create_branch(self, session_id, fork_point_id, chain) -> None:
        self.created.append([n.id for n in chain])

    def extend_current_branch(self, session_id, nodes) -> None:
        self.extended.append([n.id for n in nodes])


class TestRegenerateWithTools(unittest.IsolatedAsyncioTestCase):
    """预分支路径：工具轮不得产生额外落库，停止仍然零持久化。"""

    def make(self, llm, stop=False):
        from app.storage.models import ConversationNode, MessageNode

        conv = ConversationNode(id="conv-1", parent_id=None, title="t")
        asst = MessageNode(id="asst-1", parent_id="conv-1", message_id="asst-1",
                           role="assistant", preview="", title="A")
        svc = ConversationService.__new__(ConversationService)
        svc._llm = llm
        svc._stop_event = asyncio.Event()
        if stop:
            svc._stop_event.set()
        svc._file_tools = FileToolService()
        svc._read_log = None
        svc._memory_reads = {}
        svc._blobs = None
        svc._tree = StubTreeMap({"conv-1": conv, "asst-1": asst})
        svc._branch_svc = StubBranchLog()
        svc._context_svc = StubContext()

        self.saved: list = []

        def _save(session_id, msg_id, content, thinking):
            self.saved.append((msg_id, content, thinking))
            return MessageNode(id=msg_id, parent_id=session_id, message_id=msg_id,
                               role="assistant", preview="", title="new")

        svc._save_assistant_with_thinking = _save
        svc._update_meta_after_reply = lambda *a, **k: None
        svc._user_text_before = lambda sid, tid: "原问题"
        return svc

    async def run_regen(self, svc):
        return [c async for c in svc.regenerate_message("conv-1", "asst-1")]

    async def test_tools_passed_but_no_breadcrumb(self) -> None:
        root = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "logs", "_test_regen_root",
        )
        os.makedirs(root, exist_ok=True)
        self.addCleanup(shutil.rmtree, root, True)

        svc = self.make(FakeLLM([[text("好", done=True)]]))
        svc._memory_reads["conv-1"] = [
            {"tool": "read_file", "path": r"D:\x\a.md", "lines": [1, 2]}
        ]
        with ConfigScope(file_access_enabled=True, file_access_roots=[root]):
            await self.run_regen(svc)

        kw = svc._context_svc.kw
        self.assertEqual(
            [t["function"]["name"] for t in kw["tools"]],
            ["search_files", "read_file"],
        )
        self.assertNotIn("系统记录", kw["new_user_message"],
                         "预分支路径不得注入面包屑")

    async def test_tool_round_saves_exactly_once(self) -> None:
        llm = FakeLLM([
            [tool_call("call_a", "search_files", {"query": "预算"})],
            [text("找到 1 个", done=True)],
        ])
        svc = self.make(llm)
        with ConfigScope(file_access_enabled=True, file_access_roots=[os.getcwd()]):
            await self.run_regen(svc)

        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(len(self.saved), 1, "工具轮不得产生额外 assistant")
        self.assertEqual(len(svc._branch_svc.created), 1)
        self.assertEqual(self.saved[0][1], "找到 1 个")

    async def test_stop_persists_nothing(self) -> None:
        """
        停止语义：已产出的部分只存在于内存，不落库、不建分支。
        注意 regenerate_message 入口会 clear() 停止事件，所以必须在
        流式中途触发，不能在调用前置位。
        """

        class StopAfterFirst:
            def __init__(self, svc) -> None:
                self.svc = svc
                self.calls = 0

            async def stream_chat(self, context):
                self.calls += 1
                yield text("部分内容")
                self.svc._stop_event.set()
                yield text("后续内容")

        svc = self.make(FakeLLM([]))
        svc._llm = StopAfterFirst(svc)

        with ConfigScope(file_access_enabled=True, file_access_roots=[os.getcwd()]):
            chunks = await self.run_regen(svc)

        self.assertTrue(chunks[-1].is_done)
        self.assertEqual(self.saved, [], "停止后不得落库")
        self.assertEqual(svc._branch_svc.created, [], "停止后不得建分支")


class TestResendWithTools(unittest.IsolatedAsyncioTestCase):
    async def test_breadcrumb_is_injected(self) -> None:
        from app.storage.models import ConversationNode, MessageNode

        conv = ConversationNode(id="conv-1", parent_id=None, title="t")
        orig = MessageNode(id="u-1", parent_id="conv-1", message_id="u-1",
                           role="user", preview="", title="U")
        svc = ConversationService.__new__(ConversationService)
        svc._llm = FakeLLM([[text("好", done=True)]])
        svc._stop_event = asyncio.Event()
        svc._file_tools = FileToolService()
        svc._read_log = None
        # 内存态读取记录（与 audit 条目同形）：面包屑应由它推导
        svc._memory_reads = {"conv-1": [
            {"tool": "read_file", "path": r"D:\x\预算.md", "lines": [1, 9]}
        ]}
        svc._blobs = None
        svc._tree = StubTreeMap({"conv-1": conv, "u-1": orig})
        svc._branch_svc = StubBranchLog()
        svc._context_svc = StubContext()
        svc._msg_repo = StubRepo()
        svc._file_svc = StubFile()
        svc._search_svc = StubSearch()
        svc._save_assistant_with_thinking = lambda *a, **k: orig
        svc._update_meta_after_reply = lambda *a, **k: None

        with ConfigScope(file_access_enabled=True, file_access_roots=[os.getcwd()]):
            async for _ in svc.resend_edited_message("conv-1", "u-1", "新问题", []):
                pass

        kw = svc._context_svc.kw
        self.assertIn("系统记录", kw["new_user_message"])
        self.assertIn("预算.md", kw["new_user_message"])
        self.assertTrue(kw["tools"])


if __name__ == "__main__":
    unittest.main()
