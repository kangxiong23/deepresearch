# Layer: tests
# File: tests/test_config_dialog.py
# Responsibility: 验证应用配置面板——回填、取值类型、保存/失败/恢复默认、
#                 以及只读部署项不会变出可编辑控件。纯离屏 Qt。

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog, QLabel  # noqa: E402

from app.utils.app_settings import EDITABLE  # noqa: E402
from app.ui.widgets.config_dialog import _ConfigDialog  # noqa: E402

_app = QApplication.instance() or QApplication([])

VALUES = {
    "model_type": "deepseek-v4-flash",
    "thinking_enabled": True,
    "reasoning_effort": "max",
    "temperature": 0.7,
    "max_tokens": 8192,
    "max_history_tokens": 120000,
    "search_enabled": False,
    "search_max_results": 5,
    "kg_injection_enabled": True,
    "kg_max_inject": 10,
    "kg_extract_recent_n": 6,
}

FACTORY = dict(VALUES, model_type="deepseek-v4-pro", temperature=1.0, max_tokens=4096)

READONLY = [
    ("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    ("DB_PATH", r"D:\data\deepresearch.db"),
]


class TestConfigDialog(unittest.TestCase):
    def make(self, result=None):
        self.applied: list[dict] = []
        self.result = {"ok": True} if result is None else result

        def on_apply(values):
            self.applied.append(dict(values))
            return self.result

        dlg = _ConfigDialog(None, VALUES, FACTORY, READONLY, on_apply)
        self.addCleanup(dlg.deleteLater)
        return dlg

    def test_renders_current_values(self) -> None:
        dlg = self.make()
        self.assertEqual(dlg._widgets["model_type"].currentData(), "deepseek-v4-flash")
        self.assertTrue(dlg._widgets["thinking_enabled"].isChecked())
        self.assertEqual(dlg._widgets["max_tokens"].value(), 8192)
        self.assertAlmostEqual(dlg._widgets["temperature"].value(), 0.7, places=2)

    def test_every_editable_key_has_a_widget(self) -> None:
        dlg = self.make()
        self.assertEqual(set(dlg._widgets), set(EDITABLE))

    def test_collect_returns_typed_values(self) -> None:
        dlg = self.make()
        dlg._widgets["max_tokens"].setValue(2048)
        dlg._widgets["search_enabled"].setChecked(True)
        dlg._widgets["reasoning_effort"].setCurrentIndex(
            dlg._widgets["reasoning_effort"].findData("low")
        )
        values = dlg._collect()
        self.assertEqual(values["max_tokens"], 2048)
        self.assertIs(values["search_enabled"], True)
        self.assertEqual(values["reasoning_effort"], "low")
        self.assertEqual(len(values), len(EDITABLE))

    def test_save_passes_values_and_closes(self) -> None:
        dlg = self.make()
        dlg._widgets["temperature"].setValue(0.25)
        dlg._on_save()
        self.assertEqual(self.applied[0]["temperature"], 0.25)
        self.assertEqual(dlg.result(), int(QDialog.DialogCode.Accepted))

    def test_save_failure_keeps_dialog_open(self) -> None:
        dlg = self.make(result={})
        dlg._on_save()
        self.assertNotEqual(dlg.result(), int(QDialog.DialogCode.Accepted))
        self.assertTrue(dlg._status.text().startswith("⚠"))

    def test_apply_exception_is_reported(self) -> None:
        def boom(_values):
            raise OSError("磁盘满了")

        dlg = _ConfigDialog(None, VALUES, FACTORY, READONLY, boom)
        self.addCleanup(dlg.deleteLater)
        dlg._on_save()
        self.assertIn("磁盘满了", dlg._status.text())
        self.assertNotEqual(dlg.result(), int(QDialog.DialogCode.Accepted))

    def test_reset_fills_factory_defaults(self) -> None:
        dlg = self.make()
        dlg._on_reset()
        self.assertEqual(dlg._widgets["model_type"].currentData(), "deepseek-v4-pro")
        self.assertEqual(dlg._widgets["max_tokens"].value(), 4096)
        self.assertAlmostEqual(dlg._widgets["temperature"].value(), 1.0, places=2)
        self.assertIn("保存", dlg._status.text())

    def test_readonly_items_are_display_only(self) -> None:
        dlg = self.make()
        self.assertNotIn("DB_PATH", dlg._widgets)
        texts = [w.text() for w in dlg.findChildren(QLabel)]
        self.assertIn("DB_PATH", texts)
        self.assertIn(r"D:\data\deepresearch.db", texts)

    def test_widget_range_comes_from_the_spec(self) -> None:
        dlg = self.make()
        spin = dlg._widgets["max_tokens"]
        spin.setValue(10 ** 9)
        self.assertEqual(spin.value(), int(EDITABLE["max_tokens"].maximum))


if __name__ == "__main__":
    unittest.main()