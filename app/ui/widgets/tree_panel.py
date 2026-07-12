# Layer: UI (PySide6) → widgets
# File: app/ui/widgets/tree_panel.py
# Responsibility: 树形面板控件 — 使用 QTreeView + QStandardItemModel 渲染完整对话目录树，
#                 支持右键菜单、拖拽移动、展开/折叠、启用状态复选框。
#                 替代 Flet 版本的 ExpansionTile 嵌套实现。
# Input:  list[TreeNodeVM] — 预计算好的 DFS 排序节点列表
# Output: 通过 Qt 信号将用户交互事件传递给上层 (Sidebar → ChatApp → Controller)

from __future__ import annotations

import re

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QTreeView,
    QAbstractItemView,
    QMenu,
    QApplication,
    QProxyStyle,
    QStyle,
)
from PySide6.QtCore import (
    Qt,
    Signal,
    QModelIndex,
    QMimeData,
    QPoint,
    QPointF,
    QRectF,
)
from PySide6.QtGui import (
    QStandardItemModel,
    QStandardItem,
    QColor,
    QFont,
    QBrush,
    QAction,
    QDrag,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)

from app.controllers.view_models import TreeNodeVM
from app.ui.theme import Colors, Fonts, Spacing

# ──────────────────────────────────────────────
# 自定义数据角色（存储在 QStandardItem 中）
# ──────────────────────────────────────────────

ROLE_NODE_TYPE = Qt.ItemDataRole.UserRole + 1       # "folder"|"conversation"|"message"
ROLE_NODE_ID = Qt.ItemDataRole.UserRole + 2          # str — node.id
ROLE_ENABLED = Qt.ItemDataRole.UserRole + 3          # True|False|"some"
ROLE_DEPTH = Qt.ItemDataRole.UserRole + 4            # int
ROLE_IS_ACTIVE = Qt.ItemDataRole.UserRole + 5        # bool
ROLE_MESSAGE_COUNT = Qt.ItemDataRole.UserRole + 6    # int
ROLE_TIMESTAMP = Qt.ItemDataRole.UserRole + 7        # str — formatted time
ROLE_ROLE = Qt.ItemDataRole.UserRole + 8             # str — message role
ROLE_PREVIEW = Qt.ItemDataRole.UserRole + 9          # str — message preview

# ──────────────────────────────────────────────
# 节点类型图标映射
# ──────────────────────────────────────────────

_NODE_ICONS: dict[str, str] = {
    "folder": "📁",
    "conversation": "📄",
}

_MESSAGE_ROLE_ICONS: dict[str, str] = {
    "user":      "👤",
    "assistant": "🤖",
    "thinking":  "🧠",
    "system":    "⚙️",
}

_MESSAGE_ROLE_LABELS: dict[str, str] = {
    "user":      "用户",
    "assistant": "助手",
    "thinking":  "思考",
    "system":    "系统",
}

# 消息节点预览最大字符数
_MESSAGE_PREVIEW_MAX = 30


def _icon_for_node(node_type: str, role: str = "") -> str:
    """返回节点类型对应的 emoji 图标。"""
    if node_type == "message":
        return _MESSAGE_ROLE_ICONS.get(role, "💬")
    return _NODE_ICONS.get(node_type, "📄")


def _enabled_tooltip(enabled) -> str:
    """返回启用状态的 tooltip 文本。"""
    if enabled is False:
        return "已禁用 — 点击启用"
    elif enabled == "some":
        return "部分启用 — 点击全部启用"
    return "已启用 — 点击禁用"


# ──────────────────────────────────────────────
# 自定义 QTreeView（处理拖拽放置）
# ──────────────────────────────────────────────


