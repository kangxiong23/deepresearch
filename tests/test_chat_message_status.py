# Layer: tests
# File: tests/test_chat_message_status.py
# Responsibility: 验证 ChatMessage 的临时状态行——只参与渲染，
#                 不进正文、不落库、结束时清除。

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.widgets.chat_message import ChatMessage  # noqa: E402

_app = QApplication.instance() or QApplication([])


class TestStatusLine(unittest.TestCase):
    def make(self) -> ChatMessage:
        msg = ChatMessage(role="assistant", content="")
        msg.start_stream()
        return msg

    def test_status_does_not_enter_stream_buffer(self) -> None:
        msg = self.make()
        msg.set_status("🔍 检索「预算」")
        msg.append_stream("找到 2 个文件")

        self.assertEqual(msg._stream_buffer, "找到 2 个文件")
        self.assertIn("预算", msg._status_line)

    def test_finalize_clears_status(self) -> None:
        msg = self.make()
        msg.set_status("📄 读取 a.md")
        msg.append_stream("正文")
        msg.finalize_stream()

        self.assertEqual(msg._status_line, "")
        self.assertEqual(msg._stream_buffer, "正文")

    def test_mark_incomplete_clears_status(self) -> None:
        msg = self.make()
        msg.set_status("🔍 检索「x」")
        msg.mark_incomplete()
        self.assertEqual(msg._status_line, "")

    def test_append_without_status_is_unchanged(self) -> None:
        msg = self.make()
        msg.append_stream("你好")
        self.assertEqual(msg._stream_buffer, "你好")
        self.assertEqual(msg._status_line, "")


if __name__ == "__main__":
    unittest.main()
