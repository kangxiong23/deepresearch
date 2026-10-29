# Layer: UI (PySide6) → widgets
# File: app/ui/widgets/settings_panel.py
# Responsibility: 设置页——侧栏整块切换过来，列出所有设置入口（回收站 / 本地文件 /
#                 上下文管理 / 知识图谱 / 主题风格 / 应用配置）。
#                 只负责列表与导航，具体功能仍由 ChatApp 分发。
# Input:  无（条目是常量表）
# Output: back_requested 信号 + item_clicked(item_id) 信号

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ui.theme import apply_style, Colors, Fonts, Radius, Spacing


# (id, 图标, 标题, 悬停说明)
SETTINGS_ITEMS: tuple[tuple[str, str, str, str], ...] = (
    ("trash", "🗑️", "回收站", "恢复或彻底删除已删除的对话与目录"),
    ("file_access", "📁", "本地文件", "模型可以只读访问的文件夹（全局 / 节点）"),
    ("context", "📚", "上下文管理", "上下文块与模板"),
    ("kg", "🔗", "知识图谱", "对话里抽取出的实体与关系"),
    ("theme", "🎨", "主题风格", "切换配色方案"),
    ("config", "⚙️", "应用配置", "模型、温度、token 预算、搜索与知识注入"),
)


class _SettingsRow(QPushButton):
    """一行导航：图标 + 名称 + 右箭头，整行可点（QPushButton 自带悬停/键盘可达）。"""

    def __init__(self, item_id: str, icon: str, label: str, hint: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item_id = item_id
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(44)
        self.setToolTip(hint)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(Spacing.LG, Spacing.SM, Spacing.MD, Spacing.SM)
        layout.setSpacing(Spacing.SM)

        icon_label = QLabel(icon)
        icon_label.setFont(Fonts.body(14))
        icon_label.setFixedWidth(22)
        icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        apply_style(icon_label, lambda: (
            "QLabel { background-color: transparent; border: none; }"
        ))

        text_label = QLabel(label)
        text_label.setFont(Fonts.body(Fonts.SIZE_SM))
        text_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        apply_style(text_label, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_PRIMARY};
                background-color: transparent;
                border: none;
            }}
        """)

        arrow_label = QLabel("›")
        arrow_label.setFont(Fonts.body(16))
        arrow_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        apply_style(arrow_label, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_DISABLED};
                background-color: transparent;
                border: none;
            }}
        """)

        layout.addWidget(icon_label)
        layout.addWidget(text_label, 1)
        layout.addWidget(arrow_label)

        apply_style(self, lambda: f"""
            QPushButton {{
                background-color: transparent;
                border: none;
                border-top: 1px solid {Colors.DIVIDER};
                border-radius: 0;
            }}
            QPushButton:hover {{ background-color: {Colors.BG_OVERLAY}; }}
            QPushButton:pressed {{ background-color: {Colors.BG_ELEVATED}; }}
        """)


class SettingsPanel(QWidget):
    """
    设置页（占满整个侧栏宽度）。

    点「返回」回到对话树。页面本身不持有任何业务数据，关掉即销毁
    （由 Sidebar.close_settings 负责），反复开关不会留下悬挂控件。
    """

    back_requested = Signal()
    item_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPanel")

        self._rows: list[_SettingsRow] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_header())

        for item_id, icon, label, hint in SETTINGS_ITEMS:
            row = _SettingsRow(item_id, icon, label, hint, self)
            row.clicked.connect(
                lambda _checked=False, iid=item_id: self.item_clicked.emit(iid)
            )
            self._rows.append(row)
            layout.addWidget(row)

        layout.addStretch(1)

        footer = QLabel("偏好保存在 data/app_settings.json")
        footer.setWordWrap(True)
        footer.setFont(Fonts.mono(Fonts.SIZE_XS))
        layout.addWidget(footer)
        apply_style(footer, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_DISABLED};
                background-color: transparent;
                border: none;
                padding: {Spacing.MD}px {Spacing.LG}px;
            }}
        """)

        apply_style(self, lambda: f"""
            SettingsPanel {{
                background-color: {Colors.BG_SURFACE};
                border: none;
            }}
        """)

    # ── 头部 ─────────────────────────────────

    def _build_header(self) -> QWidget:
        back = QPushButton("←")
        back.setFont(Fonts.body(Fonts.SIZE_MD))
        back.setFixedSize(28, 28)
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setToolTip("返回对话列表")
        back.clicked.connect(self.back_requested)
        apply_style(back, lambda: f"""
            QPushButton {{
                color: {Colors.TEXT_SECONDARY};
                background-color: transparent;
                border: none;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                color: {Colors.TEXT_PRIMARY};
                background-color: {Colors.BG_OVERLAY};
            }}
        """)

        title = QLabel("设置")
        title.setFont(Fonts.mono(Fonts.SIZE_LG, QFont.Weight.DemiBold))
        apply_style(title, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_PRIMARY};
                background-color: transparent;
                border: none;
            }}
        """)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(Spacing.SM)
        row.addWidget(back)
        row.addWidget(title)
        row.addStretch(1)

        wrapper = QWidget()
        wrapper.setLayout(row)
        apply_style(wrapper, lambda: f"""
            QWidget {{
                background-color: transparent;
                border-bottom: 1px solid {Colors.DIVIDER};
            }}
        """)

        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(
            Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG
        )
        outer_layout.setSpacing(0)
        outer_layout.addWidget(wrapper)
        return outer

    # ── 查询（测试 / 外部导航用）─────────────

    def item_ids(self) -> list[str]:
        return [row.item_id for row in self._rows]

    def row(self, item_id: str) -> _SettingsRow | None:
        for row in self._rows:
            if row.item_id == item_id:
                return row
        return None
