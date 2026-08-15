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
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QKeyEvent, QTextOption

import config as app_config
from app.ui.theme import apply_style, Colors, Fonts, Spacing, Radius


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

    def refresh_theme(self) -> None:
        """切主题时按当前状态重绘样式。"""
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
        apply_style(self, lambda: f"""
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


class ReasoningEffortSelector(QComboBox):
    """
    思考强度下拉选择（low / high / max）。

    写入 app_config.reasoning_effort，通知 effort_changed 回调。
    仅当 thinking_enabled=True 时生效（API 要求）；思考关闭时控件应被禁用。
    """

    effort_changed = Signal(str)

    _EFFORT_OPTIONS = [
        ("low", "LOW"),
        ("high", "HIGH"),
        ("max", "MAX"),
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        current = getattr(app_config, "reasoning_effort", "high")

        for key, label in self._EFFORT_OPTIONS:
            self.addItem(label, key)

        # 设置当前选中项
        for i in range(self.count()):
            if self.itemData(i) == current:
                self.setCurrentIndex(i)
                break

        self.setFont(Fonts.mono(Fonts.SIZE_SM))
        self.setFixedWidth(90)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("思考强度 (reasoning_effort)：low / high / max，仅思考模式开启时生效")
        apply_style(self, lambda: f"""
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
            QComboBox:disabled {{
                color: {Colors.TEXT_DISABLED};
                border-color: {Colors.BORDER};
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
        effort = self.itemData(index)
        if effort:
            app_config.reasoning_effort = effort
            self.effort_changed.emit(effort)


# ──────────────────────────────────────────────
# 温度滑块
# ──────────────────────────────────────────────


class TemperatureSlider(QWidget):
    """
    温度滑块（0.0 ~ 2.0，浮点，步长 0.01）。

    作用：控制输出随机性 —— 更高值（如 0.8）输出更随机、更多样；
    更低值（如 0.2）输出更集中、更确定。

    仅非思考模式下生效（temperature 只在 thinking 关闭时随请求下发）；
    思考模式开启时由 InputArea 禁用本控件。
    """

    temperature_changed = Signal(float)

    _SCALE = 100  # 滑块整数 0..200 ↔ 浮点 0.0..2.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        initial = float(getattr(app_config, "temperature", 1.0))
        initial = max(0.0, min(2.0, initial))

        # ── 标签 ──
        label = QLabel("TEMP")
        label.setFont(Fonts.mono(Fonts.SIZE_XS))
        label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        apply_style(label, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_SECONDARY};
                background-color: transparent;
                border: none;
            }}
        """)

        # ── 滑块（0..200 映射 0.0..2.0）──
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, int(2.0 * self._SCALE))
        self._slider.setValue(int(round(initial * self._SCALE)))
        self._slider.setFixedWidth(100)
        # 负左边距：抵掉 Fusion 滑块槽的固有缩进，让轨道视觉上紧贴"TEMP"文字
        # （min/max 两端 handle 中心距边缘仍 ≥4px，不会裁剪）
        self._slider.setContentsMargins(-4, 0, 0, 0)
        self._slider.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_style(self._slider, lambda: f"""
            QSlider {{
                background: transparent;
            }}
            QSlider::groove:horizontal {{
                height: 4px;
                background: {Colors.BORDER};
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {Colors.PRIMARY};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                width: 12px;
                height: 12px;
                margin: -4px 0;
                background: {Colors.PRIMARY};
                border-radius: 6px;
            }}
            QSlider::handle:horizontal:disabled {{
                background: {Colors.TEXT_DISABLED};
            }}
        """)

        # ── 数值显示 ──
        self._value_label = QLabel(f"{initial:.2f}")
        self._value_label.setFont(Fonts.mono(Fonts.SIZE_XS))
        self._value_label.setFixedWidth(36)
        apply_style(self._value_label, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_PRIMARY};
                background-color: transparent;
                border: none;
            }}
        """)

        self._slider.valueChanged.connect(self._on_value_changed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(label)
        layout.addWidget(self._slider)
        layout.addWidget(self._value_label)

        self.setToolTip(
            "温度：控制输出随机性。更高值（如 0.8）输出更随机、多样；"
            "更低值（如 0.2）输出更集中、确定。思考模式开启时不生效。"
        )
        # 固定宽度（关键）：内部 QSlider 默认水平 sizePolicy 为 Expanding，
        # 会让外层 toolbar 把本控件撑宽、多出的宽度全落进 QLabel 的透明方框，
        # 造成"TEMP 与滑块被拉开、滑块仿佛居中"的视觉假象。
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

    def _on_value_changed(self, value: int) -> None:
        """滑块移动 → 写入 config 并发出信号。"""
        temp = value / self._SCALE
        app_config.temperature = temp
        self._value_label.setText(f"{temp:.2f}")
        self.temperature_changed.emit(temp)

    @property
    def value(self) -> float:
        """当前温度值（0.0 ~ 2.0）。"""
        return self._slider.value() / self._SCALE


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
        apply_style(chip, lambda: f"""
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
        apply_style(name_label, lambda: f"""
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
        apply_style(remove_btn, lambda: f"""
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
    reasoning_effort_changed = Signal(str)
    edit_cancel_requested = Signal()   # 修改状态下点击 ✕ 退出修改（5.2）

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("inputArea")
        self._generating: bool = False
        self._edit_mode: bool = False   # 修改状态（5.1/5.2）

        # ── 输入栏高度控制 ──────────────────────
        self._pinned: bool = True          # True=自动调整高度, False=手动拖动
        self._manual_height: int = 0       # 手动模式下保存的用户拖拽高度
        self._resize_dragging: bool = False
        self._resize_drag_start_y: float = 0.0
        self._resize_drag_start_h: int = 0

        # ── 附件条 ──────────────────────────────
        self._attachment_bar = AttachmentBar()

        # ── 输入文本框 ──────────────────────────
        self._text_edit = QTextEdit()
        self._text_edit.setPlaceholderText(
            "输入消息，Shift+Enter 换行，Enter 发送..."
        )
        self._text_edit.setFont(Fonts.body(Fonts.SIZE_MD))
        apply_style(self._text_edit, lambda: f"""
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

        # ── 行高 & 最大高度（14 行）─────────────
        fm = self._text_edit.fontMetrics()
        self._line_height = fm.lineSpacing()
        # 14 行文字高度 + 上下 padding (8+8 from stylesheet)
        self._max_text_height = self._line_height * 14 + 16
        self._text_edit.setMaximumHeight(self._max_text_height)
        # 文本变化时自动调整高度（仅图钉模式生效）
        self._text_edit.textChanged.connect(self._on_text_changed_for_resize)

        # ── 拖拽把手（手动调整高度模式可见）────
        self._resize_handle = QFrame()
        self._resize_handle.setObjectName("resizeHandle")
        self._resize_handle.setFixedHeight(6)
        self._resize_handle.setCursor(Qt.CursorShape.SizeVerCursor)
        self._resize_handle.setToolTip("拖动调整输入栏高度")
        apply_style(self._resize_handle, lambda: f"""
            QFrame#resizeHandle {{
                background-color: transparent;
                border: none;
            }}
            QFrame#resizeHandle:hover {{
                background-color: {Colors.BORDER};
            }}
        """)
        self._resize_handle.setVisible(False)  # 初始图钉模式=自动，隐藏把手
        self._resize_handle.installEventFilter(self)
        # 启用 hover 样式（QFrame 默认不追踪鼠标）
        self._resize_handle.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        # ── 图钉按钮 ────────────────────────────
        self._pin_btn = QPushButton("📌")
        self._pin_btn.setFont(Fonts.body(12))
        self._pin_btn.setFixedSize(28, 28)
        self._pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pin_btn.clicked.connect(self._on_pin_toggle)
        self._apply_pin_style()

        # ── 发送/停止按钮 ───────────────────────
        self._send_btn = QPushButton()
        self._send_btn.setFont(Fonts.body(18))
        self._send_btn.setFixedSize(40, 40)
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.setToolTip("发送 (Enter)")
        self._send_btn.clicked.connect(self._on_send_click)
        self._apply_send_style()

        # ── 修改状态 ✕ 按钮（输入框右上方，5.1.5）──
        self._edit_cancel_btn = QPushButton("✕")
        self._edit_cancel_btn.setFont(Fonts.body(14))
        self._edit_cancel_btn.setFixedSize(28, 28)
        self._edit_cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_cancel_btn.setToolTip("退出修改状态")
        apply_style(self._edit_cancel_btn, lambda: f"""
            QPushButton {{
                color: {Colors.TEXT_SECONDARY};
                background-color: transparent;
                border: 1px solid {Colors.BORDER};
                border-radius: 14px;
            }}
            QPushButton:hover {{
                color: {Colors.ERROR};
                background-color: {Colors.BG_OVERLAY};
            }}
        """)
        self._edit_cancel_btn.clicked.connect(self.edit_cancel_requested)
        self._edit_cancel_btn.setVisible(False)

        # ── 输入行 ──────────────────────────────
        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(Spacing.SM)
        input_row.addWidget(self._text_edit, stretch=1)
        input_row.addWidget(self._edit_cancel_btn)
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

        # 思考强度下拉（思考关闭时禁用，因为 API 要求 thinking 启用才可传 reasoning_effort）
        self._effort_selector = ReasoningEffortSelector()
        self._effort_selector.effort_changed.connect(self.reasoning_effort_changed)
        self._effort_selector.setEnabled(initial_thinking)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, Spacing.SM, 0, 0)
        toolbar.setSpacing(Spacing.SM)
        toolbar.addWidget(self._file_btn)
        toolbar.addWidget(self._pin_btn)
        toolbar.addWidget(self._thinking_chip)
        toolbar.addWidget(self._effort_selector)
        toolbar.addWidget(self._search_chip)
        toolbar.addStretch()
        toolbar.addWidget(self._model_selector)

        # ── 整体组装 ────────────────────────────
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            Spacing.LG, Spacing.MD, Spacing.LG, Spacing.MD
        )
        layout.setSpacing(Spacing.SM)
        layout.addWidget(self._resize_handle)
        layout.addWidget(self._attachment_bar)
        layout.addLayout(input_row, stretch=1)
        layout.addLayout(toolbar)

        # ── 边框样式 ────────────────────────────
        toolbar_line_style = f"""
            border-top: 1px solid {Colors.DIVIDER};
        """
        # Apply top-border via a separator approach - the toolbar itself handles this
        apply_style(self, lambda: f"""
            InputArea {{
                background-color: {Colors.BG_SURFACE};
                border: none;
                border-top: 1px solid {Colors.BORDER};
            }}
        """)

    # ── 图钉 / 高度控制 ───────────────────────

    def _on_pin_toggle(self) -> None:
        """切换图钉模式：自动调整 ↔ 手动拖动。"""
        self._pinned = not self._pinned
        self._apply_pin_style()
        self._resize_handle.setVisible(not self._pinned)

        if self._pinned:
            # 切回自动模式：解除 InputArea 的固定高度，让文本驱动高度
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)  # QWIDGETSIZE_MAX
            self._auto_resize_text_edit()
        else:
            # 切到手动模式：以当前高度为初始手动高度
            current = self.height()
            self._manual_height = current if current > 50 else 200
            self._apply_manual_height()

    def _apply_pin_style(self) -> None:
        """根据图钉状态更新按钮样式。"""
        if self._pinned:
            apply_style(self._pin_btn, lambda: f"""
                QPushButton {{
                    color: {Colors.PRIMARY};
                    background-color: {Colors.PRIMARY_GLOW};
                    border: 1px solid {Colors.PRIMARY};
                    border-radius: 4px;
                    padding: 2px;
                }}
                QPushButton:hover {{
                    background-color: {Colors.PRIMARY}33;
                }}
            """)
            self._pin_btn.setToolTip("自动调整高度：已启用（点击切换为手动调整）")
        else:
            apply_style(self._pin_btn, lambda: f"""
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
            self._pin_btn.setToolTip("手动调整高度：已禁用（点击切换为自动调整）")

    def _on_text_changed_for_resize(self) -> None:
        """文本内容变化时，图钉模式下自动调整高度。"""
        if self._pinned:
            # 延迟到下一轮事件循环，确保 QTextDocument 已完成布局
            QTimer.singleShot(0, self._auto_resize_text_edit)

    def _auto_resize_text_edit(self) -> None:
        """根据文档内容高度调整文本编辑框高度（1~14 行）。"""
        if not self._pinned:
            return

        doc = self._text_edit.document()
        doc_height = int(doc.size().height())
        # stylesheet padding 8px top + 8px bottom
        target = doc_height + 16
        min_h = self._line_height + 16   # 最少 1 行
        target = max(min_h, min(target, self._max_text_height))

        self._text_edit.setMinimumHeight(target)
        self._text_edit.setMaximumHeight(target)

    def _estimate_chrome(self) -> int:
        """估算输入栏中除文本编辑框内容区之外的总高度（布局边距/间距/控件）。"""
        chrome = 0
        # layout margins (top + bottom)
        chrome += Spacing.MD * 2  # 24
        # layout spacing: resize_handle ↔ attachment ↔ input_row ↔ toolbar
        # 4 widgets → 3 gaps (attachment 隐藏时不计高度但 gap 仍存在)
        chrome += Spacing.SM * 3  # 24
        # resize handle
        if self._resize_handle.isVisible():
            chrome += self._resize_handle.height()  # 6
        # attachment bar
        if self._attachment_bar.isVisible():
            chrome += self._attachment_bar.sizeHint().height()
        # toolbar — 芯片/按钮高度 ≈ 每行 36~40
        chrome += 40
        return chrome

    def _apply_manual_height(self) -> None:
        """将手动拖拽高度应用到 InputArea 和文本编辑框。"""
        h = self._manual_height
        # 上下限：至少 1 行文字 + chrome，至多 14 行文字 + chrome
        chrome = self._estimate_chrome()
        min_total = self._line_height + 16 + chrome
        max_total = self._max_text_height + chrome
        h = max(min_total, min(h, max_total))
        self._manual_height = h

        self.setMinimumHeight(h)
        self.setMaximumHeight(h)

        # 文本编辑框填充剩余空间
        text_h = h - chrome
        text_h = max(self._line_height + 16, min(text_h, self._max_text_height))
        self._text_edit.setMinimumHeight(text_h)
        self._text_edit.setMaximumHeight(text_h)

    # ── 事件过滤器（Enter 发送 + 拖拽把手）───

    def eventFilter(self, obj, event) -> bool:
        """拦截文本编辑框按键（Enter 发送）和拖拽把手鼠标事件。"""
        # ── 拖拽把手：鼠标拖拽调整输入栏高度 ──
        if obj is self._resize_handle:
            if event.type() == event.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._resize_dragging = True
                    self._resize_drag_start_y = event.globalPosition().y()
                    self._resize_drag_start_h = self.height()
                    self._resize_handle.grabMouse()
                    return True
            elif event.type() == event.Type.MouseMove and self._resize_dragging:
                delta = self._resize_drag_start_y - event.globalPosition().y()
                self._manual_height = self._resize_drag_start_h + int(delta)
                self._apply_manual_height()
                return True
            elif event.type() == event.Type.MouseButtonRelease and self._resize_dragging:
                self._resize_dragging = False
                self._resize_handle.releaseMouse()
                return True
            return super().eventFilter(obj, event)

        # ── 文本编辑框：Enter 发送 ──
        if obj is self._text_edit and event.type() == event.Type.KeyPress:
            key_event = event
            if key_event.key() == Qt.Key.Key_Return or key_event.key() == Qt.Key.Key_Enter:
                if not key_event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self._on_send_click()
                    return True
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:
        """InputArea 宽度变化时（窗口缩放），图钉模式下重算文本高度（影响换行）。"""
        super().resizeEvent(event)
        if self._pinned:
            QTimer.singleShot(0, self._auto_resize_text_edit)

    # ── 按钮样式 ──────────────────────────────

    def _make_toolbar_btn(self, text: str, tooltip: str) -> QPushButton:
        """创建工具栏小按钮。"""
        btn = QPushButton(text)
        btn.setFont(Fonts.body(12))
        btn.setToolTip(tooltip)
        btn.setFixedSize(28, 28)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_style(btn, lambda: f"""
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
        # 思考关闭时禁用思考强度控件（API 要求 thinking 启用才可传 reasoning_effort）
        self._effort_selector.setEnabled(val)
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

    def refresh_theme(self) -> None:
        """切主题时重绘运行时状态相关的样式（发送/图钉按钮 + 开关芯片）。"""
        self._apply_send_style()
        self._apply_pin_style()
        self._thinking_chip.refresh_theme()
        self._search_chip.refresh_theme()

    def clear(self) -> None:
        """清空输入框和附件。"""
        self._text_edit.clear()
        self._attachment_bar.clear_files()

    def set_enabled(self, enabled: bool) -> None:
        """启用/禁用输入区。"""
        self._text_edit.setEnabled(enabled)
        self._send_btn.setEnabled(enabled)

    def set_text_locked(self, locked: bool) -> None:
        """锁定/解锁输入框（预分支状态：禁发新消息，但停止按钮保持可用，3.1.3）。"""
        self._text_edit.setEnabled(not locked)
        self._file_btn.setEnabled(not locked)
        self._thinking_chip.setEnabled(not locked)
        self._search_chip.setEnabled(not locked)
        self._model_selector.setEnabled(not locked)

    # ── 修改状态（5.1/5.2）────────────────────

    def enter_edit_mode(self, text: str) -> None:
        """进入修改状态：文本填入输入框、显示 ✕ 按钮（5.1）。"""
        self._edit_mode = True
        self._text_edit.setPlainText(text or "")
        self._text_edit.setFocus()
        self._edit_cancel_btn.setVisible(True)

    def exit_edit_mode(self, keep_text: bool = True) -> None:
        """退出修改状态：隐藏 ✕ 按钮；文本保留（5.2）。"""
        self._edit_mode = False
        self._edit_cancel_btn.setVisible(False)
        if not keep_text:
            self._text_edit.clear()

    def is_edit_mode(self) -> bool:
        """当前是否处于修改状态。"""
        return self._edit_mode

    def has_text(self) -> bool:
        """输入框是否有内容（修改状态进入时的覆盖确认用，5.1.2）。"""
        return bool(self._text_edit.toPlainText().strip())
