# Layer: tests
# File: tests/test_deepseek_client.py
# Responsibility: 验证 DeepSeek payload 构建（thinking / temperature / reasoning_effort）
#                 与 SSE 解析（含 reasoning+finish_reason 同块时不丢结束信号）。

from __future__ import annotations

import json
import unittest

from config import ConfigScope
from app.adapters.deepseek_client import DeepSeekClient, _SSEParser, _parse_sse_line
from app.storage.models import ChunkType, LLMContext


def _context() -> LLMContext:
    return LLMContext(
        messages=[{"role": "user", "content": "hi"}],
        system_prompt="sys",
        max_tokens=1024,
        temperature=0.8,
    )


class TestBuildPayload(unittest.TestCase):
    def test_thinking_enabled_no_temperature(self) -> None:
        """思考模式启用：flash / pro 均不得携带 temperature。"""
        for model in ("deepseek-v4-pro", "deepseek-v4-flash"):
            with self.subTest(model=model):
                with ConfigScope(
                    model_type=model,
                    thinking_enabled=True,
                    reasoning_effort="high",
                    temperature=0.8,
                ):
                    payload = DeepSeekClient._build_payload(_context())
                self.assertEqual(payload["model"], model)
                self.assertEqual(payload["thinking"], {"type": "enabled"})
                self.assertEqual(payload["reasoning_effort"], "high")
                self.assertNotIn("temperature", payload)

    def test_thinking_disabled_sends_temperature(self) -> None:
        """思考模式关闭：flash / pro 均携带 temperature。"""
        for model in ("deepseek-v4-pro", "deepseek-v4-flash"):
            with self.subTest(model=model):
                with ConfigScope(
                    model_type=model,
                    thinking_enabled=False,
                    temperature=0.8,
                ):
                    payload = DeepSeekClient._build_payload(_context())
                self.assertEqual(payload["model"], model)
                self.assertEqual(payload["thinking"], {"type": "disabled"})
                self.assertNotIn("reasoning_effort", payload)
                self.assertEqual(payload["temperature"], 0.8)

    def test_system_prompt_and_stream(self) -> None:
        with ConfigScope(thinking_enabled=False):
            payload = DeepSeekClient._build_payload(_context())
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["messages"][0]["role"], "system")


class TestParseSSE(unittest.TestCase):
    def test_ignores_non_data_lines(self) -> None:
        self.assertIsNone(_parse_sse_line(""))
        self.assertIsNone(_parse_sse_line(": keep-alive"))
        self.assertIsNone(_parse_sse_line("event: message"))

    def test_done_marker(self) -> None:
        chunk = _parse_sse_line("data: [DONE]")
        self.assertIsNotNone(chunk)
        self.assertTrue(chunk.is_done)

    def test_text_chunk(self) -> None:
        line = (
            'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}'
        )
        chunk = _parse_sse_line(line)
        self.assertIsNotNone(chunk)
        self.assertEqual(chunk.delta, "hello")
        self.assertEqual(chunk.chunk_type, ChunkType.TEXT)
        self.assertFalse(chunk.is_done)

    def test_reasoning_chunk(self) -> None:
        line = (
            'data: {"choices":[{"delta":{"reasoning_content":"think..."},'
            '"finish_reason":null}]}'
        )
        chunk = _parse_sse_line(line)
        self.assertIsNotNone(chunk)
        self.assertEqual(chunk.delta, "think...")
        self.assertEqual(chunk.chunk_type, ChunkType.THINKING)
        self.assertFalse(chunk.is_done)

    def test_reasoning_with_finish_reason_keeps_done(self) -> None:
        """reasoning 与 finish_reason 同块时，结束信号不得丢失。"""
        line = (
            'data: {"choices":[{"delta":{"reasoning_content":"final"}'
            ',"finish_reason":"stop"}]}'
        )
        chunk = _parse_sse_line(line)
        self.assertIsNotNone(chunk)
        self.assertEqual(chunk.chunk_type, ChunkType.THINKING)
        self.assertTrue(chunk.is_done)


def _sse(delta: dict, finish: str | None = None) -> str:
    """构造一行 DeepSeek SSE。"""
    return "data: " + json.dumps(
        {"choices": [{"delta": delta, "finish_reason": finish}]},
        ensure_ascii=False,
    )


