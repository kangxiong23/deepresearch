# Layer: UI (PySide6) → widgets
# File: app/ui/widgets/search_popup.py
# Responsibility: Search result popup — 显示在搜索框下方，
#                 展示可滚动的关键词搜索结果列表。
# Input:  SearchResultVM 列表
# Output: result_clicked / dismissed 信号

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QScrollArea,
    QWidget,
    QLabel,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from app.controllers.view_models import SearchResultVM
from app.ui.theme import Colors, Fonts, Spacing, Radius

# 角色图标（与 tree_panel.py 保持一致）
_ROLE_ICONS = {
    "user": "👤",
    "assistant": "🤖",
    "thinking": "🧠",
    "system": "⚙️",
}


class SearchPopup(QFrame):
    """搜索结果显示弹窗（下拉式）。"""

    result_clicked = Signal(object)  # 携带 SearchResultVM
    dismissed = Signal()             # 弹窗关闭时发出

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("searchPopup")
        self.setWindowFlags(
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedHeight(320)

        # ── 整体样式 ──────────────────────────────
        self.setStyleSheet(f"""
            SearchPopup {{
                background-color: {Colors.BG_ELEVATED};
                border: 1px solid {Colors.BORDER};
                border-radius: {Radius.MD}px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 滚动区域 ──────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
        """)

        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._container_layout.setSpacing(0)
        self._scroll.setWidget(self._container)

        layout.addWidget(self._scroll)

    # ── 公开接口 ──────────────────────────────────

    def show_results(
        self, results: list[SearchResultVM], position_global
    ) -> None:
        """在给定位置展示搜索结果。"""
        self._rebuild_results(results)
        self.move(position_global)
        self.show()

    # ── 内部构建 ──────────────────────────────────

    def _rebuild_results(self, results: list[SearchResultVM]) -> None:
        """清除旧结果并构建新行。"""
        # 清除已有条目
        while self._container_layout.count():
            child = self._container_layout.takeAt(0)
            w = child.widget()
            if w is not None:
                w.deleteLater()

        if not results:
            empty = QLabel("  无匹配结果")
            empty.setFont(Fonts.body(Fonts.SIZE_SM))
            empty.setStyleSheet(f"""
                QLabel {{
                    color: {Colors.TEXT_DISABLED};
                    padding: {Spacing.MD}px;
                    background: transparent;
                    border: none;
                }}
            """)
            self._container_layout.addWidget(empty)
            self._container_layout.addStretch()
            return

        for r in results:
            row = self._make_result_row(r)
            self._container_layout.addWidget(row)

        self._container_layout.addStretch()

    def _make_result_row(self, result: SearchResultVM) -> QFrame:
        """创建单行可点击结果。"""
        row = QFrame()
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setStyleSheet(f"""
            QFrame {{
                background-color: transparent;
                border-bottom: 1px solid {Colors.DIVIDER};
            }}
            QFrame:hover {{
                background-color: {Colors.BG_OVERLAY};
            }}
        """)

        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(
            Spacing.MD, Spacing.SM, Spacing.MD, Spacing.SM
        )
        row_layout.setSpacing(Spacing.SM)

        # 角色图标
        icon_text = _ROLE_ICONS.get(result.role, "💬")
        icon = QLabel(icon_text)
        icon.setFixedWidth(20)
        icon.setStyleSheet(
            "QLabel { background: transparent; border: none; }"
        )

        # 文本列：路径 + 片段
        path_label = QLabel(result.tree_path)
        path_label.setFont(Fonts.mono(Fonts.SIZE_XS))
        path_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_DISABLED};
                background: transparent;
                border: none;
            }}
        """)

        snippet_label = QLabel(result.snippet)
        snippet_label.setFont(Fonts.body(Fonts.SIZE_SM))
        snippet_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_PRIMARY};
                background: transparent;
                border: none;
            }}
        """)
        snippet_label.setWordWrap(False)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        text_col.addWidget(path_label)
        text_col.addWidget(snippet_label)

        # 时间标签
        time_label = QLabel(result.created_at)
        time_label.setFont(Fonts.mono(Fonts.SIZE_XS))
        time_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_DISABLED};
                background: transparent;
                border: none;
            }}
        """)

        row_layout.addWidget(icon)
        row_layout.addLayout(text_col, stretch=1)
        row_layout.addWidget(time_label)

        # 点击事件
        row.mousePressEvent = lambda e, r=result: self._on_result_clicked(r)

        return row

    # ── 事件处理 ──────────────────────────────────

    def _on_result_clicked(self, result: SearchResultVM) -> None:
        """点击搜索结果 → 发出信号并关闭弹窗。"""
        self.result_clicked.emit(result)
        self.hide()

    def keyPressEvent(self, event) -> None:
        """Escape 关闭弹窗。"""
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            self.dismissed.emit()
        super().keyPressEvent(event)
