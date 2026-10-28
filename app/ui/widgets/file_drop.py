# Layer: UI (PySide6) → widgets
# File: app/ui/widgets/file_drop.py
# Responsibility: 从资源管理器把文件拖进聊天区，变成消息附件（与点回形针等价）。
# Input:  QDropEvent 里的本地路径（文件或文件夹）
# Output: files_dropped(list[str]) 信号 + 拖拽中的虚线遮罩
# 禁止: 自动发送、解析文件、落库；这里只挑出"能解析的文件路径"交给 InputArea。

from __future__ import annotations

import os
from dataclasses import dataclass, field

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from app.ui.theme import Colors, Fonts, Radius


# 与输入区回形针的文件对话框保持一致（app/ui/widgets/input_area.py）
SUPPORTED_EXTS = (
    ".txt", ".md", ".json", ".docx", ".pdf",
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
)

# 一次拖放最多收几个文件。拖整个文件夹进来的上限，
# 再多就不是"带点资料"而是"喂数据库"了。
MAX_DROP_FILES = 20


def is_supported_path(path: str) -> bool:
    return os.path.splitext(str(path or ""))[1].lower() in SUPPORTED_EXTS


@dataclass
class DropResult:
    """一次拖放的筛选结果。"""

    files: list[str] = field(default_factory=list)
    skipped: int = 0        # 格式不支持 / 路径失效
    truncated: bool = False  # 超过 MAX_DROP_FILES，只收了前面这些


def _walk_supported(root: str, budget: int) -> tuple[list[str], bool]:
    """递归取文件夹里的支持文件；返回 (文件列表, 是否因超额被截断)。"""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # 隐藏目录（.git / .venv）里不会有用户想发的资料
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith(".") or not is_supported_path(name):
                continue
            if len(found) >= budget:
                return found, True
            found.append(os.path.join(dirpath, name))
    return found, False


def collect_files(paths: list[str], limit: int = MAX_DROP_FILES) -> DropResult:
    """
    把拖进来的路径整理成"可以加进附件条"的列表。

    - 文件：格式支持就收，否则记一次 skipped
    - 文件夹：递归取里面的支持文件（拖一整个作业文件夹也能用）
    - 去重：同一个路径只收一次（按绝对路径比较）
    """
    result = DropResult()
    seen: set[str] = set()

    def _add(path: str) -> bool:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            return False
        seen.add(key)
        result.files.append(path)
        return True

    for raw in (paths or []):
        text = str(raw or "").strip()
        if not text:
            continue
        path = os.path.abspath(text)
        if os.path.isdir(path):
            budget = limit - len(result.files)
            if budget <= 0:
                result.truncated = True
                break
            picked, cut = _walk_supported(path, budget)
            for item in picked:
                _add(item)
            if cut or len(result.files) >= limit:
                result.truncated = True
        elif os.path.isfile(path):
            if not is_supported_path(path):
                result.skipped += 1
                continue
            if len(result.files) >= limit:
                result.truncated = True
                continue
            _add(path)
        else:
            result.skipped += 1

    return result


def _has_text(mime) -> bool:
    """纯文字拖拽（没有文件 URL）。"""
    return mime is not None and not mime.hasUrls() and bool(mime.text())


def local_file_paths(mime) -> list[str]:
    """从 QMimeData 里取出本地文件路径（网页拖来的 http URL 不算）。"""
    if mime is None or not mime.hasUrls():
        return []
    paths = []
    for url in mime.urls():
        if url.isLocalFile():
            local = url.toLocalFile()
            if local:
                # toLocalFile() 在 Windows 上给 "D:/a/b.txt"，统一成本地分隔符
                paths.append(os.path.normpath(local))
    return paths