class TestToolCallPayload(unittest.TestCase):
    def test_payload_omits_tools_when_absent(self) -> None:
        """不带工具时请求体保持原样，多一个字段都不行。"""
        with ConfigScope(thinking_enabled=False):
            payload = DeepSeekClient._build_payload(_context())
        self.assertNotIn("tools", payload)

    def test_payload_includes_tools(self) -> None:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        ctx = _context()
        ctx.tools = tools
        with ConfigScope(thinking_enabled=False):
            payload = DeepSeekClient._build_payload(ctx)
        self.assertEqual(payload["tools"], tools)


class TestToolCallStream(unittest.TestCase):
    """工具调用的解析规则：分片累积、finish_reason 语义、结束信号归属。"""

    def test_fragments_accumulate_into_one_chunk(self) -> None:
        p = _SSEParser()
        lines = [
            _sse({"tool_calls": [{"index": 0, "id": "call_a", "function": {
                "name": "search_files", "arguments": '{"qu'}}]}),
            _sse({"tool_calls": [{"index": 0, "function": {
                "arguments": 'ery": "预算"}'}}]}),
            _sse({"tool_calls": [{"index": 1, "id": "call_b", "function": {
                "name": "read_file", "arguments": '{"path"'}}]}),
            _sse({"tool_calls": [{"index": 1, "function": {
                "arguments": ': "a.md"}'}}]}),
            _sse({}, finish="tool_calls"),
        ]
        chunks: list = []
        for ln in lines:
            chunks.extend(p.feed(ln))

        self.assertEqual(len(chunks), 1)
        c = chunks[0]
        self.assertEqual(c.chunk_type, ChunkType.TOOL_CALL)
        self.assertFalse(c.is_done, "工具轮不得携带 is_done，否则上层会提前落库")
        self.assertEqual(len(c.tool_calls), 2)
        self.assertEqual(
            json.loads(c.tool_calls[0]["function"]["arguments"]), {"query": "预算"}
        )
        self.assertEqual(c.tool_calls[1]["id"], "call_b")
        self.assertEqual(
            json.loads(c.tool_calls[1]["function"]["arguments"]), {"path": "a.md"}
        )

    def test_done_marker_suppressed_after_tool_calls(self) -> None:
        """[DONE] 不得在工具轮补一个结束块。"""
        p = _SSEParser()
        p.feed(_sse({"tool_calls": [{"index": 0, "id": "c", "function": {
            "name": "f", "arguments": "{}"}}]}))
        p.feed(_sse({}, finish="tool_calls"))
        self.assertEqual(p.feed("data: [DONE]"), [])

    def test_reasoning_and_tool_fragment_same_delta(self) -> None:
        """同一行可能既有思考又有工具分片，两路都不能丢。"""
        p = _SSEParser()
        out = p.feed(_sse({"reasoning_content": "想一想", "tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "f", "arguments": "{}"}}
        ]}))
        self.assertEqual([c.chunk_type for c in out], [ChunkType.THINKING])
        out2 = p.feed(_sse({}, finish="tool_calls"))
        self.assertEqual([c.chunk_type for c in out2], [ChunkType.TOOL_CALL])

    def test_content_before_tool_call_is_emitted(self) -> None:
        """模型会先说一句话再调工具，那句话必须保留。"""
        p = _SSEParser()
        out = p.feed(_sse({"content": "我先找一下", "tool_calls": [
            {"index": 0, "id": "c", "function": {"name": "f", "arguments": "{}"}}
        ]}))
        self.assertEqual([c.chunk_type for c in out], [ChunkType.TEXT])
        out2 = p.feed(_sse({}, finish="tool_calls"))
        self.assertEqual(out2[0].chunk_type, ChunkType.TOOL_CALL)

    def test_tool_finish_without_fragments_falls_back_to_done(self) -> None:
        """异常兜底：声明了工具调用却没有分片，不能把界面永远挂在生成中。"""
        p = _SSEParser()
        out = p.feed(_sse({}, finish="tool_calls"))
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].is_done)

    def test_normal_stream_still_ends_once(self) -> None:
        p = _SSEParser()
        out = p.feed(_sse({"content": "hello"}, finish="stop"))
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].is_done)
        self.assertEqual(out[0].delta, "hello")

    def test_reasoning_and_content_both_emitted(self) -> None:
        """旧实现会在有 reasoning 时丢掉同块的 content；现在两块都要出。"""
        p = _SSEParser()
        out = p.feed(_sse({"reasoning_content": "think", "content": "answer"},
                          finish="stop"))
        self.assertEqual(
            [c.chunk_type for c in out],
            [ChunkType.THINKING, ChunkType.TEXT],
        )
        self.assertFalse(out[0].is_done)
        self.assertTrue(out[1].is_done, "结束标记只落在最后一个块上")


if __name__ == "__main__":
    unittest.main()
