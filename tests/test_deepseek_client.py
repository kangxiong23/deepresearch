# Layer: tests
# File: tests/test_deepseek_client.py
# Responsibility: 验证 DeepSeek payload 构建（thinking / temperature / reasoning_effort）
#                 与 SSE 解析（含 reasoning+finish_reason 同块时不丢结束信号）。

from __future__ import annotations

import unittest

from config import ConfigScope
from app.adapters.deepseek_client import DeepSeekClient, _parse_sse_line
from app.storage.models import ChunkType, LLMContext


def _context() -> LLMContext:
    return LLMContext(
        messages=[{"role": "user", "content": "hi"}],
        system_prompt="sys",
        max_tokens=1024,
        temperature=0.8,
    )


class TestBuildPayload(unittest.TestCase):
    def test_thinking_enabled(self) -> None:
        with ConfigScope(
            thinking_enabled=True, reasoning_effort="high", temperature=0.8
        ):
            payload = DeepSeekClient._build_payload(_context())
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "high")
        self.assertNotIn("temperature", payload)

    def test_thinking_disabled(self) -> None:
        with ConfigScope(thinking_enabled=False, temperature=0.8):
            payload = DeepSeekClient._build_payload(_context())
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


if __name__ == "__main__":
    unittest.main()
