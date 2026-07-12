# Layer: UI (PySide6) → widgets
# File: app/ui/widgets/input_area.py
# Responsibility: 底部输入区 — 输入框、模型切换、思考/搜索开关、文件上传、发送/停止按钮
# 与 Flet 版本 app/ui_flet_legacy/widgets/input_area.py 视觉完全一致

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTextEdit,
    QPushButton,
    QComboBox,
    QLabel,
    QSizePolicy,
    QFileDialog,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent, QTextOption

import config as app_config
from app.ui.theme import Colors, Fonts, Spacing, Radius


# ──────────────────────────────────────────────
# 开关芯片（Toggle Chip）
# ──────────────────────────────────────────────


class ToggleChip(QPushButton):
    """
    自定义开关按钮。与原 Flet 版本 _ToggleChip 视觉完全一致。

    信号：
        toggled(bool) — 状态变化时发出
    """

    toggled = Signal(bool)

    def __init__(
        self,
        label: str,
        icon: str,
        active_color: str,
        initial: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._active: bool = initial
        self._active_color: str = active_color
        self._label: str = label
        self._icon: str = icon

        self.setText(f"  {icon}  {label}")
        self.setFont(Fonts.mono(Fonts.SIZE_XS))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(False)
        self.clicked.connect(self._toggle)

        self._apply_style()
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

    def _toggle(self) -> None:
        """切换状态。"""
        self._active = not self._active
        self._apply_style()
        self.toggled.emit(self._active)

    def _apply_style(self) -> None:
        """根据当前状态更新样式。"""
        if self._active:
            border_color = self._active_color
            text_color = self._active_color
            bg = f"{self._active_color}22"
        else:
            border_color = Colors.BORDER
            text_color = Colors.TEXT_DISABLED
            bg = "transparent"

        self.setStyleSheet(f"""
            QPushButton {{
                color: {text_color};
                background-color: {bg};
                border: 1px solid {border_color};
                border-radius: {Radius.SM}px;
                padding: 4px 8px;
            }}
            QPushButton:hover {{
                border-color: {self._active_color};
            }}
        """)

    @property
    def value(self) -> bool:
        """当前激活状态。"""
        return self._active

    def set_value(self, val: bool) -> None:
        """程序化设置状态（不触发信号）。"""
        if self._active != val:
            self._active = val
            self._apply_style()


# ──────────────────────────────────────────────
# 模型选择器
# ──────────────────────────────────────────────


class ModelSelector(QComboBox):
    """
    模型下拉选择。与原 Flet 版本 _ModelSelector 功能完全一致。

    写入 app_config.model_type，通知 on_change 回调。
    """

    model_changed = Signal(str)

    _MODEL_OPTIONS = [
        ("deepseek-v4-flash", "Flash"),
        ("deepseek-v4-pro", "Pro"),
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        current = getattr(app_config, "model_type", "deepseek-v4-pro")

        for key, label in self._MODEL_OPTIONS:
            self.addItem(label, key)

        # 设置当前选中项
        for i in range(self.count()):
            if self.itemData(i) == current:
                self.setCurrentIndex(i)
                break

        self.setFont(Fonts.mono(Fonts.SIZE_SM))
        self.setFixedWidth(150)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"""
            QComboBox {{
                color: {Colors.TEXT_PRIMARY};
                background-color: {Colors.BG_ELEVATED};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                padding: 6px 10px;
            }}
            QComboBox:hover {{
                border-color: {Colors.PRIMARY};
            }}
            QComboBox:focus {{
                border-color: {Colors.PRIMARY};
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 20px;
                border: none;
                padding-right: 4px;
            }}
            QComboBox::down-arrow {{
                width: 8px;
                height: 8px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {Colors.BG_ELEVATED};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                selection-background-color: {Colors.BG_OVERLAY};
                outline: none;
            }}
        """)

        self.currentIndexChanged.connect(self._on_change)

    def _on_change(self, index: int) -> None:
        """下拉选择变更。"""
        model_key = self.itemData(index)
        if model_key:
            app_config.model_type = model_key
            self.model_changed.emit(model_key)


# ──────────────────────────────────────────────
# 附件条
# ──────────────────────────────────────────────


class AttachmentBar(QWidget):
    """
    附件文件标签栏。与原 Flet 版本 _AttachmentBar 功能完全一致。
    """

    files_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._files: list[tuple[str, str]] = []  # (display_name, abs_path)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(Spacing.SM)
        self._layout.addStretch()

        self.setVisible(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def add_file(self, name: str, abs_path: str = "") -> None:
        """添加文件标签。"""
        self._files.append((name, abs_path or name))

        chip = self._make_chip(name)
        self._layout.insertWidget(self._layout.count() - 1, chip)
        self.setVisible(True)
        self.files_changed.emit()

    def _make_chip(self, name: str) -> QFrame:
        """创建单个文件标签控件。"""
        chip = QFrame()
        chip.setStyleSheet(f"""
            QFrame {{
                background-color: transparent;
                border: 1px solid {Colors.BORDER};
                border-radius: {Radius.SM}px;
                padding: 2px 4px;
            }}
        """)

        chip_layout = QHBoxLayout(chip)
        chip_layout.setContentsMargins(6, 3, 2, 3)
        chip_layout.setSpacing(4)

        # 文件图标
        icon_label = QLabel("📎")
        icon_label.setFont(Fonts.body(10))

        # 文件名
        name_label = QLabel(name)
        name_label.setFont(Fonts.body(Fonts.SIZE_XS))
        name_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_SECONDARY};
                background-color: transparent;
                border: none;
            }}
        """)
        name_label.setMaximumWidth(200)

        # 移除按钮
        remove_btn = QPushButton("✕")
        remove_btn.setFont(Fonts.body(8))
        remove_btn.setFixedSize(16, 16)
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.setStyleSheet(f"""
            QPushButton {{
                color: {Colors.TEXT_DISABLED};
                background-color: transparent;
                border: none;
                padding: 0;
            }}
            QPushButton:hover {{
                color: {Colors.ERROR};
            }}
        """)
        remove_btn.clicked.connect(lambda: self._remove(name))

        chip_layout.addWidget(icon_label)
        chip_layout.addWidget(name_label)
        chip_layout.addWidget(remove_btn)

        return chip

    def _remove(self, name: str) -> None:
        """移除指定文件。"""
        self._files = [(n, p) for n, p in self._files if n != name]
        self._rebuild_chips()

    def _rebuild_chips(self) -> None:
        """重建所有标签。"""
        # 移除所有子控件（保留 stretch）
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 重新添加标签
        for name, _ in self._files:
            chip = self._make_chip(name)
            self._layout.insertWidget(self._layout.count() - 1, chip)

        if not self._files:
            self.setVisible(False)

        self.files_changed.emit()

    def get_files(self) -> list[str]:
        """返回绝对路径列表。"""
        return [p for _, p in self._files]

    def clear_files(self) -> None:
        """清空所有附件。"""
        self._files.clear()
        self._rebuild_chips()


# ──────────────────────────────────────────────
# 输入区
# ──────────────────────────────────────────────


class InputArea(QFrame):
    """
    底部输入复合体。与原 Flet 版本 InputArea 视觉完全一致。

    信号：
        send_requested(str, list[str]) — 用户点击发送（文本，文件路径列表）
        stop_requested()               — 用户点击停止生成
        model_changed(str)             — 模型变更
        thinking_toggled(bool)         — 思考模式切换
        search_toggled(bool)           — 搜索开关切换
    """

    send_requested = Signal(str, list)
    stop_requested = Signal()
    model_changed = Signal(str)
    thinking_toggled = Signal(bool)
    search_toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("inputArea")
        self._generating: bool = False

        # ── 附件条 ──────────────────────────────
        self._attachment_bar = AttachmentBar()

        # ── 输入文本框 ──────────────────────────
        self._text_edit = QTextEdit()
        self._text_edit.setPlaceholderText(
            "输入消息，Shift+Enter 换行，Enter 发送..."
        )
        self._text_edit.setFont(Fonts.body(Fonts.SIZE_MD))
        self._text_edit.setStyleSheet(f"""
            QTextEdit {{
                color: {Colors.TEXT_PRIMARY};
                background-color: transparent;
                border: none;
                padding: 8px 0px;
            }}
        """)
        self._text_edit.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._text_edit.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._text_edit.setMinimumHeight(40)
        self._text_edit.setMaximumHeight(200)
        self._text_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        # 启用 Enter 发送
        self._text_edit.installEventFilter(self)
        self._text_edit.setWordWrapMode(QTextOption.WrapMode.WordWrap)

        # ── 发送/停止按钮 ───────────────────────
        self._send_btn = QPushButton()
        self._send_btn.setFont(Fonts.body(18))
        self._send_btn.setFixedSize(40, 40)
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.setToolTip("发送 (Enter)")
        self._send_btn.clicked.connect(self._on_send_click)
        self._apply_send_style()

        # ── 输入行 ──────────────────────────────
        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(Spacing.SM)
        input_row.addWidget(self._text_edit, stretch=1)
        input_row.addWidget(self._send_btn)
        input_row.setAlignment(Qt.AlignmentFlag.AlignBottom)

        # ── 工具栏 ──────────────────────────────
        self._file_btn = self._make_toolbar_btn("📎", "上传文件")
        self._file_btn.clicked.connect(self._open_file_dialog)

        initial_thinking = getattr(app_config, "thinking_enabled", False)
        self._thinking_chip = ToggleChip(
            "THINK", "🧠", Colors.ROLE_THINKING, initial=initial_thinking
        )
        self._thinking_chip.toggled.connect(self._on_thinking_toggle)

        initial_search = getattr(app_config, "search_enabled", False)
        self._search_chip = ToggleChip(
            "SEARCH", "🌐", Colors.ACCENT, initial=initial_search
        )
        self._search_chip.toggled.connect(self._on_search_toggle)

        self._model_selector = ModelSelector()
        self._model_selector.model_changed.connect(self.model_changed)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, Spacing.SM, 0, 0)
        toolbar.setSpacing(Spacing.SM)
        toolbar.addWidget(self._file_btn)
        toolbar.addWidget(self._thinking_chip)
        toolbar.addWidget(self._search_chip)
        toolbar.addStretch()
        toolbar.addWidget(self._model_selector)

        # ── 整体组装 ────────────────────────────
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            Spacing.LG, Spacing.MD, Spacing.LG, Spacing.MD
        )
        layout.setSpacing(Spacing.SM)
        layout.addWidget(self._attachment_bar)
        layout.addLayout(input_row)
        layout.addLayout(toolbar)

        # ── 边框样式 ────────────────────────────
        toolbar_line_style = f"""
            border-top: 1px solid {Colors.DIVIDER};
        """
        # Apply top-border via a separator approach - the toolbar itself handles this
        self.setStyleSheet(f"""
            InputArea {{
                background-color: {Colors.BG_SURFACE};
                border: none;
                border-top: 1px solid {Colors.BORDER};
            }}
        """)

    # ── 事件过滤器（Enter 发送）────────────────

    def eventFilter(self, obj, event) -> bool:
        """拦截文本编辑框的按键事件：Enter 发送，Shift+Enter 换行。"""
        if obj is self._text_edit and event.type() == event.Type.KeyPress:
            key_event = event
            if key_event.key() == Qt.Key.Key_Return or key_event.key() == Qt.Key.Key_Enter:
                if not key_event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self._on_send_click()
                    return True
        return super().eventFilter(obj, event)

    # ── 按钮样式 ──────────────────────────────

    def _make_toolbar_btn(self, text: str, tooltip: str) -> QPushButton:
        """创建工具栏小按钮。"""
        btn = QPushButton(text)
        btn.setFont(Fonts.body(12))
        btn.setToolTip(tooltip)
        btn.setFixedSize(28, 28)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                color: {Colors.TEXT_SECONDARY};
                background-color: transparent;
                border: none;
                border-radius: 4px;
                padding: 2px;
            }}
            QPushButton:hover {{
                color: {Colors.TEXT_PRIMARY};
                background-color: {Colors.BG_OVERLAY};
            }}
        """)
        return btn

    def _apply_send_style(self) -> None:
        """根据生成状态应用发送/停止按钮样式。"""
        if self._generating:
            self._send_btn.setText("⏹")
            self._send_btn.setToolTip("停止生成")
            icon_color = Colors.ERROR
            bg = f"{Colors.ERROR}22"
        else:
            self._send_btn.setText("➤")
            self._send_btn.setToolTip("发送 (Enter)")
            icon_color = Colors.PRIMARY
            bg = Colors.PRIMARY_GLOW

        self._send_btn.setStyleSheet(f"""
            QPushButton {{
                color: {icon_color};
                background-color: {bg};
                border: none;
                border-radius: 20px;
                padding: 8px;
            }}
            QPushButton:hover {{
                background-color: {icon_color}33;
            }}
        """)

    # ── 发送/停止逻辑 ─────────────────────────

    def _on_send_click(self) -> None:
        """发送或停止按钮点击。"""
        if self._generating:
            self.stop_requested.emit()
        else:
            text = self._text_edit.toPlainText().strip()
            files = self._attachment_bar.get_files()
            if not text and not files:
                return
            self.send_requested.emit(text, files)

    # ── 文件对话框 ─────────────────────────────

    def _open_file_dialog(self) -> None:
        """打开原生文件选择对话框。"""
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择文件",
            "",
            "支持的文件 (*.txt *.md *.json *.docx *.pdf);;所有文件 (*.*)",
        )
        if paths:
            import os
            for p in paths:
                name = os.path.basename(p)
                self._attachment_bar.add_file(name, p)

    # ── 芯片回调 ───────────────────────────────

    def _on_thinking_toggle(self, val: bool) -> None:
        """思考模式切换。"""
        app_config.thinking_enabled = val
        self.thinking_toggled.emit(val)

    def _on_search_toggle(self, val: bool) -> None:
        """搜索开关切换。"""
        app_config.search_enabled = val
        self.search_toggled.emit(val)

    # ── 公开接口 ──────────────────────────────

    def set_generating(self, generating: bool) -> None:
        """切换生成状态（发送 ↔ 停止）。"""
        self._generating = generating
        self._apply_send_style()

    def clear(self) -> None:
        """清空输入框和附件。"""
        self._text_edit.clear()
        self._attachment_bar.clear_files()

    def set_enabled(self, enabled: bool) -> None:
        """启用/禁用输入区。"""
        self._text_edit.setEnabled(enabled)
        self._send_btn.setEnabled(enabled)
