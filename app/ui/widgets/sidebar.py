# Layer: UI (PySide6) → widgets
# File: app/ui/widgets/sidebar.py
# Responsibility: 侧边栏 — 头部 + 操作按钮 + TreePanel + 底部入口
#                 替换 Phase 1/2 的 SidebarPlaceholder 占位控件。
#                 与 Flet 版本 app/ui_flet_legacy/widgets/sidebar.py 功能完全对等。

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QIcon

from app.ui.theme import apply_style, Colors, Fonts, Spacing, Radius
from app.ui.widgets.tree_panel import TreePanel


class Sidebar(QWidget):
    """
    左侧边栏（Phase 3 — 完整树形面板替代占位符）。

    结构（从上到下）：
        ┌──────────────────────┐
        │  [DR] DeepResearch   │  ← 头部
        ├──────────────────────┤
        │  [＋对话] [📁文件夹]│  ← 操作按钮行
        ├──────────────────────┤
        │  对话历史             │  ← 节标签
        ├──────────────────────┤
        │                      │
        │  TreePanel (expand)  │  ← 树形面板
        │                      │
        ├──────────────────────┤
        │  🗑️ 回收站           │  ← 底部入口
        │  上下文管理           │
        │  知识图谱             │
        └──────────────────────┘

    公开接口：
        load_tree(nodes) → None
        set_active(node_id) → None
        tree_panel → TreePanel (供 ChatApp 直接连接信号)
    """

    # ── Sidebar 级别信号（顶部按钮 + 底部入口）──
    new_conversation_clicked = Signal()
    new_folder_clicked = Signal()
    open_trash_clicked = Signal()
    open_context_panel_clicked = Signal()
    open_kg_panel_clicked = Signal()
    search_requested = Signal(str)  # 携带搜索关键词（去抖后）
    multi_select_toggled = Signal(bool)  # True=进入多选, False=退出多选
    refresh_requested = Signal()    # 刷新整个对话树界面与对话界面
    open_theme_clicked = Signal()   # 打开主题风格选择对话框

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(260)

        # ── 头部 ──────────────────────────────────
        header = self._build_header()

        # ── 操作按钮行 ────────────────────────────
        button_row = self._build_button_row()

        # ── 节标签 ────────────────────────────────
        section_label = QLabel("对话历史")
        section_label.setFont(Fonts.mono(Fonts.SIZE_XS))
        apply_style(section_label, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_DISABLED};
                background-color: transparent;
                border: none;
                padding: {Spacing.SM}px {Spacing.LG}px;
            }}
        """)

        # ── 搜索框 ──────────────────────────────
        self._search_box = self._build_search_box()
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._on_search_timeout)
        self._search_box.textChanged.connect(self._on_search_text_changed)

        # ── 树形面板 ──────────────────────────────
        self._tree_panel = TreePanel()

        # ── 底部入口 ──────────────────────────────
        bottom_widget = self._build_bottom_entries()

        # ── 组装 ──────────────────────────────────
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(button_row)
        layout.addWidget(section_label)
        layout.addWidget(self._search_box)
        layout.addWidget(self._tree_panel, stretch=1)
        layout.addWidget(bottom_widget)

        # ── 整体边框 ──────────────────────────────
        apply_style(self, lambda: f"""
            Sidebar {{
                background-color: {Colors.BG_SURFACE};
                border-right: 1px solid {Colors.BORDER};
            }}
        """)

    # ── 头部 ──────────────────────────────────────

    def _build_header(self) -> QWidget:
        """构建侧边栏头部：32×32 DR logo + DeepResearch 标题。"""
        # DR logo
        logo_label = QLabel("DR")
        logo_label.setFont(Fonts.mono(Fonts.SIZE_MD, QFont.Weight.Bold))
        apply_style(logo_label, lambda: f"""
            QLabel {{
                color: {Colors.PRIMARY};
                background-color: transparent;
                border: none;
            }}
        """)
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_label.setFixedSize(32, 32)

        logo_container = QWidget()
        logo_container.setFixedSize(32, 32)
        apply_style(logo_container, lambda: f"""
            QWidget {{
                background-color: {Colors.PRIMARY_GLOW};
                border: 1px solid {Colors.PRIMARY};
                border-radius: {Radius.SM}px;
            }}
        """)
        logo_inner = QVBoxLayout(logo_container)
        logo_inner.setContentsMargins(0, 0, 0, 0)
        logo_inner.setSpacing(0)
        logo_inner.addWidget(logo_label)
        logo_inner.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 标题
        title_label = QLabel("DeepResearch")
        title_label.setFont(Fonts.mono(Fonts.SIZE_LG, QFont.Weight.DemiBold))
        apply_style(title_label, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_PRIMARY};
                background-color: transparent;
                border: none;
            }}
        """)

        # 刷新按钮（标题右侧：重绘整个对话树界面与对话界面）
        refresh_btn = QPushButton("⟳")
        refresh_btn.setFont(Fonts.body(Fonts.SIZE_MD))
        refresh_btn.setFixedSize(28, 28)
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.setToolTip("刷新对话树与对话界面")
        apply_style(refresh_btn, lambda: f"""
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
        refresh_btn.clicked.connect(self.refresh_requested)

        # 头行
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(Spacing.SM)
        header_row.addWidget(logo_container)
        header_row.addWidget(title_label)
        header_row.addWidget(refresh_btn)
        header_row.addStretch()

        header_widget = QWidget()
        header_widget.setLayout(header_row)
        apply_style(header_widget, lambda: f"""
            QWidget {{
                background-color: transparent;
                border-bottom: 1px solid {Colors.DIVIDER};
            }}
        """)

        # 外层容器（添加 padding）
        wrapper = QWidget()
        wrapper.setStyleSheet("QWidget { background-color: transparent; border: none; }")
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(
            Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG
        )
        wrapper_layout.setSpacing(0)
        wrapper_layout.addWidget(header_widget)

        return wrapper

    # ── 操作按钮行 ────────────────────────────────

    def _build_button_row(self) -> QWidget:
        """构建操作按钮行：新建对话 + 新建文件夹 + 多选切换。"""
        # 新建对话按钮
        new_conv_btn = QPushButton("  ＋  对话")
        new_conv_btn.setFont(Fonts.body(Fonts.SIZE_SM, QFont.Weight.Medium))
        new_conv_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_style(new_conv_btn, lambda: f"""
            QPushButton {{
                color: {Colors.BG_BASE};
                background-color: {Colors.PRIMARY};
                border: none;
                border-radius: {Radius.MD}px;
                padding: 10px 12px;
            }}
            QPushButton:hover {{
                background-color: {Colors.PRIMARY_DIM};
            }}
        """)
        new_conv_btn.clicked.connect(self.new_conversation_clicked.emit)

        # 新建文件夹按钮
        new_folder_btn = QPushButton("  📁  文件夹")
        new_folder_btn.setFont(Fonts.body(Fonts.SIZE_SM, QFont.Weight.Medium))
        new_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_style(new_folder_btn, lambda: f"""
            QPushButton {{
                color: {Colors.BG_BASE};
                background-color: {Colors.PRIMARY};
                border: none;
                border-radius: {Radius.MD}px;
                padding: 10px 12px;
            }}
            QPushButton:hover {{
                background-color: {Colors.PRIMARY_DIM};
            }}
        """)
        new_folder_btn.clicked.connect(self.new_folder_clicked.emit)

        # 多选切换按钮
        self._multi_select_btn = QPushButton("  ☐  多选")
        self._multi_select_btn.setFont(Fonts.body(Fonts.SIZE_SM, QFont.Weight.Medium))
        self._multi_select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._multi_select_btn.setCheckable(True)
        apply_style(self._multi_select_btn, lambda: f"""
            QPushButton {{
                color: {Colors.TEXT_PRIMARY};
                background-color: {Colors.BG_ELEVATED};
                border: 1px solid {Colors.BORDER};
                border-radius: {Radius.MD}px;
                padding: 10px 12px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BG_OVERLAY};
            }}
            QPushButton:checked {{
                color: {Colors.BG_BASE};
                background-color: {Colors.PRIMARY};
                border-color: {Colors.PRIMARY};
            }}
        """)
        self._multi_select_btn.toggled.connect(self._on_multi_select_toggled)

        # 按钮行布局（两行：第一行"对话+文件夹"，第二行"多选"）
        row1_layout = QHBoxLayout()
        row1_layout.setContentsMargins(0, 0, 0, 0)
        row1_layout.setSpacing(Spacing.SM)
        row1_layout.addWidget(new_conv_btn, stretch=1)
        row1_layout.addWidget(new_folder_btn, stretch=1)

        row2_layout = QHBoxLayout()
        row2_layout.setContentsMargins(0, 0, 0, 0)
        row2_layout.setSpacing(Spacing.SM)
        row2_layout.addWidget(self._multi_select_btn, stretch=1)

        inner_layout = QVBoxLayout()
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(Spacing.SM)
        inner_layout.addLayout(row1_layout)
        inner_layout.addLayout(row2_layout)

        btn_row = QWidget()
        btn_row.setLayout(inner_layout)

        # 外层容器（添加 padding）
        wrapper = QWidget()
        wrapper.setStyleSheet("QWidget { background-color: transparent; border: none; }")
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(
            Spacing.LG, Spacing.MD, Spacing.LG, Spacing.MD
        )
        wrapper_layout.setSpacing(0)
        wrapper_layout.addWidget(btn_row)

        return wrapper

    def _on_multi_select_toggled(self, checked: bool) -> None:
        """多选按钮切换。"""
        if checked:
            self._multi_select_btn.setText("  ✕  退出多选")
        else:
            self._multi_select_btn.setText("  ☐  多选")
        self.multi_select_toggled.emit(checked)

    # ── 底部入口 ──────────────────────────────────

    def _build_bottom_entries(self) -> QWidget:
        """构建底部三个入口：回收站、上下文管理、知识图谱。"""
        container = QWidget()
        container.setStyleSheet("""
            QWidget {
                background-color: transparent;
                border: none;
            }
        """)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 回收站入口
        trash_entry = self._make_bottom_entry(
            icon_text="🗑️",
            label="回收站",
            on_click=lambda: self.open_trash_clicked.emit(),
        )

        # 上下文管理入口
        context_entry = self._make_bottom_entry(
            icon_text="📚",
            label="上下文管理",
            on_click=lambda: self.open_context_panel_clicked.emit(),
        )

        # 知识图谱入口
        kg_entry = self._make_bottom_entry(
            icon_text="🔗",
            label="知识图谱",
            on_click=lambda: self.open_kg_panel_clicked.emit(),
        )

        # 主题风格入口
        theme_entry = self._make_bottom_entry(
            icon_text="🎨",
            label="主题风格",
            on_click=lambda: self.open_theme_clicked.emit(),
        )

        layout.addWidget(trash_entry)
        layout.addWidget(context_entry)
        layout.addWidget(kg_entry)
        layout.addWidget(theme_entry)

        return container

    def _make_bottom_entry(
        self,
        icon_text: str,
        label: str,
        on_click,
    ) -> QWidget:
        """
        创建单个底部入口行。

        Args:
            icon_text: 图标文本（emoji）
            label:     标签文本
            on_click:  点击回调

        Returns:
            QWidget — 可点击的入口行
        """
        row_widget = QWidget()
        row_widget.setCursor(Qt.CursorShape.PointingHandCursor)

        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(
            Spacing.LG, Spacing.MD, Spacing.MD, Spacing.MD
        )
        row_layout.setSpacing(Spacing.SM)

        # 图标
        icon_label = QLabel(icon_text)
        icon_label.setFont(Fonts.body(14))
        icon_label.setFixedWidth(22)
        icon_label.setStyleSheet("""
            QLabel {
                color: inherit;
                background-color: transparent;
                border: none;
            }
        """)

        # 标签
        text_label = QLabel(label)
        text_label.setFont(Fonts.body(Fonts.SIZE_SM))
        apply_style(text_label, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_SECONDARY};
                background-color: transparent;
                border: none;
            }}
        """)

        # 右箭头
        arrow_label = QLabel("›")
        arrow_label.setFont(Fonts.body(16))
        apply_style(arrow_label, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_DISABLED};
                background-color: transparent;
                border: none;
            }}
        """)

        row_layout.addWidget(icon_label)
        row_layout.addWidget(text_label, stretch=1)
        row_layout.addWidget(arrow_label)

        # 整行样式
        apply_style(row_widget, lambda: f"""
            QWidget {{
                background-color: transparent;
                border-top: 1px solid {Colors.DIVIDER};
            }}
        """)

        # 点击事件 — 使用 mousePressEvent 实现整行点击
        row_widget.mousePressEvent = lambda e: on_click()

        return row_widget

    # ──────────────────────────────────────────────
    # ──────────────────────────────────────────────
    # 搜索框
    # ──────────────────────────────────────────────

    def _build_search_box(self) -> QLineEdit:
        """构建搜索输入框。"""
        search = QLineEdit()
        search.setPlaceholderText("搜索消息... (Ctrl+F)")
        search.setFont(Fonts.body(Fonts.SIZE_SM))
        search.setClearButtonEnabled(True)
        apply_style(search, lambda: f"""
            QLineEdit {{
                color: {Colors.TEXT_PRIMARY};
                background-color: {Colors.BG_ELEVATED};
                border: 1px solid {Colors.BORDER};
                border-radius: {Radius.MD}px;
                padding: 8px 12px;
                margin: 0 {Spacing.LG}px {Spacing.MD}px {Spacing.LG}px;
            }}
            QLineEdit:focus {{
                border-color: {Colors.PRIMARY};
            }}
        """)
        return search

    def _on_search_text_changed(self, text: str) -> None:
        """搜索文本变化 → 重启去抖定时器。"""
        self._search_timer.stop()
        if text.strip():
            self._search_timer.start()
        else:
            self.search_requested.emit("")

    def _on_search_timeout(self) -> None:
        """去抖定时器超时 → 发出搜索请求。"""
        text = self._search_box.text()
        self.search_requested.emit(text)

    # ──────────────────────────────────────────────
    # 公开接口
    # ──────────────────────────────────────────────

    @property
    def tree_panel(self) -> TreePanel:
        """返回内部的 TreePanel 实例，供 ChatApp 直接连接信号。"""
        return self._tree_panel

    @property
    def search_box(self) -> QLineEdit:
        """返回搜索输入框，供 SearchPopup 定位。"""
        return self._search_box

    def focus_search(self) -> None:
        """聚焦搜索框并选中全部文本。"""
        self._search_box.setFocus()
        self._search_box.selectAll()

    def clear_search(self) -> None:
        """清空搜索框内容。"""
        self._search_box.clear()

    def load_tree(self, nodes: list) -> None:
        """
        加载/刷新树面板数据。

        Args:
            nodes: DFS 排序的 TreeNodeVM 列表
        """
        self._tree_panel.load_tree(nodes)

    def set_active(self, node_id: str) -> None:
        """
        高亮激活的对话节点。

        Args:
            node_id: 要激活的节点 ID
        """
        self._tree_panel.set_active(node_id)
