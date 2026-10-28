# Layer: tests
# File: tests/test_file_drop.py
# Responsibility: 文件拖进聊天区 → 消息附件的落点判定、筛选、去重与 UI 接线。

from __future__ import annotations

import os
import shutil
import unittest
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication

from app.ui.widgets.file_drop import (
    MAX_DROP_FILES,
    ChatDropArea,
    collect_files,
    is_supported_path,
    local_file_paths,
)

_FIXTURE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_drop")
_APP: QApplication | None = None


def _ensure_app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def _touch(path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"x")
    return path


def _mime(paths: list[str], urls: list[str] | None = None) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths]
                 + [QUrl(u) for u in (urls or [])])
    return mime


def _drop_event(mime: QMimeData) -> QDropEvent:
    return QDropEvent(
        QPointF(10.0, 10.0),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


class FileDropTestCase(unittest.TestCase):
    def setUp(self) -> None:
        _ensure_app()
        self._dir = os.path.join(_FIXTURE_ROOT, uuid.uuid4().hex)
        os.makedirs(self._dir, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)

    def _file(self, *parts: str) -> str:
        return _touch(os.path.join(self._dir, *parts))

    # ── 格式判定 ──────────────────────────────
    def test_is_supported_path(self) -> None:
        self.assertTrue(is_supported_path("a.docx"))
        self.assertTrue(is_supported_path("dir/B.PNG"))
        self.assertFalse(is_supported_path("a.zip"))
        self.assertFalse(is_supported_path(""))
        self.assertFalse(is_supported_path("noext"))

    # ── 收集 ──────────────────────────────────
    def test_collect_files_filters_and_counts(self) -> None:
        doc = self._file("报告.docx")
        bad = self._file("node_modules.zip")
        result = collect_files([doc, bad, os.path.join(self._dir, "不存在.txt")])
        self.assertEqual(result.files, [doc])
        self.assertEqual(result.skipped, 2)
        self.assertFalse(result.truncated)

    def test_collect_files_expands_folder(self) -> None:
        top = self._file("作业", "实验报告.docx")
        deep = self._file("作业", "子目录", "笔记.md")
        _touch(os.path.join(self._dir, "作业", "图片.png"))
        _touch(os.path.join(self._dir, "作业", "压缩包.rar"))
        _touch(os.path.join(self._dir, "作业", ".git", "config.txt"))

        result = collect_files([os.path.join(self._dir, "作业")])
        self.assertIn(top, result.files)
        self.assertIn(deep, result.files)
        self.assertEqual(len(result.files), 3)  # .rar 和 .git 里的都不收
        self.assertFalse(result.truncated)

    def test_collect_files_caps_total(self) -> None:
        for index in range(MAX_DROP_FILES + 5):
            self._file("批量", "f%02d.txt" % index)
        result = collect_files([os.path.join(self._dir, "批量")])
        self.assertEqual(len(result.files), MAX_DROP_FILES)
        self.assertTrue(result.truncated)

    def test_collect_files_dedupes(self) -> None:
        doc = self._file("a.docx")
        result = collect_files([doc, doc, os.path.join(self._dir, ".")])
        self.assertEqual(result.files.count(doc), 1)

    def test_local_file_paths_skips_http(self) -> None:
        doc = self._file("x.pdf")
        paths = local_file_paths(_mime([doc], ["https://example.com/a.png"]))
        self.assertEqual(paths, [doc])
        self.assertEqual(local_file_paths(None), [])

    # ── 拖放容器 ──────────────────────────────
    def test_drop_area_accepts_files_and_shows_overlay(self) -> None:
        area = ChatDropArea()
        area.resize(400, 300)
        received: list[list[str]] = []
        area.files_dropped.connect(lambda paths: received.append(list(paths)))
        try:
            doc = self._file("拖进来的.docx")
            mime = _mime([doc])
            event = _drop_event(mime)
            area.dragEnterEvent(event)
            self.assertTrue(event.isAccepted())
            self.assertFalse(area._overlay.isHidden())

            drop_mime = _mime([doc])
            area.dropEvent(_drop_event(drop_mime))
            self.assertEqual(received, [[doc]])
            self.assertTrue(area._overlay.isHidden())
        finally:
            area.deleteLater()

    def test_drop_area_passes_through_plain_text(self) -> None:
        """拖一段文字进来：接住、交给输入框，但不算附件、也不出遮罩。"""
        area = ChatDropArea()
        files: list[list[str]] = []
        texts: list[str] = []
        area.files_dropped.connect(lambda paths: files.append(list(paths)))
        area.text_dropped.connect(texts.append)
        try:
            mime = QMimeData()
            mime.setText("拖进来的文字")
            event = _drop_event(mime)
            area.dragEnterEvent(event)
            self.assertTrue(event.isAccepted())
            self.assertTrue(area._overlay.isHidden())
            area.dropEvent(_drop_event(mime))
            self.assertEqual(texts, ["拖进来的文字"])
            self.assertEqual(files, [])
        finally:
            area.deleteLater()

    def test_drop_area_ignores_tree_node_drags(self) -> None:
        """树节点拖拽只有自定义 MIME，不能被聊天区当成附件或文字接住。"""
        area = ChatDropArea()
        files: list[list[str]] = []
        texts: list[str] = []
        area.files_dropped.connect(lambda paths: files.append(list(paths)))
        area.text_dropped.connect(texts.append)
        try:
            mime = QMimeData()
            mime.setData("application/x-treenode-id", b"node-1")
            event = _drop_event(mime)
            area.dragEnterEvent(event)
            self.assertFalse(event.isAccepted())
            area.dropEvent(_drop_event(mime))
            self.assertEqual(files, [])
            self.assertEqual(texts, [])
        finally:
            area.deleteLater()

    def test_drop_area_leave_hides_overlay(self) -> None:
        from PySide6.QtGui import QDragLeaveEvent

        area = ChatDropArea()
        area.resize(300, 200)
        try:
            doc = self._file("a.txt")
            mime = _mime([doc])
            area.dragEnterEvent(_drop_event(mime))
            self.assertFalse(area._overlay.isHidden())
            area.dragLeaveEvent(QDragLeaveEvent())
            self.assertTrue(area._overlay.isHidden())
        finally:
            area.deleteLater()

    # ── 接入输入区 ────────────────────────────
    def test_input_area_add_files_dedupes(self) -> None:
        from app.ui.widgets.input_area import InputArea

        area = InputArea()
        try:
            doc = self._file("附件.docx")
            added, skipped = area.add_files([doc, doc])
            self.assertEqual((added, skipped), (1, 1))
            self.assertEqual(area._attachment_bar.get_files(), [doc])
            self.assertFalse(area._attachment_bar.isHidden())
        finally:
            area.deleteLater()

    # ── ChatApp 接线 ──────────────────────────
    def test_chat_app_handler_adds_attachments(self) -> None:
        import types

        from app.ui.app import ChatApp
        from app.ui.widgets.input_area import InputArea
        from PySide6.QtWidgets import QWidget

        holder = QWidget()
        holder.input_area = InputArea()
        try:
            doc = self._file("报告.docx")
            folder = os.path.join(self._dir, "资料")
            _touch(os.path.join(folder, "a.md"))
            _touch(os.path.join(folder, "b.zip"))
            fake_app = types.SimpleNamespace(_window=holder)
            ChatApp._on_files_dropped(fake_app, [doc, folder, doc])
            self.assertEqual(
                sorted(holder.input_area._attachment_bar.get_files()),
                sorted([doc, os.path.join(folder, "a.md")]),
            )
        finally:
            holder.deleteLater()

    def test_input_area_add_files_skips_unsupported(self) -> None:
        from app.ui.widgets.input_area import InputArea

        area = InputArea()
        try:
            bad = self._file("安装包.exe")
            added, skipped = area.add_files([bad, os.path.join(self._dir, "没有.txt")])
            self.assertEqual((added, skipped), (0, 2))
            self.assertEqual(area._attachment_bar.get_files(), [])
        finally:
            area.deleteLater()


if __name__ == "__main__":
    unittest.main()