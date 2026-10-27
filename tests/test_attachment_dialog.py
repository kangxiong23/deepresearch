# Layer: tests
# File: tests/test_attachment_dialog.py
# Responsibility: 验证附件管理面板的渲染与只读规则——
#                 列出哪些文件、继承项不给移除按钮、旧式路径给"转为快照"。
#                 纯离屏 Qt，不触网、不碰真实数据。

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from app.ui.widgets.attachment_dialog import _AttachmentDialog  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _payload(mounts=None, legacy=None) -> dict:
    return {"title": "预算对话", "mounts": mounts or [], "legacy": legacy or []}


def _mount(name, inherited=False, owner="预算对话", owner_id="c-1",
           enabled=True) -> dict:
    return {
        "blob_id": name, "name": name, "size_text": "12.3 KB",
        "added_text": "08-14 20:11", "inherited": inherited,
        "owner_id": owner_id, "owner_title": owner, "enabled": enabled,
    }


def _legacy(name, inherited=False, owner="预算对话", owner_id="c-1") -> dict:
    return {
        "path": "D:\\旧\\" + name, "name": name, "inherited": inherited,
        "owner_id": owner_id, "owner_title": owner, "enabled": True,
    }


class TestAttachmentDialog(unittest.TestCase):
    def make(self, data, removed=None, converted=None) -> _AttachmentDialog:
        self.removed = removed if removed is not None else []
        self.converted = converted if converted is not None else []
        return _AttachmentDialog(
            None,
            "c-1",
            fetch=lambda _nid: data,
            on_add=lambda _nid, _p: "",
            on_remove=lambda owner, handle: (self.removed.append((owner, handle)) or ""),
            on_convert=lambda owner, path: (self.converted.append((owner, path)) or ""),
        )

    @staticmethod
    def buttons(dialog) -> list:
        return dialog.findChildren(QPushButton)

    @staticmethod
    def labels(dialog) -> list[str]:
        """所有标签的可见文本 + tooltip（元信息列会按宽度省略，全文在 tooltip）。"""
        out: list[str] = []
        for w in dialog.findChildren(QLabel):
            out.append(w.text())
            if w.toolTip():
                out.append(w.toolTip())
        return out

    def test_lists_mounted_file_names(self) -> None:
        dlg = self.make(_payload(mounts=[_mount("年度预算.md")]))
        self.assertTrue(any("年度预算.md" in t for t in self.labels(dlg)))
        self.assertFalse(dlg._empty_hint.isVisible())

    def test_inherited_mount_cannot_be_removed_here(self) -> None:
        dlg = self.make(_payload(mounts=[
            _mount("自己的.md"),
            _mount("父级的.md", inherited=True, owner="项目A", owner_id="f-1"),
        ]))
        remove_buttons = [b for b in self.buttons(dlg)
                          if b.text() == "移除" and b.isEnabled()]
        self.assertEqual(len(remove_buttons), 1, "继承项不给可用的移除按钮")
        self.assertTrue(any("继承自「项目A」" in t for t in self.labels(dlg)))

    def test_legacy_row_offers_snapshot_conversion(self) -> None:
        dlg = self.make(_payload(legacy=[_legacy("合同.md")]))
        convert = [b for b in self.buttons(dlg) if b.text() == "转为快照"]
        self.assertEqual(len(convert), 1)

        convert[0].click()
        self.assertEqual(self.converted, [("c-1", "D:\\旧\\合同.md")])

    def test_remove_click_passes_blob_id(self) -> None:
        dlg = self.make(_payload(mounts=[_mount("自己的.md")]))
        remove = [b for b in self.buttons(dlg) if b.text() == "移除"][0]
        remove.click()
        self.assertEqual(self.removed, [("c-1", "自己的.md")])

    def test_empty_state_shows_hint(self) -> None:
        dlg = self.make(_payload())
        self.assertTrue(dlg._empty_hint.text())

    def test_disabled_owner_is_marked(self) -> None:
        dlg = self.make(_payload(mounts=[
            _mount("父级的.md", inherited=True, owner="项目A",
                   owner_id="f-1", enabled=False)
        ]))
        self.assertTrue(any("已禁用" in t for t in self.labels(dlg)))




class TestTreeTooltip(unittest.TestCase):
    """树节点的悬停提示要说出"是哪个文件"，而不只是"有几个"。"""

    def vm(self, **kw):
        from app.controllers.view_models import TreeNodeVM
        base = dict(id="f-1", title="项目A", node_type="folder")
        base.update(kw)
        return TreeNodeVM(**base)

    def test_counts_are_replaced_by_names(self) -> None:
        from app.ui.widgets.tree_panel import _build_tooltip
        text = _build_tooltip(self.vm(
            attachment_count=2, attachment_names=["预算.md", "纪要.docx"],
        ))
        self.assertIn("预算.md", text)
        self.assertIn("纪要.docx", text)

    def test_long_lists_are_capped(self) -> None:
        from app.ui.widgets.tree_panel import _build_tooltip
        names = [f"f{i}.md" for i in range(9)]
        text = _build_tooltip(self.vm(attachment_count=9, attachment_names=names))
        self.assertIn("f0.md", text)
        self.assertNotIn("f8.md", text)
        self.assertIn("等 9 个", text)

    def test_inherited_count_is_reported(self) -> None:
        from app.ui.widgets.tree_panel import _build_tooltip
        text = _build_tooltip(self.vm(
            attachment_count=1, attachment_names=["自己的.md"],
            inherited_attachment_count=2,
        ))
        self.assertIn("自己的.md", text)
        self.assertIn("另继承: 2", text)

    def test_falls_back_to_count_without_names(self) -> None:
        from app.ui.widgets.tree_panel import _build_tooltip
        text = _build_tooltip(self.vm(attachment_count=3))
        self.assertIn("附件: 3", text)

    def test_names_helper_mixes_mounts_and_legacy_paths(self) -> None:
        from app.controllers.app_controller import _attachment_names
        from app.storage.models import FolderNode
        node = FolderNode(id="f-1", parent_id=None, title="t")
        node.mounts = [{"blob_id": "a", "name": "挂载.md"}]
        node.attachment_paths = ["D:\\x\\旧式.md", "D:\\x\\挂载.md"]
        self.assertEqual(_attachment_names(node), ["挂载.md", "旧式.md"])


if __name__ == "__main__":
    unittest.main()
