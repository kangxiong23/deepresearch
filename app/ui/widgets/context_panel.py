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
    QTextBrowser,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QCheckBox,
    QApplication,
    QGraphicsOpacityEffect,
)
from PySide6.QtCore import Qt, Signal, QPoint, QMimeData, QTimer
from PySide6.QtGui import (
    QFont,
    QTextOption,
    QDrag,
    QPixmap,
    QPainter,
)

from app.ui.theme import apply_style, Colors, Fonts, Spacing, Radius


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
    edit_requested = Signal(str)     # block_id
    copy_requested = Signal(str)     # block_id

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
        # 拖拽排序：长按文字区拖动，半透明行副本跟随鼠标
        self._drag_press_pos: QPoint | None = None

        # ── 开关 ──────────────────────────────────
        self._switch = QCheckBox()
        self._switch.setChecked(enabled)
        self._switch.setFixedSize(36, 18)
        apply_style(self._switch, lambda: f"""
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
        # 文本区域水平尺寸策略设为 Ignored：QLabel 的 sizeHint 会随超长
        # 无空格文本膨胀，把行宽撑破面板（380px）导致右侧"移除"按钮被挤出。
        # Ignored 使其不再参与行最小宽度计算，只占据开关/按钮之外的剩余空间。
        label_widget = QLabel(label)
        label_widget.setFont(Fonts.body(Fonts.SIZE_SM, QFont.Weight.Medium))
        apply_style(label_widget, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_PRIMARY};
                background: transparent;
                border: none;
            }}
        """)
        label_widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        preview_widget = QLabel(preview)
        preview_widget.setFont(Fonts.mono(Fonts.SIZE_XS))
        apply_style(preview_widget, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_DISABLED};
                background: transparent;
                border: none;
            }}
        """)
        preview_widget.setWordWrap(True)
        preview_widget.setMaximumHeight(32)
        preview_widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        text_col.addWidget(label_widget)
        text_col.addWidget(preview_widget)

        # ── 移除按钮（编辑模式下隐藏）─────────────
        self._remove_btn = QPushButton("✕")
        self._remove_btn.setFont(Fonts.body(12))
        self._remove_btn.setFixedSize(22, 22)
        self._remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._remove_btn.setToolTip("移除")
        apply_style(self._remove_btn, lambda: f"""
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
        self._remove_btn.clicked.connect(
            lambda: self.remove_requested.emit(block_id)
        )

        # ── 编辑按钮（移除按钮下方，编辑模式下隐藏）──
        self._edit_btn = QPushButton("✏️")
        self._edit_btn.setFont(Fonts.body(11))
        self._edit_btn.setFixedSize(22, 22)
        self._edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_btn.setToolTip("编辑")
        apply_style(self._edit_btn, lambda: f"""
            QPushButton {{
                color: {Colors.PRIMARY};
                background: transparent;
                border: none;
                padding: 2px;
            }}
            QPushButton:hover {{
                background-color: {Colors.PRIMARY_GLOW};
                border-radius: 3px;
            }}
        """)
        self._edit_btn.clicked.connect(
            lambda: self.edit_requested.emit(block_id)
        )

        # ── 复制按钮（编辑按钮下方，编辑模式下隐藏）──
        self._copy_btn = QPushButton("📋")
        self._copy_btn.setFont(Fonts.body(10))
        self._copy_btn.setFixedSize(22, 22)
        self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_btn.setToolTip("复制（克隆到下一位置）")
        apply_style(self._copy_btn, lambda: f"""
            QPushButton {{
                color: {Colors.TEXT_SECONDARY};
                background: transparent;
                border: none;
                padding: 2px;
            }}
            QPushButton:hover {{
                color: {Colors.TEXT_PRIMARY};
                background-color: {Colors.BG_OVERLAY};
                border-radius: 3px;
            }}
        """)
        self._copy_btn.clicked.connect(
            lambda: self.copy_requested.emit(block_id)
        )

        # ── 右侧按钮列：移除（上）+ 编辑（中）+ 复制（下）──
        btn_col = QVBoxLayout()
        btn_col.setContentsMargins(0, 0, 0, 0)
        btn_col.setSpacing(2)
        btn_col.addWidget(self._remove_btn)
        btn_col.addWidget(self._edit_btn)
        btn_col.addWidget(self._copy_btn)

        # ── 行布局 ────────────────────────────────
        row_layout = QHBoxLayout(self)
        row_layout.setContentsMargins(Spacing.SM, Spacing.SM, Spacing.SM, Spacing.SM)
        row_layout.setSpacing(Spacing.SM)
        row_layout.addWidget(self._switch)
        row_layout.addLayout(text_col, stretch=1)
        row_layout.addLayout(btn_col)

        # ── 底部边框 ──────────────────────────────
        apply_style(self, lambda: f"""
            _ContextBlockRow {{
                background-color: transparent;
                border-bottom: 1px solid {Colors.DIVIDER};
            }}
        """)

        # 文字区可拖拽提示（开关/移除按钮等子控件有各自的 cursor）
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    # ──────────────────────────────────────────
    # 拖拽排序（参考对话树 _TreeView 但大幅简化）
    # ──────────────────────────────────────────
    # 鼠标按下发生在文字区（开关/移除按钮等子控件自行消费事件，不会触发拖拽）；
    # 移动超过阈值后启动 QDrag —— 半透明行图像跟随鼠标。
    # 放置位置与插入指示线由 _BlockListContainer 计算。

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_press_pos is not None:
            distance = (
                event.position().toPoint() - self._drag_press_pos
            ).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._drag_press_pos = None
                self._start_block_drag(event)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_press_pos = None
        super().mouseReleaseEvent(event)

    def _start_block_drag(self, event) -> None:
        """启动上下文块拖拽。"""
        if not self._block_id:
            return

        # 半透明拖拽图像（0.6 透明度）
        grabbed = self.grab()
        drag_pixmap = QPixmap(grabbed.size())
        drag_pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(drag_pixmap)
        painter.setOpacity(0.6)
        painter.drawPixmap(0, 0, grabbed)
        painter.end()

        # 自定义 MIME 数据，供 _BlockListContainer 识别
        mime = QMimeData()
        mime.setData(
            "application/x-contextblock-id", self._block_id.encode("utf-8")
        )

        # 原行半透明置灰（拖拽结束后恢复）
        self._set_source_dimmed(True)

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(drag_pixmap)
        drag.setHotSpot(event.position().toPoint())
        drag.exec(Qt.DropAction.MoveAction)

        self._set_source_dimmed(False)

    def _set_source_dimmed(self, on: bool) -> None:
        """拖拽期间把源行变半透明。"""
        if on:
            effect = QGraphicsOpacityEffect(self)
            effect.setOpacity(0.35)
            self.setGraphicsEffect(effect)
        else:
            self.setGraphicsEffect(None)

    def set_edited(self, edited: bool) -> None:
        """编辑模式下：隐藏移除/编辑/复制按钮并让整行半透明。"""
        self._remove_btn.setVisible(not edited)
        self._edit_btn.setVisible(not edited)
        self._copy_btn.setVisible(not edited)
        if edited:
            effect = QGraphicsOpacityEffect(self)
            effect.setOpacity(0.4)
            self.setGraphicsEffect(effect)
        else:
            self.setGraphicsEffect(None)


