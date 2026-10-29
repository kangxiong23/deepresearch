# Layer: tests
# File: tests/test_file_access_settings.py
# Responsibility: 验证全局授权的持久化（含损坏兜底）与统一管理面板的即时回调。

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
from app.ui.widgets.file_access_dialog import (  # noqa: E402
    _FileAccessDialog,
    _KIND_GROUP,
    _KIND_NODE,
    _KIND_PATH,
    _ROLE_KIND,
)

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


def _node(nid="f-1", title="项目A", node_type="folder", roots=None,
          enabled=True) -> dict:
    return {
        "id": nid, "title": title, "node_type": node_type,
        "path": "未分类/" + title, "roots": list(roots or [r"D:\proj"]),
        "enabled": enabled,
    }


class TestDialog(SettingsTestBase):
    """统一管理面板：三类作用域同屏、加/删分别落到正确的回调。"""

    def make(self, enabled=True, roots=None, nodes=None):
        self.state = {
            "enabled": bool(enabled),
            "global_roots": list(roots if roots is not None else [r"D:\a", r"D:\b"]),
            "nodes": list(nodes or []),
        }
        self.global_calls: list[tuple[bool, list[str]]] = []
        self.add_calls: list[tuple[str, str]] = []
        self.remove_calls: list[tuple[str, str]] = []
        self.add_error = ""
        dlg = _FileAccessDialog(
            None,
            self._fetch,
            self._on_global,
            self._on_add_node,
            self._on_remove_node,
        )
        self.addCleanup(dlg.deleteLater)
        return dlg

    # ── 假的后端 ──
    def _fetch(self) -> dict:
        return {
            "enabled": self.state["enabled"],
            "global_roots": list(self.state["global_roots"]),
            "nodes": [dict(n, roots=list(n["roots"])) for n in self.state["nodes"]],
        }

    def _on_global(self, enabled, roots) -> None:
        self.global_calls.append((bool(enabled), list(roots)))
        self.state["enabled"] = bool(enabled)
        self.state["global_roots"] = list(roots)

    def _on_add_node(self, node_id, path) -> str:
        self.add_calls.append((node_id, path))
        if self.add_error:
            return self.add_error
        for node in self.state["nodes"]:
            if node["id"] == node_id:
                node["roots"].append(path)
        return ""

    def _on_remove_node(self, node_id, path) -> str:
        self.remove_calls.append((node_id, path))
        for node in self.state["nodes"]:
            if node["id"] == node_id and path in node["roots"]:
                node["roots"].remove(path)
        return ""

    # ── 树项工具 ──
    @staticmethod
    def items(dlg) -> list:
        out: list = []

        def walk(item) -> None:
            out.append(item)
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(dlg._tree.topLevelItemCount()):
            walk(dlg._tree.topLevelItem(i))
        return out

    def find(self, dlg, kind: str, text: str = ""):
        for item in self.items(dlg):
            if item.data(0, _ROLE_KIND) == kind and text in item.text(0):
                return item
        raise AssertionError(f"找不到 {kind} / {text}")

    # ── 用例 ──
    def test_shows_all_three_scopes(self) -> None:
        dlg = self.make(nodes=[
            _node(),
            _node("c-1", "合同审查", "conversation", [r"D:\合同"]),
        ])
        texts = [item.text(0) for item in self.items(dlg)]
        self.assertTrue(any("D:\\a" in t for t in texts), "全局授权")
        self.assertTrue(any("项目A" in t for t in texts), "文件夹节点")
        self.assertTrue(any("合同审查" in t for t in texts), "对话节点")
        self.assertTrue(any("D:\\合同" in t for t in texts))

    def test_remove_selected_global_path(self) -> None:
        dlg = self.make()
        dlg._tree.setCurrentItem(self.find(dlg, _KIND_PATH, "D:\\a"))
        self.assertTrue(dlg._remove_btn.isEnabled())
        dlg._on_remove()
        self.assertEqual(self.global_calls[-1], (True, [r"D:\b"]))

    def test_add_to_global(self) -> None:
        dlg = self.make()
        dlg._tree.setCurrentItem(self.find(dlg, _KIND_GROUP, "全局授权"))
        dlg._add_path(r"D:\c")
        self.assertEqual(self.global_calls[-1], (True, [r"D:\a", r"D:\b", r"D:\c"]))

    def test_duplicate_global_path_is_reported_not_added(self) -> None:
        dlg = self.make()
        dlg._tree.setCurrentItem(self.find(dlg, _KIND_GROUP, "全局授权"))
        dlg._add_path(r"D:\a")
        self.assertEqual(self.global_calls, [])
        self.assertIn("已在全局授权中", dlg._status.text())

    def test_add_to_selected_node(self) -> None:
        dlg = self.make(nodes=[_node()])
        dlg._tree.setCurrentItem(self.find(dlg, _KIND_NODE, "项目A"))
        self.assertIn("项目A", dlg._add_btn.text())
        dlg._add_path(r"D:\new")
        self.assertEqual(self.add_calls, [("f-1", r"D:\new")])
        self.assertIn(r"D:\new", dlg._status.text())

    def test_remove_node_path(self) -> None:
        dlg = self.make(nodes=[_node()])
        dlg._tree.setCurrentItem(self.find(dlg, _KIND_PATH, "D:\\proj"))
        dlg._on_remove()
        self.assertEqual(self.remove_calls, [("f-1", r"D:\proj")])

    def test_node_add_error_is_shown(self) -> None:
        dlg = self.make(nodes=[_node()])
        self.add_error = "目录不存在或无法访问"
        dlg._tree.setCurrentItem(self.find(dlg, _KIND_NODE, "项目A"))
        dlg._add_path(r"Z:\nope")
        self.assertTrue(dlg._status.text().startswith("⚠"))

    def test_toggle_off_notifies_and_blocks_global_add(self) -> None:
        dlg = self.make()
        dlg._tree.setCurrentItem(self.find(dlg, _KIND_GROUP, "全局授权"))
        self.assertTrue(dlg._add_btn.isEnabled())
        dlg._toggle.setChecked(False)
        self.assertEqual(self.global_calls[-1], (False, [r"D:\a", r"D:\b"]))
        self.assertFalse(dlg._add_btn.isEnabled())
        self.assertIn("已关闭", self.find(dlg, _KIND_GROUP, "全局授权").text(0))

    def test_node_add_still_works_with_global_off(self) -> None:
        dlg = self.make(nodes=[_node()])
        dlg._toggle.setChecked(False)
        dlg._tree.setCurrentItem(self.find(dlg, _KIND_NODE, "项目A"))
        self.assertTrue(dlg._add_btn.isEnabled(), "节点级授权不依赖全局开关")

    def test_node_group_header_has_no_add_target(self) -> None:
        dlg = self.make(nodes=[_node()])
        dlg._tree.setCurrentItem(self.find(dlg, _KIND_GROUP, "文件夹节点"))
        self.assertFalse(dlg._add_btn.isEnabled())

    def test_disabled_node_is_marked(self) -> None:
        dlg = self.make(nodes=[_node(enabled=False)])
        item = self.find(dlg, _KIND_NODE, "项目A")
        self.assertIn("已禁用", item.text(0))

    def test_empty_state_shows_hint(self) -> None:
        dlg = self.make(roots=[], nodes=[])
        self.assertTrue(dlg._empty_hint.isVisibleTo(dlg))
        self.assertFalse(dlg._remove_btn.isEnabled())


class TestTreeMenuWiring(unittest.TestCase):
    """右键菜单入口只给容器节点（文件夹/对话），消息节点不给。"""

    def test_local_root_action_available_for_containers(self) -> None:
        from app.ui.widgets.tree_panel import (
            _CONVERSATION_OPS,
            _FOLDER_OPS,
            _MESSAGE_OPS,
        )

        self.assertIn("local_root", _FOLDER_OPS)
        self.assertIn("local_root", _CONVERSATION_OPS)
        self.assertNotIn("local_root", _MESSAGE_OPS)


if __name__ == "__main__":
    unittest.main()
