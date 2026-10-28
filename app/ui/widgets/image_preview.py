# Layer: UI (PySide6) → widgets
# File: app/ui/widgets/image_preview.py
# Responsibility: 图片附件的展示层 —— 气泡内的缩略图、点击放大的查看器。
# Input:  blob_id（已落库的附件）或本地路径（尚未发送的待发附件）
# Output: ImageTile（缩略图控件）/ ImageViewer（模态查看器）
# 禁止: 解析、缩放、落库、发送逻辑；读字节一律经 set_blob_reader 注入的 Core 访问器。

from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QIODevice,
    QPointF,
    QRectF,
    QSize,
    QSizeF,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImageReader,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ui.theme import apply_style, Colors, Fonts, Radius, Spacing


# 只用来决定"要不要走图片预览"——真正的格式判定仍在 Core（按魔数）。
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# 缩略图外框上限（气泡里的图不能比正文还嚣张）
# 图片少的时候给大一点：单图 288×190，两张 200×140，三张以上 132×100
TILE_MAX_W = 132
TILE_MAX_H = 100
TILE_SIZE_PRESETS = {1: (288, 190), 2: (200, 140)}
# 细长图（830×42 的行内公式）按比例缩完只有几像素高，给个下限保证可点
TILE_MIN_H = 40
TILE_MIN_W = 92
_CAPTION_H = 15
_CACHE_LIMIT = 64


# ──────────────────────────────────────────────
# 字节来源
# ──────────────────────────────────────────────

_blob_reader: Callable[[str], bytes | None] | None = None

# (来源, 宽, 高) → 已解码缩略图。滚动/重建会反复要同一张图，
# 缓存住可避免每次重绘都重新解码（一张 4 MB JPEG 解码约 30 ms）。
_CACHE: "OrderedDict[tuple, QPixmap]" = OrderedDict()


def set_blob_reader(reader: Callable[[str], bytes | None] | None) -> None:
    """注入按 blob_id 取字节的访问器（由 ChatApp 在启动时接上 Controller）。"""
    global _blob_reader
    _blob_reader = reader


def _read_blob(blob_id: str) -> bytes | None:
    if _blob_reader is None or not blob_id:
        return None
    try:
        return _blob_reader(blob_id)
    except Exception as e:  # 预览失败绝不能影响聊天主流程
        print(f"[UI] 读取图片附件字节失败: {e}")
        return None


def is_image_path(path: str) -> bool:
    """按扩展名粗判（仅用于"发送中的附件先画个缩略图"）。"""
    return os.path.splitext(str(path or ""))[1].lower() in _IMAGE_EXTS


@dataclass(frozen=True)
class ImageSource:
    """一张待显示的图：可能来自快照仓库（blob_id），也可能来自本地待发文件。"""

    name: str = ""
    blob_id: str = ""
    path: str = ""
    size: int = 0
    width: int = 0
    height: int = 0

    @classmethod
    def of(cls, attachment: dict | None) -> "ImageSource":
        att = attachment or {}
        return cls(
            name=str(att.get("name") or ""),
            blob_id=str(att.get("blob_id") or ""),
            path=str(att.get("path") or ""),
            size=_to_int(att.get("size")),
            width=_to_int(att.get("width")),
            height=_to_int(att.get("height")),
        )

    def key(self) -> str:
        """缓存键：快照优先（内容寻址，天然稳定），否则用本地路径。"""
        return self.blob_id or self.path

    def exists(self) -> bool:
        if self.blob_id:
            return True  # 快照是否存在只有读一次才知道，交给 read()
        return bool(self.path) and os.path.isfile(self.path)

    def read(self) -> bytes | None:
        data = _read_blob(self.blob_id)
        if data is not None:
            return data
        if not self.path:
            return None
        try:
            with open(self.path, "rb") as handle:
                return handle.read()
        except OSError as e:
            print(f"[UI] 读取本地图片失败: {e}")
            return None


# ──────────────────────────────────────────────
# 解码
# ──────────────────────────────────────────────

def _device_ratio() -> float:
    """屏幕缩放比：1.5 的屏上按 1 倍解码，缩略图会糊。"""
    app = QGuiApplication.instance()
    screen = app.primaryScreen() if app is not None else None
    try:
        return max(1.0, float(screen.devicePixelRatio())) if screen else 1.0
    except (AttributeError, TypeError, ValueError):
        return 1.0