# ──────────────────────────────────────────────
# 已选上下文块列表容器（接收拖放，计算插入位置）
# ──────────────────────────────────────────────


class _BlockListContainer(QWidget):
    """
    已选上下文块列表容器：接收同面板拖入的上下文块，计算插入位置，
    绘制插入指示线，发射 reorder_requested 交由面板处理。

    拖拽逻辑参考对话树（_TreeView）但大幅简化：无类型约束、无防循环、
    无多选 —— 只在兄弟行之间调整顺序。
    """

    reorder_requested = Signal(str, int)  # block_id, new_index

    _INDICATOR_HEIGHT = 2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._blocks_layout: QVBoxLayout | None = None

        # 插入指示线（绝对定位的子控件，不参与布局）
        self._drop_indicator = QFrame(self)
        self._drop_indicator.setFixedHeight(self._INDICATOR_HEIGHT)
        apply_style(
            self._drop_indicator,
            lambda: f"background-color: {Colors.PRIMARY}; border: none;",
        )
        self._drop_indicator.hide()

    def set_blocks_layout(self, layout: QVBoxLayout) -> None:
        """绑定块列表布局（用于枚举行顺序）。"""
        self._blocks_layout = layout

    # ── 拖拽事件 ──────────────────────────────

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat("application/x-contextblock-id"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if not event.mimeData().hasFormat("application/x-contextblock-id"):
            event.ignore()
            return
        index = self._compute_insert_index(event.position().toPoint())
        self._show_indicator(index)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._drop_indicator.hide()
        event.accept()

    def dropEvent(self, event) -> None:
        mime = event.mimeData()
        if not mime.hasFormat("application/x-contextblock-id"):
            event.ignore()
            self._drop_indicator.hide()
            return
        block_id = mime.data("application/x-contextblock-id").data().decode("utf-8")
        index = self._compute_insert_index(event.position().toPoint())
        self._drop_indicator.hide()
        event.acceptProposedAction()
        self.reorder_requested.emit(block_id, index)

    # ── 插入位置计算与指示线 ───────────────────

    def _rows(self) -> list:
        """按布局顺序返回所有 _ContextBlockRow 子行。"""
        rows = []
        if self._blocks_layout is None:
            return rows
        for i in range(self._blocks_layout.count()):
            item = self._blocks_layout.itemAt(i)
            w = item.widget() if item is not None else None
            if isinstance(w, _ContextBlockRow):
                rows.append(w)
        return rows

    def _compute_insert_index(self, pos: QPoint) -> int:
        """根据鼠标 y 坐标计算插入位置（0..len(rows)）。"""
        rows = self._rows()
        for i, row in enumerate(rows):
            if pos.y() < row.geometry().center().y():
                return i
        return len(rows)

    def _show_indicator(self, index: int) -> None:
        """在插入位置显示指示线。"""
        rows = self._rows()
        if not rows:
            self._drop_indicator.hide()
            return
        if index <= 0:
            y = rows[0].geometry().top()
        elif index >= len(rows):
            y = rows[-1].geometry().bottom() + 1
        else:
            y = rows[index].geometry().top()
        self._drop_indicator.setGeometry(
            0, y, self.width(), self._INDICATOR_HEIGHT
        )
        self._drop_indicator.raise_()
        self._drop_indicator.show()


# ──────────────────────────────────────────────
# 模板条目控件
# ──────────────────────────────────────────────


class _TemplateRow(QFrame):
    """
    模板条目行：图标 + 名称 + 描述 + 应用按钮 + 删除按钮。
    与 Flet 版本的 _TemplateItem 功能完全一致。
    """

    apply_requested = Signal(str)   # template_id
    add_requested = Signal(str)     # template_id — 增量追加
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
        # 水平尺寸策略设为 Ignored：超长文本不再撑破行最小宽度，
        # 保证右侧"应用/+ /删除"按钮完整显示（与 _ContextBlockRow 一致）。
        name_widget = QLabel(name)
        name_widget.setFont(Fonts.body(Fonts.SIZE_SM))
        name_widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        apply_style(name_widget, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_PRIMARY};
                background: transparent;
                border: none;
            }}
        """)

        desc_widget = QLabel(description)
        desc_widget.setFont(Fonts.body(Fonts.SIZE_XS))
        desc_widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        apply_style(desc_widget, lambda: f"""
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
        apply_style(apply_btn, lambda: f"""
            QPushButton {{
                color: {Colors.PRIMARY};
                background-color: transparent;
                border: 1px solid {Colors.PRIMARY};
                border-radius: {Radius.SM}px;
                padding: 3px 6px;
            }}
            QPushButton:hover {{
                background-color: {Colors.PRIMARY_GLOW};
            }}
        """)
        apply_btn.clicked.connect(
            lambda: self.apply_requested.emit(template_id)
        )

        # ── 增量追加按钮 "+"（应用按钮之后）──────
        add_btn = QPushButton("+")
        add_btn.setFont(Fonts.mono(Fonts.SIZE_XS, QFont.Weight.Bold))
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setToolTip("增量追加（不替换现有块）")
        apply_style(add_btn, lambda: f"""
            QPushButton {{
                color: {Colors.ACCENT};
                background-color: transparent;
                border: 1px solid {Colors.ACCENT};
                border-radius: {Radius.SM}px;
                padding: 3px 5px;
            }}
            QPushButton:hover {{
                background-color: #64FFDA22;
            }}
        """)
        add_btn.clicked.connect(
            lambda: self.add_requested.emit(template_id)
        )

        # ── 删除按钮 ──────────────────────────────
        delete_btn = QPushButton("🗑")
        delete_btn.setFont(Fonts.body(10))
        delete_btn.setFixedSize(22, 22)
        delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_btn.setToolTip("删除模板")
        apply_style(delete_btn, lambda: f"""
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
        row_layout.addWidget(add_btn)
        row_layout.addWidget(delete_btn)

        apply_style(self, lambda: f"""
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
    reorder_block = Signal(str, int)   # block_id, new_index — 拖拽调整顺序
    add_text_block = Signal(str, str)           # content, title — 添加文本块
    edit_block_requested = Signal(str)          # block_id — 请求进入编辑模式
    save_block_edit = Signal(str, str, str)     # block_id, new_content, new_title — 保存修改
    copy_block = Signal(str)                    # block_id — 复制/克隆上下文块
    save_as_template = Signal(str)
    apply_template = Signal(str)                # 整体替换应用
    add_template = Signal(str)                  # 增量追加应用
    delete_template = Signal(str)
    close_requested = Signal()
    apply_node_clicked = Signal()               # 节点模式：点击"应用"

    # 输入框自动高度的最大行数
    _MAX_INPUT_LINES = 10

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("contextPanel")
        self.setFixedWidth(380)

        # 编辑模式状态：正在编辑的块 ID（None = 未编辑）
        self._edit_block_id: str | None = None

        # 节点上下文管理模式状态：True = 正在管理某节点的挂靠块
        self._node_mode: bool = False
        self._dirty: bool = False

        # ── 头部 ──────────────────────────────────
        header = self._build_header()

        # ── 已选上下文块区域（标题行内嵌"保存为模板"）──
        blocks_section = self._build_blocks_section()

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
        apply_style(scroll, lambda: f"""
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
        apply_style(self, lambda: f"""
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
        apply_style(header, lambda: f"""
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

        self._header_title = QLabel("上下文管理")
        self._header_title.setFont(Fonts.mono(Fonts.SIZE_LG, QFont.Weight.DemiBold))
        apply_style(self._header_title, lambda: f"color: {Colors.TEXT_PRIMARY}; background: transparent; border: none;")

        layout.addWidget(icon)
        layout.addWidget(self._header_title)
        layout.addStretch()

        # 节点模式"应用"控件（纯文字，灰色 → 有改动变绿）
        self._apply_btn = QPushButton("应用")
        self._apply_btn.setFont(Fonts.body(Fonts.SIZE_SM))
        self._apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_btn.setToolTip("将启用的上下文块应用到节点")
        self._apply_btn.clicked.connect(self.apply_node_clicked.emit)
        self._apply_btn.hide()
        layout.addWidget(self._apply_btn)

        close_btn = QPushButton("✕")
        close_btn.setFont(Fonts.body(14))
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setToolTip("关闭")
        apply_style(close_btn, lambda: f"""
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
        """构建已选上下文块列表区域。

        标题行左侧为"已选上下文块"，右侧内嵌"保存为模板"控件
        （模板名称输入框 + 🔖 按钮）。
        """
        section = QWidget()
        section.setStyleSheet("QWidget { background-color: transparent; }")

        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 标题行：标题 + 保存为模板控件 ─────────
        header_row = QWidget()
        header_row.setStyleSheet("QWidget { background-color: transparent; }")

        header_layout = QHBoxLayout(header_row)
        header_layout.setContentsMargins(Spacing.LG, Spacing.MD, Spacing.MD, Spacing.SM)
        header_layout.setSpacing(Spacing.SM)

        section_label = QLabel("已选上下文块")
        section_label.setFont(Fonts.mono(Fonts.SIZE_XS))
        apply_style(section_label, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_DISABLED};
                background: transparent;
                border: none;
            }}
        """)
        header_layout.addWidget(section_label)

        self._template_name_input = QLineEdit()
        self._template_name_input.setPlaceholderText("输入模板名称，保存当前块为模板...")
        self._template_name_input.setFont(Fonts.body(Fonts.SIZE_XS))
        apply_style(self._template_name_input, lambda: f"""
            QLineEdit {{
                color: {Colors.TEXT_PRIMARY};
                background-color: {Colors.BG_ELEVATED};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 4px 8px;
            }}
            QLineEdit:focus {{
                border-color: {Colors.PRIMARY};
            }}
        """)
        header_layout.addWidget(self._template_name_input, stretch=1)

        save_btn = QPushButton("🔖")
        save_btn.setFont(Fonts.body(14))
        save_btn.setFixedSize(28, 28)
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setToolTip("保存为模板")
        apply_style(save_btn, lambda: f"""
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
        header_layout.addWidget(save_btn)

        layout.addWidget(header_row)

        # 块列表容器（接收拖放，计算插入位置）
        self._blocks_container = _BlockListContainer()
        self._blocks_container.setStyleSheet("QWidget { background-color: transparent; }")
        self._blocks_layout = QVBoxLayout(self._blocks_container)
        self._blocks_layout.setContentsMargins(0, 0, 0, 0)
        self._blocks_layout.setSpacing(0)
        self._blocks_container.set_blocks_layout(self._blocks_layout)
        self._blocks_container.reorder_requested.connect(self.reorder_block)

        # 固定高度的滚动区域
        blocks_scroll = QScrollArea()
        blocks_scroll.setWidgetResizable(True)
        blocks_scroll.setFixedHeight(200)
        blocks_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        apply_style(blocks_scroll, lambda: f"""
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

    def _on_save_template(self) -> None:
        """保存为模板按钮：发送模板名称。"""
        name = self._template_name_input.text().strip()
        if name:
            self.save_as_template.emit(name)
            self._template_name_input.clear()

    # ── 添加文本块区域 ───────────────────────────

    def _build_add_block_section(self) -> QWidget:
        """构建添加文本块区域（编辑模式下变为"修改文本块"）。

        编辑模式布局：输入框右侧为 [✕ 取消修改]（上） + [保存修改]（下）。
        """
        section = QWidget()
        apply_style(section, lambda: f"""
            QWidget {{
                background-color: transparent;
                border-bottom: 1px solid {Colors.DIVIDER};
            }}
        """)

        layout = QVBoxLayout(section)
        layout.setContentsMargins(Spacing.LG, Spacing.MD, Spacing.LG, Spacing.MD)
        layout.setSpacing(Spacing.SM)

        self._add_block_label = QLabel("添加文本块")
        self._add_block_label.setFont(Fonts.mono(Fonts.SIZE_XS))
        apply_style(self._add_block_label, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_DISABLED};
                background: transparent;
                border: none;
            }}
        """)

        # 标题输入框（单行；编辑模式下复制被编辑块的标题）
        self._block_title_input = QLineEdit()
        self._block_title_input.setPlaceholderText("标题（可选，默认取内容前15字）")
        self._block_title_input.setFont(Fonts.body(Fonts.SIZE_XS))
        apply_style(self._block_title_input, lambda: f"""
            QLineEdit {{
                color: {Colors.TEXT_PRIMARY};
                background-color: {Colors.BG_ELEVATED};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 3px 8px;
            }}
            QLineEdit:focus {{
                border-color: {Colors.PRIMARY};
            }}
        """)

        header_row = QHBoxLayout()
        header_row.setSpacing(Spacing.SM)
        header_row.addWidget(self._add_block_label)
        header_row.addWidget(self._block_title_input, stretch=1)
        layout.addLayout(header_row)

        input_row = QHBoxLayout()
        input_row.setSpacing(Spacing.SM)

        self._new_block_input = QTextEdit()
        self._new_block_input.setPlaceholderText("输入自定义上下文内容...")
        self._new_block_input.setFont(Fonts.body(Fonts.SIZE_SM))
        apply_style(self._new_block_input, lambda: f"""
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
        # 单行行高（用于 10 行封顶高度）
        self._line_height = self._new_block_input.fontMetrics().lineSpacing()
        # 根据文字行数自动调整高度（最多 _MAX_INPUT_LINES 行）
        self._new_block_input.textChanged.connect(self._on_input_text_changed)

        # 取消编辑按钮（仅编辑模式显示，位于保存修改上方）
        self._cancel_edit_btn = QPushButton("✕")
        self._cancel_edit_btn.setFont(Fonts.body(14))
        self._cancel_edit_btn.setFixedSize(36, 36)
        self._cancel_edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_edit_btn.setToolTip("取消修改")
        apply_style(self._cancel_edit_btn, lambda: f"""
            QPushButton {{
                color: {Colors.ERROR};
                background: transparent;
                border: none;
                padding: 4px;
            }}
            QPushButton:hover {{
                background-color: {Colors.ERROR}22;
                border-radius: 4px;
            }}
        """)
        self._cancel_edit_btn.clicked.connect(self._on_cancel_edit)
        self._cancel_edit_btn.hide()

        # 添加 / 保存修改 按钮（图标样式，与"保存为模板"🔖 同风格）
        self._add_btn = QPushButton("＋")
        self._add_btn.setFont(Fonts.body(18))
        self._add_btn.setFixedSize(36, 36)
        self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_btn.setToolTip("添加")
        self._set_add_btn_style(save_mode=False)
        self._add_btn.clicked.connect(self._on_add_block)

        btn_col = QVBoxLayout()
        btn_col.setContentsMargins(0, 0, 0, 0)
        btn_col.setSpacing(4)
        btn_col.addWidget(self._cancel_edit_btn)
        btn_col.addWidget(self._add_btn)
        btn_col.addStretch()

        input_row.addWidget(self._new_block_input, stretch=1)
        input_row.addLayout(btn_col)
        input_row.setAlignment(btn_col, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(input_row)

        # 初始高度
        self._adjust_input_height()

        return section

    def _adjust_input_height(self) -> None:
        """根据文档实际渲染高度调整输入框高度，最多 10 行。

        用 document().size().height() 而非 lineCount()：lineCount() 只统计
        段落数，不反映长文本按当前宽度换行后的真实行数；size() 是布局后的
        实际渲染高度（与 InputArea 的自动高度一致）。
        """
        doc = self._new_block_input.document()
        doc_height = int(doc.size().height())
        extra = 2 * (Spacing.SM + 1)  # 上下 QSS padding + 边框
        target = doc_height + extra
        max_h = self._MAX_INPUT_LINES * self._line_height + extra
        self._new_block_input.setFixedHeight(max(48, min(target, max_h)))

    def _on_input_text_changed(self) -> None:
        """内容变化后延迟到文档布局完成再重算高度。

        setPlainText() 等程序化写入后，QTextDocument 尚未按当前宽度完成布局，
        立即读 document().size() 会拿到旧值。延迟到下一个事件循环即可拿到
        换行后的真实高度。
        """
        QTimer.singleShot(0, self._adjust_input_height)

    def resizeEvent(self, event) -> None:
        """面板尺寸变化（含隐藏→显示）后重算输入框高度（影响换行）。"""
        super().resizeEvent(event)
        if hasattr(self, "_new_block_input"):
            QTimer.singleShot(0, self._adjust_input_height)

    def _set_add_btn_style(self, save_mode: bool) -> None:
        """设置添加/保存按钮图标颜色（添加=主蓝，保存=成功绿）。"""
        color = Colors.SUCCESS if save_mode else Colors.PRIMARY
        hover_bg = f"{color}22" if save_mode else Colors.PRIMARY_GLOW
        self._add_btn.setStyleSheet(f"""
            QPushButton {{
                color: {color};
                background: transparent;
                border: none;
                padding: 4px;
            }}
            QPushButton:hover {{
                background-color: {hover_bg};
                border-radius: 4px;
            }}
        """)

    def _on_add_block(self) -> None:
        """添加文本块 / 保存修改 按钮。"""
        text = self._new_block_input.toPlainText().strip()
        title = self._block_title_input.text().strip()
        if self._edit_block_id is not None:
            # 编辑模式 → 保存修改
            if text:
                self.save_block_edit.emit(self._edit_block_id, text, title)
                self._exit_edit_mode()
        else:
            if text:
                self.add_text_block.emit(text, title)
                self._new_block_input.clear()
                self._block_title_input.clear()

    def _on_cancel_edit(self) -> None:
        """取消修改：退出编辑模式。"""
        self._exit_edit_mode()

    # ── 编辑模式状态管理 ────────────────────────

    def enter_edit_mode(self, block_id: str, content: str, title: str = "") -> None:
        """进入编辑模式：填充输入框与标题，切换按钮与标签，被编辑行半透明。"""
        # 若正在编辑其他块，先退出（保留输入内容，随后覆盖）
        self._exit_edit_mode(clear_input=False)
        self._edit_block_id = block_id

        row = self._find_block_row(block_id)
        if row is not None:
            row.set_edited(True)

        self._add_block_label.setText("修改文本块")
        self._block_title_input.setText(title or "")
        # setPlainText 触发 textChanged → 延迟到布局完成后重算高度
        self._new_block_input.setPlainText(content)
        self._add_btn.setText("✓")
        self._add_btn.setFixedSize(36, 36)
        self._add_btn.setToolTip("保存修改")
        self._set_add_btn_style(save_mode=True)
        self._cancel_edit_btn.show()

    def _exit_edit_mode(self, *, clear_input: bool = True) -> None:
        """退出编辑模式，恢复输入区与被编辑行。"""
        if self._edit_block_id is None:
            return
        row = self._find_block_row(self._edit_block_id)
        if row is not None:
            row.set_edited(False)
        self._edit_block_id = None

        self._add_block_label.setText("添加文本块")
        self._add_btn.setText("＋")
        self._add_btn.setFixedSize(36, 36)
        self._add_btn.setToolTip("添加")
        self._set_add_btn_style(save_mode=False)
        self._cancel_edit_btn.hide()
        if clear_input:
            self._new_block_input.clear()
            self._block_title_input.clear()
            self._adjust_input_height()

    def _find_block_row(self, block_id: str):
        """按块 ID 在当前块列表中查找对应行。"""
        for row in self._blocks_container._rows():
            if row._block_id == block_id:
                return row
        return None

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
        apply_style(section_label, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_DISABLED};
                background: transparent;
                border: none;
                padding: {Spacing.MD}px {Spacing.LG}px {Spacing.SM}px {Spacing.LG}px;
            }}
        """)
        layout.addWidget(section_label)

        # 用只读 QTextBrowser 承载预览：内容超出 120px 高度时自动出现
        # 垂直滚动条，可查看全部内容（边框固定不随内容滚动）。
        self._preview_text = QTextBrowser()
        self._preview_text.setReadOnly(True)
        self._preview_text.setFixedHeight(120)
        self._preview_text.setFont(Fonts.mono(Fonts.SIZE_XS))
        self._preview_text.setWordWrapMode(
            QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere
        )
        apply_style(self._preview_text, lambda: f"""
            QTextBrowser {{
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
        apply_style(section_label, lambda: f"""
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
        apply_style(templates_scroll, lambda: f"""
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

    # ── 节点上下文管理模式 ─────────────────────

    def enter_node_mode(self, node_name: str, blocks: list[dict]) -> None:
        """
        进入节点上下文管理模式：标题变为"上下文管理（{node_name}）"，
        "已选上下文块"加载节点挂靠的块，标题栏右侧显示灰色"应用"。
        """
        self._node_mode = True
        self._exit_edit_mode()
        self._header_title.setText(f"上下文管理（{node_name}）")
        self._apply_btn.show()
        self.set_dirty(False)
        self.load_blocks(blocks)

    def exit_node_mode(self) -> None:
        """退出节点模式，恢复全局模式外观。"""
        self._node_mode = False
        self._dirty = False
        self._apply_btn.hide()
        self._header_title.setText("上下文管理")

    def is_node_mode(self) -> bool:
        """当前是否处于节点上下文管理模式。"""
        return self._node_mode

    def has_dirty(self) -> bool:
        """节点模式下是否有未应用的修改。"""
        return self._dirty

    def set_dirty(self, dirty: bool) -> None:
        """设置未应用修改状态，并同步"应用"按钮颜色（脏=绿）。"""
        self._dirty = dirty
        self._set_apply_btn_color(dirty)

    def loaded_block_ids(self) -> list[str]:
        """返回当前"已选上下文块"列表中的块 ID（用于覆盖确认比对）。"""
        return [row._block_id for row in self._blocks_container._rows()]

    def set_template_name(self, name: str) -> None:
        """恢复模板名称输入框内容（取消/不覆盖保存时保留用户输入）。"""
        self._template_name_input.setText(name or "")

    def _set_apply_btn_color(self, dirty: bool) -> None:
        """"应用"按钮颜色：灰色（未改动）→ 绿色（有改动）。"""
        color = Colors.SUCCESS if dirty else Colors.TEXT_SECONDARY
        self._apply_btn.setStyleSheet(f"""
            QPushButton {{
                color: {color};
                background: transparent;
                border: none;
                padding: 4px 8px;
            }}
            QPushButton:hover {{
                color: {Colors.TEXT_PRIMARY};
                background-color: {Colors.BG_OVERLAY};
                border-radius: 4px;
            }}
        """)

    def refresh_theme(self) -> None:
        """切主题时按当前状态重绘"添加/保存"与"应用"按钮样式。"""
        self._set_add_btn_style(self._edit_block_id is not None)
        self._set_apply_btn_color(self._dirty)

    def load_blocks(self, items: list[dict]) -> None:
        """
        加载上下文块列表。

        Args:
            items: dict 列表，每项含 id, label, preview, enabled
        """
        # 清除旧条目
        self._clear_layout(self._blocks_layout)

        edit_id = self._edit_block_id

        for block in items:
            row = _ContextBlockRow(
                block_id=block.get("id", ""),
                label=block.get("label", ""),
                preview=block.get("preview", ""),
                enabled=block.get("enabled", True),
            )
            row.remove_requested.connect(self.remove_block.emit)
            row.toggled.connect(self.toggle_block.emit)
            row.edit_requested.connect(self.edit_block_requested.emit)
            row.copy_requested.connect(self.copy_block.emit)
            # 重建后若仍处于编辑模式，恢复被编辑行的视觉
            if edit_id and block.get("id") == edit_id:
                row.set_edited(True)
            self._blocks_layout.addWidget(row)

        self._blocks_layout.addStretch()

        # 被编辑的块已不存在（如被删除）→ 退出编辑模式
        if edit_id and not any(b.get("id") == edit_id for b in items):
            self._exit_edit_mode()

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
            row.add_requested.connect(self.add_template.emit)
            row.delete_requested.connect(self.delete_template.emit)
            self._templates_layout.addWidget(row)

        self._templates_layout.addStretch()

    def set_preview(self, text: str) -> None:
        """
        更新拼接预览文本。

        Args:
            text: 预览文本内容
        """
        self._preview_text.setPlainText(text or "（尚无上下文）")
        # 内容刷新后回到顶部
        self._preview_text.verticalScrollBar().setValue(0)

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
