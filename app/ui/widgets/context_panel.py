# Layer: UI (PySide6) → widgets
# File: app/ui/widgets/context_panel.py
# Responsibility: 上下文管理面板（右侧滑出抽屉）— 显示已选块列表、保存模板、
#                 添加文本块、拼接预览、模板库。
#                 与 Flet 版本 app/ui_flet_legacy/widgets/context_panel.py 功能完全对等。

from __future__ import annotations
from typing import Callable

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QLabel,
    QPushButton,
    QTextEdit,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QCheckBox,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from app.ui.theme import Colors, Fonts, Spacing, Radius


# ──────────────────────────────────────────────
# 单个上下文块控件
# ──────────────────────────────────────────────


class _ContextBlockRow(QFrame):
    """
    上下文块条目行：开关 + 标签 + 预览 + 移除按钮。
    与 Flet 版本的 _ContextBlockItem 功能完全一致。
    """

    remove_requested = Signal(str)   # block_id
    toggled = Signal(str, bool)      # block_id, enabled

    def __init__(
        self,
        block_id: str,
        label: str,
        preview: str,
        enabled: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._block_id = block_id

        # ── 开关 ──────────────────────────────────
        self._switch = QCheckBox()
        self._switch.setChecked(enabled)
        self._switch.setFixedSize(36, 18)
        self._switch.setStyleSheet(f"""
            QCheckBox::indicator {{
                width: 36px;
                height: 18px;
                border-radius: 9px;
                border: 1px solid {Colors.BORDER};
                background-color: transparent;
            }}
            QCheckBox::indicator:checked {{
                background-color: {Colors.PRIMARY};
                border-color: {Colors.PRIMARY};
            }}
        """)
        self._switch.toggled.connect(
            lambda val: self.toggled.emit(block_id, val)
        )

        # ── 标签 + 预览 ───────────────────────────
        label_widget = QLabel(label)
        label_widget.setFont(Fonts.body(Fonts.SIZE_SM, QFont.Weight.Medium))
        label_widget.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_PRIMARY};
                background: transparent;
                border: none;
            }}
        """)

        preview_widget = QLabel(preview)
        preview_widget.setFont(Fonts.mono(Fonts.SIZE_XS))
        preview_widget.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_DISABLED};
                background: transparent;
                border: none;
            }}
        """)
        preview_widget.setWordWrap(True)
        preview_widget.setMaximumHeight(32)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        text_col.addWidget(label_widget)
        text_col.addWidget(preview_widget)

        # ── 移除按钮 ──────────────────────────────
        remove_btn = QPushButton("✕")
        remove_btn.setFont(Fonts.body(12))
        remove_btn.setFixedSize(22, 22)
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.setToolTip("移除")
        remove_btn.setStyleSheet(f"""
            QPushButton {{
                color: {Colors.ERROR};
                background: transparent;
                border: none;
                padding: 2px;
            }}
            QPushButton:hover {{
                background-color: {Colors.ERROR}22;
                border-radius: 3px;
            }}
        """)
        remove_btn.clicked.connect(
            lambda: self.remove_requested.emit(block_id)
        )

        # ── 行布局 ────────────────────────────────
        row_layout = QHBoxLayout(self)
        row_layout.setContentsMargins(Spacing.SM, Spacing.SM, Spacing.SM, Spacing.SM)
        row_layout.setSpacing(Spacing.SM)
        row_layout.addWidget(self._switch)
        row_layout.addLayout(text_col, stretch=1)
        row_layout.addWidget(remove_btn)

        # ── 底部边框 ──────────────────────────────
        self.setStyleSheet(f"""
            _ContextBlockRow {{
                background-color: transparent;
                border-bottom: 1px solid {Colors.DIVIDER};
            }}
        """)


# ──────────────────────────────────────────────
# 模板条目控件
# ──────────────────────────────────────────────