class _TreeView(QTreeView):
    """
    自定义 QTreeView 子类，覆写 dropEvent 以拦截拖拽放置事件，
    不调用 super().dropEvent() 从而阻止 Qt 自动修改 Model，
    改为通过信号通知上层执行业务操作。

    同时覆写 mousePressEvent 以检测 checkbox 区域点击，
    避免 clicked 和 itemChanged 双重信号导致重复处理。
    """

    drop_occurred = Signal(str, str)  # dragged_node_id, target_folder_id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._last_click_on_checkbox: bool = False

    def mousePressEvent(self, event) -> None:
        """记录本次点击是否在 checkbox 区域，供 _on_item_clicked 判断。"""
        self._last_click_on_checkbox = False
        index: QModelIndex = self.indexAt(event.position().toPoint())
        if index.isValid():
            item = self.model().itemFromIndex(index)
            if item is not None and item.isCheckable():
                # checkbox 位于 item 左边缘到 indentation 之间
                # 对于顶层节点(depth=0)，checkbox 从 x≈indent-14 到 x≈indent+2
                # 更简便的方法：点击位置 x 在 visualRect 左边缘 22px 以内认为是 checkbox
                rect = self.visualRect(index)
                rel_x = event.position().toPoint().x() - rect.x()
                if 0 <= rel_x <= 22:
                    self._last_click_on_checkbox = True
        super().mousePressEvent(event)

    def dropEvent(self, event) -> None:
        """
        拦截拖拽放置事件。
        提取被拖拽节点 ID 和目标文件夹 ID，
        通过 drop_occurred 信号通知上层，不修改 Model。
        """
        index: QModelIndex = self.indexAt(event.position().toPoint())
        if not index.isValid():
            event.ignore()
            return

        target_item: QStandardItem = self.model().itemFromIndex(index)
        if target_item is None:
            event.ignore()
            return

        target_node_type = target_item.data(ROLE_NODE_TYPE)
        if target_node_type != "folder":
            event.ignore()
            return

        target_id = target_item.data(ROLE_NODE_ID)
        if target_id is None:
            event.ignore()
            return

        # 从 mimeData 中获取被拖拽的节点 ID
        mime: QMimeData = event.mimeData()
        if mime is None:
            event.ignore()
            return

        dragged_bytes = mime.data("application/x-treenode-id")
        if dragged_bytes is None or not dragged_bytes:
            event.ignore()
            return

        dragged_id = dragged_bytes.data().decode("utf-8")
        if dragged_id == target_id:
            event.ignore()
            return

        event.accept()
        self.drop_occurred.emit(dragged_id, target_id)

    def startDrag(self, supported_actions) -> None:
        """
        覆写拖拽启动，将节点 ID 写入 mimeData 以便 dropEvent 读取。
        """
        index: QModelIndex = self.currentIndex()
        if not index.isValid():
            return

        item: QStandardItem = self.model().itemFromIndex(index)
        if item is None:
            return

        node_type = item.data(ROLE_NODE_TYPE)
        if node_type == "message":
            return  # 消息节点不可拖拽

        node_id = item.data(ROLE_NODE_ID)
        if node_id is None:
            return

        mime = QMimeData()
        mime.setData("application/x-treenode-id", node_id.encode("utf-8"))

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)


# ──────────────────────────────────────────────
# 自定义 Item Delegate（绘制激活节点指示器）
# ──────────────────────────────────────────────


class _ActiveNodeDelegate(QTreeView):
    """
    通过设置 QTreeView 的样式和 item 数据来实现激活节点高亮。

    由于 PySide6 的 QStyledItemDelegate 难以在保留默认复选框/图标
    渲染的同时叠加自定义背景和左边框，这里改用更简洁的方案：
    在 item 数据中设置背景色，通过 stylesheet 控制选中样式。
    """

    pass  # 实际使用 QStandardItem 的 BackgroundRole 控制背景色


# ──────────────────────────────────────────────
# 自定义 QProxyStyle — 白色展开/折叠箭头
# ──────────────────────────────────────────────