def load_pixmap(source: ImageSource, max_w: int, max_h: int) -> QPixmap | None:
    """按目标尺寸解码。max_w/max_h 传 0 表示按原尺寸解码（查看大图用）。"""
    if not source.key():
        return None
    ratio = _device_ratio() if max_w > 0 and max_h > 0 else 1.0
    # 原图（0/0）不进缓存：一张 4000×3000 的照片解出来就是 48 MB，
    # 缓存住会把内存吃光，而查看器本来就只开一个
    cacheable = max_w > 0 and max_h > 0
    key = (source.key(), int(max_w), int(max_h))
    if cacheable:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached

    pix = _decode(source, int(max_w * ratio), int(max_h * ratio))
    if pix is None:
        return None
    if ratio > 1.0:
        pix.setDevicePixelRatio(ratio)
    if cacheable:
        _CACHE[key] = pix
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)
    return pix


def _decode(source: ImageSource, max_w: int, max_h: int) -> QPixmap | None:
    data = source.read()
    if not data:
        return None
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
        return None
    try:
        reader = QImageReader(buffer)
        reader.setAutoTransform(True)  # 手机照片的 EXIF 旋转
        size = reader.size()
        if (
            max_w > 0
            and max_h > 0
            and size.isValid()
            and size.width() > 0
            and size.height() > 0
            and (size.width() > max_w or size.height() > max_h)
        ):
            # 让解码器直接出小图，避免先把 4000×3000 全解出来再缩
            reader.setScaledSize(
                size.scaled(QSize(max_w, max_h), Qt.AspectRatioMode.KeepAspectRatio)
            )
        image = reader.read()
        if image.isNull():
            return None
        return QPixmap.fromImage(image)
    finally:
        buffer.close()


def _logical_size(pix: QPixmap | None) -> QSize:
    if pix is None or pix.isNull():
        return QSize(0, 0)
    size = pix.deviceIndependentSize()
    return QSize(max(1, round(size.width())), max(1, round(size.height())))


def tile_size(
    width: int, height: int, max_w: int = TILE_MAX_W, max_h: int = TILE_MAX_H
) -> QSize:
    """缩略图显示框：按比例缩进 max_w×max_h，细条给 40px 下限保证可点。"""
    if width <= 0 or height <= 0:
        return QSize(max_w, 72)
    scale = min(max_w / float(width), max_h / float(height), 1.0)
    box_w = min(max(TILE_MIN_W, round(width * scale)), max_w)
    box_h = min(max(TILE_MIN_H, round(height * scale)), max_h)
    return QSize(box_w, box_h)


def _fit_rect(pix: QPixmap, area: QRectF) -> QRectF:
    """等比放进 area 并居中（不变形、不裁切）。"""
    size = _logical_size(pix)
    pw, ph = float(size.width()), float(size.height())
    if pw <= 0 or ph <= 0 or area.width() <= 0 or area.height() <= 0:
        return area
    scale = min(area.width() / pw, area.height() / ph)
    w, h = pw * scale, ph * scale
    return QRectF(
        area.x() + (area.width() - w) / 2.0,
        area.y() + (area.height() - h) / 2.0,
        w,
        h,
    )


# ──────────────────────────────────────────────
# 缩略图
# ──────────────────────────────────────────────