class _TemplateRow(QFrame):
    """
    模板条目行：图标 + 名称 + 描述 + 应用按钮 + 删除按钮。
    与 Flet 版本的 _TemplateItem 功能完全一致。
    """

    apply_requested = Signal(str)   # template_id
    delete_requested = Signal(str)  # template_id

    def __init__(
        self,
        template_id: str,
        name: str,
        description: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._template_id = template_id

        # ── 图标 ──────────────────────────────────
        icon_label = QLabel("📄")
        icon_label.setFont(Fonts.body(14))
        icon_label.setFixedWidth(20)
        icon_label.setStyleSheet("QLabel { background: transparent; border: none; }")

        # ── 名称 + 描述 ───────────────────────────
        name_widget = QLabel(name)
        name_widget.setFont(Fonts.body(Fonts.SIZE_SM))
        name_widget.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_PRIMARY};
                background: transparent;
                border: none;
            }}
        """)

        desc_widget = QLabel(description)
        desc_widget.setFont(Fonts.body(Fonts.SIZE_XS))
        desc_widget.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_DISABLED};
                background: transparent;
                border: none;
            }}
        """)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        text_col.addWidget(name_widget)
        if description:
            text_col.addWidget(desc_widget)

        # ── 应用按钮 ──────────────────────────────
        apply_btn = QPushButton("应用")
        apply_btn.setFont(Fonts.mono(Fonts.SIZE_XS))
        apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_btn.setStyleSheet(f"""
            QPushButton {{
                color: {Colors.PRIMARY};
                background-color: transparent;
                border: 1px solid {Colors.PRIMARY};
                border-radius: {Radius.SM}px;
                padding: 4px 10px;
            }}
            QPushButton:hover {{
                background-color: {Colors.PRIMARY_GLOW};
            }}
        """)
        apply_btn.clicked.connect(
            lambda: self.apply_requested.emit(template_id)
        )

        # ── 删除按钮 ──────────────────────────────
        delete_btn = QPushButton("🗑")
        delete_btn.setFont(Fonts.body(10))
        delete_btn.setFixedSize(22, 22)
        delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_btn.setToolTip("删除模板")
        delete_btn.setStyleSheet(f"""
            QPushButton {{
                color: {Colors.ERROR};
                background: transparent;
                border: none;
                padding: 2px;
            }}
            QPushButton:hover {{
                background-color: {Colors.ERROR}22;
                border-radius: 3px;
            }}
        """)
        delete_btn.clicked.connect(
            lambda: self.delete_requested.emit(template_id)
        )

        # ── 行布局 ────────────────────────────────
        row_layout = QHBoxLayout(self)
        row_layout.setContentsMargins(Spacing.MD, Spacing.SM, Spacing.MD, Spacing.SM)
        row_layout.setSpacing(Spacing.SM)
        row_layout.addWidget(icon_label)
        row_layout.addLayout(text_col, stretch=1)
        row_layout.addWidget(apply_btn)
        row_layout.addWidget(delete_btn)

        self.setStyleSheet(f"""
            _TemplateRow {{
                background-color: transparent;
                border-bottom: 1px solid {Colors.DIVIDER};
            }}
        """)


# ──────────────────────────────────────────────
# 上下文管理面板主体
# ──────────────────────────────────────────────