class _DropOverlay(QWidget):
    """拖拽经过聊天区时盖一层虚线提示（不吃鼠标事件，只负责画）。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.hide()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        area = QRectF(self.rect()).adjusted(8.0, 8.0, -8.0, -8.0)
        if area.width() <= 0 or area.height() <= 0:
            return
        path = QPainterPath()
        path.addRoundedRect(area, float(Radius.MD), float(Radius.MD))

        shade = QColor(Colors.BG_BASE)
        shade.setAlpha(150)
        painter.fillPath(path, shade)

        pen = QPen(QColor(Colors.PRIMARY))
        pen.setWidth(2)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawPath(path)

        # 文字压在一张小卡片上：直接画在遮罩上会和底下的空状态/气泡文字叠在一起
        card_w = min(max(area.width() - 48.0, 240.0), 480.0)
        card = QRectF(
            area.center().x() - card_w / 2.0,
            area.center().y() - 44.0,
            card_w,
            88.0,
        )
        card_path = QPainterPath()
        card_path.addRoundedRect(card, float(Radius.MD), float(Radius.MD))
        card_bg = QColor(Colors.BG_SURFACE)
        card_bg.setAlpha(245)
        painter.fillPath(card_path, card_bg)
        card_pen = QPen(QColor(Colors.BORDER))
        card_pen.setWidth(1)
        painter.setPen(card_pen)
        painter.drawPath(card_path)

        painter.setPen(QColor(Colors.TEXT_PRIMARY))
        painter.setFont(Fonts.body(Fonts.SIZE_LG))
        title = QRectF(card.x(), card.y() + 18.0, card.width(), 28.0)
        painter.drawText(title, Qt.AlignmentFlag.AlignCenter, "松开鼠标，添加为附件")

        painter.setPen(QColor(Colors.TEXT_SECONDARY))
        painter.setFont(Fonts.body(Fonts.SIZE_XS))
        hint = QRectF(card.x() + 8.0, card.y() + 52.0, card.width() - 16.0, 20.0)
        painter.drawText(
            hint,
            Qt.AlignmentFlag.AlignCenter,
            "txt / md / json / docx / pdf / png / jpg / gif · 文件夹会自动展开",
        )

    def refresh_theme(self) -> None:
        self.update()


class ChatDropArea(QWidget):
    """
    聊天区的拖放容器：消息列表 + 输入框都在里面，所以拖到哪一片都算数。

    刻意不做成"整个窗口"的拖放：左侧树面板自己吃树节点拖拽（移动节点），
    两边抢同一个落点很容易误操作。
    """

    files_dropped = Signal(list)
    # 纯文字拖拽（选中一段文字拖进输入框）——QTextEdit 关掉了自己的拖放，
    # 这条通路把它原本的行为接回来
    text_dropped = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._overlay = _DropOverlay(self)
        self._drag_payload: list[str] = []

    # ── 拖拽事件 ──────────────────────────────
    def dragEnterEvent(self, event) -> None:
        paths = local_file_paths(event.mimeData())
        if not paths:
            self._drag_payload = []
            self._hide_overlay()
            if _has_text(event.mimeData()):
                # 文字拖拽：接受但不出遮罩（不然满屏虚线太吵）
                event.acceptProposedAction()
                return
            event.ignore()
            return
        self._drag_payload = paths
        event.acceptProposedAction()
        self._show_overlay()

    def dragMoveEvent(self, event) -> None:
        if self._drag_payload or local_file_paths(event.mimeData()):
            event.acceptProposedAction()
            self._show_overlay()
            return
        if _has_text(event.mimeData()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._drag_payload = []
        self._hide_overlay()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        paths = local_file_paths(event.mimeData()) or self._drag_payload
        self._drag_payload = []
        self._hide_overlay()
        if not paths:
            text = event.mimeData().text() if _has_text(event.mimeData()) else ""
            if text:
                event.acceptProposedAction()
                self.text_dropped.emit(text)
                return
            event.ignore()
            return
        event.acceptProposedAction()
        # 交给上层（ChatApp）决定去重与提示
        self.files_dropped.emit(paths)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._overlay.setGeometry(self.rect())

    # ── 遮罩 ──────────────────────────────────
    def _show_overlay(self) -> None:
        self._overlay.setGeometry(self.rect())
        self._overlay.show()
        self._overlay.raise_()

    def _hide_overlay(self) -> None:
        self._overlay.hide()
