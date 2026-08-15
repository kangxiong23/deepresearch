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
    QStyleFactory,
    QStyleOptionViewItem, QStyleOption,
)
from PySide6.QtCore import (
    Qt,
    Signal,
    QModelIndex,
    QMimeData,
    QPoint,
    QPointF,
    QRectF,
    QRect, QSize,
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
    QPixmap,
    QCursor,
    QIcon,
)

from app.controllers.view_models import TreeNodeVM
from app.ui.theme import apply_style, Colors, Fonts, Spacing

# ──────────────────────────────────────────────
# 自定义数据角色（存储在 QStandardItem 中）
# ──────────────────────────────────────────────

ROLE_NODE_TYPE = Qt.ItemDataRole.UserRole + 1  # "folder"|"conversation"|"message"
ROLE_NODE_ID = Qt.ItemDataRole.UserRole + 2  # str — node.id
ROLE_ENABLED = Qt.ItemDataRole.UserRole + 3  # True|False|"some"
ROLE_DEPTH = Qt.ItemDataRole.UserRole + 4  # int
ROLE_IS_ACTIVE = Qt.ItemDataRole.UserRole + 5  # bool
ROLE_MESSAGE_COUNT = Qt.ItemDataRole.UserRole + 6  # int
ROLE_TIMESTAMP = Qt.ItemDataRole.UserRole + 7  # str — formatted time
ROLE_ROLE = Qt.ItemDataRole.UserRole + 8  # str — message role
ROLE_PREVIEW = Qt.ItemDataRole.UserRole + 9  # str — message preview
ROLE_IS_SELECTED = Qt.ItemDataRole.UserRole + 10  # bool — 多选模式选中
ROLE_IS_MODIFIED = Qt.ItemDataRole.UserRole + 11  # bool — 被修改节点（分叉点后继，3.6 拖拽）

# ──────────────────────────────────────────────
# 节点类型图标映射
# ──────────────────────────────────────────────

_NODE_ICONS: dict[str, str] = {
    "folder": "📁",
    "conversation": "📄",
}

_MESSAGE_ROLE_ICONS: dict[str, str] = {
    "user": "👤",
    "assistant": "🤖",
    "thinking": "🧠",
    "system": "⚙️",
}

_MESSAGE_ROLE_LABELS: dict[str, str] = {
    "user": "用户",
    "assistant": "助手",
    "thinking": "思考",
    "system": "系统",
}

# 节点图标（DecorationRole）逻辑尺寸。
# QTreeView 会把图标缩放到 setIconSize() 指定的图标槽，因此 _ICON_W 需 ≥ 图标最大宽度。
# 每项图标宽度独立影响文字起点：无块节点只画 emoji（不留白），挂块节点 emoji + "+"。
_ICON_H = 20               # 图标高度
_ICON_EMOJI_W = 18         # emoji 区域宽度（无块节点图标总宽 = 此值，不留白）
_ICON_PLUS_W = 10           # "+" 徽标区域宽度（仅挂块节点，紧贴 emoji 右侧）
_ICON_W = _ICON_EMOJI_W + _ICON_PLUS_W   # 挂块节点图标总宽 / setIconSize 图标槽上限

# 消息节点预览最大字符数
_MESSAGE_PREVIEW_MAX = 30

# ──────────────────────────────────────────────
# 多选模式 — 每种节点类型的可用操作集合
# ──────────────────────────────────────────────

_FOLDER_OPS = {
    "new_folder", "new_conversation", "rename",
    "toggle_enabled", "manage_context", "attach_file", "delete",
}
_CONVERSATION_OPS = {
    "rename", "toggle_enabled", "manage_context", "attach_file", "delete",
}
_MESSAGE_OPS = {"toggle_enabled", "delete"}

_OPS_BY_TYPE: dict[str, set[str]] = {
    "folder": _FOLDER_OPS,
    "conversation": _CONVERSATION_OPS,
    "message": _MESSAGE_OPS,
}

# 选中高亮颜色（蓝紫调，区别于激活绿色和拖拽暗绿）
SELECTION_BG = QColor("#1E2A4A")
SELECTION_BORDER = QColor("#5B7EC2")

# 节点上下文管理模式高亮（区别于激活/选中/拖拽）
NODE_CTX_BG = QColor("#17345C")


def _icon_for_node(node_type: str, role: str = "") -> str:
    """返回节点类型对应的 emoji 图标。"""
    if node_type == "message":
        return _MESSAGE_ROLE_ICONS.get(role, "💬")
    return _NODE_ICONS.get(node_type, "📄")


