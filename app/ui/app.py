# Layer: UI (PySide6)
# File: app/ui/app.py
# Responsibility: ChatApp 主应用类 — 持有 MainWindow，连接 Controller，
#                 管理对话生命周期、流式响应、面板切换。
# 与原 Flet 版本 app/ui/app.py 的 ChatApp 功能对等

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QApplication,
    QInputDialog,
    QMessageBox,
    QFileDialog,
)
from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtGui import QShortcut, QKeySequence

from app.ui.theme import Colors, build_global_stylesheet
from app.ui.main_window import MainWindow
from app.ui.widgets.chat_message import ChatMessage, ThinkingBlock
from app.ui.widgets.search_popup import SearchPopup
from app.ui.widgets.sidebar import Sidebar
from app.ui.async_bridge import (
    AsyncStreamWorker,
    StreamRelay,
    run_async_in_thread,
)


class ChatApp:
    """
    DeepResearch 主应用类。

    与原 Flet 版本功能对等：
    - 持有 QMainWindow（替代 ft.Page）
    - 接收 AppController + SettingsController 注入
    - 管理对话生命周期、流式响应、消息列表
    """

    def __init__(self, app_controller, settings_controller) -> None:
        self._ctrl = app_controller
        self._settings_ctrl = settings_controller

        # ── 状态（与 Flet 版本 ChatApp 字段一一对应）──
        self._current_session_id: str = ""
        self._streaming_message: ChatMessage | None = None
        # 流式期间收到消息列表重建请求（删除/启用切换等）时置 True，
        # 流结束后统一重建，避免流式控件被销毁导致输出效果丢失
        self._pending_rebuild_after_stream: bool = False
        self._tree_nodes: list = []

        # ── Phase 5: 精确滚动定位 ─────────────
        # 点击消息节点时暂存目标 message_id，消息加载完成后滚动定位
        self._pending_scroll_target: str | None = None
        # 每个对话的滚动位置记录（用于切换对话后恢复）
        self._scroll_states: dict[str, dict] = {}
        self._load_scroll_positions()  # 从磁盘恢复

        # ── 流式工作线程引用（防 GC）───────────
        self._stream_thread: QThread | None = None
        self._stream_worker: AsyncStreamWorker | None = None
        self._stream_relay: StreamRelay | None = None  # 保持 relay 引用，防止 GC
        self._async_task_threads: list = []  # 保持引用防止 GC
        # 未完成的助手消息（用户停止生成后保留引用，供"继续生成"）
        self._incomplete_message: ChatMessage | None = None
        # 未完成轮次的用户消息节点 id（重绘后 user widget 有真实 ID，靠它定位）
        self._incomplete_user_id: str | None = None
        # 最近一次发送的文本（供未完成轮次"重新发送"用）
        self._last_sent_text: str = ""

        # ── 构建主窗口 ──────────────────────────
        self._window = MainWindow()

        # ── 搜索弹窗 ──────────────────────────
        self._search_popup = SearchPopup(self._window)
        self._search_popup.result_clicked.connect(
            self._on_search_result_clicked
        )
        self._search_popup.dismissed.connect(
            self._on_search_popup_dismissed
        )
        self._skip_next_anchor_restore = False

        # ── Ctrl+F 快捷键 ───────────────────────
        self._search_shortcut = QShortcut(
            QKeySequence("Ctrl+F"), self._window
        )
        self._search_shortcut.activated.connect(
            self._window.sidebar.focus_search
        )

        # ── 应用全局样式表 ─────────────────────
        self._apply_theme()

        # ── 连接信号 ────────────────────────────
        self._connect_signals()

    # ── 主题 ──────────────────────────────────

    def _apply_theme(self) -> None:
        """应用全局深色主题样式表。"""
        app = QApplication.instance()
        if app:
            app.setStyleSheet(build_global_stylesheet())

    # ── 信号连接 ──────────────────────────────

    def _connect_signals(self) -> None:
        """连接 InputArea、Sidebar 和 TreePanel 的信号到对应槽。"""
        # ── InputArea 信号 ────────────────────────
        input_area = self._window.input_area
        input_area.send_requested.connect(self._handle_send)
        input_area.stop_requested.connect(self._handle_stop)
        input_area.model_changed.connect(
            lambda m: self._settings_ctrl.on_change_model(m)
        )
        input_area.thinking_toggled.connect(
            lambda v: self._settings_ctrl.on_toggle_thinking(v)
        )
        input_area.search_toggled.connect(
            lambda v: self._settings_ctrl.on_toggle_search(v)
        )
        input_area.reasoning_effort_changed.connect(
            lambda e: self._settings_ctrl.on_change_reasoning_effort(e)
        )

        # ── Sidebar 顶部按钮信号 ──────────────────
        sidebar = self._window.sidebar
        sidebar.new_conversation_clicked.connect(self._handle_new_conversation)
        sidebar.new_folder_clicked.connect(self._handle_new_root_folder)
        sidebar.open_trash_clicked.connect(self._handle_open_trash)
        sidebar.open_context_panel_clicked.connect(self._handle_open_context_panel)
        sidebar.open_kg_panel_clicked.connect(self._handle_open_kg_panel)
        sidebar.search_requested.connect(self._on_search)
        sidebar.multi_select_toggled.connect(self._on_multi_select_toggled)

        # ── TreePanel 信号 ────────────────────────
        tree = sidebar.tree_panel
        tree.switch_conversation.connect(self._on_tree_switch)
        tree.new_conversation.connect(self._on_tree_new_conversation)
        tree.new_folder.connect(self._on_tree_new_folder)
        tree.rename_node.connect(self._on_tree_rename)
        tree.delete_node.connect(self._on_tree_delete)
        tree.toggle_enabled.connect(self._on_tree_toggle_enabled)
        tree.move_node.connect(self._on_tree_move)
        tree.manage_context.connect(self._on_tree_manage_context)
        tree.attach_file.connect(self._on_tree_attach_file)
        # 多选批量操作
        tree.batch_operation.connect(self._on_batch_operation)
        tree.batch_move_nodes.connect(self._on_batch_move_nodes)

        # ── ContextPanel 信号 ────────────────────
        ctx_panel = self._window.context_panel
        ctx_panel.remove_block.connect(self._on_ctx_remove_block)
        ctx_panel.toggle_block.connect(self._on_ctx_toggle_block)
        ctx_panel.add_text_block.connect(self._on_ctx_add_text_block)
        ctx_panel.save_as_template.connect(self._on_ctx_save_template)
        ctx_panel.apply_template.connect(self._on_ctx_apply_template)
        ctx_panel.delete_template.connect(self._on_ctx_delete_template)
        ctx_panel.close_requested.connect(self._window.hide_right_panel)

        # ── KGPanel 信号 ─────────────────────────
        kg_panel = self._window.kg_panel
        kg_panel.delete_entity.connect(self._on_kg_delete_entity)
        kg_panel.delete_relation.connect(self._on_kg_delete_relation)
        kg_panel.close_requested.connect(self._window.hide_right_panel)

    # ── 窗口显示 ──────────────────────────────

    def show(self) -> None:
        """
        显示主窗口，加载树数据，并加载所有已启用消息。

        多对话模式：启动时自动聚合所有已启用对话的消息，
        按时间排序显示，并滚动到列表底部（最新消息）。

        关键顺序：先 show() 窗口（建立有效布局/几何），
        再加载消息和滚动。否则所有 widget 几何无效，
        scrollbar.maximum() 返回 0，scroll_to_bottom 无效。
        """
        # 启动时先同步清理上次运行遗留的未完成消息节点（软删除至回收站）。
        # 必须在 _load_tree / _load_all_messages 之前执行——否则渲染先于清理，
        # 前端会短暂显示即将被删除的 incomplete 节点。
        self._ctrl.cleanup_incomplete_nodes()
        self._load_tree()
        self._window.show()
        # 窗口显示后加载消息 — 此时布局有效，scrollbar 能返回正确的 maximum
        QTimer.singleShot(0, lambda: self._load_all_messages(scroll_to_bottom=True))
        # 应用关闭时持久化滚动位置
        app = QApplication.instance()
        if app:
            app.aboutToQuit.connect(self._on_app_quit)

    def _on_app_quit(self) -> None:
        """应用退出前保存当前滚动位置。"""
        self._scroll_states["_global"] = (
            self._window.message_list.save_scroll_state()
        )
        self._save_scroll_positions()

    # ── 树数据加载 ──────────────────────────

    def _load_tree(self) -> None:
        """
        从 Controller 加载完整树数据，刷新侧边栏树面板。
        在启动、流完成和任何树结构变更后调用。
        """
        try:
            nodes = self._ctrl.get_tree()
            self._tree_nodes = nodes
            self._window.sidebar.load_tree(nodes)
        except Exception as exc:
            print(f"[UI] _load_tree error: {exc}")
            import traceback
            traceback.print_exc()

    # ── 公开属性 ──────────────────────────────

    @property
    def window(self) -> MainWindow:
        """返回主窗口实例。"""
        return self._window

    @property
    def current_session_id(self) -> str:
        """当前活跃对话 ID。"""
        return self._current_session_id

    # ── 对话生命周期 ──────────────────────────

    def _load_all_messages(self, scroll_to_bottom: bool = False) -> None:
        """
        加载所有已启用对话的消息（聚合视图），重建消息列表。

        多对话模式核心方法 — 替代旧的单对话 _handle_switch_conversation。

        Args:
            scroll_to_bottom:
                True → 重建后滚动到底部（用于启动、流完成）
                False → 保持阅读位置（用于启用/禁用切换等运行时刷新）

        scroll_to_bottom=True 使用 scrollbar.rangeChanged 信号驱动。
        scroll_to_bottom=False 使用滚动锚点机制：
            刷新前记录视口中点最近的消息 ID 和偏移；
            刷新后定位到该消息，若已消失则向上查找最近幸存消息。

        流式期间不重建：重建会销毁流式控件（_streaming_message / thinking block），
        后续 chunk 会追加到脱离布局的控件上，导致输出效果与最终回复丢失。
        此时将请求延后到流结束/中止后统一执行。
        """
        if self._streaming_message is not None:
            self._pending_rebuild_after_stream = True
            print("[UI] 流式期间跳过消息列表重建，延后到流结束后执行")
            return

        msg_list = self._window.message_list

        if not scroll_to_bottom:
            if self._skip_next_anchor_restore:
                self._skip_next_anchor_restore = False
            else:
                old_ids = self._get_current_message_ids()
                msg_list.save_scroll_anchor(old_ids)

        messages = self._ctrl.on_get_multi_conversation_messages()
        self._rebuild_message_list(messages)

        if scroll_to_bottom:
            scrollbar = msg_list._scroll_area.verticalScrollBar()

            def _scroll_on_range(min_val: int, max_val: int) -> None:
                if max_val > 0:
                    scrollbar.setValue(max_val)
                    try:
                        scrollbar.rangeChanged.disconnect(_scroll_on_range)
                    except (TypeError, RuntimeError):
                        pass

            scrollbar.rangeChanged.connect(_scroll_on_range)
            if scrollbar.maximum() > 0:
                scrollbar.setValue(scrollbar.maximum())
                try:
                    scrollbar.rangeChanged.disconnect(_scroll_on_range)
                except (TypeError, RuntimeError):
                    pass
        elif self._skip_next_anchor_restore:
            self._skip_next_anchor_restore = False
        else:
            new_ids = [vm.id for vm in messages if vm.id]
            QTimer.singleShot(0, lambda: QTimer.singleShot(
                0, lambda: msg_list.restore_scroll_anchor(new_ids)
            ))

    def _get_current_message_ids(self) -> list[str]:
        """收集当前消息列表中所有 ChatMessage 的 ID（按布局顺序）。"""
        ids: list[str] = []
        layout = self._window.message_list.message_layout()
        for i in range(layout.count()):
            w = layout.itemAt(i).widget()
            if w is not None and hasattr(w, "message_id") and w.message_id:
                ids.append(w.message_id)
        return ids

    def _post_stream_refresh(self, aborted: bool = False) -> None:
        """
        流结束/中止后的消息列表刷新。

        - 正常结束（aborted=False）：刷新侧边栏 + 轻量同步消息 ID
          （流式结果已显示，避免整页重建闪烁）。
        - 流式期间若有延后的重建请求（删除/启用切换等），统一执行整页重建：
          结束 → 滚动到底部；中止 → 保持当前位置（未完成轮次由
          _restore_incomplete_after_rebuild 恢复）。
        """
        QTimer.singleShot(0, self._load_tree)
        if self._pending_rebuild_after_stream:
            self._pending_rebuild_after_stream = False
            QTimer.singleShot(
                0,
                lambda: self._load_all_messages(
                    scroll_to_bottom=not aborted
                ),
            )
        elif not aborted:
            QTimer.singleShot(0, self._sync_message_widget_ids)

    def _handle_new_conversation(self) -> None:
        """
        新建对话（多对话模式）。

        创建新对话作为当前发送目标，刷新树，但不影响消息列表显示。
        消息列表始终显示所有已启用消息。
        """
        session_id = self._ctrl.on_new_conversation()
        self._current_session_id = session_id
        self._window.sidebar.set_active(session_id)
        QTimer.singleShot(0, self._load_tree)
        # 新对话无消息，不刷新消息列表

    def _do_scroll_to_target(self, target: str) -> None:
        """在消息列表中滚动到指定消息并高亮。"""
        self._window.message_list.scroll_to_message(target)
        self._window.message_list.highlight_message(target)

    # ── 消息列表操作 ──────────────────────────

    @staticmethod
    def _find_stretch_index(layout) -> int:
        """返回布局中最后一个 stretch（弹簧）项的索引；无 stretch 则返回 count。"""
        for i in range(layout.count() - 1, -1, -1):
            item = layout.itemAt(i)
            if item is not None and item.spacerItem() is not None:
                return i
        return layout.count()

    def _append_message(self, msg: ChatMessage) -> None:
        """追加一条消息到列表底部并隐藏空状态。

        始终在末尾 stretch 之前插入（若无 stretch 则在最末尾追加），
        保证新消息永远位于对话区底部，与 tree.json 顺序一致。
        """
        layout = self._window.message_list.message_layout()
        stretch_idx = self._find_stretch_index(layout)
        layout.insertWidget(stretch_idx, msg)
        self._window.message_list.show_empty_hint(False)
        # Phase 5: 注册消息以便精确滚动定位
        if msg.message_id:
            self._window.message_list.register_message(msg.message_id, msg)

    def _insert_before_last(self, widget) -> None:
        """在最后一条消息之前插入控件（用于 ThinkingBlock）。"""
        layout = self._window.message_list.message_layout()
        stretch_idx = self._find_stretch_index(layout)
        # stretch 前一个位置即最后一条消息；若无 stretch，取末尾
        insert_idx = max(0, stretch_idx - 1)
        layout.insertWidget(insert_idx, widget)
        self._window.message_list.show_empty_hint(False)

    def _rebuild_message_list(self, message_vms: list) -> None:
        """用 ViewModel 列表完全重建消息控件。"""
        # 记录现有 thinking 块的展开状态（按 message_id），重建后恢复；
        # 新增的 thinking 一律默认折叠（折叠状态不持久化）。
        expanded_map = self._capture_thinking_states()
        # 捕获未完成轮次的 partial 内容（内存方案），重建后恢复
        incomplete_state = self._capture_incomplete_state()
        self._window.message_list.clear_messages()

        if not message_vms:
            self._window.message_list.show_empty_hint(True)
            # 即使无消息也要恢复未完成轮次（如删除触发重绘的边界情况）
            if incomplete_state:
                self._restore_incomplete_after_rebuild(incomplete_state)
            return

        layout = self._window.message_list.message_layout()
        for vm in message_vms:
            # thinking 消息统一用可折叠的 ThinkingBlock 展示（与流式一致，重启后也如此）
            if vm.role == "thinking":
                block = ThinkingBlock()
                block.append_text(vm.content or "")
                block.message_id = vm.id
                # 已显示的 thinking 保持原展开状态；新增的默认折叠
                if vm.id in expanded_map:
                    block.set_expanded(expanded_map[vm.id])
                layout.insertWidget(self._find_stretch_index(layout), block)
                if vm.id:
                    self._window.message_list.register_message(vm.id, block)
                continue
            msg = ChatMessage(
                role=vm.role,
                content=vm.content,
                message_id=vm.id,
                is_thinking=getattr(vm, "is_thinking", False),
            )
            self._connect_message_signals(msg)
            # 在 stretch 之前插入（鲁棒：无 stretch 则在末尾）
            layout.insertWidget(self._find_stretch_index(layout), msg)
            # Phase 5: 注册消息以便精确滚动定位
            if vm.id:
                self._window.message_list.register_message(vm.id, msg)

        # 强制布局重新计算，确保 QScrollArea 内容高度正确
        container = self._window.message_list.message_container()
        container.updateGeometry()
        container.adjustSize()

        self._window.message_list.show_empty_hint(False)

        # 重建后恢复未完成轮次的 partial 内容（若有）
        if incomplete_state:
            self._restore_incomplete_after_rebuild(incomplete_state)

    def _capture_incomplete_state(self) -> dict | None:
        """重绘前捕获未完成轮次的内容（内存方案，供重建后恢复）。

        partial thinking/assistant 内容只存在于内存 widget，不持久化；
        重启时 incomplete 会被软删除，无需恢复。
        """
        incomplete = self._incomplete_message
        if incomplete is None:
            return None
        thinking = self._extract_thinking_content(incomplete)
        return {
            "thinking": thinking,
            "assistant_content": incomplete.current_content,
            "assistant_id": incomplete.message_id,
        }

    def _restore_incomplete_after_rebuild(self, state: dict) -> None:
        """重建后恢复未完成轮次的 partial 内容（内存方案）。"""
        layout = self._window.message_list.message_layout()
        thinking = state.get("thinking") or ""
        assistant_content = state.get("assistant_content") or ""

        if thinking:
            block = ThinkingBlock()
            block.append_text(thinking)
            layout.insertWidget(self._find_stretch_index(layout), block)

        if assistant_content.strip():
            # 场景2/3：有 partial 内容 → assistant 显示"重新生成"+"继续"
            assistant = ChatMessage(role="assistant", content=assistant_content)
            assistant.message_id = state.get("assistant_id", "") or ""
            if assistant.message_id:
                self._window.message_list.register_message(
                    assistant.message_id, assistant
                )
            assistant.mark_incomplete()
            self._connect_message_signals(assistant)
            layout.insertWidget(self._find_stretch_index(layout), assistant)
            self._incomplete_message = assistant
        else:
            # 场景1：无内容 → 在最后一条 user 消息上显示"重新发送"
            self._incomplete_message = None
            last_user = None
            for i in range(layout.count()):
                w = layout.itemAt(i).widget()
                if isinstance(w, ChatMessage) and w.role == "user":
                    last_user = w
            if last_user is not None:
                last_user.show_resend_button()

        container = self._window.message_list.message_container()
        container.updateGeometry()
        container.adjustSize()

    def _capture_thinking_states(self) -> dict[str, bool]:
        """收集当前消息列表中 ThinkingBlock 的展开状态（message_id → is_expanded）。"""
        layout = self._window.message_list.message_layout()
        states: dict[str, bool] = {}
        for i in range(layout.count()):
            w = layout.itemAt(i).widget()
            if isinstance(w, ThinkingBlock) and w.message_id:
                states[w.message_id] = w.is_expanded
        return states

    def _sync_message_widget_ids(self) -> None:
        """
        流式结束后轻量同步消息控件 ID（不重建页面，避免整页重绘闪烁）。

        流式期间，用户消息的 message_id 为占位空串（在 _handle_send 中置空，
        注释为"流完成后由 tree refresh 更新"）。本方法从树中取到真实 ID，
        按 role + 内容前缀匹配到对应控件并注册，供"点击树中消息 → 滚动定位"使用。
        流式结果本身已正确显示在页面上，无需重建。
        """
        message_vms = self._ctrl.on_get_multi_conversation_messages()
        msg_list = self._window.message_list
        layout = msg_list.message_layout()

        # 收集现有 ChatMessage 控件中 message_id 为空的（通常是本轮用户消息）
        unused: list = [
            layout.itemAt(i).widget()
            for i in range(layout.count())
            if isinstance(layout.itemAt(i).widget(), ChatMessage)
            and not layout.itemAt(i).widget().message_id
        ]

        for vm in message_vms:
            if not getattr(vm, "id", ""):
                continue
            for w in unused:
                content = getattr(w, "current_content", "") or ""
                if w.role == vm.role and content[:30] == (vm.content or "")[:30]:
                    w.message_id = vm.id
                    msg_list.register_message(vm.id, w)
                    unused.remove(w)
                    break

    def _connect_message_signals(self, msg: ChatMessage) -> None:
        """连接消息气泡的操作按钮信号。"""
        msg.copy_requested.connect(self._on_copy_content)
        msg.regenerate_requested.connect(
            lambda: self._handle_regenerate(self._current_session_id)
        )
        msg.remember_requested.connect(
            lambda: self._handle_remember(msg)
        )
        msg.continue_requested.connect(
            lambda: self._handle_continue(self._current_session_id)
        )
        msg.resend_requested.connect(self._handle_resend)

    def _on_copy_content(self, text: str) -> None:
        """复制文本到系统剪贴板。"""
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(text)

    # ── 发送消息 ──────────────────────────────

    def _handle_send(self, text: str, files: list[str]) -> None:
        """
        发送消息入口。

        1. 中止现有流（如有）
        2. 确定消息插入目标：树中最后一个有效启用消息所在的对话
           （使新内容在聚合时间线末尾继续）；无启用消息且无活跃会话时新建
        3. 追加用户消息气泡
        4. 清空输入区，切换到生成状态
        5. 启动流式响应
        """
        # 中止现有流（会标记旧轮次为未完成）
        if self._stream_worker is not None:
            self._handle_stop()

        # 清理所有标记为未完成的节点（用户已开新轮次，旧未完成是垃圾 → 软删至回收站）
        self._ctrl.cleanup_incomplete_nodes()

        # 记录本次发送文本（供未完成轮次"重新发送"用）
        self._last_sent_text = text

        # ★ 插入目标决策：
        #   - 若当前会话是「新建空对话」且位于所有已启用消息之后（DFS 前序），
        #     则强制在该新对话开始，不重定向。
        #   - 否则重定向到树中最后一个有效启用消息所在的对话（聚合时间线末尾）。
        #   - 若树中无任何启用消息、且当前会话不可用（空/禁用/不存在），
        #     则在根目录自动新建对话，保证这场对话被记录并可见。
        if not self._ctrl.should_continue_in_new_conversation(self._current_session_id):
            target = self._ctrl.find_last_enabled_conversation_id()
            if target:
                self._current_session_id = target
                self._window.sidebar.set_active(target)
            elif not self._ctrl.is_conversation_usable(self._current_session_id):
                self._handle_new_conversation()

        if not text and not files:
            return

        # 移除旧未完成轮次的控件（被标记的 user + 其下所有消息），
        # 保持 UI 与 tree.json 的一致性（tree.json 已在上面 cleanup 时软删）
        self._remove_incomplete_widgets()
        self._incomplete_message = None

        # 追加用户消息
        user_msg = ChatMessage(role="user", content=text)
        user_msg.message_id = ""  # 用户消息在流完成后由 tree refresh 更新
        self._connect_message_signals(user_msg)
        self._append_message(user_msg)

        # 输入区状态
        self._window.input_area.clear()
        self._window.input_area.set_generating(True)

        # 启动流式线程
        self._start_stream(self._current_session_id, text, files)

    def _handle_stop(self) -> None:
        """停止当前生成。"""
        self._ctrl.on_stop_generation()
        self._window.input_area.set_generating(False)
        if self._streaming_message:
            self._streaming_message.finalize_stream()
            self._streaming_message.mark_incomplete()
            # 保留引用供"继续生成"
            self._incomplete_message = self._streaming_message
            self._streaming_message = None
        if self._stream_worker:
            self._stream_worker.request_abort()
        # 标记未完成轮次 + 处理三种停止场景的 UI
        self._mark_current_turn_incomplete()

    def _mark_current_turn_incomplete(self) -> None:
        """停止生成后：标记用户消息节点未完成，并按三种场景调整 UI。

        场景1（首字延迟停止）：无输出内容 → 用户消息显示"重新发送"，隐藏空 assistant。
        场景2/3（thinking / assistant 中断）：有部分内容 → assistant 已由
        mark_incomplete() 显示"重新生成"+"继续"。
        """
        session_id = self._current_session_id
        # 1. 标记用户消息节点为未完成（后续自动软删除）
        user_id = self._ctrl.find_latest_user_message_id(session_id) if session_id else None
        if user_id:
            self._ctrl.mark_incomplete(user_id)
            self._incomplete_user_id = user_id
            # 刷新侧边栏：tree.json 已更新 user 节点，侧边栏需同步显示
            QTimer.singleShot(0, self._load_tree)

        # 2. 场景1：首字延迟停止（无内容）
        incomplete = self._incomplete_message
        if incomplete is None or not incomplete.current_content:
            self._handle_first_byte_stop_scenario(incomplete)

    def _handle_first_byte_stop_scenario(self, incomplete: ChatMessage | None) -> None:
        """首字延迟停止：隐藏空 assistant widget，在用户消息上显示"重新发送"。"""
        if incomplete is not None:
            incomplete.setVisible(False)
        self._show_resend_on_user_message()

    def _show_resend_on_user_message(self) -> None:
        """在当前轮次（未同步 ID）的用户消息控件上显示"重新发送"按钮。"""
        layout = self._window.message_list.message_layout()
        for i in range(layout.count()):
            w = layout.itemAt(i).widget()
            if isinstance(w, ChatMessage) and w.role == "user" and not w.message_id:
                w.show_resend_button()
                break

    def _handle_regenerate(self, session_id: str) -> None:
        """
        重新生成助手回复。

        若当前是未完成轮次：丢弃部分内容，重新回答该用户消息；
        否则走正常 regenerate。
        """
        if not session_id:
            return
        # 未完成轮次：重新回答该用户消息（清除标记 + 移除部分内容 + 重发）
        if self._incomplete_message is not None:
            self._resend_current_turn()
            return

        self._window.input_area.set_generating(True)

        self._start_regenerate_stream(session_id)

    def _handle_continue(self, session_id: str) -> None:
        """继续生成未完成的助手消息（DeepSeek Beta 前缀续写）。"""
        if not session_id:
            return
        incomplete = self._incomplete_message
        if incomplete is None:
            return
        partial_content = incomplete.current_content
        partial_thinking = self._extract_thinking_content(incomplete)
        self._incomplete_message = None
        self._incomplete_user_id = None
        # 被"继续"抢救：清除用户消息的未完成标记（防止被自动清理）
        user_id = self._ctrl.find_latest_user_message_id(session_id)
        if user_id:
            self._ctrl.clear_incomplete_mark(user_id)
        self._window.input_area.set_generating(True)
        self._start_continue_stream(
            session_id, partial_content, partial_thinking, incomplete
        )

    def _handle_resend(self, text: str) -> None:
        """重新发送（首字延迟停止后）：丢弃未完成轮次，重新发送该用户消息。"""
        if text:
            self._last_sent_text = text
        self._resend_current_turn()

    def _resend_current_turn(self) -> None:
        """丢弃未完成轮次（软删 user 节点 + 移除 widget），重新发送该用户消息。"""
        session_id = self._current_session_id
        user_text = self._last_sent_text or ""
        # 软删旧 user 节点（未完成标记的）→ 进回收站
        user_id = self._ctrl.find_latest_user_message_id(session_id) if session_id else None
        if user_id:
            self._ctrl.on_soft_delete_node(user_id)
        # 移除未完成轮次的 widget（user + thinking + 部分 assistant）
        self._remove_incomplete_widgets()
        self._incomplete_message = None
        if user_text:
            self._handle_send(user_text, [])

    def _remove_incomplete_widgets(self) -> None:
        """移除当前未完成轮次的控件（user + thinking + 部分 assistant），保持 UI 与 tree.json 一致。

        删除区间：从"未同步 ID 的 user 消息"到"_incomplete_message"（闭区间）。
        只影响当前未完成轮次，不动之前轮的控件。
        """
        layout = self._window.message_list.message_layout()
        incomplete = self._incomplete_message

        assistant_idx = -1
        for i in range(layout.count()):
            if layout.itemAt(i).widget() is incomplete:
                assistant_idx = i
                break
        user_idx = -1
        for i in range(layout.count()):
            w = layout.itemAt(i).widget()
            if isinstance(w, ChatMessage) and w.role == "user":
                # 匹配：未同步 ID（首次停止）或重绘后带真实 ID（_incomplete_user_id）
                if not w.message_id or (
                    self._incomplete_user_id
                    and w.message_id == self._incomplete_user_id
                ):
                    user_idx = i

        # 都找不到 → 无未完成轮次可移除
        if assistant_idx < 0 and user_idx < 0:
            return
        if user_idx < 0:
            user_idx = assistant_idx
        if assistant_idx < 0:
            assistant_idx = user_idx

        start = min(user_idx, assistant_idx)
        end = max(user_idx, assistant_idx)
        to_remove: list = []
        # 从后往前 takeAt 移出布局（避免索引漂移），再隐藏+删除
        for i in range(end, start - 1, -1):
            item = layout.takeAt(i)
            if item is not None and item.widget() is not None:
                to_remove.append(item.widget())
        for w in to_remove:
            w.setVisible(False)
            w.deleteLater()
        self._incomplete_user_id = None
        self._window.message_list.message_container().updateGeometry()

    def _extract_thinking_content(self, target_msg: ChatMessage) -> str:
        """从消息列表中提取紧邻 target_msg 之前最近一个 ThinkingBlock 的内容。"""
        layout = self._window.message_list.message_layout()
        target_index = -1
        for i in range(layout.count()):
            if layout.itemAt(i).widget() is target_msg:
                target_index = i
                break
        if target_index < 0:
            return ""
        for i in range(target_index - 1, -1, -1):
            w = layout.itemAt(i).widget()
            if isinstance(w, ThinkingBlock):
                return w.current_content
        return ""

    # ── 流式引擎 ──────────────────────────────

    def _start_stream(self, session_id: str, text: str, files: list[str]) -> None:
        """
        启动异步流式响应。

        创建 AsyncStreamWorker 在后台线程中迭代
        controller.on_send_message() 的 async generator，
        通过 Qt 信号将每个 chunk 安全传递到主线程。
        """
        # 创建 assistant 消息气泡
        # ★ 本轮对话首次输出时强制滚动到底部一次（标志位，见 on_chunk）
        self._first_chunk_scrolled = False
        # 重置流式期间的延迟重建标志（防上一轮残留导致重复重建）
        self._pending_rebuild_after_stream = False
        assistant_msg = ChatMessage(role="assistant", content="")
        assistant_msg.start_stream()
        self._streaming_message = assistant_msg
        self._connect_message_signals(assistant_msg)
        self._append_message(assistant_msg)

        # thinking block 引用 — 用可变容器在闭包中共享
        thinking_ref: list = [None]

        # 创建 worker + thread — 用局部变量捕获，防止 lambda 闭包过时
        stream_thread = QThread()
        stream_worker = AsyncStreamWorker()
        stream_worker.moveToThread(stream_thread)
        self._stream_thread = stream_thread
        self._stream_worker = stream_worker

        def on_chunk(delta: str, is_done: bool, chunk_type: str, _msg_id: str):
            """主线程：处理每个流式块。"""
            # ★ 本轮对话首次输出：强制滚动到底部一次（无论是否开思考模式）。
            #   thinking + assistant 两条消息也只触发这一次。
            if not self._first_chunk_scrolled:
                self._first_chunk_scrolled = True
                self._window.message_list.force_scroll_to_bottom()

            if chunk_type == "thinking":
                if thinking_ref[0] is None:
                    thinking_ref[0] = ThinkingBlock()
                    self._insert_before_last(thinking_ref[0])
                thinking_ref[0].append_text(delta)
                thinking_ref[0].repaint()  # 强制立即重绘，实现流式效果
            else:
                if self._streaming_message:
                    self._streaming_message.append_stream(delta)
                    if _msg_id and not self._streaming_message.message_id:
                        self._streaming_message.message_id = _msg_id
                        self._window.message_list.register_message(
                            _msg_id, self._streaming_message
                        )
                    self._streaming_message.repaint()  # 强制立即重绘

            # 多对话模式：仅当用户在底部附近时才自动跟随
            self._window.message_list.scroll_to_bottom()

        def on_finished():
            """流结束。"""
            # 防止重入：_cleanup_stream_thread → thread.quit() → thread.finished
            # → relay._on_finished → 再次触发本回调。此时 worker 已置 None。
            if self._stream_worker is None:
                return
            if self._streaming_message:
                self._streaming_message.finalize_stream()
                self._streaming_message = None
            self._window.input_area.set_generating(False)
            self._cleanup_stream_thread()
            self._post_stream_refresh(aborted=False)

        def on_error(error_msg: str):
            """流出错。"""
            print(f"[UI] Stream error: {error_msg}")
            on_finished()

        def on_aborted():
            """流被用户中止。"""
            if self._streaming_message:
                self._streaming_message.finalize_stream()
                self._streaming_message.mark_incomplete()
                # 保留引用供"继续生成"
                self._incomplete_message = self._streaming_message
                self._streaming_message = None
            self._window.input_area.set_generating(False)
            self._cleanup_stream_thread()
            self._post_stream_refresh(aborted=True)

        # ── 创建 StreamRelay 桥接器（主线程 QObject）────────────
        # Worker 信号 → Relay Slot（跨线程，有 QObject receiver →
        # AutoConnection 正确解析为 QueuedConnection）
        # Relay Signal → UI 回调（同线程，DirectConnection）
        relay = StreamRelay()
        self._stream_relay = relay  # 保持引用，防止 GC 回收 relay

        # Worker → Relay: 显式 QueuedConnection（有 QObject receiver，安全）
        stream_worker.chunk_ready.connect(
            relay._on_chunk, Qt.ConnectionType.QueuedConnection
        )
        stream_worker.stream_finished.connect(
            relay._on_finished, Qt.ConnectionType.QueuedConnection
        )
        stream_worker.stream_error.connect(
            relay._on_error, Qt.ConnectionType.QueuedConnection
        )
        stream_worker.stream_aborted.connect(
            relay._on_aborted, Qt.ConnectionType.QueuedConnection
        )

        # Relay → 回调: 同线程，DirectConnection 即可
        relay.chunk_ready.connect(on_chunk)
        relay.stream_finished.connect(on_finished)
        relay.stream_error.connect(on_error)
        relay.stream_aborted.connect(on_aborted)

        # stream_thread.finished 也经 relay 桥接
        stream_thread.finished.connect(
            relay._on_finished, Qt.ConnectionType.QueuedConnection
        )

        # 在线程启动后运行流 — 用局部变量捕获，防止竞态
        _ctrl = self._ctrl
        # ★ 必须显式 DirectConnection：started 由 worker 线程发出，而 lambda
        #   没有 QObject receiver 时，AutoConnection 会按"连接时所在线程"
        #   （主线程）路由，导致 run_stream 阻塞主线程 → 无法流式显示。
        #   显式 DirectConnection 强制在发出信号的 worker 线程执行。
        stream_thread.started.connect(
            lambda: stream_worker.run_stream(
                _ctrl.on_send_message, session_id, text, files
            ),
            Qt.ConnectionType.DirectConnection,
        )

        stream_thread.start()

    def _start_regenerate_stream(self, session_id: str) -> None:
        """
        启动重新生成流。
        与 _start_stream 类似，但调用 controller.on_regenerate_message。
        """
        # ★ 本轮对话首次输出时强制滚动到底部一次（标志位，见 on_chunk）
        self._first_chunk_scrolled = False
        # 重置流式期间的延迟重建标志（防上一轮残留导致重复重建）
        self._pending_rebuild_after_stream = False
        assistant_msg = ChatMessage(role="assistant", content="")
        assistant_msg.start_stream()
        self._streaming_message = assistant_msg
        self._connect_message_signals(assistant_msg)
        self._append_message(assistant_msg)

        thinking_ref: list = [None]

        stream_thread = QThread()
        stream_worker = AsyncStreamWorker()
        stream_worker.moveToThread(stream_thread)
        self._stream_thread = stream_thread
        self._stream_worker = stream_worker

        def on_chunk(delta: str, is_done: bool, chunk_type: str, _msg_id: str):
            # ★ 本轮对话首次输出：强制滚动到底部一次（无论是否开思考模式）。
            #   thinking + assistant 两条消息也只触发这一次。
            if not self._first_chunk_scrolled:
                self._first_chunk_scrolled = True
                self._window.message_list.force_scroll_to_bottom()

            if chunk_type == "thinking":
                if thinking_ref[0] is None:
                    thinking_ref[0] = ThinkingBlock()
                    self._insert_before_last(thinking_ref[0])
                thinking_ref[0].append_text(delta)
                thinking_ref[0].repaint()
            else:
                if self._streaming_message:
                    self._streaming_message.append_stream(delta)
                    if _msg_id and not self._streaming_message.message_id:
                        self._streaming_message.message_id = _msg_id
                        self._window.message_list.register_message(
                            _msg_id, self._streaming_message
                        )
                    self._streaming_message.repaint()
            # 多对话模式：仅当用户在底部附近时才自动跟随
            self._window.message_list.scroll_to_bottom()

        def on_finished():
            # 防止重入（同 _start_stream 中的 on_finished）
            if self._stream_worker is None:
                return
            if self._streaming_message:
                self._streaming_message.finalize_stream()
                self._streaming_message = None
            self._window.input_area.set_generating(False)
            self._cleanup_stream_thread()
            self._post_stream_refresh(aborted=False)

        def on_error(error_msg: str):
            print(f"[UI] Regenerate error: {error_msg}")
            on_finished()

        def on_aborted():
            if self._streaming_message:
                self._streaming_message.finalize_stream()
                self._streaming_message.mark_incomplete()
                # 保留引用供"继续生成"
                self._incomplete_message = self._streaming_message
                self._streaming_message = None
            self._window.input_area.set_generating(False)
            self._cleanup_stream_thread()
            self._post_stream_refresh(aborted=True)

        # ── StreamRelay 桥接（与 _start_stream 相同模式）──
        relay = StreamRelay()
        self._stream_relay = relay  # 保持引用，防止 GC 回收 relay

        # Worker → Relay: 显式 QueuedConnection
        stream_worker.chunk_ready.connect(
            relay._on_chunk, Qt.ConnectionType.QueuedConnection
        )
        stream_worker.stream_finished.connect(
            relay._on_finished, Qt.ConnectionType.QueuedConnection
        )
        stream_worker.stream_error.connect(
            relay._on_error, Qt.ConnectionType.QueuedConnection
        )
        stream_worker.stream_aborted.connect(
            relay._on_aborted, Qt.ConnectionType.QueuedConnection
        )

        # Relay → 回调: 同线程 DirectConnection
        relay.chunk_ready.connect(on_chunk)
        relay.stream_finished.connect(on_finished)
        relay.stream_error.connect(on_error)
        relay.stream_aborted.connect(on_aborted)

        stream_thread.finished.connect(
            relay._on_finished, Qt.ConnectionType.QueuedConnection
        )

        _ctrl = self._ctrl
        # ★ 同 _start_stream：显式 DirectConnection 确保 run_stream 在 worker 线程执行
        stream_thread.started.connect(
            lambda: stream_worker.run_stream(
                _ctrl.on_regenerate_message, session_id, ""
            ),
            Qt.ConnectionType.DirectConnection,
        )

        stream_thread.start()

    def _start_continue_stream(
        self,
        session_id: str,
        partial_content: str,
        partial_thinking: str,
        target_msg: ChatMessage,
    ) -> None:
        """
        启动继续生成流（追加到已有消息控件，不新建气泡）。

        复用 AsyncStreamWorker 在后台线程迭代
        controller.on_continue_message() 的 async generator。
        """
        target_msg.restart_stream_from(partial_content)
        self._streaming_message = target_msg
        # 本轮对话首次输出时强制滚动到底部一次（标志位）
        self._first_chunk_scrolled = False
        # 重置流式期间的延迟重建标志（防上一轮残留导致重复重建）
        self._pending_rebuild_after_stream = False

        # thinking block 引用（续写通常不再出 thinking，但兼容）
        thinking_ref: list = [None]

        stream_thread = QThread()
        stream_worker = AsyncStreamWorker()
        stream_worker.moveToThread(stream_thread)
        self._stream_thread = stream_thread
        self._stream_worker = stream_worker

        def on_chunk(delta: str, is_done: bool, chunk_type: str, _msg_id: str):
            if not self._first_chunk_scrolled:
                self._first_chunk_scrolled = True
                self._window.message_list.force_scroll_to_bottom()

            if chunk_type == "thinking":
                if thinking_ref[0] is None:
                    thinking_ref[0] = ThinkingBlock()
                    self._insert_before_last(thinking_ref[0])
                thinking_ref[0].append_text(delta)
                thinking_ref[0].repaint()
            else:
                if self._streaming_message:
                    self._streaming_message.append_stream(delta)
                    if _msg_id:
                        self._streaming_message.message_id = _msg_id
                        self._window.message_list.register_message(
                            _msg_id, self._streaming_message
                        )
                    self._streaming_message.repaint()
            self._window.message_list.scroll_to_bottom()

        def on_finished():
            if self._stream_worker is None:
                return
            if self._streaming_message:
                self._streaming_message.finalize_stream()
                self._streaming_message = None
            self._window.input_area.set_generating(False)
            self._cleanup_stream_thread()
            self._post_stream_refresh(aborted=False)

        def on_error(error_msg: str):
            print(f"[UI] Continue error: {error_msg}")
            on_finished()

        def on_aborted():
            if self._streaming_message:
                self._streaming_message.finalize_stream()
                self._streaming_message.mark_incomplete()
                self._incomplete_message = self._streaming_message
                self._streaming_message = None
            self._window.input_area.set_generating(False)
            self._cleanup_stream_thread()
            self._post_stream_refresh(aborted=True)

        relay = StreamRelay()
        self._stream_relay = relay
        stream_worker.chunk_ready.connect(
            relay._on_chunk, Qt.ConnectionType.QueuedConnection
        )
        stream_worker.stream_finished.connect(
            relay._on_finished, Qt.ConnectionType.QueuedConnection
        )
        stream_worker.stream_error.connect(
            relay._on_error, Qt.ConnectionType.QueuedConnection
        )
        stream_worker.stream_aborted.connect(
            relay._on_aborted, Qt.ConnectionType.QueuedConnection
        )
        relay.chunk_ready.connect(on_chunk)
        relay.stream_finished.connect(on_finished)
        relay.stream_error.connect(on_error)
        relay.stream_aborted.connect(on_aborted)
        stream_thread.finished.connect(
            relay._on_finished, Qt.ConnectionType.QueuedConnection
        )

        _ctrl = self._ctrl
        # ★ 必须 DirectConnection：确保 run_stream 在 worker 线程执行
        stream_thread.started.connect(
            lambda: stream_worker.run_stream(
                _ctrl.on_continue_message,
                session_id,
                partial_content,
                partial_thinking,
            ),
            Qt.ConnectionType.DirectConnection,
        )

        stream_thread.start()


    def _load_scroll_positions(self) -> None:
        """Phase 5: 从磁盘加载滚动位置记录。"""
        import json
        import os

        path = os.path.join("data", "scroll_positions.json")
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    self._scroll_states = json.load(f)
        except Exception:
            self._scroll_states = {}

    def _save_scroll_positions(self) -> None:
        """Phase 5: 将滚动位置持久化到磁盘。"""
        import json
        import os

        os.makedirs("data", exist_ok=True)
        path = os.path.join("data", "scroll_positions.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._scroll_states, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _cleanup_stream_thread(self) -> None:
        """清理流式工作线程资源（幂等）。"""
        worker = self._stream_worker
        thread = self._stream_thread
        relay = self._stream_relay

        # 置空引用，防止重入。
        # ⚠️ 注意：self._stream_thread 暂不置空 —— 必须保留引用直到线程 finished，
        #    否则 Python GC 可能提前销毁仍在运行的 QThread / worker / relay，
        #    触发 "QThread: Destroyed while thread is still running" 闪退。
        self._stream_worker = None
        self._stream_relay = None

        if relay is not None:
            try:
                relay.chunk_ready.disconnect()
                relay.stream_finished.disconnect()
                relay.stream_error.disconnect()
                relay.stream_aborted.disconnect()
            except (TypeError, RuntimeError):
                pass
            # 延迟到线程结束后统一 deleteLater（见下方 _finalize）

        if worker is not None:
            try:
                worker.chunk_ready.disconnect()
                worker.stream_finished.disconnect()
                worker.stream_error.disconnect()
                worker.stream_aborted.disconnect()
            except (TypeError, RuntimeError):
                pass
            # 延迟到线程结束后统一 deleteLater

        if thread is not None:
            # 不阻塞等待（wait() 在 Windows 上可能与 COM 消息泵冲突导致
            # 0x8001010d 崩溃）。
            # ★ 关键修复：finished 信号在 worker 线程 run() 真正退出后才发出。
            #   用闭包同时持有 thread + worker + relay 的 Python 引用，直到
            #   finished 再统一 deleteLater，杜绝"仍在运行却被提前销毁"。
            def _finalize(t=thread, w=worker, r=relay):
                if self._stream_thread is t:
                    self._stream_thread = None
                t.deleteLater()
                if w is not None:
                    w.deleteLater()
                if r is not None:
                    r.deleteLater()

            thread.finished.connect(_finalize)
            thread.quit()

    # ── 树形结构操作 ──────────────────────────

    def _on_tree_switch(self, node_id: str) -> None:
        """
        树节点点击 → 仅设置活跃对话 + 滚动/高亮，不重新加载消息列表。

        多对话模式：消息列表始终显示所有已启用消息的聚合视图。
        点击树节点仅用于：
        1. 设置 _current_session_id（后续发送消息的目标对话）
        2. 如果是消息节点 → 在消息列表中滚动到该消息并高亮
        3. 如果是对话/文件夹节点 → 在侧边栏高亮
        """
        print(f"[UI] Tree switch to node: {node_id}")

        # 查找节点信息，确定 _current_session_id 和滚动目标
        target_message_id: str | None = None
        for n in self._tree_nodes:
            if n.id == node_id:
                if n.node_type == "message":
                    target_message_id = node_id
                    # 消息节点：发送目标为其所属对话
                    self._current_session_id = n.parent_id or ""
                elif n.node_type == "conversation":
                    self._current_session_id = node_id
                # folder 节点不改变 _current_session_id
                break

        # 侧边栏高亮
        self._window.sidebar.set_active(
            self._current_session_id or node_id
        )

        # 如果是消息节点，滚动到该消息并高亮
        if target_message_id:
            self._pending_scroll_target = target_message_id
            QTimer.singleShot(0, lambda: self._do_scroll_to_target(target_message_id))

    def _on_tree_new_conversation(self, parent_id: str) -> None:
        """
        右键菜单 → 在指定目录下新建对话。
        创建后设为当前发送目标，刷新树。不改变消息列表。
        """
        session_id = self._ctrl.on_new_conversation(parent_id=parent_id)
        self._current_session_id = session_id
        self._window.sidebar.set_active(session_id)
        QTimer.singleShot(0, self._load_tree)

    def _on_tree_new_folder(self, parent_id: str) -> None:
        """
        右键菜单 → 新建文件夹。
        弹出 QInputDialog 输入目录名称，调用 controller 创建。
        """
        name, ok = QInputDialog.getText(
            self._window,
            "新建目录",
            "目录名称:",
            text="新文件夹",
        )
        if ok and name.strip():
            self._ctrl.on_create_folder(parent_id, name.strip())
            QTimer.singleShot(0, self._load_tree)

    def _on_tree_rename(self, node_id: str) -> None:
        """
        右键菜单 → 重命名节点。
        弹出 QInputDialog 输入新名称，调用 controller 重命名。
        """
        # 从当前树数据中获取当前标题作为预填值
        current_title = ""
        for n in self._tree_nodes:
            if n.id == node_id:
                current_title = n.title
                break

        name, ok = QInputDialog.getText(
            self._window,
            "重命名",
            "新名称:",
            text=current_title,
        )
        if ok and name.strip():
            self._ctrl.on_rename_node(node_id, name.strip())
            QTimer.singleShot(0, self._load_tree)

    def _on_tree_delete(self, node_id: str) -> None:
        """
        右键菜单 → 删除节点（软删除到回收站）。
        弹出 QMessageBox 确认对话框。
        """
        # 从树数据中获取节点信息用于确认消息
        node_title = node_id
        is_folder = False
        for n in self._tree_nodes:
            if n.id == node_id:
                node_title = n.title or node_id
                is_folder = (n.node_type == "folder")
                break

        if is_folder:
            msg = f"确定要删除目录「{node_title}」及其所有内容吗？\n\n删除后将移入回收站，可以恢复。"
        else:
            msg = f"确定要删除「{node_title}」吗？\n\n删除后将移入回收站，可以恢复。"

        reply = QMessageBox.question(
            self._window,
            "确认删除",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self._ctrl.on_soft_delete_node(node_id, mode="recursive")
            QTimer.singleShot(0, self._load_tree)
            # 如果删除的是当前发送目标对话，清空 _current_session_id
            if node_id == self._current_session_id:
                self._current_session_id = ""
            # 被删除节点的消息从列表中消失，静默刷新
            QTimer.singleShot(0, lambda: self._load_all_messages(scroll_to_bottom=False))

    def _on_tree_toggle_enabled(self, node_id: str) -> None:
        """
        复选框点击 / 右键菜单 → 切换节点启用/禁用状态。

        1. 持久化级联状态变更
        2. 增量刷新树节点的 checkState（避免 _rebuild 竞态）
        3. 静默刷新消息列表（保持当前滚动位置不变）
        """
        self._ctrl.on_toggle_enabled(node_id)
        tree_nodes = self._ctrl.get_tree()
        self._tree_nodes = tree_nodes
        self._window.sidebar.tree_panel.refresh_enabled_states(tree_nodes)

        # 静默刷新消息列表 — 保持滚动位置
        QTimer.singleShot(0, lambda: self._load_all_messages(scroll_to_bottom=False))

    def _refresh_multi_conv_messages(self) -> None:
        """刷新多对话模式消息列表，保持滚动位置不变。"""
        self._load_all_messages(scroll_to_bottom=False)

    def _on_tree_move(self, node_id: str, target_parent_id: str, position: object) -> None:
        """
        拖拽放置 → 移动节点到目标位置。
        target_parent_id 为空时移到根级，position 为 None 时追加到末尾。
        """
        parent = target_parent_id if target_parent_id else None
        print(f"[UI] Tree move: {node_id} -> parent={parent}, pos={position}")
        self._ctrl.on_move_node(node_id, parent, position)
        QTimer.singleShot(0, self._load_tree)
        # 移动后静默刷新消息列表（父级变化可能影响可见性）
        QTimer.singleShot(50, lambda: self._load_all_messages(scroll_to_bottom=False))

    # ── 多选模式操作 ──────────────────────────

    def _on_multi_select_toggled(self, enabled: bool) -> None:
        """侧边栏多选按钮切换。"""
        self._window.sidebar.tree_panel.set_multi_select_mode(enabled)

    def _on_batch_operation(self, operation: str, node_ids: list[str]) -> None:
        """
        处理多选批量操作。
        对每个选中节点依次调用对应的单节点操作。
        """
        if not node_ids:
            return

        if operation == "toggle_enabled":
            for nid in node_ids:
                try:
                    self._ctrl.on_toggle_enabled(nid)
                except Exception as exc:
                    print(f"[UI] Batch toggle error for {nid}: {exc}")
            # 增量刷新树状态 + 消息列表
            tree_nodes = self._ctrl.get_tree()
            self._tree_nodes = tree_nodes
            self._window.sidebar.tree_panel.refresh_enabled_states(tree_nodes)
            QTimer.singleShot(0, lambda: self._load_all_messages(scroll_to_bottom=False))

        elif operation == "delete":
            # 构建确认消息
            count = len(node_ids)
            titles = []
            for nid in node_ids:
                for n in self._tree_nodes:
                    if n.id == nid:
                        titles.append(n.title or nid)
                        break
            title_preview = "、".join(titles[:3])
            if count > 3:
                title_preview += f" 等{count}个节点"
            msg = f"确定要删除 {count} 个选中节点吗？\n\n{title_preview}\n\n删除后将移入回收站，可以恢复。"

            reply = QMessageBox.question(
                self._window,
                "确认批量删除",
                msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                for nid in node_ids:
                    try:
                        self._ctrl.on_soft_delete_node(nid, mode="recursive")
                    except Exception as exc:
                        print(f"[UI] Batch delete error for {nid}: {exc}")
                # 清理被删除的当前对话
                if self._current_session_id in node_ids:
                    self._current_session_id = ""
                QTimer.singleShot(0, self._load_tree)
                QTimer.singleShot(50, lambda: self._load_all_messages(scroll_to_bottom=False))

        elif operation == "rename":
            # 批量重命名：用一个新名称
            current_title = ""
            for n in self._tree_nodes:
                if n.id == node_ids[0]:
                    current_title = n.title
                    break
            name, ok = QInputDialog.getText(
                self._window,
                f"批量重命名 ({len(node_ids)} 个节点)",
                "新名称:",
                text=current_title,
            )
            if ok and name.strip():
                for nid in node_ids:
                    try:
                        self._ctrl.on_rename_node(nid, name.strip())
                    except Exception as exc:
                        print(f"[UI] Batch rename error for {nid}: {exc}")
                QTimer.singleShot(0, self._load_tree)

        elif operation == "manage_context":
            # 批量管理上下文块：取第一个节点的当前设置
            first_id = node_ids[0]
            folder_info = self._ctrl.on_get_folder_context(first_id)
            folder_title = folder_info.get("title", "节点")
            current_ids = folder_info.get("context_block_ids", [])
            all_blocks = self._ctrl.on_get_context_blocks()

            from app.ui.widgets.dialogs import show_context_block_manager_dialog
            show_context_block_manager_dialog(
                self._window,
                f"{folder_title} 等{len(node_ids)}个节点",
                all_blocks,
                current_ids,
                on_save=lambda selected_ids: self._on_batch_ctx_save(node_ids, selected_ids),
            )

        elif operation == "attach_file":
            # 批量添加附件
            paths, _ = QFileDialog.getOpenFileNames(
                self._window,
                f"选择附件（将添加到 {len(node_ids)} 个节点）",
                "",
                "支持的文件 (*.txt *.md *.json *.docx *.pdf);;所有文件 (*.*)",
            )
            if paths:
                for nid in node_ids:
                    for p in paths:
                        try:
                            self._ctrl.on_attach_file(nid, p)
                        except Exception as exc:
                            print(f"[UI] Batch attach error for {nid}: {exc}")
                QTimer.singleShot(0, self._load_tree)

    def _on_batch_ctx_save(self, node_ids: list[str], selected_ids: list[str]) -> None:
        """批量保存上下文块关联。"""
        for nid in node_ids:
            try:
                self._ctrl.on_update_context_blocks(nid, selected_ids)
            except Exception as exc:
                print(f"[UI] Batch context save error for {nid}: {exc}")
        QTimer.singleShot(0, self._load_tree)

    def _on_batch_move_nodes(self, dragged_ids_str: str, target_parent_id: str, position: object) -> None:
        """
        多选批量拖拽放置。
        按 DFS 反转顺序依次移动节点，保持相对顺序。
        反转移动确保所有节点插入到同一位置时顺次排列。
        """
        dragged_ids = [i for i in dragged_ids_str.split(",") if i]
        parent = target_parent_id if target_parent_id else None

        print(f"[UI] Batch tree move: {len(dragged_ids)} nodes -> parent={parent}, pos={position}")

        # 反转顺序移动：最后一个先移，确保所有节点插入到同一 position 时相对顺序不变
        for nid in reversed(dragged_ids):
            try:
                self._ctrl.on_move_node(nid, parent, position)
            except Exception as exc:
                print(f"[UI] Batch move error for {nid}: {exc}")

        QTimer.singleShot(0, self._load_tree)
        QTimer.singleShot(50, lambda: self._load_all_messages(scroll_to_bottom=False))

    def _on_tree_manage_context(self, folder_id: str) -> None:
        """
        右键菜单 → 管理上下文块（仅目录节点）。
        弹出上下文块管理对话框，选择要关联的块并保存。
        """
        folder_info = self._ctrl.on_get_folder_context(folder_id)
        folder_title = folder_info.get("title", "目录")
        current_ids = folder_info.get("context_block_ids", [])

        all_blocks = self._ctrl.on_get_context_blocks()

        from app.ui.widgets.dialogs import show_context_block_manager_dialog
        show_context_block_manager_dialog(
            self._window,
            folder_title,
            all_blocks,
            current_ids,
            on_save=lambda selected_ids: self._on_ctx_save_folder_blocks(
                folder_id, selected_ids
            ),
        )

    def _on_ctx_save_folder_blocks(
        self, folder_id: str, selected_ids: list[str]
    ) -> None:
        """保存目录关联的上下文块。"""
        self._ctrl.on_update_context_blocks(folder_id, selected_ids)
        QTimer.singleShot(0, self._load_tree)

    def _on_tree_attach_file(self, folder_id: str) -> None:
        """
        右键菜单 → 添加附件（仅目录节点）。
        打开原生文件对话框，选择文件后调用 controller 挂载。
        """
        paths, _ = QFileDialog.getOpenFileNames(
            self._window,
            "选择附件",
            "",
            "支持的文件 (*.txt *.md *.json *.docx *.pdf);;所有文件 (*.*)",
        )
        if paths:
            for p in paths:
                self._ctrl.on_attach_file(folder_id, p)
            QTimer.singleShot(0, self._load_tree)

    # ── Sidebar 顶部按钮 ─────────────────────

    def _handle_new_root_folder(self) -> None:
        """
        Sidebar 顶部"文件夹"按钮 → 在根目录下创建新文件夹。
        使用默认名称"新文件夹"。
        """
        self._ctrl.on_create_folder(None, "新文件夹")
        QTimer.singleShot(0, self._load_tree)

    # ── Sidebar 底部入口 ─────────────────────

    def _handle_open_trash(self) -> None:
        """
        Sidebar 底部"回收站"入口。
        弹出回收站对话框，列出所有已删除条目，
        支持恢复、彻底删除和清空。
        """
        try:
            entries = self._ctrl.on_list_trash()
        except Exception as exc:
            print(f"[UI] List trash error: {exc}")
            QMessageBox.warning(self._window, "错误", f"无法加载回收站: {exc}")
            return

        from app.ui.widgets.dialogs import show_recycle_bin_dialog
        show_recycle_bin_dialog(
            self._window,
            entries,
            on_restore=self._on_trash_restore,
            on_permanent_delete=self._on_trash_permanent_delete,
            on_clear_all=self._on_trash_clear_all,
        )

    def _on_trash_restore(self, trash_entry_id: str) -> None:
        """回收站：恢复条目。"""
        self._ctrl.on_restore_from_trash(trash_entry_id)
        QTimer.singleShot(0, self._load_tree)

    def _on_trash_permanent_delete(self, trash_entry_id: str) -> None:
        """回收站：彻底删除条目。"""
        self._ctrl.on_permanently_delete(trash_entry_id)
        QTimer.singleShot(0, self._load_tree)

    def _on_trash_clear_all(self) -> None:
        """回收站：清空全部。"""
        count = self._ctrl.on_clear_trash()
        QTimer.singleShot(0, self._load_tree)
        print(f"[UI] Cleared {count} items from trash")

    def _handle_open_context_panel(self) -> None:
        """
        Sidebar 底部"上下文管理"入口。
        显示右侧上下文管理面板并刷新数据。
        """
        self._refresh_context_panel()
        self._window.show_context_panel()

    def _handle_open_kg_panel(self) -> None:
        """
        Sidebar 底部"知识图谱"入口。
        显示右侧知识图谱面板并刷新数据。
        """
        self._refresh_kg_panel()
        self._window.show_kg_panel()

    # ── 搜索操作 ────────────────────────────

    def _on_search(self, text: str) -> None:
        """执行搜索并显示结果弹窗。"""
        if not text.strip():
            self._search_popup.hide()
            return

        try:
            results = self._ctrl.on_search(text)
        except Exception as exc:
            print(f"[UI] Search error: {exc}")
            return

        search_box = self._window.sidebar.search_box
        global_pos = search_box.mapToGlobal(
            search_box.rect().bottomLeft()
        )
        self._search_popup.setFixedWidth(search_box.width())
        self._search_popup.show_results(results, global_pos)

    def _on_search_result_clicked(self, result) -> None:
        """
        点击搜索结果：导航到目标消息。
        1. 关闭弹窗 + 清空搜索框
        2. 若消息被禁用 → 自动启用
        3. 展开树到消息节点 + 高亮
        4. 若新启用了消息 → 重载消息列表（跳过锚点恢复）
        5. 滚动到消息并高亮
        """
        msg_id = result.message_id
        conv_id = result.conversation_id

        # 关闭弹窗 + 清空搜索框
        self._search_popup.hide()
        self._window.sidebar.clear_search()

        # 查找树节点，判断是否需要启用
        node = self._get_tree_node_by_id(msg_id)
        needs_enable = node is not None and node.enabled is False

        # 自动启用禁用的消息
        if needs_enable:
            try:
                self._ctrl.on_toggle_enabled(msg_id)
                tree_nodes = self._ctrl.get_tree()
                self._tree_nodes = tree_nodes
                self._window.sidebar.tree_panel.refresh_enabled_states(
                    tree_nodes
                )
            except Exception as exc:
                print(f"[UI] Auto-enable error: {exc}")

        # 展开树 + 高亮
        self._window.sidebar.tree_panel.expand_to_node(msg_id)
        self._window.sidebar.tree_panel.set_active(msg_id)

        # 设置当前发送目标
        self._current_session_id = conv_id

        # 若启用了新消息 → 重载消息列表
        if needs_enable:
            self._skip_next_anchor_restore = True
            self._load_all_messages(scroll_to_bottom=False)

        # 滚动到消息并高亮
        self._pending_scroll_target = msg_id
        delay = 50 if needs_enable else 0
        QTimer.singleShot(delay, lambda: self._do_scroll_to_target(msg_id))

    def _on_search_popup_dismissed(self) -> None:
        """搜索弹窗关闭时清除搜索框。"""
        self._window.sidebar.clear_search()

    def _get_tree_node_by_id(self, node_id: str):
        """从当前缓存的树节点中查找指定 ID 的节点。"""
        for n in self._tree_nodes:
            if n.id == node_id:
                return n
        return None

    # ── 上下文面板操作 ──────────────────────

    def _refresh_context_panel(self) -> None:
        """
        刷新上下文管理面板数据：
        - 加载所有上下文块
        - 加载拼接预览
        - 加载模板库
        """
        ctx_panel = self._window.context_panel
        blocks = self._ctrl.on_get_context_blocks()
        ctx_panel.load_blocks(blocks)

        preview = self._ctrl.on_get_context_preview()
        ctx_panel.set_preview(preview)

        templates = self._ctrl.on_get_templates()
        ctx_panel.load_templates(templates)

    def _on_ctx_remove_block(self, block_id: str) -> None:
        """上下文面板：移除上下文块。"""
        self._ctrl.on_remove_context_block(block_id)
        self._refresh_context_panel()

    def _on_ctx_toggle_block(self, block_id: str, enabled: bool) -> None:
        """上下文面板：切换块启用/禁用状态。"""
        self._ctrl.on_toggle_context_block(block_id, enabled)
        self._refresh_context_panel()

    def _on_ctx_add_text_block(self, text: str) -> None:
        """上下文面板：添加自定义文本块。"""
        self._ctrl.on_add_text_block(text)
        self._refresh_context_panel()

    def _on_ctx_save_template(self, name: str) -> None:
        """上下文面板：保存当前块为模板。"""
        result = self._ctrl.on_save_current_as_template(name)
        print(f"[UI] Template saved: {result}")
        self._refresh_context_panel()

    def _on_ctx_apply_template(self, template_id: str) -> None:
        """上下文面板：应用模板。"""
        self._ctrl.on_apply_template(template_id)
        self._refresh_context_panel()

    def _on_ctx_delete_template(self, template_id: str) -> None:
        """上下文面板：删除模板。"""
        self._ctrl.on_delete_template(template_id)
        self._refresh_context_panel()

    # ── 知识图谱面板操作 ────────────────────

    def _refresh_kg_panel(self) -> None:
        """
        刷新知识图谱面板数据：
        - 加载实体列表
        - 加载关系列表
        - 加载统计信息
        """
        kg_panel = self._window.kg_panel
        entities = self._ctrl.on_get_all_entities()
        relations = self._ctrl.on_get_all_relations()
        stats = self._ctrl.on_get_kg_stats()
        kg_panel.load_data(entities, relations, stats)

    def _on_kg_delete_entity(self, entity_name: str) -> None:
        """知识图谱面板：删除实体（同时删除相关关系）。"""
        self._ctrl.on_delete_kg_entity(entity_name)
        self._refresh_kg_panel()

    def _on_kg_delete_relation(self, relation_id: str) -> None:
        """知识图谱面板：删除单条关系。"""
        self._ctrl.on_delete_kg_relation(relation_id)
        self._refresh_kg_panel()

    # ── 知识图谱提取（记住按钮）───────────────

    def _handle_remember(self, msg: ChatMessage) -> None:
        """处理"记住"按钮点击。"""
        if not self._current_session_id:
            return

        msg.set_remember_loading()

        def on_result(result: str):
            success = "提取完成" in result or "✓" in result
            msg.set_remember_done(success)

        def on_error(error_msg: str):
            print(f"[KG] Extract error: {error_msg}")
            msg.set_remember_done(False)

        thread, runner = run_async_in_thread(
            self._ctrl.on_remember_conversation,
            self._current_session_id,
            on_result=on_result,
            on_error=on_error,
        )
        self._async_task_threads.append(thread)
        # 清理已完成线程的引用
        thread.finished.connect(
            lambda t=thread: self._async_task_threads.remove(t)
            if t in self._async_task_threads else None
        )