class ContextPanel(QWidget):
    """
    上下文管理面板（右侧滑出抽屉，380px 宽）。

    公开接口：
        show_panel()           — 显示面板
        hide_panel()           — 隐藏面板
        load_blocks(items)     — 加载上下文块列表
        load_templates(items)  — 加载模板列表
        set_preview(text)      — 更新拼接预览

    信号：
        remove_block(str)          — 移除上下文块（block_id）
        toggle_block(str, bool)    — 切换块启用状态
        add_text_block(str)        — 添加自定义文本块（内容文本）
        save_as_template(str)      — 保存当前块为模板（模板名称）
        apply_template(str)        — 应用模板（template_id）
        delete_template(str)       — 删除模板（template_id）
        close_requested()          — 关闭面板
    """

    remove_block = Signal(str)
    toggle_block = Signal(str, bool)
    add_text_block = Signal(str)
    save_as_template = Signal(str)
    apply_template = Signal(str)
    delete_template = Signal(str)
    close_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("contextPanel")
        self.setFixedWidth(380)

        # ── 头部 ──────────────────────────────────
        header = self._build_header()

        # ── 已选上下文块区域 ──────────────────────
        blocks_section = self._build_blocks_section()

        # ── 保存为模板区域 ────────────────────────
        save_template_section = self._build_save_template_section()

        # ── 添加文本块区域 ────────────────────────
        add_block_section = self._build_add_block_section()

        # ── 拼接预览区域 ─────────────────────────
        preview_section = self._build_preview_section()

        # ── 模板库区域 ────────────────────────────
        templates_section = self._build_templates_section()

        # ── 整体布局（可滚动）─────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: transparent;
                border: none;
            }}
        """)

        content = QWidget()
        content.setStyleSheet("QWidget { background-color: transparent; }")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(header)
        content_layout.addWidget(blocks_section)
        content_layout.addWidget(save_template_section)
        content_layout.addWidget(add_block_section)
        content_layout.addWidget(preview_section)
        content_layout.addWidget(templates_section)
        content_layout.addStretch()

        scroll.setWidget(content)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(scroll)

        # ── 整体样式 ──────────────────────────────
        self.setStyleSheet(f"""
            ContextPanel {{
                background-color: {Colors.BG_SURFACE};
                border-left: 1px solid {Colors.BORDER};
            }}
        """)

        # 默认隐藏
        self.setVisible(False)

    # ── 头部 ──────────────────────────────────────

    def _build_header(self) -> QWidget:
        """构建面板头部。"""
        header = QWidget()
        header.setStyleSheet(f"""
            QWidget {{
                background-color: transparent;
                border-bottom: 1px solid {Colors.DIVIDER};
            }}
        """)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(Spacing.LG, Spacing.MD, Spacing.MD, Spacing.MD)
        layout.setSpacing(Spacing.SM)

        icon = QLabel("📚")
        icon.setFont(Fonts.body(18))
        icon.setStyleSheet("background: transparent; border: none;")

        title = QLabel("上下文管理")
        title.setFont(Fonts.mono(Fonts.SIZE_LG, QFont.Weight.DemiBold))
        title.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent; border: none;")

        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setFont(Fonts.body(14))
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setToolTip("关闭")
        close_btn.setStyleSheet(f"""
            QPushButton {{
                color: {Colors.TEXT_SECONDARY};
                background: transparent;
                border: none;
                padding: 4px;
            }}
            QPushButton:hover {{
                color: {Colors.TEXT_PRIMARY};
                background-color: {Colors.BG_OVERLAY};
                border-radius: 4px;
            }}
        """)
        close_btn.clicked.connect(self.close_requested.emit)
        layout.addWidget(close_btn)

        return header

    # ── 已选上下文块区域 ─────────────────────────

    def _build_blocks_section(self) -> QWidget:
        """构建已选上下文块列表区域。"""
        section = QWidget()
        section.setStyleSheet("QWidget { background-color: transparent; }")

        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 节标题
        section_label = QLabel("已选上下文块")
        section_label.setFont(Fonts.mono(Fonts.SIZE_XS))
        section_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_DISABLED};
                background: transparent;
                border: none;
                padding: {Spacing.MD}px {Spacing.LG}px {Spacing.SM}px {Spacing.LG}px;
            }}
        """)
        layout.addWidget(section_label)

        # 块列表容器
        self._blocks_container = QWidget()
        self._blocks_container.setStyleSheet("QWidget { background-color: transparent; }")
        self._blocks_layout = QVBoxLayout(self._blocks_container)
        self._blocks_layout.setContentsMargins(0, 0, 0, 0)
        self._blocks_layout.setSpacing(0)

        # 固定高度的滚动区域
        blocks_scroll = QScrollArea()
        blocks_scroll.setWidgetResizable(True)
        blocks_scroll.setFixedHeight(200)
        blocks_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        blocks_scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: transparent;
                border: none;
            }}
        """)
        blocks_scroll.setWidget(self._blocks_container)

        inner_layout = QVBoxLayout()
        inner_layout.setContentsMargins(Spacing.MD, 0, Spacing.MD, 0)
        inner_layout.setSpacing(0)
        inner_layout.addWidget(blocks_scroll)
        layout.addLayout(inner_layout)

        return section

    # ── 保存为模板区域 ───────────────────────────

    def _build_save_template_section(self) -> QWidget:
        """构建保存为模板区域。"""
        section = QWidget()
        section.setStyleSheet(f"""
            QWidget {{
                background-color: transparent;
                border-bottom: 1px solid {Colors.DIVIDER};
            }}
        """)

        layout = QHBoxLayout(section)
        layout.setContentsMargins(Spacing.LG, Spacing.SM, Spacing.LG, Spacing.SM)
        layout.setSpacing(Spacing.SM)

        self._template_name_input = QLineEdit()
        self._template_name_input.setPlaceholderText("输入模板名称，保存当前块为模板...")
        self._template_name_input.setFont(Fonts.body(Fonts.SIZE_XS))
        self._template_name_input.setStyleSheet(f"""
            QLineEdit {{
                color: {Colors.TEXT_PRIMARY};
                background-color: {Colors.BG_ELEVATED};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 6px 8px;
            }}
            QLineEdit:focus {{
                border-color: {Colors.PRIMARY};
            }}
        """)

        save_btn = QPushButton("🔖")
        save_btn.setFont(Fonts.body(14))
        save_btn.setFixedSize(32, 32)
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setToolTip("保存为模板")
        save_btn.setStyleSheet(f"""
            QPushButton {{
                color: {Colors.ACCENT};
                background: transparent;
                border: none;
                padding: 4px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BG_OVERLAY};
                border-radius: 4px;
            }}
        """)
        save_btn.clicked.connect(self._on_save_template)

        layout.addWidget(self._template_name_input, stretch=1)
        layout.addWidget(save_btn)

        return section

    def _on_save_template(self) -> None:
        """保存为模板按钮：发送模板名称。"""
        name = self._template_name_input.text().strip()
        if name:
            self.save_as_template.emit(name)
            self._template_name_input.clear()

    # ── 添加文本块区域 ───────────────────────────

    def _build_add_block_section(self) -> QWidget:
        """构建添加文本块区域。"""
        section = QWidget()
        section.setStyleSheet(f"""
            QWidget {{
                background-color: transparent;
                border-bottom: 1px solid {Colors.DIVIDER};
            }}
        """)

        layout = QVBoxLayout(section)
        layout.setContentsMargins(Spacing.LG, Spacing.MD, Spacing.LG, Spacing.MD)
        layout.setSpacing(Spacing.SM)

        section_label = QLabel("添加文本块")
        section_label.setFont(Fonts.mono(Fonts.SIZE_XS))
        section_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_DISABLED};
                background: transparent;
                border: none;
            }}
        """)
        layout.addWidget(section_label)

        input_row = QHBoxLayout()
        input_row.setSpacing(Spacing.SM)

        self._new_block_input = QTextEdit()
        self._new_block_input.setPlaceholderText("输入自定义上下文内容...")
        self._new_block_input.setFont(Fonts.body(Fonts.SIZE_SM))
        self._new_block_input.setMinimumHeight(48)
        self._new_block_input.setMaximumHeight(100)
        self._new_block_input.setStyleSheet(f"""
            QTextEdit {{
                color: {Colors.TEXT_PRIMARY};
                background-color: {Colors.BG_ELEVATED};
                border: 1px solid {Colors.BORDER};
                border-radius: {Radius.MD}px;
                padding: {Spacing.SM}px {Spacing.MD}px;
            }}
            QTextEdit:focus {{
                border-color: {Colors.PRIMARY};
            }}
        """)

        add_btn = QPushButton("＋")
        add_btn.setFont(Fonts.body(18))
        add_btn.setFixedSize(36, 36)
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setToolTip("添加")
        add_btn.setStyleSheet(f"""
            QPushButton {{
                color: {Colors.PRIMARY};
                background: transparent;
                border: none;
                padding: 4px;
            }}
            QPushButton:hover {{
                background-color: {Colors.PRIMARY_GLOW};
                border-radius: 4px;
            }}
        """)
        add_btn.clicked.connect(self._on_add_block)

        input_row.addWidget(self._new_block_input, stretch=1)
        input_row.addWidget(add_btn, alignment=Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(input_row)

        return section

    def _on_add_block(self) -> None:
        """添加文本块按钮：发送内容文本。"""
        text = self._new_block_input.toPlainText().strip()
        if text:
            self.add_text_block.emit(text)
            self._new_block_input.clear()

    # ── 拼接预览区域 ─────────────────────────────

    def _build_preview_section(self) -> QWidget:
        """构建拼接预览区域。"""
        section = QWidget()
        section.setStyleSheet("QWidget { background-color: transparent; }")

        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        section_label = QLabel("拼接预览")
        section_label.setFont(Fonts.mono(Fonts.SIZE_XS))
        section_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_DISABLED};
                background: transparent;
                border: none;
                padding: {Spacing.MD}px {Spacing.LG}px {Spacing.SM}px {Spacing.LG}px;
            }}
        """)
        layout.addWidget(section_label)

        self._preview_text = QLabel("（尚无上下文）")
        self._preview_text.setFont(Fonts.mono(Fonts.SIZE_XS))
        self._preview_text.setWordWrap(True)
        self._preview_text.setFixedHeight(120)
        self._preview_text.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_CODE};
                background-color: {Colors.BG_BASE};
                border-top: 1px solid {Colors.DIVIDER};
                border-bottom: 1px solid {Colors.DIVIDER};
                border-left: 2px solid {Colors.PRIMARY_DIM};
                border-right: 1px solid {Colors.DIVIDER};
                padding: {Spacing.SM}px {Spacing.LG}px;
            }}
        """)
        layout.addWidget(self._preview_text)

        return section

    # ── 模板库区域 ───────────────────────────────

    def _build_templates_section(self) -> QWidget:
        """构建模板库区域。"""
        section = QWidget()
        section.setStyleSheet("QWidget { background-color: transparent; }")

        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        section_label = QLabel("模板库")
        section_label.setFont(Fonts.mono(Fonts.SIZE_XS))
        section_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_DISABLED};
                background: transparent;
                border: none;
                padding: {Spacing.MD}px {Spacing.LG}px {Spacing.SM}px {Spacing.LG}px;
            }}
        """)
        layout.addWidget(section_label)

        # 模板列表容器
        self._templates_container = QWidget()
        self._templates_container.setStyleSheet("QWidget { background-color: transparent; }")
        self._templates_layout = QVBoxLayout(self._templates_container)
        self._templates_layout.setContentsMargins(0, 0, 0, 0)
        self._templates_layout.setSpacing(0)

        templates_scroll = QScrollArea()
        templates_scroll.setWidgetResizable(True)
        templates_scroll.setFixedHeight(180)
        templates_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        templates_scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: transparent;
                border: none;
            }}
        """)
        templates_scroll.setWidget(self._templates_container)
        layout.addWidget(templates_scroll)

        return section

    # ──────────────────────────────────────────────
    # 公开接口
    # ──────────────────────────────────────────────

    def show_panel(self) -> None:
        """显示面板。"""
        self.setVisible(True)

    def hide_panel(self) -> None:
        """隐藏面板。"""
        self.setVisible(False)

    def is_visible(self) -> bool:
        """面板是否可见。"""
        return self.isVisible()

    def load_blocks(self, items: list[dict]) -> None:
        """
        加载上下文块列表。

        Args:
            items: dict 列表，每项含 id, label, preview, enabled
        """
        # 清除旧条目
        self._clear_layout(self._blocks_layout)

        for block in items:
            row = _ContextBlockRow(
                block_id=block.get("id", ""),
                label=block.get("label", ""),
                preview=block.get("preview", ""),
                enabled=block.get("enabled", True),
            )
            row.remove_requested.connect(self.remove_block.emit)
            row.toggled.connect(self.toggle_block.emit)
            self._blocks_layout.addWidget(row)

        self._blocks_layout.addStretch()

    def load_templates(self, items: list[dict]) -> None:
        """
        加载模板列表。

        Args:
            items: dict 列表，每项含 id, name, description
        """
        self._clear_layout(self._templates_layout)

        for tmpl in items:
            row = _TemplateRow(
                template_id=tmpl.get("id", ""),
                name=tmpl.get("name", ""),
                description=tmpl.get("description", ""),
            )
            row.apply_requested.connect(self.apply_template.emit)
            row.delete_requested.connect(self.delete_template.emit)
            self._templates_layout.addWidget(row)

        self._templates_layout.addStretch()

    def set_preview(self, text: str) -> None:
        """
        更新拼接预览文本。

        Args:
            text: 预览文本内容
        """
        self._preview_text.setText(text or "（尚无上下文）")

    @staticmethod
    def _clear_layout(layout) -> None:
        """清空布局中的所有子控件。"""
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            # 如果还有子布局，递归清除
            sub_layout = item.layout()
            if sub_layout:
                ContextPanel._clear_layout(sub_layout)
                sub_layout.deleteLater()
