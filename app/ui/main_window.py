# Layer: UI (PySide6)
# File: app/ui/main_window.py
# Responsibility: 主窗口 — 3 栏布局、侧边栏（完整树面板）、聊天区、消息列表、空状态
# Phase 3: 集成完整的 Sidebar + TreePanel，替换 Phase 1/2 占位符

from __future__ import annotations

from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QSplitter,
    QStackedWidget,
    QScrollArea,
    QFrame,
    QLabel,
    QPushButton,
    QSizePolicy,
    QApplication,
)
from PySide6.QtCore import Qt, QMargins, QPropertyAnimation, QEasingCurve, QPoint, QTimer
from PySide6.QtGui import QFont

from app.ui.theme import Colors, Fonts, Spacing, Radius
from app.ui.widgets.input_area import InputArea
from app.ui.widgets.sidebar import Sidebar
from app.ui.widgets.context_panel import ContextPanel
from app.ui.widgets.kg_panel import KGPanel


# ──────────────────────────────────────────────
# 空状态提示控件
# ──────────────────────────────────────────────


class EmptyHintWidget(QWidget):
    """
    聊天区空状态提示 — 居中显示 "DR" logo + 应用名 + 副标题。
    与原 Flet 版本 ChatApp._set_empty_hint 视觉完全一致。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("emptyHint")

        # ── "DR" logo ──────────────────────────
        logo_label = QLabel("DR")
        logo_label.setFont(Fonts.mono(Fonts.SIZE_XL, QFont.Weight.Bold))
        logo_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.PRIMARY};
                background-color: transparent;
                border: none;
            }}
        """)
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_label.setFixedSize(64, 64)

        logo_container = QWidget()
        logo_container.setFixedSize(64, 64)
        logo_container.setStyleSheet(f"""
            QWidget {{
                background-color: {Colors.PRIMARY_GLOW};
                border: 1px solid {Colors.PRIMARY};
                border-radius: 12px;
            }}
        """)
        # 在容器上叠加 label
        logo_layout = QVBoxLayout(logo_container)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo_layout.setSpacing(0)
        logo_layout.addWidget(logo_label)
        logo_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ── "DeepResearch" 标题 ────────────────
        title_label = QLabel("DeepResearch")
        title_label.setFont(Fonts.mono(Fonts.SIZE_XXL, QFont.Weight.DemiBold))
        title_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_PRIMARY};
                background-color: transparent;
                border: none;
            }}
        """)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ── 副标题 ──────────────────────────────
        subtitle_label = QLabel("选择或新建一个对话以开始")
        subtitle_label.setFont(Fonts.body(Fonts.SIZE_MD))
        subtitle_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_SECONDARY};
                background-color: transparent;
                border: none;
            }}
        """)
        subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ── 组装 ────────────────────────────────
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.MD)
        layout.addStretch()
        layout.addWidget(logo_container, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle_label, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.setStyleSheet(f"""
            EmptyHintWidget {{
                background-color: {Colors.BG_BASE};
            }}
        """)


# ──────────────────────────────────────────────
# 消息列表控件（Phase 1 — 仅空状态）
# ──────────────────────────────────────────────


class _ScrollToBottomButton(QPushButton):
    """浮动"滚动到底部"按钮 — 半透明圆形，点击后平滑滚动到消息列表底部。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText("↓")
        self.setFixedSize(40, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("滚动到底部")
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(26, 30, 40, 0.85);
                color: {Colors.PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 20px;
                font-size: 18px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {Colors.BG_ELEVATED};
                border-color: {Colors.PRIMARY};
            }}
        """)
        self.hide()


