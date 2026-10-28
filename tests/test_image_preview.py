# Layer: tests
# File: tests/test_image_preview.py
# Responsibility: 图片预览（气泡缩略图 / 原图查看器 / 待发附件小图）的展示层测试。
#                 只测 UI 展示与换算，不涉及发送、解析、落库。

from __future__ import annotations

import os
import shutil
import unittest
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QApplication

from app.ui.widgets import image_preview
from app.ui.widgets.image_preview import (
    ImageSource,
    ImageTile,
    ImageViewer,
    is_image_path,
    load_pixmap,
    tile_size,
)

_FIXTURE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_images")
_APP: QApplication | None = None


def _ensure_app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def _make_png(path: str, width: int, height: int, color=(30, 60, 120)) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (width, height), color).save(path)
    return path


class ImagePreviewTestCase(unittest.TestCase):
    def setUp(self) -> None:
        _ensure_app()
        self._dir = os.path.join(_FIXTURE_ROOT, uuid.uuid4().hex)
        os.makedirs(self._dir, exist_ok=True)
        image_preview._CACHE.clear()
        image_preview.set_blob_reader(None)

    def tearDown(self) -> None:
        image_preview.set_blob_reader(None)
        image_preview._CACHE.clear()
        shutil.rmtree(self._dir, ignore_errors=True)

    def _png(self, name: str, width: int, height: int) -> str:
        return _make_png(os.path.join(self._dir, name), width, height)

    # ── 来源 ──────────────────────────────────
    def test_is_image_path(self) -> None:
        self.assertTrue(is_image_path("a.PNG"))
        self.assertTrue(is_image_path("dir/b.jpeg"))
        self.assertFalse(is_image_path("a.docx"))
        self.assertFalse(is_image_path(""))

    def test_source_of_maps_attachment_fields(self) -> None:
        source = ImageSource.of({
            "name": "case-图6.png",
            "kind": "image",
            "blob_id": "abc123",
            "size": 4096,
            "width": 1344,
            "height": 709,
        })
        self.assertEqual(source.name, "case-图6.png")
        self.assertEqual(source.blob_id, "abc123")
        self.assertEqual((source.width, source.height), (1344, 709))
        self.assertEqual(source.key(), "abc123")

    def test_source_reads_blob_through_reader(self) -> None:
        payload = b"\x89PNG\r\n\x1a\n fake bytes"
        image_preview.set_blob_reader(lambda blob_id: payload if blob_id == "b1" else None)
        self.assertEqual(ImageSource(name="x.png", blob_id="b1").read(), payload)
        self.assertIsNone(ImageSource(name="y.png", blob_id="missing").read())

    def test_source_falls_back_to_local_path(self) -> None:
        path = self._png("local.png", 40, 30)
        with open(path, "rb") as handle:
            expected = handle.read()
        source = ImageSource(name="local.png", path=path)
        self.assertEqual(source.read(), expected)

    def test_broken_source_reads_none(self) -> None:
        source = ImageSource(name="gone.png", path=os.path.join(self._dir, "nope.png"))
        self.assertIsNone(source.read())
        self.assertIsNone(load_pixmap(source, 132, 100))

    # ── 尺寸换算 ──────────────────────────────
    def test_tile_size_keeps_aspect_and_caps(self) -> None:
        wide = tile_size(1344, 709)
        self.assertLessEqual(wide.width(), image_preview.TILE_MAX_W)
        self.assertLessEqual(wide.height(), image_preview.TILE_MAX_H)
        self.assertEqual(wide.width(), image_preview.TILE_MAX_W)

        # 830×42 的行内公式细条：宽拉满、高度保住可点的下限
        strip = tile_size(830, 42)
        self.assertEqual(strip.width(), image_preview.TILE_MAX_W)
        self.assertEqual(strip.height(), image_preview.TILE_MIN_H)

        # 小图不放大
        small = tile_size(40, 30)
        self.assertEqual(small.width(), image_preview.TILE_MIN_W)

    def test_tile_size_uses_preset_bounds(self) -> None:
        big = tile_size(1344, 709, 288, 190)
        self.assertEqual(big.width(), 288)
        self.assertLessEqual(big.height(), 190)

    # ── 解码 ──────────────────────────────────
    def test_load_pixmap_scales_down(self) -> None:
        path = self._png("big.png", 800, 600)
        thumb = load_pixmap(ImageSource(path=path), 132, 100)
        self.assertIsNotNone(thumb)
        self.assertLessEqual(thumb.width(), 132)
        self.assertLessEqual(thumb.height(), 100)

        full = load_pixmap(ImageSource(path=path), 0, 0)
        self.assertEqual(full.size(), QSize(800, 600))

    def test_load_pixmap_caches(self) -> None:
        path = self._png("cache.png", 200, 160)
        first = load_pixmap(ImageSource(path=path), 132, 100)
        second = load_pixmap(ImageSource(path=path), 132, 100)
        self.assertIs(first, second)

    # ── 缩略图控件 ────────────────────────────
    def test_image_tile_from_path(self) -> None:
        path = self._png("tile.png", 800, 600)
        tile = ImageTile(ImageSource(name="tile.png", path=path))
        try:
            box = tile.image_box
            self.assertLessEqual(box.width(), image_preview.TILE_MAX_W)
            self.assertLessEqual(box.height(), image_preview.TILE_MAX_H)
            self.assertEqual(tile.width(), box.width())
            self.assertEqual(
                tile.height(), box.height() + image_preview._CAPTION_H + 3
            )
            self.assertIn("tile.png", tile.toolTip())
            self.assertIn("点击查看原图", tile.toolTip())
        finally:
            tile.deleteLater()

    def test_image_tile_align_height(self) -> None:
        path = self._png("short.png", 830, 42)
        tile = ImageTile(ImageSource(name="short.png", path=path))
        try:
            before = tile.image_box.height()
            tile.align_height(100)
            self.assertEqual(tile.image_box.height(), 100)
            self.assertEqual(tile.height(), 100 + image_preview._CAPTION_H + 3)
            tile.align_height(60)  # 只放大不缩小
            self.assertEqual(tile.image_box.height(), 100)
            self.assertGreater(before, 0)
        finally:
            tile.deleteLater()

    def test_image_tile_without_data(self) -> None:
        tile = ImageTile(ImageSource(name="missing.png", path="/nope/missing.png"))
        try:
            self.assertEqual(tile.width(), image_preview.TILE_MAX_W)
            self.assertIn("缩略图不可用", tile.toolTip())
        finally:
            tile.deleteLater()

    # ── 消息气泡 ──────────────────────────────
    def test_chat_message_shows_tile_and_chip(self) -> None:
        from app.ui.widgets.chat_message import ChatMessage, _AttachmentChip

        path = self._png("bubble.png", 640, 480)
        message = ChatMessage(role="user", content="看看这张图")
        try:
            message.set_attachments([
                {"name": "bubble.png", "kind": "image", "path": path},
                {"name": "notes.docx", "chars": 1200},
            ])
            tiles = message.findChildren(ImageTile)
            chips = message.findChildren(_AttachmentChip)
            self.assertEqual(len(tiles), 1)
            self.assertEqual(len(chips), 1)
            self.assertEqual(len(message._attachment_details), 2)
        finally:
            message.deleteLater()

    def test_chat_message_renders_blob_backed_image(self) -> None:
        """历史消息走 blob_id + reader（不碰本地路径）。"""
        from app.ui.widgets.chat_message import ChatMessage

        path = self._png("blob.png", 300, 200)
        with open(path, "rb") as handle:
            payload = handle.read()
        image_preview.set_blob_reader(
            lambda blob_id: payload if blob_id == "blob123" else None
        )
        message = ChatMessage(role="user", content="历史消息")
        try:
            message.set_attachments([{
                "name": "blob.png",
                "kind": "image",
                "blob_id": "blob123",
                "width": 300,
                "height": 200,
            }])
            tiles = message.findChildren(ImageTile)
            self.assertEqual(len(tiles), 1)
            self.assertTrue(tiles[0].has_preview)
            # 单图走大预览档（288×190），300×200 按比例落成 285×190
            self.assertLessEqual(tiles[0].image_box.width(), 288)
            self.assertGreater(tiles[0].image_box.height(), image_preview.TILE_MAX_H)
        finally:
            message.deleteLater()

    def test_chat_message_single_image_gets_bigger_tile(self) -> None:
        from app.ui.widgets.chat_message import ChatMessage

        path = self._png("solo.png", 1344, 709)
        message = ChatMessage(role="user", content="一张")
        try:
            message.set_attachments([{"name": "solo.png", "kind": "image", "path": path}])
            tile = message.findChildren(ImageTile)[0]
            self.assertLessEqual(tile.image_box.width(), 288)
            self.assertGreater(tile.image_box.width(), image_preview.TILE_MAX_W)
        finally:
            message.deleteLater()

    def test_chat_message_skips_unnamed_attachments(self) -> None:
        from app.ui.widgets.chat_message import ChatMessage

        message = ChatMessage(role="user", content="x")
        try:
            message.set_attachments([{"name": ""}, {"kind": "image"}])
            self.assertEqual(message.findChildren(ImageTile), [])
            self.assertEqual(message._attachment_details, [])
        finally:
            message.deleteLater()

    # ── 查看器 ────────────────────────────────
    def test_viewer_zoom_and_fit(self) -> None:
        path = self._png("view.png", 1000, 500)
        source = ImageSource(name="view.png", path=path)
        pixmap = load_pixmap(source, 0, 0)
        viewer = ImageViewer(source, pixmap)
        try:
            viewer.resize(600, 400)
            viewer._canvas.resize(600, 300)
            viewer._on_fit()
            self.assertTrue(viewer._canvas.is_fit())
            self.assertLessEqual(viewer._canvas.zoom(), 1.0)

            viewer._on_actual()
            self.assertAlmostEqual(viewer._canvas.zoom(), 1.0)
            self.assertFalse(viewer._canvas.is_fit())

            viewer._canvas.zoom_by(2.0)
            self.assertAlmostEqual(viewer._canvas.zoom(), 2.0)
            self.assertEqual(viewer._zoom_label.text(), "200%")

            viewer._canvas.zoom_by(0.0001)
            self.assertAlmostEqual(
                viewer._canvas.zoom(), image_preview._Canvas.MIN_ZOOM
            )
        finally:
            viewer.deleteLater()

    def test_viewer_without_pixmap_does_not_crash(self) -> None:
        source = ImageSource(name="lost.png", blob_id="deadbeef")
        image_preview.set_blob_reader(lambda _bid: None)
        viewer = ImageViewer(source, None)
        try:
            viewer.resize(400, 300)
            viewer.grab()  # 触发一次绘制：应画出"数据不可用"而不是崩
            self.assertIsNone(viewer._canvas.pixmap())
        finally:
            viewer.deleteLater()


if __name__ == "__main__":
    unittest.main()