def _build_node_icon(node: TreeNodeVM) -> QIcon:
    """构建节点图标（emoji + 可选 "+" 徽标），返回 QIcon。

    模型约定：上下文块必须挂靠到目录/对话节点才能存在；挂靠了块的节点
    图标后显示 "+" 徽标（仅展示、不可交互）。禁用上下文块（context_blocks_enabled=False）
    时，"+" 用低 alpha（半透明）绘制，仅该徽标变淡，不整行变淡。

    尺寸注意：QTreeView 的 iconSize() 会把 DecorationRole 图标缩放到图标槽，
    因此图标 pixmap 逻辑尺寸必须与树视图 setIconSize() 一致（_ICON_W×_ICON_H），
    并做 DPR 适配（否则 HiDPI 下会缩小一半）。emoji 用与文字相同字号（SIZE_SM）。
    """
    emoji = _icon_for_node(node.node_type, node.role)
    has_ctx = (
        node.node_type in ("folder", "conversation")
        and getattr(node, "context_block_count", 0) > 0
    )
    disabled = has_ctx and not getattr(node, "context_blocks_enabled", True)

    app = QApplication.instance()
    dpr = app.devicePixelRatio() if app is not None else 1.0
    h = _ICON_H
    # 无块节点图标只含 emoji（不留白）；挂块节点 emoji + "+" 徽标
    total_w = _ICON_EMOJI_W + (_ICON_PLUS_W if has_ctx else 0)
    pix = QPixmap(int(total_w * dpr), int(h * dpr))
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setFont(Fonts.body(Fonts.SIZE_SM))
    painter.setPen(QColor(Colors.TEXT_SECONDARY))
    painter.drawText(
        0, 0, _ICON_EMOJI_W, h,
        Qt.AlignmentFlag.AlignCenter, emoji,
    )
    if has_ctx:
        alpha = 100 if disabled else 255
        painter.setPen(QColor(122, 128, 153, alpha))
        painter.drawText(
            _ICON_EMOJI_W, 0, _ICON_PLUS_W, h,
            Qt.AlignmentFlag.AlignCenter, "+",
        )
    painter.end()
    return QIcon(pix)


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
    自定义 QTreeView 子类，实现完整的拖拽放置体验。

    拖拽规则：
    - 所有节点（目录/对话/消息）均可拖拽
    - 目录接受任何节点作为子节点
    - 对话仅接受消息节点作为子节点
    - 消息不接受任何子节点
    - 不允许自拖放、不允许循环
    - 支持在兄弟节点间插入（排序重排）

    视觉反馈：
    - 拖拽时显示半透明副本
    - 有效目标：绿色边框高亮（放在目标上）或插入线（放在兄弟间）
    - 无效目标：禁止光标
    - dragLeaveEvent 清除所有反馈

    放置位置判断（dragMoveEvent/dropEvent）：
    - 上方 25% 区域 → 插入到目标兄弟节点之前（同父级）
    - 下方 25% 区域 → 插入到目标兄弟节点之后（同父级）
    - 中间 50% 区域 → 若目标为有效容器则作为子节点放入

    同时覆写 mousePressEvent 以检测 checkbox 区域点击，
    避免 clicked 和 itemChanged 双重信号导致重复处理。
    """

    # 信号：dragged_node_id, target_parent_id, position (0-based 插入位置，None=末尾)
    # node_id, target_parent_id, position, prev_id, next_id（分支感知拖拽，3.6）
    drop_occurred = Signal(str, str, object, str, str)
    # 多选模式 — 批量拖拽：逗号分隔的 dragged_ids, target_parent_id, position
    batch_drop_occurred = Signal(str, str, object)
    # 选中状态变化
    selection_changed = Signal()

    # ── 拖拽高亮颜色常量 ──
    DROP_HIGHLIGHT_BORDER = QColor("#4CAF82")  # 绿色边框
    DROP_HIGHLIGHT_BG = QColor("#1A3A2A")  # 暗绿背景
    INSERT_LINE_COLOR = QColor("#4CAF82")  # 绿色插入线
    INSERT_LINE_WIDTH = 2  # 插入线宽度

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._last_click_on_checkbox: bool = False
        # 节点上下文管理模式高亮的节点 id（"" = 无；与 TreePanel 同步）
        self._node_ctx_highlight_id: str = ""
        # 拖拽状态跟踪
        self._drag_active: bool = False
        self._drag_node_type: str = ""
        self._drag_node_id: str = ""
        # 手动拖拽检测
        self._drag_press_pos: QPoint | None = None
        self._drag_press_index: QModelIndex = QModelIndex()
        # 当前拖拽悬停的目标信息
        self._hover_target_index: QModelIndex | None = None
        self._hover_drop_zone: str = ""  # "on_item" | "above" | "below" | ""
        # 上一个被高亮的 item（用于清除旧高亮）
        self._last_highlighted_row: int = -1
        self._last_highlighted_parent: QStandardItem | None = None

        # ── 多选模式状态 ──
        self._multi_select_mode: bool = False
        self._selected_ids: set[str] = set()
        # 跟踪上次点击是否含 Ctrl（用于延迟的 clicked 信号到达时正确判断）
        self._last_click_was_ctrl: bool = False
        self._last_click_was_multi: bool = False
        # 鼠标悬停跟踪（替代被移除的 QSS ::item:hover 规则）
        self._hovered_index: QModelIndex | None = None

        # 复选框变化标志（mousePressEvent 期间检测 itemChanged 信号）
        self._checkbox_changed_during_press: bool = False

    # ──────────────────────────────────────────
    # 多选模式公开 API
    # ──────────────────────────────────────────

    def set_multi_select_mode(self, enabled: bool) -> None:
        """启用/禁用多选模式，切换时清空选中状态。"""
        self._multi_select_mode = enabled
        self._clear_all_selections()
        self.selection_changed.emit()

    @property
    def multi_select_mode(self) -> bool:
        return self._multi_select_mode

    @property
    def selected_ids(self) -> set[str]:
        return self._selected_ids

    def clear_selection(self) -> None:
        """清空所有选中状态。"""
        self._clear_all_selections()
        self.selection_changed.emit()

    def _clear_all_selections(self) -> None:
        """内部：清空所有选中视觉并重置 _selected_ids。"""
        for nid in list(self._selected_ids):
            item = self._find_item_by_node_id(nid)
            if item is not None:
                self._apply_selection_visual(item, False)
        self._selected_ids.clear()

    def _apply_selection_visual(self, item: QStandardItem, selected: bool) -> None:
        """更新单个 item 的选中视觉（多选模式）。"""
        if selected:
            item.setBackground(QBrush(SELECTION_BG))
            item.setData(True, ROLE_IS_SELECTED)
        else:
            # 恢复默认背景（保留 active / 节点上下文高亮状态的颜色）
            item.setData(False, ROLE_IS_SELECTED)
            self._restore_item_background(item)

    def _update_selection_visual(self, item: QStandardItem, selected: bool) -> None:
        """更新选中视觉（_apply_selection_visual 的别名，对外统一命名）。"""
        self._apply_selection_visual(item, selected)

    def _get_descendant_ids(self, node_id: str) -> set[str]:
        """递归获取 node_id 下的所有后代节点 ID（不含自身）。"""
        descendants: set[str] = set()
        item = self._find_item_by_node_id(node_id)
        if item is None:
            return descendants

        def collect(parent: QStandardItem) -> None:
            for row in range(parent.rowCount()):
                child = parent.child(row)
                if child is None:
                    continue
                cid = child.data(ROLE_NODE_ID)
                if cid:
                    descendants.add(cid)
                collect(child)

        collect(item)
        return descendants

    def _has_selected_ancestor(self, node_id: str) -> bool:
        """检查 node_id 是否有已选中的祖先节点（用于级联去重）。"""
        if not self._selected_ids:
            return False
        item = self._find_item_by_node_id(node_id)
        if item is None:
            return False
        parent = item.parent()
        while parent is not None:
            pid = parent.data(ROLE_NODE_ID)
            if pid and pid in self._selected_ids:
                return True
            parent = parent.parent()
        return False

    # ──────────────────────────────────────────
    # 鼠标事件（checkbox 检测 + 手动拖拽启动 + 多选）
    # ──────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        """处理鼠标按下事件：检测复选框/箭头点击，多选切换，拖拽起始记录"""
        self._last_click_on_checkbox = False
        self._last_click_was_ctrl = False
        self._last_click_was_multi = False
        pos = event.position().toPoint()
        index: QModelIndex = self.indexAt(pos)

        ctrl_held = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        is_multi = self._multi_select_mode

        # ── 存储当前点击的节点ID ──
        clicked_node_id: str | None = None
        item: QStandardItem | None = None
        if index.isValid():
            item = self.model().itemFromIndex(index)
            if item is not None:
                clicked_node_id = item.data(ROLE_NODE_ID)

        # ── 提前检测复选框区域（使用手动计算，避免访问样式导致崩溃） ──
        is_checkbox_click = False
        if index.isValid() and item is not None and item.isCheckable():
            rect = self.visualRect(index)
            rel_x = pos.x() - rect.x()
            depth = item.data(ROLE_DEPTH) or 0
            # 缩进宽度 = depth * indentation(18)
            # 复选框通常位于缩进之后，起始偏移约为 8px，宽度约为 16px
            checkbox_left = depth + 8
            checkbox_right = checkbox_left + 16
            if checkbox_left <= rel_x <= checkbox_right:
                is_checkbox_click = True

        self._last_click_on_checkbox = is_checkbox_click

        # ── 记录展开状态（用于箭头检测） ──
        was_expanded = self.isExpanded(index) if index.isValid() else None

        # 让 Qt 处理事件：展开/折叠箭头、复选框切换等
        # 注意：不要在 super() 之前调用 initViewItemOption / subElementRect，
        # 那会破坏 Qt 内部状态导致 access violation 崩溃。
        # ── 调用父类处理默认事件（安全，不访问样式） ──
        super().mousePressEvent(event)

        # ── 检测箭头点击（展开状态变化） ──
        is_arrow_click = False
        if was_expanded is not None and clicked_node_id:
            found_item = self._find_item_by_node_id(clicked_node_id)
            if found_item is not None:
                idx = self.model().indexFromItem(found_item)
                if idx.isValid() and self.isExpanded(idx) != was_expanded:
                    is_arrow_click = True

        # ── 如果是复选框或箭头点击，不处理多选切换 ──
        if is_checkbox_click or is_arrow_click:
            # 不处理多选切换，不记录拖拽起始
            self._drag_press_pos = None
            self._drag_press_index = QModelIndex()
            return

        # ── 多选模式或 Ctrl+点击 → 切换选中状态 ──
        # 仅当不是箭头点击且不是复选框点击时才触发多选
        if (is_multi or ctrl_held) and clicked_node_id:
            found_item = self._find_item_by_node_id(clicked_node_id)
            if found_item is not None:
                if clicked_node_id in self._selected_ids:
                    # ── 取消选中 ──
                    self._selected_ids.discard(clicked_node_id)
                    self._apply_selection_visual(found_item, False)
                    self.selection_changed.emit()
                else:
                    # ── 选中：级联去重 ──
                    # 1) 若有祖先已选中 → 拒绝（祖先选中已覆盖该节点）
                    if self._has_selected_ancestor(clicked_node_id):
                        self._drag_press_pos = None
                        self._drag_press_index = QModelIndex()
                        return
                    # 2) 清除所有后代选中（避免重复级联操作）
                    for desc_id in self._get_descendant_ids(clicked_node_id):
                        if desc_id in self._selected_ids:
                            self._selected_ids.discard(desc_id)
                            desc_item = self._find_item_by_node_id(desc_id)
                            if desc_item is not None:
                                self._apply_selection_visual(desc_item, False)
                    # 3) 选中当前节点
                    self._selected_ids.add(clicked_node_id)
                    self._apply_selection_visual(found_item, True)
                    self.selection_changed.emit()

                # 记录以便 _on_item_clicked 跳过导航（clicked 信号延迟到达时 Ctrl 可能已松开）
                self._last_click_was_ctrl = ctrl_held
                self._last_click_was_multi = is_multi
                # 多选切换后不启动拖拽
                self._drag_press_pos = None
                self._drag_press_index = QModelIndex()
                return

        # ── 正常模式：记录拖拽起始 ──
        self._drag_press_pos = pos
        self._drag_press_index = index if index.isValid() else QModelIndex()

    def mouseMoveEvent(self, event) -> None:
        """
        手动检测拖拽阈值 + 鼠标悬停背景跟踪。

        - 达到拖拽阈值 → 启动拖拽
        - 未在拖拽 → 更新悬停背景（替代被移除的 QSS ::item:hover）
        """
        if self._drag_press_pos is not None and self._drag_press_index.isValid():
            distance = (event.position().toPoint() - self._drag_press_pos).manhattanLength()
            if distance >= QApplication.startDragDistance():
                press_index = self._drag_press_index
                self._drag_press_pos = None
                self._drag_press_index = QModelIndex()

                # 多选模式：只允许从已选中节点拖拽
                if self._multi_select_mode:
                    item = self.model().itemFromIndex(press_index)
                    node_id = item.data(ROLE_NODE_ID) if item else None
                    if node_id not in self._selected_ids:
                        return

                self._start_manual_drag(press_index)
                return
        else:
            # ── 悬停背景跟踪（非拖拽状态）──
            idx = self.indexAt(event.position().toPoint())
            if idx != self._hovered_index:
                # 清除旧悬停
                if self._hovered_index is not None and self._hovered_index.isValid():
                    old_item = self.model().itemFromIndex(self._hovered_index)
                    if old_item is not None:
                        self._restore_item_background(old_item)
                # 设置新悬停
                self._hovered_index = idx
                if idx.isValid():
                    hover_item = self.model().itemFromIndex(idx)
                    if hover_item is not None:
                        is_selected = hover_item.data(ROLE_IS_SELECTED)
                        is_active = hover_item.data(ROLE_IS_ACTIVE)
                        if not is_selected and not is_active:
                            hover_item.setBackground(QBrush(QColor(Colors.BG_OVERLAY)))

        super().mouseMoveEvent(event)

    def _restore_item_background(self, item: QStandardItem) -> None:
        """按优先级恢复 item 背景：选中 > 激活 > 节点上下文高亮 > 透明。"""
        is_selected = item.data(ROLE_IS_SELECTED)
        is_active = item.data(ROLE_IS_ACTIVE)
        nid = item.data(ROLE_NODE_ID)
        if is_selected:
            item.setBackground(QBrush(SELECTION_BG))
        elif is_active:
            item.setBackground(QBrush(QColor(Colors.BG_OVERLAY)))
        elif nid and nid == self._node_ctx_highlight_id:
            item.setBackground(QBrush(NODE_CTX_BG))
        else:
            item.setBackground(QBrush(QColor("transparent")))

    def leaveEvent(self, event) -> None:
        """鼠标离开视图 → 清除悬停背景。"""
        if self._hovered_index is not None and self._hovered_index.isValid():
            old_item = self.model().itemFromIndex(self._hovered_index)
            if old_item is not None:
                self._restore_item_background(old_item)
            self._hovered_index = None
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """清除拖拽按下状态。"""
        self._drag_press_pos = None
        self._drag_press_index = QModelIndex()
        super().mouseReleaseEvent(event)

    # ──────────────────────────────────────────
    # 键盘事件（Ctrl 临时多选模式）
    # ──────────────────────────────────────────

    def keyPressEvent(self, event) -> None:
        """Ctrl 按下 → 若非多选模式则临时进入。"""
        if (
                event.key() == Qt.Key.Key_Control
                and not self._multi_select_mode
                and not event.isAutoRepeat()
        ):
            # Ctrl 按下不立刻切换模式，由 mousePressEvent 实时检测 modifiers
            pass
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        """
        Ctrl 松开 → 若非多选模式，视为退出临时多选，清空选中。
        event.isAutoRepeat() 排除长按产生的重复释放事件。
        """
        if (
                event.key() == Qt.Key.Key_Control
                and not self._multi_select_mode
                and not event.isAutoRepeat()
        ):
            if self._selected_ids:
                self._clear_all_selections()
                self.selection_changed.emit()
        super().keyReleaseEvent(event)

    # ──────────────────────────────────────────
    # 拖拽启动（手动触发）
    # ──────────────────────────────────────────

    def _start_manual_drag(self, index: QModelIndex) -> None:
        """
        手动启动拖拽：从给定 index 创建 QDrag。
        所有节点均可拖拽（包括消息节点）。
        多选模式下，若有多个选中节点，序列化所有选中 ID。
        """
        if not index.isValid():
            return

        item: QStandardItem = self.model().itemFromIndex(index)
        if item is None:
            return

        node_type = item.data(ROLE_NODE_TYPE)
        node_id = item.data(ROLE_NODE_ID)
        if node_id is None:
            return

        # ── 多选模式：序列化所有选中节点 ID ──
        dragged_ids: list[str] = [node_id]
        if self._multi_select_mode and len(self._selected_ids) > 1 and node_id in self._selected_ids:
            dragged_ids = self._get_selected_in_dfs_order()

        # ── 构建 mimeData ──
        mime = QMimeData()
        mime.setData("application/x-treenode-id", node_id.encode("utf-8"))
        mime.setData("application/x-treenode-type", node_type.encode("utf-8"))
        if len(dragged_ids) > 1:
            mime.setData(
                "application/x-treenode-ids",
                ",".join(dragged_ids).encode("utf-8"),
            )

        # ── 半透明拖拽图像 ──
        rect = self.visualRect(index)
        pixmap = QPixmap(rect.size())
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setOpacity(0.6)
        opt = QStyleOptionViewItem()
        self.initViewItemOption(opt)
        opt.rect = QRect(0, 0, rect.width(), rect.height())
        opt.state |= QStyle.StateFlag.State_Selected
        self.itemDelegate().paint(painter, opt, index)
        painter.end()

        # ── 设置拖拽状态 ──
        self._drag_active = True
        self._drag_node_type = node_type
        self._drag_node_id = node_id

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(pixmap)
        # 热点设为鼠标在 pixmap 内的相对位置
        cursor_pos = self.viewport().mapFromGlobal(QCursor.pos())
        drag.setHotSpot(cursor_pos - rect.topLeft())
        if len(dragged_ids) > 1:
            drag.exec(Qt.DropAction.MoveAction)
        else:
            drag.exec(Qt.DropAction.MoveAction)

        # ── 清理拖拽状态 ──
        self._drag_active = False
        self._drag_node_type = ""
        self._drag_node_id = ""
        self._clear_drag_feedback()

    def _get_selected_in_dfs_order(self) -> list[str]:
        """
        按树中 DFS 前序遍历顺序返回 _selected_ids。
        保持多节点拖拽时相对顺序不变。
        """
        if self.model() is None:
            return list(self._selected_ids)

        ordered: list[str] = []

        def dfs(item: QStandardItem) -> None:
            nid = item.data(ROLE_NODE_ID)
            if nid and nid in self._selected_ids:
                ordered.append(nid)
            for row in range(item.rowCount()):
                child = item.child(row)
                if child is not None:
                    dfs(child)

        root = self.model().invisibleRootItem()
        if root is not None:
            for row in range(root.rowCount()):
                child = root.child(row)
                if child is not None:
                    dfs(child)

        # 追加任何不在树中（可能已删除）但仍被选中的 ID
        for nid in self._selected_ids:
            if nid not in ordered:
                ordered.append(nid)

        return ordered

    # ──────────────────────────────────────────
    # 拖拽进入
    # ──────────────────────────────────────────

    def dragEnterEvent(self, event) -> None:
        """拖拽进入视图区域时检查 MIME 类型是否有效。"""
        if event.mimeData().hasFormat("application/x-treenode-id"):
            event.acceptProposedAction()
        else:
            event.ignore()

    # ──────────────────────────────────────────
    # 拖拽移动（逐像素判定放置位置和有效性）
    # ──────────────────────────────────────────

    def dragMoveEvent(self, event) -> None:
        """
        拖拽在视图上移动时：
        1. 判断放置位置（上方/中间/下方）
        2. 验证是否可放置（含多选批量验证）
        3. 更新视觉反馈
        """
        mime = event.mimeData()
        if not mime.hasFormat("application/x-treenode-id"):
            event.ignore()
            return

        # 解析拖拽节点信息（支持多选批量拖拽）
        dragged_id = mime.data("application/x-treenode-id").data().decode("utf-8")
        dragged_type = (
            mime.data("application/x-treenode-type").data().decode("utf-8")
            if mime.hasFormat("application/x-treenode-type") else ""
        )
        # 多选批量拖拽 ID 列表
        all_dragged_ids: list[str] = [dragged_id]
        if mime.hasFormat("application/x-treenode-ids"):
            ids_str = mime.data("application/x-treenode-ids").data().decode("utf-8")
            all_dragged_ids = [i for i in ids_str.split(",") if i]

        # 定位目标
        index: QModelIndex = self.indexAt(event.position().toPoint())
        if not index.isValid():
            # 拖到空白区域 → 移到根级
            self._clear_drag_feedback()
            if self._can_drop_at_root_multi(all_dragged_ids):
                event.acceptProposedAction()
                self.setCursor(Qt.CursorShape.ArrowCursor)
            else:
                event.ignore()
                self.setCursor(Qt.CursorShape.ForbiddenCursor)
            return

        target_item: QStandardItem = self.model().itemFromIndex(index)
        if target_item is None:
            event.ignore()
            return

        target_type = target_item.data(ROLE_NODE_TYPE)
        target_id = target_item.data(ROLE_NODE_ID)

        # ── 确定放置区域 ──
        rect = self.visualRect(index)
        rel_y = event.position().toPoint().y() - rect.y()
        ratio = rel_y / rect.height() if rect.height() > 0 else 0.5

        if ratio < 0.25:
            zone = "above"
        elif ratio > 0.75:
            zone = "below"
        else:
            zone = "on_item"

        # ── 验证放置有效性（含多选批量验证）──
        valid, parent_id, position = self._validate_drop_multi(
            all_dragged_ids, dragged_type,
            target_item, target_type, target_id,
            zone, index,
        )

        # ── 更新视觉反馈 ──
        self._clear_drag_feedback()
        self._hover_target_index = index
        self._hover_drop_zone = zone

        if valid:
            event.acceptProposedAction()
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self._apply_drag_feedback(index, target_item, zone)
        else:
            event.ignore()
            self.setCursor(Qt.CursorShape.ForbiddenCursor)

    # ──────────────────────────────────────────
    # 拖拽离开
    # ──────────────────────────────────────────

    def dragLeaveEvent(self, event) -> None:
        """拖拽离开视图 → 清除所有反馈。"""
        self._clear_drag_feedback()
        self._hover_target_index = None
        self._hover_drop_zone = ""
        self.setCursor(Qt.CursorShape.ArrowCursor)
        event.accept()

    # ──────────────────────────────────────────
    # 放置
    # ──────────────────────────────────────────

    def dropEvent(self, event) -> None:
        """
        拦截拖拽放置事件，计算目标父节点和插入位置，
        通过 drop_occurred（单拖）或 batch_drop_occurred（多选批量拖）信号通知上层。
        """
        if getattr(self, "_ops_locked", False):
            event.ignore()
            return
        mime = event.mimeData()
        if not mime.hasFormat("application/x-treenode-id"):
            event.ignore()
            return

        dragged_id = mime.data("application/x-treenode-id").data().decode("utf-8")
        dragged_type = (
            mime.data("application/x-treenode-type").data().decode("utf-8")
            if mime.hasFormat("application/x-treenode-type") else ""
        )
        # 多选批量拖拽 ID 列表
        all_dragged_ids: list[str] = [dragged_id]
        if mime.hasFormat("application/x-treenode-ids"):
            ids_str = mime.data("application/x-treenode-ids").data().decode("utf-8")
            all_dragged_ids = [i for i in ids_str.split(",") if i]

        index: QModelIndex = self.indexAt(event.position().toPoint())

        if not index.isValid():
            # 拖到空白区域 → 移到根级
            if self._can_drop_at_root_multi(all_dragged_ids):
                event.acceptProposedAction()
                if len(all_dragged_ids) > 1:
                    self.batch_drop_occurred.emit(
                        ",".join(all_dragged_ids), "", None
                    )
                else:
                    self.drop_occurred.emit(dragged_id, "", None, "", "")
            else:
                event.ignore()
                self._show_drop_rejected()
            self._clear_drag_feedback()
            return

        target_item: QStandardItem = self.model().itemFromIndex(index)
        if target_item is None:
            event.ignore()
            self._clear_drag_feedback()
            return

        target_type = target_item.data(ROLE_NODE_TYPE)
        target_id = target_item.data(ROLE_NODE_ID)

        # ── 确定放置区域 ──
        rect = self.visualRect(index)
        rel_y = event.position().toPoint().y() - rect.y()
        ratio = rel_y / rect.height() if rect.height() > 0 else 0.5

        if ratio < 0.25:
            zone = "above"
        elif ratio > 0.75:
            zone = "below"
        else:
            zone = "on_item"

        # ── 验证并计算目标 ──
        valid, parent_id, position = self._validate_drop_multi(
            all_dragged_ids, dragged_type,
            target_item, target_type, target_id,
            zone, index,
        )

        self._clear_drag_feedback()

        if valid:
            event.acceptProposedAction()
            if len(all_dragged_ids) > 1:
                self.batch_drop_occurred.emit(
                    ",".join(all_dragged_ids), parent_id, position
                )
            else:
                prev_id, next_id = self._drop_sibling_context(
                    parent_id, position
                )
                self.drop_occurred.emit(
                    dragged_id, parent_id, position, prev_id, next_id
                )
        else:
            event.ignore()
            self._show_drop_rejected()

    def _show_drop_rejected(self) -> None:
        """被禁止的拖拽操作:弹窗告知用户(如消息节点脱离对话)。"""
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setWindowTitle("无法移动")
        box.setText(
            "该拖拽操作被禁止：\n\n"
            "· 消息节点只能存在于对话中，不能移入文件夹或根级；\n"
            "· 文件夹/对话不能被移入消息或对话内部。"
        )
        box.exec()

    def _drop_sibling_context(
        self, parent_id: str, position: object
    ) -> tuple[str, str]:
        """
        计算目标插入点前后的消息节点 ID（分支感知拖拽判定用，3.6）。

        parent_id 为对话时，从模型取该对话下消息兄弟列表；
        position 为插入索引（None = 末尾）。返回 (prev_id, next_id)，
        不存在时为空串。
        """
        if not parent_id or self.model() is None:
            return ("", "")
        parent_item: QStandardItem | None = None

        def find_item(item: QStandardItem) -> bool:
            """递归查找目标节点（对话可能嵌套在文件夹下）。"""
            nonlocal parent_item
            if item.data(ROLE_NODE_ID) == parent_id:
                parent_item = item
                return True
            for i in range(item.rowCount()):
                if find_item(item.child(i)):
                    return True
            return False

        for row in range(self.model().rowCount()):
            top = self.model().item(row)
            if top is not None and find_item(top):
                break
        if parent_item is None:
            return ("", "")
        count = parent_item.rowCount()
        pos = count if position is None else min(max(int(position), 0), count)
        prev_id = ""
        next_id = ""
        if pos > 0:
            p_item = parent_item.child(pos - 1)
            if p_item is not None:
                prev_id = p_item.data(ROLE_NODE_ID) or ""
        if pos < count:
            n_item = parent_item.child(pos)
            if n_item is not None:
                next_id = n_item.data(ROLE_NODE_ID) or ""
        return (prev_id, next_id)

    # ──────────────────────────────────────────
    # 放置验证逻辑
    # ──────────────────────────────────────────

    def _validate_drop(
            self,
            dragged_id: str,
            dragged_type: str,
            target_item: QStandardItem,
            target_type: str,
            target_id: str,
            zone: str,
            index: QModelIndex,
    ) -> tuple[bool, str, int | None]:
        """
        验证拖拽放置是否有效。

        Returns:
            (valid, parent_id, position)
            - valid: 是否有效
            - parent_id: 目标父节点 ID（空串 "" 表示根级）
            - position: 插入位置索引（None 表示末尾）
        """
        # ── 不允许拖到自己身上 ──
        if dragged_id == target_id:
            return (False, "", None)

        # ── 不允许将非消息节点放入对话 ──
        if zone == "on_item":
            if target_type == "message":
                # 消息不接受子节点
                return (False, "", None)
            elif target_type == "conversation":
                # 对话只接受消息
                if dragged_type != "message":
                    return (False, "", None)
                # 消息放入对话：parent = 对话, position = 末尾
                return (True, target_id, None)
            elif target_type == "folder":
                # 目录接受任何节点,但消息节点不能脱离对话存在
                if dragged_type == "message":
                    return (False, "", None)
                return (True, target_id, None)
            else:
                return (False, "", None)
        else:
            # above / below → 插入为兄弟节点
            target_parent = target_item.parent()
            if target_parent is None:
                # 根级兄弟
                parent_id_str = ""
            else:
                parent_type = target_parent.data(ROLE_NODE_TYPE)
                # 如果父节点是对话且拖拽的不是消息 → 不允许
                if parent_type == "conversation" and dragged_type != "message":
                    return (False, "", None)
                if parent_type == "message":
                    return (False, "", None)
                parent_id_str = target_parent.data(ROLE_NODE_ID) or ""

            # 消息节点只能作为对话的子节点:兄弟插入时父必须是对话
            if dragged_type == "message" and parent_id_str != "":
                parent_node = self._find_item_by_node_id(parent_id_str)
                if parent_node is None or parent_node.data(ROLE_NODE_TYPE) != "conversation":
                    return (False, "", None)
            if dragged_type == "message" and parent_id_str == "":
                # 消息拖到根级(作为文件夹/对话的兄弟)→ 拒绝
                return (False, "", None)

            # 计算位置
            target_row = target_item.row()
            pos = target_row if zone == "above" else target_row + 1

            # ── 防循环：不允许拖到自己的子树中 ──
            if dragged_id == parent_id_str:
                return (False, "", None)
            # 检查 parent 是否是 dragged 的后代
            if self._is_descendant_of(dragged_id, parent_id_str):
                return (False, "", None)

            return (True, parent_id_str, pos)

    def _can_drop_at_root(self, dragged_id: str) -> bool:
        """检查是否可以将节点放到根级（空白区域）。"""
        # 如果拖拽的是根目录本身 → 不允许
        root_item = self._find_item_by_node_id("root")
        if root_item is not None and dragged_id == "root":
            return False
        # 消息节点不能脱离对话存在 → 不允许拖到根级
        item = self._find_item_by_node_id(dragged_id)
        if item is not None and item.data(ROLE_NODE_TYPE) == "message":
            return False
        return True

    def _can_drop_at_root_multi(self, dragged_ids: list[str]) -> bool:
        """批量检查：所有拖拽节点是否都可放到根级。"""
        for did in dragged_ids:
            if not self._can_drop_at_root(did):
                return False
        return True

    def _validate_drop_multi(
            self,
            dragged_ids: list[str],
            dragged_type: str,
            target_item: QStandardItem,
            target_type: str,
            target_id: str,
            zone: str,
            index: QModelIndex,
    ) -> tuple[bool, str, int | None]:
        """
        批量验证拖拽放置（多选模式下多个节点同时拖拽）。

        规则：
        - 任意拖拽节点 = 目标节点 → 无效
        - 目标父节点在任意拖拽节点的子树中 → 无效（防循环）
        - 目标父节点是任意拖拽节点自身 → 无效（防把节点放入自身）
        - 类型检查针对每种拖拽节点类型
        - 消息节点不接受子节点

        Returns:
            (valid, parent_id, position)
        """
        # ── 禁止拖到任意选中节点自身 ──
        if target_id in dragged_ids and zone == "on_item":
            return (False, "", None)

        # ── 确定目标父节点 ──
        if zone == "on_item":
            if target_type == "message":
                return (False, "", None)
            elif target_type == "conversation":
                # 对话只接受消息节点
                for did in dragged_ids:
                    d_item = self._find_item_by_node_id(did)
                    if d_item is None:
                        continue
                    dt = d_item.data(ROLE_NODE_TYPE)
                    if dt != "message":
                        return (False, "", None)
                return (True, target_id, None)
            elif target_type == "folder":
                # 目录接受任何节点,但消息节点不能脱离对话存在
                for did in dragged_ids:
                    d_item = self._find_item_by_node_id(did)
                    if d_item is not None and d_item.data(ROLE_NODE_TYPE) == "message":
                        return (False, "", None)
                return (True, target_id, None)
            else:
                return (False, "", None)
        else:
            # above / below → 插入为兄弟节点
            target_parent = target_item.parent()
            if target_parent is None:
                parent_id_str = ""
            else:
                parent_type = target_parent.data(ROLE_NODE_TYPE)
                # 若父节点是对话且拖拽的有非消息 → 不允许
                if parent_type == "conversation":
                    for did in dragged_ids:
                        d_item = self._find_item_by_node_id(did)
                        if d_item is None:
                            continue
                        dt = d_item.data(ROLE_NODE_TYPE)
                        if dt != "message":
                            return (False, "", None)
                if parent_type == "message":
                    return (False, "", None)
                parent_id_str = target_parent.data(ROLE_NODE_ID) or ""

            # 消息节点只能作为对话的子节点:含消息的批量拖拽,
            # 兄弟插入时父必须是对话
            has_msg = False
            for did in dragged_ids:
                d_item = self._find_item_by_node_id(did)
                if d_item is not None and d_item.data(ROLE_NODE_TYPE) == "message":
                    has_msg = True
                    break
            if has_msg:
                if parent_id_str == "":
                    return (False, "", None)
                parent_node = self._find_item_by_node_id(parent_id_str)
                if parent_node is None or parent_node.data(ROLE_NODE_TYPE) != "conversation":
                    return (False, "", None)

            # ── 防循环：检查每个拖拽节点 ──
            for did in dragged_ids:
                # 不能拖到自己身上
                if did == parent_id_str:
                    return (False, "", None)
                # target parent 不能是任何拖拽节点的后代
                if self._is_descendant_of(did, parent_id_str):
                    return (False, "", None)
                # target parent 不能是任何拖拽节点自身（防止 A 拖入 B 而 B 也是被拖拽的）
                if parent_id_str in dragged_ids:
                    return (False, "", None)

            # 计算位置
            target_row = target_item.row()
            pos = target_row if zone == "above" else target_row + 1

            return (True, parent_id_str, pos)

    def _is_descendant_of(self, ancestor_id: str, descendant_id: str) -> bool:
        """检查 descendant_id 是否是 ancestor_id 的后代。"""
        if not ancestor_id or not descendant_id:
            return False
        item = self._find_item_by_node_id(descendant_id)
        if item is None:
            return False
        # 向上遍历父链
        parent = item.parent()
        while parent is not None:
            pid = parent.data(ROLE_NODE_ID)
            if pid == ancestor_id:
                return True
            parent = parent.parent()
        return False

    def _find_item_by_node_id(self, node_id: str) -> QStandardItem | None:
        """在 model 中递归搜索指定 node_id 的 QStandardItem。"""
        if not node_id or self.model() is None:
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

        root = self.model().invisibleRootItem()
        if root is not None:
            return search(root)
        return None

    # ──────────────────────────────────────────
    # 视觉反馈
    # ──────────────────────────────────────────

    def _apply_drag_feedback(
            self,
            index: QModelIndex,
            target_item: QStandardItem,
            zone: str,
    ) -> None:
        """
        应用拖拽悬停视觉反馈。

        - zone == "on_item" → 绿色边框高亮目标行
        - zone in ("above", "below") → 暂用 item 背景色区分
        """
        if zone == "on_item":
            target_item.setBackground(QBrush(self.DROP_HIGHLIGHT_BG))
            target_item.setData(True, ROLE_IS_ACTIVE)  # 触发特殊渲染
            self._last_highlighted_row = target_item.row()
            # 在目标行的父节点中记录
            parent = target_item.parent()
            if parent is not None:
                self._last_highlighted_parent = parent
        elif zone in ("above", "below"):
            # 插入线由 Qt 内置 drop indicator 绘制
            target_item.setBackground(QBrush(QColor("#2A2D3A")))
            self._last_highlighted_row = target_item.row()

    def _clear_drag_feedback(self) -> None:
        """清除所有拖拽反馈，尊重选中和激活状态。"""
        if self._hover_target_index is not None and self._hover_target_index.isValid():
            item = self.model().itemFromIndex(self._hover_target_index)
            if item is not None:
                # 恢复原始背景（选中 > 激活 > 节点上下文高亮 > 默认）
                self._restore_item_background(item)
                # 不清除 ROLE_IS_ACTIVE（由 set_active 管理）
        self._last_highlighted_row = -1
        self._last_highlighted_parent = None
        self._hover_target_index = None
        self._hover_drop_zone = ""


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
    CHECK_FILL_CHECKED = QColor("#4CAF82")  # 绿色
    CHECK_FILL_PARTIAL = QColor("#E8A838")  # 黄色
    CHECK_BORDER = QColor("#3D4255")  # 灰色边框
    CHECK_SYMBOL = QColor("#FFFFFF")  # 白色符号
    ARROW_COLOR = QColor("#B0B8CC")  # 浅灰白箭头

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
                painter.setPen(QPen(self.CHECK_SYMBOL, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                    Qt.PenJoinStyle.RoundJoin))
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
        move_node(str, str, object) — 拖拽完成（node_id, target_parent_id, position）
        manage_context(str)      — 右键：管理上下文块（仅目录）
        attach_file(str)         — 右键：添加附件（仅目录）
    """

    switch_conversation = Signal(str)
    new_conversation = Signal(str)
    new_folder = Signal(str)
    rename_node = Signal(str)
    delete_node = Signal(str)
    toggle_enabled = Signal(str)
    move_node = Signal(str, str, object, str, str)  # node_id, target_parent_id, position, prev_id, next_id
    # 说明: prev_id / next_id 为目标插入点前后的消息节点 ID（仅消息链内插入有效,
    #       空串表示无;供分支感知拖拽判定"分叉点与被修改节点之间"等场景,3.6）
    manage_context = Signal(str)
    attach_file = Signal(str)
    toggle_context_blocks = Signal(str)   # 启用/禁用节点上下文块（右键菜单）

    # ── 多选模式批量操作信号 ──
    # operation: str ("toggle_enabled", "delete", "rename", "manage_context", "attach_file")
    # node_ids:  list[str]
    batch_operation = Signal(str, list)
    # 批量拖拽：comma-separated node_ids, target_parent_id, position
    batch_move_nodes = Signal(str, str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("treePanel")

        self._active_id: str = ""
        self._expanded_ids: set[str] = set()
        self._tree_data: list[TreeNodeVM] = []
        self._model: QStandardItemModel | None = None
        self._building: bool = False  # 防止重建时的信号触发

        # 分叉功能: 操作锁定（预分支/修改状态下禁用拖拽与变更，3.1.3/5.2）
        self._ops_locked: bool = False
        # 修改状态下半透明的节点（5.1.4）
        self._edited_node_id: str = ""
        # 节点上下文管理模式高亮的节点 id（"" = 无）
        self._node_ctx_highlight_id: str = ""

        # ── Model ─────────────────────────────────
        self._model = QStandardItemModel(0, 1, self)
        self._model.setHorizontalHeaderLabels([""])

        # ── Tree View ─────────────────────────────
        self._tree_view = _TreeView(self)
        # 应用自定义风格 — 绘制白色展开/折叠箭头 + 带符号的复选框。
        # ⚠️ 必须用独立的 Fusion 风格实例作为 base style，而非 QApplication.style()：
        # 直接包装全局 style 会在退出阶段因析构顺序（全局 style 先于代理销毁）
        # 产生悬空指针，导致退出崩溃（access violation / abort）。
        base_style = QStyleFactory.create("Fusion")
        if base_style is not None:
            self._tree_view.setStyle(_TreeStyle(base_style))
        self._tree_view.setModel(self._model)
        self._tree_view.setHeaderHidden(True)
        # 图标槽尺寸必须与 _build_node_icon 的 pixmap 逻辑尺寸一致，
        # 否则 DecorationRole 图标会被缩放到默认小图标槽（约 16px）而变小。
        self._tree_view.setIconSize(QSize(_ICON_W, _ICON_H))
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

        # ── 拖拽放置（单拖 + 多选批量拖）─────────
        self._tree_view.drop_occurred.connect(self._on_drop)
        self._tree_view.batch_drop_occurred.connect(self._on_batch_drop)

        # ── 样式 ──────────────────────────────────
        self._apply_styles()

        # ── 布局 ──────────────────────────────────
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._tree_view)

    # ── 样式 ──────────────────────────────────────

    def _apply_styles(self) -> None:
        """应用 QTreeView 深色主题样式。

        注意：QSS 中不设置 background-color 规则，
        让 QStandardItem.setBackground() 和 _TreeStyle 自定义绘制完全控制背景色，
        避免 QSS 覆盖多选高亮 / 激活高亮 / 拖拽反馈。
        """
        apply_style(self._tree_view, lambda: f"""
            QTreeView {{
                background-color: {Colors.BG_SURFACE};
                border: none;
                outline: none;
                font-family: "{Fonts.BODY}";
                font-size: {Fonts.SIZE_SM}px;
                color: {Colors.TEXT_SECONDARY};
            }}
        """)

        apply_style(self, lambda: f"""
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
            # ⚠️ blockSignals 期间 setCheckState/setData 不发 dataChanged，
            # QTreeView 不会自动重绘 —— 强制刷新视口，保证级联后的
            # checkState 视觉立即更新
            self._tree_view.viewport().update()
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
                old_item.setData(False, ROLE_IS_ACTIVE)
                self._tree_view._restore_item_background(old_item)

            # 设置新激活节点的背景
            new_item = self._find_item_by_node_id(node_id)
            if new_item is not None:
                if new_item.data(ROLE_IS_SELECTED):
                    new_item.setBackground(QBrush(SELECTION_BG))
                else:
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
    # 分叉功能：操作锁定 / 修改状态半透明（3.1.3/5.2）
    # ──────────────────────────────────────────────

    def set_ops_locked(self, locked: bool) -> None:
        """
        锁定/解锁树操作（拖拽、复选框、右键菜单）。

        预分支状态（3.1.3）与修改状态（5.2）下调用，
        防止任何可能触发对话树结构更新的操作。
        """
        self._ops_locked = locked
        # 同步到 _TreeView（其 dropEvent 也检查该标志）
        self._tree_view._ops_locked = locked
        self._tree_view.setDragEnabled(not locked)
        self._tree_view.setAcceptDrops(not locked)

    @property
    def ops_locked(self) -> bool:
        return self._ops_locked

    def show_rejected_hint(self, message: str) -> None:
        """拖拽被拒绝时的轻量提示（QToolTip 显示在树视图中心）。"""
        from PySide6.QtWidgets import QToolTip
        pos = self._tree_view.viewport().rect().center()
        QToolTip.showText(
            self._tree_view.viewport().mapToGlobal(pos), message, self._tree_view
        )

    def set_edited_node(self, node_id: str) -> None:
        """修改状态下将被修改节点标题置为半透明（5.1.4）。"""
        if self._edited_node_id == node_id:
            return
        self._edited_node_id = node_id
        self._update_edited_item()

    def clear_edited_node(self) -> None:
        """清除修改状态的半透明标记。"""
        if not self._edited_node_id:
            return
        self._edited_node_id = ""
        self._update_edited_item()

    def _update_edited_item(self) -> None:
        """按 _edited_node_id 更新条目文字颜色（半透明/恢复）。"""
        if self._model is None:
            return
        for row in range(self._model.rowCount()):
            item = self._model.item(row)
            if item is None:
                continue
            self._apply_edited_style_recursive(item)

    def _apply_edited_style_recursive(self, item: QStandardItem) -> None:
        """递归设置/清除节点文字半透明。"""
        from PySide6.QtGui import QBrush, QColor
        node_id = item.data(ROLE_NODE_ID)
        if node_id == self._edited_node_id:
            # ⚠️ Qt 8 位 hex 是 #AARRGGBB（alpha 在前）——#RRGGBBAA 会被
            # 误解析成错乱颜色。显式用 alpha 前置格式。
            color = QColor("#88" + Colors.TEXT_SECONDARY.lstrip("#"))
            item.setForeground(QBrush(color))
        else:
            # ⚠️ 不能用空 QBrush() 恢复：NoBrush + 黑色在深色背景下不可见，
            # 会导致"文字全消失且退出不恢复"。显式恢复为树的 QSS 文字色。
            item.setForeground(QBrush(QColor(Colors.TEXT_SECONDARY)))
        for i in range(item.rowCount()):
            child = item.child(i)
            if child is not None:
                self._apply_edited_style_recursive(child)

    # ──────────────────────────────────────────────
    # 节点上下文管理模式高亮
    # ──────────────────────────────────────────────

    def set_node_context_highlight(self, node_id: str) -> None:
        """高亮正在管理上下文块的节点（独立于激活/选中高亮）。"""
        self._node_ctx_highlight_id = node_id or ""
        self._tree_view._node_ctx_highlight_id = self._node_ctx_highlight_id
        self._apply_node_ctx_highlight()

    def clear_node_context_highlight(self) -> None:
        """清除节点上下文管理模式高亮。"""
        self._node_ctx_highlight_id = ""
        self._tree_view._node_ctx_highlight_id = ""
        self._apply_node_ctx_highlight()

    def _apply_node_ctx_highlight(self) -> None:
        """按当前 _node_ctx_highlight_id 重绘整棵树的条目背景。"""
        if self._model is None:
            return
        self._building = True
        try:
            root = self._model.invisibleRootItem()
            if root is not None:
                self._walk_node_ctx(root)
        finally:
            self._building = False

    def _walk_node_ctx(self, parent: QStandardItem) -> None:
        """递归应用/清除节点上下文高亮。"""
        for row in range(parent.rowCount()):
            child = parent.child(row)
            if child is None:
                continue
            nid = child.data(ROLE_NODE_ID)
            if nid and nid == self._node_ctx_highlight_id:
                child.setBackground(QBrush(NODE_CTX_BG))
            else:
                self._tree_view._restore_item_background(child)
            self._walk_node_ctx(child)

    # ──────────────────────────────────────────────
    # 多选模式公开接口
    # ──────────────────────────────────────────────

    def set_multi_select_mode(self, enabled: bool) -> None:
        """启用/禁用多选模式。"""
        self._tree_view.set_multi_select_mode(enabled)

    @property
    def multi_select_mode(self) -> bool:
        return self._tree_view._multi_select_mode

    @property
    def selected_ids(self) -> set[str]:
        return self._tree_view._selected_ids

    def clear_selection(self) -> None:
        """清空所有选中。"""
        self._tree_view.clear_selection()

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

            # ── 7. 恢复多选选中视觉 ────────────────
            if self._tree_view._selected_ids:
                for nid in list(self._tree_view._selected_ids):
                    sel_item = self._find_item_by_node_id(nid)
                    if sel_item is not None:
                        sel_item.setBackground(QBrush(SELECTION_BG))
                        sel_item.setData(True, ROLE_IS_SELECTED)

            # ── 8. 恢复节点上下文管理模式高亮 ──────
            if self._node_ctx_highlight_id:
                hl_item = self._find_item_by_node_id(self._node_ctx_highlight_id)
                if hl_item is not None:
                    hl_item.setBackground(QBrush(NODE_CTX_BG))
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
        # 分叉点标题前缀 "<m/n> "（3.5.2，仅信息展示）
        fork_prefix = getattr(node, "fork_display", "") or ""
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
        if fork_prefix:
            display_title = f"{fork_prefix}{display_title}"

        # 对话节点追加消息计数
        if node.node_type == "conversation" and node.message_count > 0:
            display_text = f"{display_title} ({node.message_count})"
        else:
            display_text = display_title

        # ── 图标 ──────────────────────────────────
        # 挂载了上下文块的文件夹/对话 → 图标内合成 "+" 徽标（禁用时"+"半透明）
        # 图标用 QPixmap 作为 DecorationRole，从而能单独控制 "+" 的 alpha
        # （QTreeView 不支持富文本，无法只给行内 "+" 上色，故改用图标合成）。
        item = QStandardItem(f" {display_text}")
        item.setIcon(_build_node_icon(node))
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
        # 所有节点均可拖拽；目录和对话可作为放置目标
        if node.node_type == "message":
            flags = (
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsDragEnabled
                    | Qt.ItemFlag.ItemNeverHasChildren
            )
        else:
            flags = (
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsDragEnabled
                    | Qt.ItemFlag.ItemIsDropEnabled
            )
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
        item.setData(getattr(node, "is_modified", False), ROLE_IS_MODIFIED)

        # ── 文本颜色 ──────────────────────────────
        item.setForeground(QBrush(QColor(Colors.TEXT_SECONDARY)))

        # ── 设置尺寸提示 ────────────────────────
        # 补偿因移除 QSS padding: 6px 而丢失的行高
        # 宽度设为 -1 让 Qt 自动管理，高度设为 34px (约等于默认高度 + 上下 6px padding)
        item.setSizeHint(QSize(-1, 34))

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
        - 多选模式 / Ctrl+点击 → 选中切换已在 mousePressEvent 完成，此处跳过导航
        - 对话节点 / 消息节点 → 加载该节点对应的消息
        - 目录节点 → 切换展开/折叠

        注意：如果点击位置在 checkbox 指示器上，跳过此处理逻辑，
        避免与 itemChanged → toggle_enabled 信号产生竞争。

        使用 _last_click_was_ctrl/_last_click_was_multi 而非实时检测键盘，
        因为 clicked 信号在 setExpandsOnDoubleClick 模式下会延迟 ~400ms，
        到那时 Ctrl 可能已松开。
        """
        if not index.isValid():
            return

        # 跳过 checkbox 区域的点击 — 该区域由 itemChanged 信号处理
        if self._tree_view._last_click_on_checkbox:
            return

        # 多选模式或 Ctrl+点击 → 选中切换已在 mousePressEvent 处理，不导航
        if self._tree_view._last_click_was_multi or self._tree_view._last_click_was_ctrl:
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

        多选模式下：若变更节点在选中集合中，批量切换所有选中节点。
        """
        if self._building:
            return

        if self._ops_locked:
            # 锁定状态下禁止复选框变更（预分支/修改状态，3.1.3/5.2）
            old = item.data(ROLE_ENABLED)
            self._model.blockSignals(True)
            if old is True:
                item.setCheckState(Qt.CheckState.Checked)
            elif old is False:
                item.setCheckState(Qt.CheckState.Unchecked)
            else:
                item.setCheckState(Qt.CheckState.PartiallyChecked)
            self._model.blockSignals(False)
            return

        node_type = item.data(ROLE_NODE_TYPE)
        new_check = item.checkState()
        node_id = item.data(ROLE_NODE_ID)
        if node_id is None:
            return

        # ★ 设置标志：通知 mousePressEvent 复选框被用户切换
        # 这解决了级联回调 refresh_enabled_states 将 checkState 改回原值
        # 导致状态比较法失效的问题
        self._tree_view._checkbox_changed_during_press = True

        old_enabled = item.data(ROLE_ENABLED)

        # 判断是否真正变化
        changed = False
        if new_check == Qt.CheckState.Checked and old_enabled is not True:
            changed = True
        elif new_check == Qt.CheckState.Unchecked and old_enabled is not False:
            changed = True
        elif new_check == Qt.CheckState.PartiallyChecked and old_enabled != "some":
            changed = True

        if not changed:
            return

        # ── 多选模式批量操作 ──
        if self._tree_view._multi_select_mode and node_id in self._tree_view._selected_ids and len(
                self._tree_view._selected_ids) > 1:
            selected = list(self._tree_view._selected_ids)
            self.batch_operation.emit("toggle_enabled", selected)
        else:
            self.toggle_enabled.emit(node_id)

    def _on_drop(
        self,
        dragged_id: str,
        target_parent_id: str,
        position: object,
        prev_id: str = "",
        next_id: str = "",
    ) -> None:
        """
        拖拽放置完成。
        发射 move_node 信号让上层处理业务逻辑。
        target_parent_id 为空串时表示根级。
        position 为 None 时表示追加到末尾。
        prev_id/next_id 为目标插入点前后节点（分支感知拖拽，3.6）。
        """
        self.move_node.emit(dragged_id, target_parent_id, position, prev_id, next_id)

    def _on_batch_drop(self, dragged_ids_str: str, target_parent_id: str, position: object) -> None:
        """
        多选批量拖拽放置完成。
        发射 batch_move_nodes 信号让上层处理。
        """
        self.batch_move_nodes.emit(dragged_ids_str, target_parent_id, position)

    # ──────────────────────────────────────────────
    # 右键上下文菜单
    # ──────────────────────────────────────────────

    def _on_context_menu(self, pos: QPoint) -> None:
        """
        在鼠标位置弹出右键上下文菜单。
        根据节点类型构建不同的菜单项。

        多选模式下：仅当右键节点在选中集合中时才弹出菜单，
        菜单项 = 所有选中节点操作集合的交集。
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

        if self._ops_locked:
            # 锁定状态下禁用右键菜单（预分支/修改状态，3.1.3/5.2）
            return

        # ── 多选模式：仅已选中节点可弹出菜单 ──
        multi_mode = self._tree_view._multi_select_mode
        selected = self._tree_view._selected_ids

        # 多选模式下，仅已选中节点可弹出菜单
        if multi_mode and node_id not in selected:
            return

        # ── 计算可用操作交集 ──
        # 多选模式 + 多个选中，或正常模式 Ctrl+多选
        is_batch = len(selected) > 1 and node_id in selected
        if is_batch:
            available_ops = self._compute_intersection_ops(selected)
        else:
            available_ops = _OPS_BY_TYPE.get(node_type, set())

        menu = QMenu(self)
        menu.setFont(Fonts.body(Fonts.SIZE_SM))
        apply_style(menu, lambda: f"""
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
            QMenu::item:disabled {{
                color: {Colors.TEXT_DISABLED};
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {Colors.DIVIDER};
                margin: 4px 8px;
            }}
        """)

        nid = node_id
        batch_ids = list(selected) if is_batch else [nid]

        # ── 新建文件夹（非批量 + 目录节点）──
        if "new_folder" in available_ops and node_type == "folder" and not is_batch:
            menu.addAction("📁 新建文件夹", lambda: self.new_folder.emit(nid))

        # ── 新建对话（非批量 + 目录节点）──
        if "new_conversation" in available_ops and node_type == "folder" and not is_batch:
            menu.addAction("💬 新建对话", lambda: self.new_conversation.emit(nid))

        # ── 分隔线（如果有新建操作）──
        if ("new_folder" in available_ops or "new_conversation" in available_ops) and not is_batch:
            if node_type == "folder":
                menu.addSeparator()

        # ── 重命名 ──
        if "rename" in available_ops:
            menu.addAction("✏️ 重命名", lambda: self.batch_operation.emit("rename", batch_ids))

        # ── 启用/禁用 ──
        if "toggle_enabled" in available_ops:
            toggle_label = "🔳 禁用" if is_enabled is True else "🔲 启用"
            menu.addAction(toggle_label, lambda: self.batch_operation.emit("toggle_enabled", batch_ids))

        # ── 分隔线 ──
        if "manage_context" in available_ops or "attach_file" in available_ops:
            menu.addSeparator()

        # ── 管理上下文块 ──
        if "manage_context" in available_ops:
            menu.addAction("📚 管理上下文块", lambda: self.batch_operation.emit("manage_context", batch_ids))

        # ── 添加附件 ──
        if "attach_file" in available_ops:
            menu.addAction("📎 添加附件", lambda: self.batch_operation.emit("attach_file", batch_ids))

        # ── 启用/禁用上下文块（仅该节点有上下文块时，单节点）──
        if node_type in ("folder", "conversation") and not is_batch:
            node_vm = next((n for n in self._tree_data if n.id == nid), None)
            if node_vm is not None and node_vm.context_block_count > 0:
                ctx_on = getattr(node_vm, "context_blocks_enabled", True)
                ctx_label = "🚫 禁用上下文块" if ctx_on else "✅ 启用上下文块"
                menu.addAction(
                    ctx_label,
                    lambda: self.toggle_context_blocks.emit(nid),
                )

        menu.addSeparator()

        # ── 删除（所有节点通用）──
        if "delete" in available_ops:
            menu.addAction("🗑️ 删除", lambda: self.batch_operation.emit("delete", batch_ids))

        # 弹出菜单
        menu.exec(self._tree_view.viewport().mapToGlobal(pos))

    def _compute_intersection_ops(self, selected_ids: set[str]) -> set[str]:
        """
        计算所有选中节点操作集合的交集。
        每个节点按其 node_type 获取操作集合，取交集。
        """
        if not selected_ids:
            return set()

        ops = None
        for nid in selected_ids:
            item = self._find_item_by_node_id(nid)
            if item is None:
                continue
            ntype = item.data(ROLE_NODE_TYPE)
            node_ops = _OPS_BY_TYPE.get(ntype, set())
            if ops is None:
                ops = node_ops.copy()
            else:
                ops &= node_ops
            if not ops:
                break

        return ops or set()


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
    if node.node_type in ("folder", "conversation"):
        if node.context_block_count > 0:
            parts.append(f"上下文块: {node.context_block_count}")
            if not getattr(node, "context_blocks_enabled", True):
                parts.append("上下文块: 已禁用")
        if node.attachment_count > 0:
            parts.append(f"附件: {node.attachment_count}")
    if node.updated_at:
        parts.append(f"更新: {node.updated_at}")

    return "\n".join(parts)