class MessageListView(QWidget):
    """
    消息列表区域 — 使用 QStackedWidget 在空状态与消息列表之间切换。

    Phase 5: 精确滚动定位。
    - message_id → ChatMessage 映射，支持 scroll_to_message 精确居中
    - 高亮目标消息（2 秒后淡出），仅最新一条高亮
    - 流式输出自动跟随底部（用户上滚后暂停，滚回底部恢复）
    - 长列表（500+ 条消息）流畅滚动

    多对话模式：
    - is_near_bottom() 判断距底部是否 < 50px
    - 滚动到底部按钮：底部附近隐藏，否则显示
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("messageListView")

        # ── 消息滚动区域 ──────────────────────
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._scroll_area.setStyleSheet(f"""
            QScrollArea {{
                background-color: {Colors.BG_BASE};
                border: none;
            }}
        """)

        # 消息容器 — 垂直布局，消息控件追加到此
        self._message_container = QWidget()
        self._message_layout = QVBoxLayout(self._message_container)
        self._message_layout.setContentsMargins(
            Spacing.XL, Spacing.LG, Spacing.XL, Spacing.LG
        )
        self._message_layout.setSpacing(Spacing.SM)
        self._message_layout.addStretch()
        self._scroll_area.setWidget(self._message_container)

        # ── 空状态提示 ──────────────────────────
        self._empty_hint = EmptyHintWidget()

        # ── Stack 叠加 ──────────────────────────
        self._stack = QStackedWidget()
        self._stack.addWidget(self._scroll_area)   # index 0 — 消息列表
        self._stack.addWidget(self._empty_hint)     # index 1 — 空状态
        self._stack.setCurrentIndex(1)              # 默认显示空状态

        # ── Phase 5: 消息跟踪 ─────────────────
        # message_id → ChatMessage 映射表
        self._message_widget_map: dict[str, object] = {}
        # 高亮状态
        self._highlighted_id: str | None = None
        self._highlight_timer: QTimer | None = None
        # 自动跟随底部（流式输出用）
        self._auto_follow: bool = True
        self._programmatic_scroll: bool = False
        # 平滑滚动动画引用（防 GC）
        self._scroll_anim: QPropertyAnimation | None = None

        # ── 滚动锚点 ──────────────────────────
        # 用于消息列表刷新后恢复阅读位置（锚定到具体消息）
        self._anchor_message_id: str | None = None
        self._anchor_offset: int = 0
        self._anchor_old_ids: list[str] = []

        # ── 自动跟随检测 ──────────────────────
        self._setup_scroll_tracking()

        # ── 滚动到底部浮动按钮 ────────────────
        self._scroll_to_bottom_btn = _ScrollToBottomButton(self)
        self._scroll_to_bottom_btn.clicked.connect(self._smooth_scroll_to_bottom)
        # 初始检查按钮可见性
        QTimer.singleShot(0, self._update_scroll_btn_visibility)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._stack)

        self.setStyleSheet(f"""
            MessageListView {{
                background-color: {Colors.BG_BASE};
            }}
        """)

    def resizeEvent(self, event) -> None:
        """窗口大小变化时重新定位滚动按钮。"""
        super().resizeEvent(event)
        self._position_scroll_btn()

    def _position_scroll_btn(self) -> None:
        """将滚动按钮定位在右下角。"""
        btn = self._scroll_to_bottom_btn
        margin = 16
        x = self.width() - btn.width() - margin
        y = self.height() - btn.height() - margin - 8  # 输入区上方留白
        btn.move(x, y)

    # ── 滚动跟踪（自动跟随检测 + 按钮可见性）─

    def _setup_scroll_tracking(self) -> None:
        """
        连接滚动条信号以检测：
        1. 用户手动滚动 → 更新自动跟随标志
        2. 滚动位置变化 → 更新「滚动到底部」按钮可见性
        """
        scrollbar = self._scroll_area.verticalScrollBar()
        scrollbar.valueChanged.connect(self._on_scroll_value_changed)
        scrollbar.actionTriggered.connect(self._on_scroll_action)

    def _on_scroll_action(self, action: int) -> None:
        """用户通过滚动条交互（拖拽、点击箭头等）时触发。"""
        QTimer.singleShot(0, self._check_auto_follow)

    def _on_scroll_value_changed(self, value: int) -> None:
        """滚动条值变化时触发。"""
        if not self._programmatic_scroll:
            self._check_auto_follow()
        self._update_scroll_btn_visibility()

    def _check_auto_follow(self) -> None:
        """检测当前是否在底部，更新自动跟随标志。"""
        self._auto_follow = self.is_near_bottom()

    def is_near_bottom(self, threshold: int = 50) -> bool:
        """
        检查滚动位置是否在底部附近。

        Args:
            threshold: 距底部的像素阈值，默认 50px

        Returns:
            True 如果距离底部 < threshold 像素
        """
        scrollbar = self._scroll_area.verticalScrollBar()
        return scrollbar.value() >= scrollbar.maximum() - threshold

    def scrollbar_value(self) -> int:
        """返回当前滚动条位置（绝对值），供外部保存/恢复。"""
        return self._scroll_area.verticalScrollBar().value()

    def set_scrollbar_value(self, value: int) -> None:
        """设置滚动条位置（绝对值），自动 clamp 到有效范围。"""
        scrollbar = self._scroll_area.verticalScrollBar()
        self._programmatic_scroll = True
        scrollbar.setValue(max(0, min(value, scrollbar.maximum())))
        self._programmatic_scroll = False

    # ── 滚动锚点（刷新后恢复阅读位置）──────

    def save_scroll_anchor(self, sorted_ids: list[str]) -> None:
        """
        记录当前视口中心位置的消息作为滚动锚点。

        刷新前调用：找到视口中点附近最近的消息 widget，
        记录其 ID 及与视口中心的像素偏移。

        Args:
            sorted_ids: 当前消息 ID 列表（按展示顺序排列，用于降级查找）
        """
        self._anchor_message_id = None
        self._anchor_offset = 0
        self._anchor_old_ids = list(sorted_ids)

        viewport = self._scroll_area.viewport()
        if viewport is None:
            return

        scrollbar = self._scroll_area.verticalScrollBar()
        viewport_center_y = scrollbar.value() + viewport.height() // 2

        best_id: str | None = None
        best_distance: float = float("inf")
        best_offset: int = 0

        for msg_id, widget in self._message_widget_map.items():
            try:
                widget_y = widget.mapTo(self._message_container, QPoint(0, 0)).y()
            except RuntimeError:
                continue  # widget 已被删除
            widget_center = widget_y + widget.height() // 2
            distance = abs(widget_center - viewport_center_y)
            if distance < best_distance:
                best_distance = distance
                best_id = msg_id
                best_offset = widget_center - viewport_center_y

        self._anchor_message_id = best_id
        self._anchor_offset = best_offset

    def restore_scroll_anchor(self, new_sorted_ids: list[str]) -> None:
        """
        恢复到锚点消息的阅读位置。

        刷新后调用：定位到锚点消息（或降级到最近幸存消息），
        使其出现在视口中的相同相对位置。

        Args:
            new_sorted_ids: 刷新后的消息 ID 列表（按展示顺序排列）
        """
        if not new_sorted_ids:
            # 列表为空 — 滚动到顶部
            scrollbar = self._scroll_area.verticalScrollBar()
            self._programmatic_scroll = True
            scrollbar.setValue(0)
            self._programmatic_scroll = False
            return

        anchor_id = self._anchor_message_id
        offset = self._anchor_offset

        if anchor_id is None or anchor_id not in self._message_widget_map:
            # 锚点消息已消失 — 向上查找最近幸存的
            anchor_id = self._find_nearest_surviving(
                self._anchor_message_id, self._anchor_old_ids, new_sorted_ids
            )
            offset = 0  # 降级后不使用偏移

        if anchor_id is None:
            # 无幸存消息 — 滚动到底部
            self.scroll_to_bottom()
            return

        widget = self._message_widget_map.get(anchor_id)
        if widget is None:
            return

        try:
            widget_y = widget.mapTo(self._message_container, QPoint(0, 0)).y()
        except RuntimeError:
            return

        widget_center = widget_y + widget.height() // 2
        viewport = self._scroll_area.viewport()
        viewport_h = viewport.height() if viewport else 0

        target_scroll = widget_center - viewport_h // 2 - offset
        scrollbar = self._scroll_area.verticalScrollBar()
        target_scroll = max(0, min(target_scroll, scrollbar.maximum()))

        self._programmatic_scroll = True
        scrollbar.setValue(target_scroll)
        self._programmatic_scroll = False

    def _find_nearest_surviving(
        self, anchor_id: str | None, old_ids: list[str], new_ids: list[str]
    ) -> str | None:
        """
        在刷新后的消息列表中查找最近的幸存消息。

        1. 尝试在 new_ids 中找到 anchor_id 原位置
        2. 从该位置向上（时间更早）查找第一条存在于 widget map 中的消息
        3. 如果向上未找到，向下查找
        4. 如果 anchor_id 不在 old_ids 中，返回 new_ids 中间位置的消息

        Args:
            anchor_id: 原始锚点 ID（可能为 None）
            old_ids: 刷新前消息 ID 列表
            new_ids: 刷新后消息 ID 列表

        Returns:
            幸存消息 ID，或 None
        """
        new_set = set(new_ids)
        new_map = self._message_widget_map

        # 确定搜索起始位置（在 new_ids 中的索引）
        start_idx = len(new_ids) // 2  # 默认从中间开始

        if anchor_id is not None and anchor_id in old_ids:
            old_idx = old_ids.index(anchor_id)
            # 尝试在 new_ids 中找到原位置附近的消息
            # 通过 ID 交叉比对：找到 old_ids[old_idx] 附近且在 new_ids 中的 ID
            for offset in range(len(old_ids)):
                # 向上
                up_idx = old_idx - offset
                if up_idx >= 0 and old_ids[up_idx] in new_set and old_ids[up_idx] in new_map:
                    return old_ids[up_idx]
                # 向下
                down_idx = old_idx + offset
                if offset > 0 and down_idx < len(old_ids) and old_ids[down_idx] in new_set and old_ids[down_idx] in new_map:
                    return old_ids[down_idx]

        # 最后的降级：从 new_ids 中间位置开始找任意存在的消息
        for offset in range(len(new_ids)):
            up_idx = start_idx - offset
            if up_idx >= 0 and new_ids[up_idx] in new_map:
                return new_ids[up_idx]
            down_idx = start_idx + offset
            if down_idx < len(new_ids) and new_ids[down_idx] in new_map:
                return new_ids[down_idx]

        return None

    # ── 滚动到底部按钮 ──────────────────────

    def _update_scroll_btn_visibility(self) -> None:
        """根据当前滚动位置显示/隐藏「滚动到底部」按钮。"""
        if self.is_near_bottom():
            self._scroll_to_bottom_btn.hide()
        else:
            self._position_scroll_btn()
            self._scroll_to_bottom_btn.show()
            self._scroll_to_bottom_btn.raise_()

    def _smooth_scroll_to_bottom(self) -> None:
        """平滑滚动到消息列表底部。"""
        self._auto_follow = True
        scrollbar = self._scroll_area.verticalScrollBar()
        self._smooth_scroll_to(scrollbar.maximum())

    # ── 消息注册与查找 ──────────────────────

    def register_message(self, message_id: str, widget: object) -> None:
        """
        将 message_id 与 ChatMessage 控件关联。
        用于后续精确定位和高亮。
        """
        if message_id:
            self._message_widget_map[message_id] = widget

    def find_message_widget(self, message_id: str) -> object | None:
        """根据 message_id 查找对应的 ChatMessage 控件。"""
        return self._message_widget_map.get(message_id)

    # ── 精确滚动定位 ────────────────────────

    def scroll_to_message(self, message_id: str) -> None:
        """
        平滑滚动到指定消息，使其出现在视口顶部偏下位置。

        消息顶部距视口顶部约 50px，方便用户从消息开头开始阅读。
        使用 QPropertyAnimation 实现平滑动画。
        """
        widget = self._message_widget_map.get(message_id)
        if widget is None:
            return

        TOP_OFFSET = 50  # 消息顶部距视口顶部的像素偏移

        widget_y = widget.mapTo(self._message_container, QPoint(0, 0)).y()
        scrollbar = self._scroll_area.verticalScrollBar()

        # 目标：消息顶部在视口顶部下方 TOP_OFFSET 处
        target_y = widget_y - TOP_OFFSET
        target_y = max(0, min(target_y, scrollbar.maximum()))

        self._smooth_scroll_to(target_y)

    def _smooth_scroll_to(self, target_y: int) -> None:
        """使用 QPropertyAnimation 平滑滚动到目标位置。"""
        scrollbar = self._scroll_area.verticalScrollBar()

        # 停止之前的动画
        if self._scroll_anim is not None:
            self._scroll_anim.stop()
            self._scroll_anim = None

        self._programmatic_scroll = True

        anim = QPropertyAnimation(scrollbar, b"value")
        anim.setDuration(300)  # 300ms 平滑动画
        anim.setStartValue(scrollbar.value())
        anim.setEndValue(target_y)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._on_scroll_anim_finished)
        anim.start()
        self._scroll_anim = anim

    def _on_scroll_anim_finished(self) -> None:
        """平滑滚动动画完成后的清理。"""
        self._programmatic_scroll = False
        self._scroll_anim = None

    # ── 消息高亮 ────────────────────────────

    def highlight_message(self, message_id: str) -> None:
        """
        高亮指定消息（2 秒后自动淡出）。
        仅保留最新一条高亮 — 之前的高亮立即清除。
        """
        # 清除之前的高亮
        self._clear_highlight()

        widget = self._message_widget_map.get(message_id)
        if widget is None:
            return

        self._highlighted_id = message_id
        widget.set_highlighted(True)

        # 2 秒后自动清除
        self._highlight_timer = QTimer(self)
        self._highlight_timer.setSingleShot(True)
        self._highlight_timer.timeout.connect(self._clear_highlight)
        self._highlight_timer.start(2000)

    def _clear_highlight(self) -> None:
        """清除当前高亮状态。"""
        if self._highlight_timer is not None:
            self._highlight_timer.stop()
            self._highlight_timer = None

        if self._highlighted_id is not None:
            widget = self._message_widget_map.get(self._highlighted_id)
            if widget is not None:
                widget.set_highlighted(False)
            self._highlighted_id = None

    # ── 公开接口（兼容 + 新增）──────────────

    def show_empty_hint(self, visible: bool = True) -> None:
        """切换空状态 / 消息列表的显示。"""
        if visible:
            self._stack.setCurrentIndex(1)
        else:
            self._stack.setCurrentIndex(0)

    def is_showing_empty_hint(self) -> bool:
        """返回当前是否显示空状态。"""
        return self._stack.currentIndex() == 1

    def message_container(self) -> QWidget:
        """返回消息容器，供外部追加 ChatMessage 控件。"""
        return self._message_container

    def message_layout(self) -> QVBoxLayout:
        """返回消息布局，供外部追加 ChatMessage 控件。"""
        return self._message_layout

    @property
    def auto_follow(self) -> bool:
        """是否处于自动跟随底部模式。"""
        return self._auto_follow

    def clear_messages(self) -> None:
        """清空所有消息控件和映射表（保留末尾 stretch）。"""
        self._clear_highlight()
        self._message_widget_map.clear()
        self._auto_follow = True

        layout = self._message_layout
        while layout.count() > 0:
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()          # 立即隐藏，防止残影
                widget.deleteLater()   # 调度销毁
        layout.addStretch()
        # 强制容器重新计算大小
        self._message_container.updateGeometry()

    def scroll_to_bottom(self) -> None:
        """
        滚动到消息列表底部。

        多对话模式：仅当 is_near_bottom() 为 True 时才实际滚动。
        用户手动上滚后远离底部，此方法自动跳过，不再强制拉回。
        """
        if not self.is_near_bottom():
            return
        # 强制布局重新计算，确保 scrollbar maximum 正确
        self._message_container.updateGeometry()
        self._programmatic_scroll = True
        scrollbar = self._scroll_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        self._programmatic_scroll = False

    def save_scroll_state(self) -> dict:
        """
        保存当前滚动状态，供对话切换后恢复。

        Returns:
            {"position": int, "auto_follow": bool}
        """
        scrollbar = self._scroll_area.verticalScrollBar()
        return {
            "position": scrollbar.value(),
            "auto_follow": self._auto_follow,
        }

    def restore_scroll_state(self, state: dict | None) -> None:
        """
        恢复之前保存的滚动位置。

        Args:
            state: save_scroll_state() 返回的字典，None 表示滚到底部
        """
        if state is None:
            self._auto_follow = True
            self.scroll_to_bottom()
            return

        pos = state.get("position", 0)
        self._auto_follow = state.get("auto_follow", True)

        scrollbar = self._scroll_area.verticalScrollBar()
        pos = max(0, min(pos, scrollbar.maximum()))
        self._programmatic_scroll = True
        scrollbar.setValue(pos)
        self._programmatic_scroll = False


# ──────────────────────────────────────────────
# 侧边栏 — 已移至 app/ui/widgets/sidebar.py
# Sidebar 类在 Phase 3 替换了旧 SidebarPlaceholder
# ──────────────────────────────────────────────


# ──────────────────────────────────────────────
# 主窗口
# ──────────────────────────────────────────────


class MainWindow(QMainWindow):
    """
    DeepResearch 主窗口。

    布局：
        Sidebar (260px) | Divider (1px) | Chat Area (expand) | Right Panel (0/380px)

    Right Panel:
        QStackedWidget 包裹 ContextPanel (index 0) 和 KGPanel (index 1)
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DeepResearch")
        self.setMinimumSize(900, 600)

        # ── 全局背景 ────────────────────────────
        self.setStyleSheet(f"""
            MainWindow {{
                background-color: {Colors.BG_BASE};
            }}
        """)

        # ── 侧边栏 ──────────────────────────────
        self._sidebar = Sidebar()

        # ── 垂直分割线 ──────────────────────────
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setStyleSheet(f"""
            QFrame {{
                color: {Colors.BORDER};
                border: none;
                border-left: 1px solid {Colors.BORDER};
            }}
        """)
        divider.setFixedWidth(1)

        # ── 消息列表 ────────────────────────────
        self._message_list = MessageListView()

        # ── 输入区 ──────────────────────────────
        self._input_area = InputArea()

        # ── 聊天区（消息列表 + 输入区）───────────
        chat_area = QVBoxLayout()
        chat_area.setContentsMargins(0, 0, 0, 0)
        chat_area.setSpacing(0)
        chat_area.addWidget(self._message_list, stretch=1)
        chat_area.addWidget(self._input_area)

        chat_widget = QWidget()
        chat_widget.setLayout(chat_area)

        # ── 右侧面板 ─────────────────────────────
        self._context_panel = ContextPanel()
        self._kg_panel = KGPanel()

        # 用 QStackedWidget 装两个面板
        self._right_panel_stack = QStackedWidget()
        self._right_panel_stack.addWidget(self._context_panel)  # index 0
        self._right_panel_stack.addWidget(self._kg_panel)        # index 1
        self._right_panel_stack.setFixedWidth(0)  # 默认隐藏
        self._right_panel_stack.setStyleSheet(f"""
            QStackedWidget {{
                background-color: {Colors.BG_SURFACE};
                border-left: 1px solid {Colors.BORDER};
            }}
        """)

        # ── 内容区（聊天 + 右侧面板）────────────
        self._content_layout = QHBoxLayout()
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        self._content_layout.addWidget(chat_widget, stretch=1)
        self._content_layout.addWidget(self._right_panel_stack)

        content_widget = QWidget()
        content_widget.setLayout(self._content_layout)

        # ── 3 栏主布局 ──────────────────────────
        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self._sidebar)
        main_layout.addWidget(divider)
        main_layout.addWidget(content_widget, stretch=1)

        central = QWidget()
        central.setLayout(main_layout)
        self.setCentralWidget(central)

    # ── 公开属性 ──────────────────────────────

    @property
    def message_list(self) -> MessageListView:
        """消息列表控件。"""
        return self._message_list

    @property
    def input_area(self) -> InputArea:
        """输入区控件。"""
        return self._input_area

    @property
    def sidebar(self) -> Sidebar:
        """侧边栏控件（Phase 3 — 完整树面板）。"""
        return self._sidebar

    @property
    def context_panel(self) -> ContextPanel:
        """上下文管理面板。"""
        return self._context_panel

    @property
    def kg_panel(self) -> KGPanel:
        """知识图谱面板。"""
        return self._kg_panel

    # ── 右侧面板控制 ──────────────────────────

    def show_context_panel(self) -> None:
        """显示上下文管理面板（隐藏 KG 面板）。"""
        self._right_panel_stack.setFixedWidth(380)
        self._right_panel_stack.setCurrentIndex(0)
        self._context_panel.show_panel()

    def show_kg_panel(self) -> None:
        """显示知识图谱面板（隐藏上下文面板）。"""
        self._right_panel_stack.setFixedWidth(380)
        self._right_panel_stack.setCurrentIndex(1)
        self._kg_panel.show_panel()

    def hide_right_panel(self) -> None:
        """隐藏右侧面板。"""
        self._right_panel_stack.setFixedWidth(0)
        self._context_panel.hide_panel()
        self._kg_panel.hide_panel()

    def is_right_panel_visible(self) -> bool:
        """右侧面板是否可见。"""
        return self._right_panel_stack.width() > 0
