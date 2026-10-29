# Layer: tests
# File: tests/test_settings_panel.py
# Responsibility: 验证侧栏的设置页——整块切换、六项导航、返回后销毁页面
#                 （反复开关不累积控件），以及 ChatApp 按 item_id 分发。
#                 纯离屏 Qt，不碰真实数据。

from __future__ import annotations

import os
import types
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from shiboken6 import isValid  # noqa: E402

from app.ui.app import ChatApp  # noqa: E402
from app.ui.widgets.settings_panel import SETTINGS_ITEMS  # noqa: E402
from app.ui.widgets.sidebar import Sidebar  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _flush_deletes() -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class TestSidebarSettings(unittest.TestCase):
    def make(self) -> Sidebar:
        sidebar = Sidebar()
        self.addCleanup(sidebar.deleteLater)
        return sidebar

    def test_starts_on_conversation_page(self) -> None:
        sidebar = self.make()
        self.assertFalse(sidebar.is_settings_open())
        self.assertIsNone(sidebar.settings_panel)
        self.assertIsNotNone(sidebar.tree_panel)

    def test_open_lists_every_item(self) -> None:
        sidebar = self.make()
        sidebar.open_settings()
        panel = sidebar.settings_panel
        self.assertIsNotNone(panel)
        self.assertTrue(sidebar.is_settings_open())
        self.assertEqual(panel.item_ids(), [item[0] for item in SETTINGS_ITEMS])
        self.assertIn("config", panel.item_ids())

    def test_clicking_row_emits_item_id(self) -> None:
        sidebar = self.make()
        clicked: list[str] = []
        sidebar.settings_item_clicked.connect(clicked.append)
        sidebar.open_settings()

        sidebar.settings_panel.row("config").click()
        sidebar.settings_panel.row("trash").click()
        self.assertEqual(clicked, ["config", "trash"])

    def test_back_destroys_the_page(self) -> None:
        sidebar = self.make()
        sidebar.open_settings()
        panel = sidebar.settings_panel

        sidebar.settings_panel.row("theme")  # 页面仍然在
        panel.back_requested.emit()

        self.assertFalse(sidebar.is_settings_open())
        self.assertIsNone(sidebar.settings_panel)
        _flush_deletes()
        self.assertFalse(isValid(panel), "设置页必须真的被销毁，不能只是藏起来")

    def test_reopen_creates_a_fresh_page(self) -> None:
        sidebar = self.make()
        sidebar.open_settings()
        first = sidebar.settings_panel
        sidebar.close_settings()
        _flush_deletes()

        sidebar.open_settings()
        second = sidebar.settings_panel
        self.assertIsNot(first, second)
        self.assertTrue(isValid(second))

    def test_open_twice_keeps_one_page(self) -> None:
        sidebar = self.make()
        sidebar.open_settings()
        first = sidebar.settings_panel
        sidebar.open_settings()
        self.assertIs(sidebar.settings_panel, first)

    def test_focus_search_leaves_settings(self) -> None:
        sidebar = self.make()
        sidebar.open_settings()
        sidebar.focus_search()
        self.assertFalse(sidebar.is_settings_open())

    def test_close_without_open_is_safe(self) -> None:
        sidebar = self.make()
        sidebar.close_settings()
        self.assertFalse(sidebar.is_settings_open())


class TestSettingsDispatch(unittest.TestCase):
    """ChatApp 按 item_id 分发；只有右侧面板那两项会退回对话页。"""

    def make(self):
        self.calls: list[str] = []
        sidebar = types.SimpleNamespace(closed=0)

        def close_settings() -> None:
            sidebar.closed += 1

        sidebar.close_settings = close_settings

        app = ChatApp.__new__(ChatApp)
        app._window = types.SimpleNamespace(sidebar=sidebar)
        for name in (
            "_handle_open_trash",
            "_handle_open_file_access",
            "_handle_open_theme",
            "_handle_open_config",
            "_handle_open_context_panel",
            "_handle_open_kg_panel",
        ):
            setattr(app, name, (lambda n: lambda: self.calls.append(n))(name))
        return app, sidebar

    def test_each_item_routes_to_its_handler(self) -> None:
        app, sidebar = self.make()
        expected = {
            "trash": "_handle_open_trash",
            "file_access": "_handle_open_file_access",
            "theme": "_handle_open_theme",
            "config": "_handle_open_config",
            "context": "_handle_open_context_panel",
            "kg": "_handle_open_kg_panel",
        }
        for item_id, handler in expected.items():
            self.calls.clear()
            app._on_settings_item(item_id)
            self.assertEqual(self.calls, [handler])

    def test_panel_items_return_to_conversation_page(self) -> None:
        app, sidebar = self.make()
        app._on_settings_item("trash")
        self.assertEqual(sidebar.closed, 0, "弹对话框的项留在设置页")

        app._on_settings_item("context")
        app._on_settings_item("kg")
        self.assertEqual(sidebar.closed, 2)

    def test_unknown_item_is_ignored(self) -> None:
        app, sidebar = self.make()
        app._on_settings_item("nope")
        self.assertEqual(self.calls, [])
        self.assertEqual(sidebar.closed, 0)


if __name__ == "__main__":
    unittest.main()