class _TreeStyle(QProxyStyle):
    """
    自定义 QProxyStyle，处理两个被 QSS 覆盖的原生绘制：

    1. PE_IndicatorBranch — 绘制白色展开/折叠箭头（▶ / ▼）
    2. PE_IndicatorViewItemCheck — 绘制带 ✓ / — 符号的复选框
       - Checked:         绿色背景 + 白色对勾 ✓
       - Unchecked:       透明填充 + 灰色边框
       - PartiallyChecked: 黄色背景 + 白色横杠 —
    """

    # ── 复选框颜色常量 ──
    CHECK_FILL_CHECKED   = QColor("#4CAF82")  # 绿色
    CHECK_FILL_PARTIAL   = QColor("#E8A838")  # 黄色
    CHECK_BORDER         = QColor("#3D4255")  # 灰色边框
    CHECK_SYMBOL         = QColor("#FFFFFF")  # 白色符号
    ARROW_COLOR          = QColor("#B0B8CC")  # 浅灰白箭头

    def drawPrimitive(
        self,
        element: QStyle.PrimitiveElement,
        option: QStyleOption,
        painter: QPainter,
        widget: QWidget | None = None,
    ) -> None:
        # ── 1. 复选框 ──────────────────────────
        if element == QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            rect = option.rect
            # 居中缩放到 16×16
            size = min(rect.width(), rect.height(), 16)
            r = QRectF(
                rect.center().x() - size / 2,
                rect.center().y() - size / 2,
                size, size,
            )

            if option.state & QStyle.StateFlag.State_On:
                # ✅ 启用 — 绿色背景 + 白色对勾
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self.CHECK_FILL_CHECKED)
                painter.drawRoundedRect(r, 2, 2)
                # 白色对勾 ✓
                painter.setPen(QPen(self.CHECK_SYMBOL, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                cx, cy = r.center().x(), r.center().y()
                w, h = r.width(), r.height()
                # 对勾路径：从左侧偏下 → 中间偏下 → 右上
                path = QPainterPath()
                path.moveTo(cx - w * 0.3, cy + h * 0.02)
                path.lineTo(cx - w * 0.05, cy + h * 0.35)
                path.lineTo(cx + w * 0.35, cy - h * 0.35)
                painter.drawPath(path)

            elif option.state & QStyle.StateFlag.State_NoChange:
                # ⸺ 部分启用 — 黄色背景 + 白色横杠
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self.CHECK_FILL_PARTIAL)
                painter.drawRoundedRect(r, 2, 2)
                # 白色横杠 —
                painter.setPen(QPen(self.CHECK_SYMBOL, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawLine(
                    QPointF(r.center().x() - r.width() * 0.3, r.center().y()),
                    QPointF(r.center().x() + r.width() * 0.3, r.center().y()),
                )

            else:
                # ☐ 禁用 — 透明填充 + 灰色边框
                painter.setPen(QPen(self.CHECK_BORDER, 1.2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(r, 2, 2)

            painter.restore()
            return

        # ── 2. 展开/折叠箭头 ────────────────────
        if element == QStyle.PrimitiveElement.PE_IndicatorBranch:
            if option.state & QStyle.StateFlag.State_Children:
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self.ARROW_COLOR)

                rect = option.rect
                cx, cy = rect.center().x(), rect.center().y()
                size = 4

                if option.state & QStyle.StateFlag.State_Open:
                    triangle = QPolygonF([
                        QPointF(cx - size, cy - size * 0.5),
                        QPointF(cx + size, cy - size * 0.5),
                        QPointF(cx, cy + size * 1.2),
                    ])
                else:
                    triangle = QPolygonF([
                        QPointF(cx - size * 0.5, cy - size),
                        QPointF(cx + size * 1.2, cy),
                        QPointF(cx - size * 0.5, cy + size),
                    ])
                painter.drawPolygon(triangle)
                painter.restore()
                return

        super().drawPrimitive(element, option, painter, widget)


# ──────────────────────────────────────────────
# TreePanel 主体
# ──────────────────────────────────────────────


class TreePanel(QWidget):
    """
    树形面板 — 使用 QTreeView + QStandardItemModel 渲染完整对话目录树。

    公开方法：
        load_tree(nodes)  — 加载/刷新树数据
        set_active(node_id) — 高亮指定节点
        refresh()         — 强制重绘

    信号（通过构造函数注入回调的替代方案——直接发射 Qt 信号）：
        switch_conversation(str) — 点击对话/消息节点
        new_conversation(str)    — 右键：在该目录下新建对话（参数为 parent_id）
        new_folder(str)          — 右键：在该目录下新建文件夹（参数为 parent_id）
        rename_node(str)         — 右键：重命名
        delete_node(str)         — 右键：删除
        toggle_enabled(str)      — 点击复选框 / 右键：切换启用状态
        move_node(str, str)      — 拖拽完成（node_id, target_parent_id）
        manage_context(str)      — 右键：管理上下文块（仅目录）
        attach_file(str)         — 右键：添加附件（仅目录）
    """

    switch_conversation = Signal(str)
    new_conversation = Signal(str)
    new_folder = Signal(str)
    rename_node = Signal(str)
    delete_node = Signal(str)
    toggle_enabled = Signal(str)
    move_node = Signal(str, str)
    manage_context = Signal(str)
    attach_file = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("treePanel")

        self._active_id: str = ""
        self._expanded_ids: set[str] = set()
        self._tree_data: list[TreeNodeVM] = []
        self._model: QStandardItemModel | None = None
        self._building: bool = False  # 防止重建时的信号触发

        # ── Model ─────────────────────────────────
        self._model = QStandardItemModel(0, 1, self)
        self._model.setHorizontalHeaderLabels([""])

        # ── Tree View ─────────────────────────────
        self._tree_view = _TreeView(self)
        # 应用自定义风格 — 绘制白色展开/折叠箭头 + 带符号的复选框
        base_style = QApplication.style()
        if base_style:
            self._tree_view.setStyle(_TreeStyle(base_style))
        self._tree_view.setModel(self._model)
        self._tree_view.setHeaderHidden(True)
        self._tree_view.setIndentation(18)
        self._tree_view.setAnimated(False)  # 禁用动画：展开动画在模型重建后可能访问已删除的 item 导致 segfault
        self._tree_view.setExpandsOnDoubleClick(True)
        self._tree_view.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._tree_view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._tree_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tree_view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._tree_view.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._tree_view.setDragEnabled(True)
        self._tree_view.setAcceptDrops(True)
        self._tree_view.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self._tree_view.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._tree_view.setDropIndicatorShown(True)

        # ── 右键菜单 ──────────────────────────────
        self._tree_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree_view.customContextMenuRequested.connect(self._on_context_menu)

        # ── 点击处理 ──────────────────────────────
        self._tree_view.clicked.connect(self._on_item_clicked)

        # ── 展开/折叠跟踪 ─────────────────────────
        self._tree_view.expanded.connect(self._on_expanded)
        self._tree_view.collapsed.connect(self._on_collapsed)

        # ── 复选框状态变化 ────────────────────────
        self._model.itemChanged.connect(self._on_item_changed)

        # ── 拖拽放置 ──────────────────────────────
        self._tree_view.drop_occurred.connect(self._on_drop)

        # ── 样式 ──────────────────────────────────
        self._apply_styles()

        # ── 布局 ──────────────────────────────────
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._tree_view)

    # ── 样式 ──────────────────────────────────────

    def _apply_styles(self) -> None:
        """应用 QTreeView 深色主题样式，包含消息子节点的展开/折叠箭头。"""
        self._tree_view.setStyleSheet(f"""
            QTreeView {{
                background-color: {Colors.BG_SURFACE};
                border: none;
                outline: none;
                font-family: "{Fonts.BODY}";
                font-size: {Fonts.SIZE_SM}px;
                color: {Colors.TEXT_SECONDARY};
            }}
            QTreeView::item {{
                padding: 6px {Spacing.SM}px;
                border: none;
                border-left: 2px solid transparent;
            }}
            QTreeView::item:hover {{
                background-color: {Colors.BG_OVERLAY};
            }}
        """)

        self.setStyleSheet(f"""
            TreePanel {{
                background-color: {Colors.BG_SURFACE};
            }}
        """)

    # ──────────────────────────────────────────────
    # 公开接口
    # ──────────────────────────────────────────────

    def load_tree(self, nodes: list[TreeNodeVM]) -> None:
        """
        加载/刷新树数据。

        Args:
            nodes: DFS 排序的 TreeNodeVM 列表
        """
        self._tree_data = nodes
        self._rebuild()

    def refresh_enabled_states(self, nodes: list[TreeNodeVM]) -> None:
        """
        增量更新节点的启用/禁用状态，不删除或重建任何 QStandardItem。

        专用于复选框切换后的级联刷新：遍历新数据，逐一定位对应 QStandardItem，
        仅更新 checkState、ROLE_ENABLED 和 tooltip。包裹在 _building=True 中
        以避免 itemChanged 信号再次触发 toggle_enabled。

        与 load_tree() 的区别：load_tree() 完全清除模型并重建所有 item，
        用于结构变更（新建/删除/重命名节点）；refresh_enabled_states() 仅更新
        状态字段，用于级联启用的纯数据变更。

        Args:
            nodes: 最新的 DFS 排序 TreeNodeVM 列表（与当前 _tree_data 结构相同）
        """
        self._tree_data = nodes
        if self._model is None:
            return

        self._building = True
        try:
            self._model.blockSignals(True)
            for node in nodes:
                item = self._find_item_by_node_id(node.id)
                if item is None:
                    continue
                # 更新 checkState
                if node.enabled is True:
                    item.setCheckState(Qt.CheckState.Checked)
                elif node.enabled is False:
                    item.setCheckState(Qt.CheckState.Unchecked)
                else:  # "some"
                    item.setCheckState(Qt.CheckState.PartiallyChecked)
                # 更新 ROLE_ENABLED
                item.setData(node.enabled, ROLE_ENABLED)
                # 更新 tooltip
                item.setToolTip(_build_tooltip(node))
            self._model.blockSignals(False)
        finally:
            self._building = False

    def set_active(self, node_id: str) -> None:
        """
        高亮指定节点为激活状态。
        更新旧激活节点和新激活节点的背景色。
        """
        previous_id = self._active_id
        self._active_id = node_id

        if self._model is None:
            return

        self._building = True
        try:
            # 清除旧激活节点的背景
            old_item = self._find_item_by_node_id(previous_id)
            if old_item is not None:
                old_item.setBackground(QBrush(QColor("transparent")))
                old_item.setData(False, ROLE_IS_ACTIVE)

            # 设置新激活节点的背景
            new_item = self._find_item_by_node_id(node_id)
            if new_item is not None:
                new_item.setBackground(QBrush(QColor(Colors.BG_OVERLAY)))
                new_item.setData(True, ROLE_IS_ACTIVE)
                # 滚动到可见
                idx = self._model.indexFromItem(new_item)
                if idx.isValid():
                    self._tree_view.scrollTo(
                        idx, QAbstractItemView.ScrollHint.EnsureVisible
                    )
        finally:
            self._building = False

    def refresh(self) -> None:
        """强制刷新树视图。"""
        self._tree_view.viewport().update()

    # ──────────────────────────────────────────────
    # 树重建（核心）
    # ──────────────────────────────────────────────

    def _rebuild(self) -> None:
        """
        完全重建树模型。

        算法：
        1. 构建 child_map: parent_id → children 列表
        2. 递归 create_subtree(parent_id) → list[QStandardItem]
        3. 创建新模型，填充数据，替换旧模型
        4. 恢复展开状态和激活节点

        关键设计决策：使用全新 QStandardItemModel 替换旧模型，而非对旧模型
        removeRows() + blockSignals()。后者会阻止 QTreeView 接收 rowsRemoved
        信号，导致其内部数据结构（persistent indexes、layout cache）持有已删除
        QStandardItem 的悬空指针，后续 paint/layout 事件访问时 → heap corruption
        (0xc0000374 on Windows)。
        """
        self._building = True
        try:
            # ── 1. 创建全新模型 ───────────────────
            new_model = QStandardItemModel(0, 1, self)

            if self._tree_data:
                # ── 2. 构建 children 映射 ──────────
                child_map: dict[str | None, list[TreeNodeVM]] = {}
                for node in self._tree_data:
                    pid = node.parent_id
                    if pid not in child_map:
                        child_map[pid] = []
                    child_map[pid].append(node)

                # 按 sort_order 排序
                for pid in child_map:
                    child_map[pid].sort(key=lambda n: n.sort_order)

                def create_subtree(parent_id: str | None) -> list[QStandardItem]:
                    """递归创建子树，返回 QStandardItem 列表。"""
                    children = child_map.get(parent_id, [])
                    result: list[QStandardItem] = []

                    for node in children:
                        item = self._create_item(node)
                        if node.has_children:
                            sub_items = create_subtree(node.id)
                            for sub in sub_items:
                                item.appendRow(sub)
                        result.append(item)

                    return result

                root = new_model.invisibleRootItem()
                top_items = create_subtree(None)
                for item in top_items:
                    root.appendRow(item)

            # ── 3. 断开旧模型信号，替换模型 ──────
            old_model = self._model
            if old_model is not None:
                try:
                    old_model.itemChanged.disconnect(self._on_item_changed)
                except (TypeError, RuntimeError):
                    pass

            self._tree_view.setModel(new_model)
            self._model = new_model
            # 新模型上连接信号（setModel 之后才连接，避免 _create_item
            # 设置 checkState 时触发 itemChanged）
            self._model.itemChanged.connect(self._on_item_changed)

            # ── 4. 清理旧模型 ─────────────────────
            if old_model is not None:
                old_model.deleteLater()

            # ── 5. 恢复展开状态 ───────────────────
            if self._tree_data:
                self._restore_expanded()

            # ── 6. 恢复激活节点高亮 ───────────────
            if self._active_id:
                active_item = self._find_item_by_node_id(self._active_id)
                if active_item is not None:
                    active_item.setBackground(QBrush(QColor(Colors.BG_OVERLAY)))
                    active_item.setData(True, ROLE_IS_ACTIVE)
        finally:
            self._building = False

    def _create_item(self, node: TreeNodeVM) -> QStandardItem:
        """
        从 TreeNodeVM 创建单个 QStandardItem。

        设置：
        - DisplayRole: 显示文本（含消息计数后缀）
        - DecorationRole: 类型图标（emoji 文本）
        - CheckStateRole: 启用状态（Checked/Unchecked/PartiallyChecked）
        - 自定义角色: node_type, node_id, enabled, depth 等
        """
        # ── 显示文本 ──────────────────────────────
        if node.node_type == "message":
            # 消息节点格式: "  👤  用户: 内容预览前30字"
            # 显示文本始终单行 — 换行/制表/连续空格替换为单个空格
            role_label = _MESSAGE_ROLE_LABELS.get(node.role, "")
            preview = _normalize_whitespace(node.preview or node.title or "")
            if len(preview) > _MESSAGE_PREVIEW_MAX:
                preview = preview[:_MESSAGE_PREVIEW_MAX] + "…"
            if role_label:
                display_title = f"{role_label}: {preview}" if preview else role_label
            else:
                display_title = preview
        else:
            display_title = node.title

        # 对话节点追加消息计数
        if node.node_type == "conversation" and node.message_count > 0:
            display_text = f"{display_title} ({node.message_count})"
        else:
            display_text = display_title

        # ── 图标 ──────────────────────────────────
        icon_text = _icon_for_node(node.node_type, node.role)
        # QStandardItem 不支持 emoji 作为 icon 的 decoration，
        # 所以将图标作为文本前缀
        display_text = f"  {icon_text}  {display_text}"

        # ── 创建 item ─────────────────────────────
        item = QStandardItem(display_text)
        item.setToolTip(_build_tooltip(node))
        item.setFont(Fonts.body(Fonts.SIZE_SM))

        # ── 复选框（启用状态）— 所有节点均显示复选框 ──
        item.setCheckable(True)
        if node.enabled is True:
            item.setCheckState(Qt.CheckState.Checked)
        elif node.enabled is False:
            item.setCheckState(Qt.CheckState.Unchecked)
        else:  # "some"
            item.setCheckState(Qt.CheckState.PartiallyChecked)

        # ── 标志位（控制拖拽和放置）────────────────
        if node.node_type == "message":
            flags = (
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemNeverHasChildren
            )
        else:
            flags = (
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsDragEnabled
            )
        if node.node_type == "folder":
            flags |= Qt.ItemFlag.ItemIsDropEnabled
        item.setFlags(flags)

        # ── 自定义数据角色 ────────────────────────
        item.setData(node.node_type, ROLE_NODE_TYPE)
        item.setData(node.id, ROLE_NODE_ID)
        item.setData(node.enabled, ROLE_ENABLED)
        item.setData(node.depth, ROLE_DEPTH)
        item.setData(False, ROLE_IS_ACTIVE)
        item.setData(node.message_count, ROLE_MESSAGE_COUNT)
        item.setData(node.updated_at, ROLE_TIMESTAMP)
        item.setData(node.role, ROLE_ROLE)
        item.setData(node.preview, ROLE_PREVIEW)

        # ── 文本颜色 ──────────────────────────────
        item.setForeground(QBrush(QColor(Colors.TEXT_SECONDARY)))

        return item

    def _restore_expanded(self) -> None:
        """
        恢复之前展开的节点（通过 _expanded_ids 集合）。
        递归遍历 model 展开匹配的节点。
        """
        if self._model is None:
            return

        def expand_matching(parent: QStandardItem) -> None:
            for row in range(parent.rowCount()):
                child = parent.child(row)
                if child is None:
                    continue
                nid = child.data(ROLE_NODE_ID)
                if nid and nid in self._expanded_ids and child.hasChildren():
                    idx = self._model.indexFromItem(child)
                    self._tree_view.expand(idx)
                expand_matching(child)

        root = self._model.invisibleRootItem()
        if root is not None:
            expand_matching(root)

    def expand_to_node(self, node_id: str) -> None:
        """
        展开目标节点的所有祖先节点，使其在树视图中可见。

        从目标节点向上遍历父链，收集所有祖先 ID，
        然后从根向目标逐层展开。
        """
        if self._model is None or not self._tree_data:
            return

        child_to_parent: dict[str, str | None] = {}
        for n in self._tree_data:
            child_to_parent[n.id] = n.parent_id

        ancestors: list[str] = []
        current_id: str | None = node_id
        while current_id is not None:
            parent_id = child_to_parent.get(current_id)
            if parent_id is not None:
                ancestors.append(parent_id)
            current_id = parent_id

        for aid in reversed(ancestors):
            self._expanded_ids.add(aid)
            item = self._find_item_by_node_id(aid)
            if item is not None and item.hasChildren():
                idx = self._model.indexFromItem(item)
                if idx.isValid():
                    self._tree_view.expand(idx)

    def _find_item_by_node_id(self, node_id: str) -> QStandardItem | None:
        """
        在 model 中递归搜索指定 node_id 的 QStandardItem。

        Args:
            node_id: 目标节点 ID

        Returns:
            找到的 QStandardItem 或 None
        """
        if not node_id or self._model is None:
            return None

        def search(parent: QStandardItem) -> QStandardItem | None:
            for row in range(parent.rowCount()):
                child = parent.child(row)
                if child is None:
                    continue
                cid = child.data(ROLE_NODE_ID)
                if cid == node_id:
                    return child
                found = search(child)
                if found is not None:
                    return found
            return None

        root = self._model.invisibleRootItem()
        if root is not None:
            return search(root)
        return None

    # ──────────────────────────────────────────────
    # 交互事件处理
    # ──────────────────────────────────────────────

    def _on_item_clicked(self, index: QModelIndex) -> None:
        """
        节点点击处理：
        - 对话节点 / 消息节点 → 加载该节点对应的消息
        - 目录节点 → 切换展开/折叠

        注意：如果点击位置在 checkbox 指示器上，跳过此处理逻辑，
        避免与 itemChanged → toggle_enabled 信号产生竞争。
        """
        if not index.isValid():
            return

        # 跳过 checkbox 区域的点击 — 该区域由 itemChanged 信号处理
        if self._tree_view._last_click_on_checkbox:
            return

        item: QStandardItem = self._model.itemFromIndex(index)
        if item is None:
            return

        node_type = item.data(ROLE_NODE_TYPE)
        node_id = item.data(ROLE_NODE_ID)

        if node_type in ("conversation", "message"):
            if node_id:
                self.switch_conversation.emit(node_id)
        elif node_type == "folder":
            # 切换展开/折叠
            if self._tree_view.isExpanded(index):
                self._tree_view.collapse(index)
            else:
                self._tree_view.expand(index)

    def _on_expanded(self, index: QModelIndex) -> None:
        """记录展开的节点 ID。"""
        if not index.isValid():
            return
        item: QStandardItem = self._model.itemFromIndex(index)
        if item is None:
            return
        nid = item.data(ROLE_NODE_ID)
        if nid:
            self._expanded_ids.add(nid)

    def _on_collapsed(self, index: QModelIndex) -> None:
        """移除折叠的节点 ID。"""
        if not index.isValid():
            return
        item: QStandardItem = self._model.itemFromIndex(index)
        if item is None:
            return
        nid = item.data(ROLE_NODE_ID)
        if nid:
            self._expanded_ids.discard(nid)

    def _on_item_changed(self, item: QStandardItem) -> None:
        """
        检测复选框状态变化（所有节点类型，含消息节点）。
        仅在非重建期间响应，避免程序化设置复选框时误触发。
        """
        if self._building:
            return

        node_type = item.data(ROLE_NODE_TYPE)
        new_check = item.checkState()
        node_id = item.data(ROLE_NODE_ID)
        if node_id is None:
            return

        old_enabled = item.data(ROLE_ENABLED)

        # 判断是否真正变化
        if new_check == Qt.CheckState.Checked and old_enabled is not True:
            self.toggle_enabled.emit(node_id)
        elif new_check == Qt.CheckState.Unchecked and old_enabled is not False:
            self.toggle_enabled.emit(node_id)
        elif new_check == Qt.CheckState.PartiallyChecked and old_enabled != "some":
            self.toggle_enabled.emit(node_id)

    def _on_drop(self, dragged_id: str, target_folder_id: str) -> None:
        """
        拖拽放置完成。
        发射 move_node 信号让上层处理业务逻辑。
        """
        self.move_node.emit(dragged_id, target_folder_id)

    # ──────────────────────────────────────────────
    # 右键上下文菜单
    # ──────────────────────────────────────────────

    def _on_context_menu(self, pos: QPoint) -> None:
        """
        在鼠标位置弹出右键上下文菜单。
        根据节点类型构建不同的菜单项。
        """
        index: QModelIndex = self._tree_view.indexAt(pos)
        if not index.isValid():
            return

        item: QStandardItem = self._model.itemFromIndex(index)
        if item is None:
            return

        node_type = item.data(ROLE_NODE_TYPE)
        node_id = item.data(ROLE_NODE_ID)
        is_enabled = item.data(ROLE_ENABLED)

        if node_id is None:
            return

        menu = QMenu(self)
        menu.setFont(Fonts.body(Fonts.SIZE_SM))
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {Colors.BG_ELEVATED};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: {4}px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 24px 6px 16px;
            }}
            QMenu::item:selected {{
                background-color: {Colors.BG_OVERLAY};
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {Colors.DIVIDER};
                margin: 4px 8px;
            }}
        """)

        nid = node_id

        if node_type == "folder":
            # 新建文件夹
            menu.addAction("📁 新建文件夹", lambda: self.new_folder.emit(nid))
            # 新建对话
            menu.addAction("💬 新建对话", lambda: self.new_conversation.emit(nid))
            menu.addSeparator()
            # 重命名
            menu.addAction("✏️ 重命名", lambda: self.rename_node.emit(nid))
            # 启用/禁用
            toggle_label = "🔳 禁用" if is_enabled is True else "🔲 启用"
            menu.addAction(toggle_label, lambda: self.toggle_enabled.emit(nid))
            menu.addSeparator()
            # 管理上下文块
            menu.addAction("📚 管理上下文块", lambda: self.manage_context.emit(nid))
            # 添加附件
            menu.addAction("📎 添加附件", lambda: self.attach_file.emit(nid))
            menu.addSeparator()

        elif node_type == "conversation":
            # 重命名
            menu.addAction("✏️ 重命名", lambda: self.rename_node.emit(nid))
            # 启用/禁用
            toggle_label = "🔳 禁用" if is_enabled is True else "🔲 启用"
            menu.addAction(toggle_label, lambda: self.toggle_enabled.emit(nid))
            menu.addSeparator()

        elif node_type == "message":
            # 启用/禁用
            toggle_label = "🔳 禁用" if is_enabled is True else "🔲 启用"
            menu.addAction(toggle_label, lambda: self.toggle_enabled.emit(nid))
            menu.addSeparator()

        # 删除（所有节点通用）
        delete_action = menu.addAction("🗑️ 删除")
        delete_action.setData(nid)
        # 为删除项设置红色文字
        delete_action.triggered.connect(lambda: self.delete_node.emit(nid))

        # 弹出菜单
        menu.exec(self._tree_view.viewport().mapToGlobal(pos))


# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────


def _normalize_whitespace(text: str) -> str:
    """
    将 text 中所有空白字符（换行、回车、制表符、连续空格等）
    替换为单个空格，并去除首尾空白。

    用于树面板消息节点的单行显示。不影响原始数据。
    """
    return re.sub(r"\s+", " ", text).strip()


def _build_tooltip(node: TreeNodeVM) -> str:
    """构建节点的工具提示文本。"""
    parts: list[str] = []
    if node.node_type == "message":
        role_label = _MESSAGE_ROLE_LABELS.get(node.role, node.role)
        parts.append(f"{role_label}消息")
        preview = node.preview or node.title or ""
        if preview:
            parts.append(preview[:80])
    else:
        parts.append(node.title)

    if node.enabled is False:
        parts.append("状态: 已禁用")
    elif node.enabled == "some":
        parts.append("状态: 部分启用")
    else:
        parts.append("状态: ✅ 已启用")

    if node.node_type == "conversation" and node.message_count > 0:
        parts.append(f"消息: {node.message_count}")
    if node.node_type == "folder":
        if node.context_block_count > 0:
            parts.append(f"上下文块: {node.context_block_count}")
        if node.attachment_count > 0:
            parts.append(f"附件: {node.attachment_count}")
    if node.updated_at:
        parts.append(f"更新: {node.updated_at}")

    return "\n".join(parts)