class ImageTile(QFrame):
    """气泡里的一张缩略图：图 + 一行文件名，点开看原图。"""

    clicked = Signal()

    def __init__(
        self,
        source: ImageSource,
        parent: QWidget | None = None,
        max_w: int = TILE_MAX_W,
        max_h: int = TILE_MAX_H,
    ) -> None:
        super().__init__(parent)
        self._source = source
        self._pix = load_pixmap(source, max_w, max_h)
        size = _logical_size(self._pix)
        self._box = (
            tile_size(size.width(), size.height(), max_w, max_h)
            if size.width() > 0 and size.height() > 0
            else QSize(max_w, 72)
        )
        self._hover = False
        self.setFixedSize(self._box.width(), self._box.height() + _CAPTION_H + 3)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setToolTip(self._tooltip())

    # ── 查询（测试用）──────────────────────────
    @property
    def source(self) -> ImageSource:
        return self._source

    @property
    def image_box(self) -> QSize:
        return QSize(self._box)

    @property
    def has_preview(self) -> bool:
        """缩略图是否真的解出来了（测试与降级提示用）。"""
        return self._pix is not None and not self._pix.isNull()

    def align_height(self, height: int) -> None:
        """同一行的缩略图统一高度（图在框内垂直居中），避免参差不齐。"""
        height = int(height)
        if height <= self._box.height():
            return
        self._box.setHeight(height)
        self.setFixedSize(self._box.width(), self._box.height() + _CAPTION_H + 3)
        self.update()

    # ── 绘制 ──────────────────────────────────
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        area = QRectF(0.0, 0.0, float(self.width()), float(self._box.height()))
        path = QPainterPath()
        path.addRoundedRect(area, 6.0, 6.0)
        # 卡片底色比气泡略亮一点：同一行里长短图打平时的留白才不显成"黑洞"
        painter.fillPath(path, QColor(Colors.BG_OVERLAY))

        if self._pix is not None and not self._pix.isNull():
            painter.save()
            painter.setClipPath(path)
            painter.drawPixmap(
                _fit_rect(self._pix, area), self._pix, QRectF(self._pix.rect())
            )
            painter.restore()
        else:
            painter.setPen(QColor(Colors.TEXT_SECONDARY))
            painter.setFont(Fonts.body(Fonts.SIZE_LG))
            painter.drawText(
                area,
                Qt.AlignmentFlag.AlignCenter,
                "🖼 无法预览",
            )

        if self._hover:
            painter.save()
            painter.setClipPath(path)
            painter.fillPath(path, QColor(0, 0, 0, 120))
            painter.setPen(QColor(Colors.TEXT_PRIMARY))
            painter.setFont(Fonts.body(Fonts.SIZE_XS))
            painter.drawText(
                area,
                Qt.AlignmentFlag.AlignCenter,
                "🔍 查看原图",
            )
            painter.restore()

        painter.setPen(QColor(Colors.BORDER))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        painter.setPen(QColor(Colors.TEXT_SECONDARY))
        painter.setFont(Fonts.body(Fonts.SIZE_XS))
        caption = QRectF(
            0.0, float(self._box.height()) + 3.0, float(self.width()), float(_CAPTION_H)
        )
        text = painter.fontMetrics().elidedText(
            self._source.name or "图片",
            Qt.TextElideMode.ElideMiddle,
            int(self.width()),
        )
        painter.drawText(
            caption,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            text,
        )

    # ── 交互 ──────────────────────────────────
    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
            show_image(self._source, self)
            return
        super().mouseReleaseEvent(event)

    def refresh_theme(self) -> None:
        self.update()

    def _tooltip(self) -> str:
        source = self._source
        lines = [source.name or "图片"]
        if source.width > 0 and source.height > 0:
            lines.append(f"{source.width}×{source.height}")
        else:
            size = _logical_size(self._pix)
            if size.width() > 0:
                lines.append(f"{size.width()}×{size.height()}")
        human = _format_bytes(source.size)
        if human:
            lines.append(human)
        if self._pix is None or self._pix.isNull():
            lines.append("缩略图不可用，点击仍可尝试查看")
        lines.append("点击查看原图（Esc 关闭）")
        return "\n".join(lines)


