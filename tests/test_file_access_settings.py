# Layer: tests
# File: tests/test_file_access_settings.py
# Responsibility: 验证授权配置的持久化（含损坏兜底）与授权对话框的即时回调。

from __future__ import annotations

import json
import os
import shutil
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import config as app_config  # noqa: E402
from config import ConfigScope  # noqa: E402
from app.utils.file_access_settings import FileAccessSettings  # noqa: E402
from app.ui.widgets.file_access_dialog import _FileAccessDialog  # noqa: E402

_app = QApplication.instance() or QApplication([])

_BASE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "_test_file_access",
)


class SettingsTestBase(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(_BASE, ignore_errors=True)
        os.makedirs(_BASE, exist_ok=True)
        self.addCleanup(shutil.rmtree, _BASE, True)
        self.db_path = os.path.join(_BASE, "deepresearch.db")


class TestFileAccessSettings(SettingsTestBase):
    def test_roundtrip(self) -> None:
        with ConfigScope(DB_PATH=self.db_path):
            store = FileAccessSettings()
            self.assertEqual(store.path.name, "file_access.json")
            store.save(True, [r"D:\资料", r"E:\项目"])
            self.assertEqual(store.load(), {
                "enabled": True, "roots": [r"D:\资料", r"E:\项目"],
            })

    def test_missing_file_means_disabled(self) -> None:
        with ConfigScope(DB_PATH=self.db_path):
            self.assertEqual(FileAccessSettings().load(),
                             {"enabled": False, "roots": []})

    def test_corrupt_file_falls_back(self) -> None:
        with ConfigScope(DB_PATH=self.db_path):
            store = FileAccessSettings()
            with open(store.path, "w", encoding="utf-8") as f:
                f.write("{ not json")
            self.assertEqual(store.load(), {"enabled": False, "roots": []})

    def test_non_dict_and_bad_roots_are_filtered(self) -> None:
        with ConfigScope(DB_PATH=self.db_path):
            store = FileAccessSettings()
            with open(store.path, "w", encoding="utf-8") as f:
                json.dump({"enabled": 1, "roots": ["D:\\a", "", 5, None]}, f)
            self.assertEqual(store.load(), {"enabled": True, "roots": ["D:\\a"]})

    def test_apply_to_config(self) -> None:
        with ConfigScope(DB_PATH=self.db_path):
            FileAccessSettings().save(True, [r"D:\x"])
            data = FileAccessSettings().apply_to_config()
            self.assertTrue(data["enabled"])
            self.assertTrue(app_config.file_access_enabled)
            self.assertEqual(app_config.file_access_roots, [r"D:\x"])

    def test_save_creates_parent_dir(self) -> None:
        nested = os.path.join(_BASE, "sub", "deepresearch.db")
        with ConfigScope(DB_PATH=nested):
            FileAccessSettings().save(False, [])
            self.assertTrue(os.path.exists(
                os.path.join(_BASE, "sub", "file_access.json")))


class TestDialog(SettingsTestBase):
    def make(self, enabled=True, roots=None):
        self.calls: list[tuple[bool, list[str]]] = []
        dlg = _FileAccessDialog(
            None, enabled, list(roots if roots is not None else [r"D:\a", r"D:\b"]),
            lambda e, r: self.calls.append((e, list(r))),
        )
        self.addCleanup(dlg.deleteLater)
        return dlg

    def test_remove_selected(self) -> None:
        dlg = self.make()
        dlg._list.setCurrentRow(0)
        dlg._on_remove()
        self.assertEqual(self.calls[-1], (True, [r"D:\b"]))
        self.assertEqual(dlg._list.count(), 1)

    def test_toggle_off_notifies_and_disables_controls(self) -> None:
        dlg = self.make()
        dlg._toggle.setChecked(False)
        self.assertEqual(self.calls[-1][0], False)
        self.assertFalse(dlg._add_btn.isEnabled())
        self.assertFalse(dlg._list.isEnabled())

    def test_empty_list_shows_hint(self) -> None:
        dlg = self.make(roots=[])
        self.assertTrue(dlg._empty_hint.isVisibleTo(dlg))
        self.assertFalse(dlg._remove_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
