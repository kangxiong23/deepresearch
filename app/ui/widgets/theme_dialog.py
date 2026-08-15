# Layer: UI (PySide6) → widgets
# File: app/ui/widgets/theme_dialog.py
# Responsibility: 主题风格选择器 — 以卡片网格预览并一键切换应用主题。
#                 点击卡片立即应用并持久化（类似壁纸软件的一键换肤）。
# 扩展主题：只需在 app/ui/theme.py 的 THEMES 中新增条目，本对话框自动展示。

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QScrollArea,
    QLabel,
    QFrame,
    QPushButton,
    QSizePolicy,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from app.ui.theme import (
    Colors,
    Fonts,
    Spacing,
    Radius,
    Theme,
    theme_order,
    get_theme_manager,
    apply_style,
)


class _ThemeCard(QFrame):
    """单个主题预览卡片。"""

    def __init__(
        self,
        theme: Theme,
        active: bool,
        on_select,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._active = active
        self._on_select = on_select

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(172, 112)

        # ── 顶部色板（4 个水平色块）──
        preview = QWidget()
        preview.setFixedHeight(48)
        preview_layout = QHBoxLayout(preview)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(0)
        for hex_color in theme.preview:
            swatch = QLabel()
            swatch.setFixedHeight(48)
            swatch.setStyleSheet(
                f"background-color: {hex_color}; border: none;"
            )
            swatch.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            preview_layout.addWidget(swatch)

        # ── 名称 / 说明 ──
        name_label = QLabel(theme.name)
        name_label.setFont(Fonts.body(Fonts.SIZE_MD, QFont.Weight.Bold))
        apply_style(name_label, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_PRIMARY};
                background: transparent;
                border: none;
            }}
        """)

        desc_label = QLabel(theme.description)
        desc_label.setFont(Fonts.body(Fonts.SIZE_XS))
        apply_style(desc_label, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_SECONDARY};
                background: transparent;
                border: none;
            }}
        """)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        text_layout.addWidget(name_label)
        text_layout.addWidget(desc_label)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(10, 8, 10, 10)
        body_layout.setSpacing(8)
        body_layout.addWidget(preview)
        body_layout.addLayout(text_layout)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(body)

        self._apply_style()

    def _apply_style(self) -> None:
        border = f"2px solid {Colors.PRIMARY}" if self._active else f"1px solid {Colors.BORDER}"
        self.setStyleSheet(f"""
            _ThemeCard {{
                background-color: {Colors.BG_SURFACE};
                border: {border};
                border-radius: {Radius.LG}px;
            }}
            _ThemeCard:hover {{
                border: 2px solid {Colors.PRIMARY_DIM};
            }}
        """)

    def refresh_theme(self) -> None:
        self._apply_style()

    def set_active(self, active: bool) -> None:
        self._active = active
        self._apply_style()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_select(self._theme.id)
        super().mousePressEvent(event)


class ThemeDialog(QDialog):
    """主题风格选择对话框。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("主题风格")
        self.setModal(True)
        self.setMinimumWidth(420)

        self._manager = get_theme_manager()
        self._cards: dict[str, _ThemeCard] = {}

        # ── 标题 ──
        title = QLabel("主题风格")
        title.setFont(Fonts.mono(Fonts.SIZE_XL, QFont.Weight.DemiBold))
        apply_style(title, lambda: f"color: {Colors.TEXT_PRIMARY}; background: transparent; border: none;")

        subtitle = QLabel("点击任意主题即可立即切换并保存")
        subtitle.setFont(Fonts.body(Fonts.SIZE_SM))
        apply_style(subtitle, lambda: f"color: {Colors.TEXT_SECONDARY}; background: transparent; border: none;")

        # ── 卡片网格 ──
        grid_container = QWidget()
        grid = QGridLayout(grid_container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(Spacing.MD)

        themes = theme_order()
        columns = 2
        for i, theme in enumerate(themes):
            active = theme.id == self._manager.current_id
            card = _ThemeCard(theme, active, self._on_select)
            self._cards[theme.id] = card
            grid.addWidget(card, i // columns, i % columns)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(grid_container)
        apply_style(scroll, lambda: f"""
            QScrollArea {{
                background: transparent;
                border: none;
            }}
        """)

        # ── 关闭按钮 ──
        close_btn = QPushButton("关闭")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setFixedHeight(36)
        close_btn.setFont(Fonts.body(Fonts.SIZE_MD, QFont.Weight.Medium))
        apply_style(close_btn, lambda: f"""
            QPushButton {{
                color: {Colors.TEXT_PRIMARY};
                background-color: {Colors.BG_ELEVATED};
                border: 1px solid {Colors.BORDER};
                border-radius: {Radius.MD}px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BG_OVERLAY};
                border-color: {Colors.PRIMARY};
            }}
        """)
        close_btn.clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        layout.setSpacing(Spacing.MD)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(scroll, stretch=1)
        layout.addWidget(close_btn)

        apply_style(self, lambda: f"""
            ThemeDialog {{
                background-color: {Colors.BG_BASE};
            }}
        """)

    def _on_select(self, theme_id: str) -> None:
        if self._manager.apply(theme_id, save=True):
            for tid, card in self._cards.items():
                card.set_active(tid == theme_id)