class MiniThumb(QLabel):
    """附件条上的小方图（发送前就能看到选了什么图）。"""

    def __init__(
        self, source: ImageSource, side: int = 22, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._source = source
        self.setFixedSize(side, side)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{source.name or '图片'}\n点击查看原图")
        pix = load_pixmap(source, side, side)
        if pix is None or pix.isNull():
            self.setText("🖼")
            self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            self.setPixmap(pix)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            show_image(self._source, self)
            return
        super().mouseReleaseEvent(event)


# ──────────────────────────────────────────────
# 查看大图
# ──────────────────────────────────────────────

class ImageViewer(QDialog):
    """原图查看器：滚轮缩放、拖拽平移、双击在"适应窗口/原始大小"间切换。"""

    def __init__(
        self, source: ImageSource, pixmap: QPixmap | None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._source = source
        self.setWindowTitle(source.name or "图片预览")
        self.setModal(True)
        self.resize(self._initial_size(pixmap))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_toolbar(pixmap))

        self._canvas = _Canvas(pixmap)
        self._canvas.zoom_changed.connect(self._on_zoom)
        outer.addWidget(self._canvas, 1)

        self._wire_shortcuts()

    # ── 组成 ──────────────────────────────────
    def _build_toolbar(self, pixmap: QPixmap | None) -> QWidget:
        bar = QWidget()
        apply_style(bar, lambda: f"""
            QWidget {{
                background-color: {Colors.BG_SURFACE};
                border-bottom: 1px solid {Colors.BORDER};
            }}
        """)
        row = QHBoxLayout(bar)
        row.setContentsMargins(Spacing.MD, Spacing.XS, Spacing.SM, Spacing.XS)
        row.setSpacing(Spacing.XS)

        title = QLabel(self._source.name or "图片")
        title.setFont(Fonts.body(Fonts.SIZE_SM))
        apply_style(title, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_PRIMARY};
                background-color: transparent;
                border: none;
            }}
        """)
        row.addWidget(title)

        size = _logical_size(pixmap)
        detail = ""
        if size.width() > 0:
            detail = f"{size.width()}×{size.height()}"
        human = _format_bytes(self._source.size)
        if human:
            detail = (detail + " · " if detail else "") + human
        if detail:
            meta = QLabel(detail)
            meta.setFont(Fonts.body(Fonts.SIZE_XS))
            apply_style(meta, lambda: f"""
                QLabel {{
                    color: {Colors.TEXT_SECONDARY};
                    background-color: transparent;
                    border: none;
                }}
            """)
            row.addWidget(meta)

        row.addStretch()

        self._zoom_label = QLabel("100%")
        self._zoom_label.setFont(Fonts.body(Fonts.SIZE_XS))
        self._zoom_label.setMinimumWidth(44)
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        apply_style(self._zoom_label, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_SECONDARY};
                background-color: transparent;
                border: none;
            }}
        """)
        row.addWidget(self._zoom_label)

        row.addWidget(self._tool_button("适应窗口", "铺满窗口（Ctrl+0）", self._on_fit))
        row.addWidget(self._tool_button("100%", "原始大小（Ctrl+1）", self._on_actual))
        row.addWidget(self._tool_button("－", "缩小（-）", lambda: self._canvas.zoom_by(1 / 1.25)))
        row.addWidget(self._tool_button("＋", "放大（+）", lambda: self._canvas.zoom_by(1.25)))
        row.addWidget(self._tool_button("保存…", "另存为原图（Ctrl+S）", self._on_save))
        row.addWidget(self._tool_button("关闭", "关闭（Esc）", self.close))
        return bar

    def _tool_button(self, text: str, tip: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setToolTip(tip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFont(Fonts.body(Fonts.SIZE_XS))
        apply_style(button, lambda: f"""
            QPushButton {{
                color: {Colors.TEXT_PRIMARY};
                background-color: {Colors.BG_OVERLAY};
                border: 1px solid {Colors.BORDER};
                border-radius: {Radius.SM}px;
                padding: 3px 10px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BORDER};
                border-color: {Colors.BORDER_FOCUS};
            }}
        """)
        button.clicked.connect(slot)
        return button

    def _wire_shortcuts(self) -> None:
        for keys, slot in (
            (("Ctrl+0", "F"), self._on_fit),
            (("Ctrl+1",), self._on_actual),
            (("+", "="), lambda: self._canvas.zoom_by(1.25)),
            (("-",), lambda: self._canvas.zoom_by(1 / 1.25)),
            (("Ctrl+S",), self._on_save),
        ):
            for key in keys:
                QShortcut(QKeySequence(key), self).activated.connect(slot)

    # ── 行为 ──────────────────────────────────
    def _on_fit(self) -> None:
        self._canvas.fit()

    def _on_actual(self) -> None:
        self._canvas.actual_size()

    def _on_zoom(self, zoom: float) -> None:
        self._zoom_label.setText(f"{int(round(zoom * 100))}%")

    def _on_save(self) -> None:
        default = self._source.name or "image.png"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "保存图片",
            default,
            "图片 (*.png *.jpg *.jpeg *.gif *.webp);;所有文件 (*.*)",
        )
        if not path:
            return
        canvas_pix = self._canvas.pixmap()
        try:
            data = self._source.read()
            if data:
                with open(path, "wb") as handle:
                    handle.write(data)
            elif canvas_pix is not None and not canvas_pix.isNull():
                canvas_pix.toImage().save(path)
        except OSError as e:
            QMessageBox.warning(self, "保存失败", f"无法写入 {path}：{e.strerror or e}")

    def _initial_size(self, pixmap: QPixmap | None) -> QSize:
        app = QGuiApplication.instance()
        screen = app.primaryScreen() if app is not None else None
        avail_w = screen.availableGeometry().width() if screen else 1280
        avail_h = screen.availableGeometry().height() if screen else 800
        max_w = min(1400, int(avail_w * 0.85))
        max_h = min(1000, int(avail_h * 0.85))
        size = _logical_size(pixmap)
        if size.width() <= 0:
            return QSize(min(760, max_w), min(560, max_h))
        return QSize(
            max(480, min(size.width() + 80, max_w)),
            max(360, min(size.height() + 140, max_h)),
        )


class _Canvas(QWidget):
    """纯绘制画布：不建子控件，缩放/平移只改两个数再重画。"""

    zoom_changed = Signal(float)

    MIN_ZOOM = 0.05
    MAX_ZOOM = 16.0

    def __init__(self, pixmap: QPixmap | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pix = pixmap
        self._zoom = 1.0
        self._origin = QPointF(0.0, 0.0)
        self._fit_mode = True
        self._dragging = False
        self._drag_from = QPointF()
        self._drag_origin = QPointF()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(240, 160)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    # ── 查询 ──────────────────────────────────
    def pixmap(self) -> QPixmap | None:
        return self._pix

    def zoom(self) -> float:
        return self._zoom

    def is_fit(self) -> bool:
        return self._fit_mode

    # ── 缩放 ──────────────────────────────────
    def fit(self) -> None:
        pw, ph = self._image_size()
        vw, vh = float(self.width()), float(self.height())
        if pw <= 0 or ph <= 0 or vw <= 0 or vh <= 0:
            return
        self._fit_mode = True
        # 小图不放大（100% 就够，放大会糊）
        self._apply_zoom(min(1.0, vw / pw, vh / ph), None)

    def actual_size(self) -> None:
        self._fit_mode = False
        self._apply_zoom(1.0, None)

    def zoom_by(self, factor: float) -> None:
        self._fit_mode = False
        self._apply_zoom(self._zoom * factor, None)

    def _apply_zoom(self, zoom: float, anchor: QPointF | None) -> None:
        zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, float(zoom)))
        if anchor is None:
            anchor = QPointF(self.width() / 2.0, self.height() / 2.0)
        image_point = self._to_image(anchor)
        self._zoom = zoom
        self._origin = QPointF(
            anchor.x() - image_point.x() * zoom,
            anchor.y() - image_point.y() * zoom,
        )
        self._clamp()
        self.update()
        self.zoom_changed.emit(self._zoom)

    # ── 坐标 ──────────────────────────────────
    def _image_size(self) -> tuple[float, float]:
        size = _logical_size(self._pix)
        return float(size.width()), float(size.height())

    def _scaled_size(self) -> tuple[float, float]:
        pw, ph = self._image_size()
        return pw * self._zoom, ph * self._zoom

    def _to_image(self, point: QPointF) -> QPointF:
        if self._zoom <= 0:
            return QPointF()
        return QPointF(
            (point.x() - self._origin.x()) / self._zoom,
            (point.y() - self._origin.y()) / self._zoom,
        )

    def _clamp(self) -> None:
        """比窗口小的方向居中，比窗口大的方向不留白边。"""
        sw, sh = self._scaled_size()
        vw, vh = float(self.width()), float(self.height())
        if sw <= vw:
            self._origin.setX((vw - sw) / 2.0)
        else:
            self._origin.setX(min(0.0, max(vw - sw, self._origin.x())))
        if sh <= vh:
            self._origin.setY((vh - sh) / 2.0)
        else:
            self._origin.setY(min(0.0, max(vh - sh, self._origin.y())))

    # ── 绘制 ──────────────────────────────────
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(Colors.BG_BASE))
        if self._pix is None or self._pix.isNull():
            painter.setPen(QColor(Colors.TEXT_SECONDARY))
            painter.setFont(Fonts.body(Fonts.SIZE_MD))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "图片数据不可用（附件可能已被清理）",
            )
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        sw, sh = self._scaled_size()
        target = QRectF(self._origin, QSizeF(sw, sh))
        painter.drawPixmap(target, self._pix, QRectF(self._pix.rect()))

    # ── 事件 ──────────────────────────────────
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit_mode:
            self.fit()
        else:
            self._clamp()
            self.update()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return
        self._fit_mode = False
        self._apply_zoom(self._zoom * (1.15 ** (delta / 120.0)), event.position())
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_from = event.position()
            self._drag_origin = QPointF(self._origin)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            delta = event.position() - self._drag_from
            self._origin = QPointF(
                self._drag_origin.x() + delta.x(),
                self._drag_origin.y() + delta.y(),
            )
            self._clamp()
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._fit_mode:
                self.actual_size()
            else:
                self.fit()
            return
        super().mouseDoubleClickEvent(event)


def show_image(source: ImageSource, parent: QWidget | None = None) -> ImageViewer:
    """打开原图查看器（模态）。解码失败也照开，画布会说明原因。"""
    pixmap = load_pixmap(source, 0, 0)
    viewer = ImageViewer(source, pixmap, parent)
    viewer.exec()
    return viewer


def _to_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _format_bytes(size: int) -> str:
    try:
        value = float(size or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    if value < 1024:
        return f"{int(value)} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.0f} KB"
    return f"{value / (1024 * 1024):.1f} MB"
