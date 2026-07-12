# Layer: UI
# File: app/ui/app.py
# Flet 0.85 兼容版本
# Phase 4 — 树形面板集成

from __future__ import annotations
import asyncio
import flet as ft

import config as app_config

from app.ui.theme import Colors, Fonts, Spacing, build_theme
from app.ui.widgets.chat_message import ChatMessage
from app.ui.widgets.sidebar import Sidebar
from app.ui.widgets.input_area import InputArea
from app.ui.widgets.context_panel import ContextPanel
from app.ui.widgets.kg_panel import KGPanel


class ChatApp:
    def __init__(self, app_controller, settings_controller) -> None:
        self._ctrl = app_controller
        self._settings_ctrl = settings_controller
        self._current_session_id: str = ""
        self._streaming_message: ChatMessage | None = None
        self._tree_nodes: list = []  # 缓存最近一次加载的树节点

        self._sidebar: Sidebar | None = None
        self._input_area: InputArea | None = None
        self._context_panel: ContextPanel | None = None
        self._kg_panel: KGPanel | None = None
        self._message_list_ref = ft.Ref[ft.ListView]()
        self._empty_hint_ref = ft.Ref[ft.Container]()
        self._page: ft.Page | None = None

        # 滚动任务追踪：generation 确保 cancelled 的旧任务不会覆盖新任务
        self._scroll_generation: int = 0

    def build(self, page: ft.Page) -> None:
        self._page = page
        self._configure_page(page)

        # ── 侧边栏（Phase 4 — 树形面板）───────
        self._sidebar = Sidebar(
            on_new_conversation=self._handle_new_conversation,
            on_switch_conversation=self._handle_switch_conversation,
            on_delete_conversation=self._handle_delete_conversation,
            on_open_context_panel=self._handle_open_context_panel,
            on_open_kg_panel=self._handle_open_kg_panel,
            # Phase 4 — 树操作回调
            on_create_folder=self._handle_create_folder,
            on_new_conversation_in_folder=self._handle_new_conversation_in_folder,
            on_rename_node=self._handle_rename_node,
            on_delete_node=self._handle_soft_delete_node,
            on_toggle_enabled=self._handle_toggle_enabled,
            on_move_node=self._handle_move_node,
            on_manage_context=self._handle_manage_context,
            on_attach_file=self._handle_attach_file,
            on_open_trash=self._handle_open_trash,
        )
        self._sidebar.set_page(page)

        self._input_area = InputArea(
            on_send=self._handle_send,
            on_stop=self._handle_stop,
            on_model_change=self._settings_ctrl.on_change_model,
            on_thinking_change=self._settings_ctrl.on_toggle_thinking,
        )

        self._context_panel = ContextPanel(
            on_remove_block=self._handle_remove_context_block,
            on_toggle_block=self._handle_toggle_context_block,
            on_apply_template=self._handle_apply_template,
            on_delete_template=self._handle_delete_template,
            on_add_text_block=self._handle_add_text_block,
            on_close=self._handle_close_context_panel,
        )

        self._kg_panel = KGPanel(
            on_delete_entity=self._handle_delete_kg_entity,
            on_delete_relation=self._handle_delete_kg_relation,
            on_close=self._handle_close_kg_panel,
        )

        message_list = ft.ListView(
            ref=self._message_list_ref,
            expand=True,
            spacing=Spacing.SM,
            padding=ft.Padding(
                left=Spacing.XL, right=Spacing.XL,
                top=Spacing.LG, bottom=Spacing.LG,
            ),
            auto_scroll=True,
            build_controls_on_demand=False,  # scroll_to 需要所有控件已构建
        )

        empty_hint = ft.Container(
            ref=self._empty_hint_ref,
            content=ft.Column(
                controls=[
                    ft.Container(
                        content=ft.Text(
                            "DR",
                            size=32,
                            font_family=Fonts.MONO,
                            color=Colors.PRIMARY,
                            weight=ft.FontWeight.BOLD,
                        ),
                        width=64, height=64,
                        border_radius=ft.BorderRadius(
                            top_left=12, top_right=12,
                            bottom_left=12, bottom_right=12,
                        ),
                        bgcolor=Colors.PRIMARY_GLOW,
                        alignment=ft.Alignment(0, 0),
                        border=ft.Border(
                            top=ft.BorderSide(1, Colors.PRIMARY),
                            bottom=ft.BorderSide(1, Colors.PRIMARY),
                            left=ft.BorderSide(1, Colors.PRIMARY),
                            right=ft.BorderSide(1, Colors.PRIMARY),
                        ),
                    ),
                    ft.Text(
                        "DeepResearch",
                        size=Fonts.SIZE_XXL,
                        font_family=Fonts.MONO,
                        color=Colors.TEXT_PRIMARY,
                        weight=ft.FontWeight.W_600,
                    ),
                    ft.Text(
                        "选择或新建一个对话以开始",
                        size=Fonts.SIZE_MD,
                        color=Colors.TEXT_SECONDARY,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=Spacing.MD,
            ),
            expand=True,
            alignment=ft.Alignment(0, 0),
            visible=True,
        )

        chat_area = ft.Stack(
            controls=[
                ft.Container(content=message_list, expand=True),  # 底层：消息列表
                empty_hint,                                        # 顶层：空状态提示
            ],
            expand=True,
        )

        content_area = ft.Row(
            controls=[
                ft.Column(
                    expand=True,
                    spacing=0,
                    controls=[
                        chat_area,
                        self._input_area,
                    ],
                ),
                self._context_panel,
                self._kg_panel,
            ],
            expand=True,
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        page.add(
            ft.Row(
                controls=[
                    self._sidebar,
                    ft.VerticalDivider(width=1, color=Colors.BORDER),
                    content_area,
                ],
                expand=True,
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            )
        )

        self._load_tree()

    def _configure_page(self, page: ft.Page) -> None:
        page.title = "DeepResearch"
        page.theme_mode = ft.ThemeMode.DARK
        page.theme = build_theme()
        page.bgcolor = Colors.BG_BASE
        page.padding = 0
        page.spacing = 0
        page.fonts = {
            "JetBrains Mono": "https://fonts.gstatic.com/s/jetbrainsmono/v18/tDbY2o-flEEny0FZhsfKu5WU4xD-IQ.woff2",
            "Noto Sans SC": "https://fonts.gstatic.com/s/notosanssc/v36/k3kCo84MPvpLmixcA63oeAL7Iqp5IZJF9bmaG9_FnYxNbPzS5HE.woff2",
        }
        page.window.min_width = 900
        page.window.min_height = 600

    # ──────────────────────────────────────────
    # 树数据加载（Phase 4）
    # ──────────────────────────────────────────

    def _load_tree(self) -> None:
        """从 Controller 获取树数据并加载到侧边栏树面板。"""
        tree_nodes = self._ctrl.get_tree()
        self._tree_nodes = tree_nodes  # 缓存用于节点类型查找
        if self._sidebar:
            self._sidebar.load_tree(tree_nodes)

    def _load_history(self) -> None:
        """[已弃用] Phase 4 使用 _load_tree() 代替。保留向后兼容。"""
        self._load_tree()

    # ──────────────────────────────────────────
    # 对话生命周期
    # ──────────────────────────────────────────

    def _handle_new_conversation(self) -> None:
        """新建对话（默认放在根目录"未分类"下）。"""
        session_id = self._ctrl.on_new_conversation()
        self._current_session_id = session_id
        self._clear_message_list()
        self._set_empty_hint(True)
        if self._sidebar:
            self._sidebar.set_active(session_id)
        self._load_tree()

    def _handle_new_conversation_in_folder(self, parent_id: str | None) -> None:
        """在指定目录下新建对话。"""
        session_id = self._ctrl.on_new_conversation(parent_id=parent_id)
        self._current_session_id = session_id
        self._clear_message_list()
        self._set_empty_hint(True)
        if self._sidebar:
            self._sidebar.set_active(session_id)
        self._load_tree()

    def _handle_switch_conversation(self, session_id: str) -> None:
        # ── 解析节点类型，确定加载目标和滚动目标 ──
        load_id = session_id      # 要加载消息的对话/目录 ID
        focus_id = ""             # 要滚动到的消息 ID（仅 MessageNode 有效）
        for n in self._tree_nodes:
            if n.id == session_id:
                if n.node_type == "message" and n.parent_id:
                    load_id = n.parent_id   # MessageNode → 加载父对话
                    focus_id = n.id         # MessageNode.id == message_id
                break

        print(f"[UI   ] _handle_switch_conversation: click={session_id} "
              f"load={load_id} focus={focus_id} multi_conv={app_config.multi_conv_mode}")
        self._current_session_id = load_id

        if app_config.multi_conv_mode:
            # Phase 5: 加载所有启用对话的消息（合并时间线）
            messages = self._ctrl.on_get_multi_conversation_messages()
            print(f"[UI   ] multi_conv 模式: 获取到 {len(messages)} 条消息")
            # 如果 multi_conv 模式无结果（例如无 MessageNode），
            # 回退到加载当前点击节点的消息
            if not messages:
                print(f"[UI   ] multi_conv 无消息，回退到按节点加载: {load_id}")
                messages = self._ctrl.on_load_messages_for_node(load_id)
            self._rebuild_message_list(messages, scroll_to_id=focus_id)
        else:
            # Phase 5: 以树结构为依据加载该节点对应的消息
            messages = self._ctrl.on_load_messages_for_node(load_id)
            print(f"[UI   ] 单节点模式: 获取到 {len(messages)} 条消息")
            self._rebuild_message_list(messages, scroll_to_id=focus_id)

        # 高亮被点击的节点
        if self._sidebar:
            self._sidebar.set_active(session_id)
        if self._page:
            self._page.update()

    def _handle_delete_conversation(self, session_id: str) -> None:
        self._ctrl.on_delete_conversation(session_id)
        if session_id == self._current_session_id:
            self._current_session_id = ""
            self._clear_message_list()
            self._set_empty_hint(True)
        self._load_tree()
        if self._page:
            self._page.update()

    # ──────────────────────────────────────────
    # 树操作（Phase 4）
    # ──────────────────────────────────────────

    def _handle_create_folder(self, parent_id: str | None, title: str) -> None:
        """创建新目录。"""
        from app.ui.widgets.dialogs import show_new_folder_dialog

        def on_confirm(name: str) -> None:
            print(f"[TREE] 创建目录: parent={parent_id} name={name}")
            self._ctrl.on_create_folder(parent_id, name)
            self._load_tree()

        show_new_folder_dialog(
            self._page,
            on_confirm=on_confirm,
            default_title=title or "新文件夹",
        )

    def _handle_rename_node(self, node_id: str) -> None:
        """重命名节点。"""
        from app.ui.widgets.dialogs import show_rename_dialog

        # 获取当前名称
        current_title = ""
        tree_nodes = self._ctrl.get_tree()
        for n in tree_nodes:
            if n.id == node_id:
                current_title = n.title
                break

        def on_confirm(new_title: str) -> None:
            print(f"[TREE] 重命名: {node_id} -> {new_title}")
            self._ctrl.on_rename_node(node_id, new_title)
            self._load_tree()

        show_rename_dialog(self._page, current_title, on_confirm=on_confirm)

    def _handle_toggle_enabled(self, node_id: str) -> None:
        """切换节点启用/禁用状态（含级联）。"""
        print(f"[TREE] 切换启用状态(级联): {node_id}")
        self._ctrl.on_toggle_enabled(node_id)
        self._load_tree()
        # Phase 5: 多对话模式下，刷新聊天区的消息列表
        if app_config.multi_conv_mode and self._current_session_id:
            self._refresh_multi_conv_messages()

    def _handle_move_node(self, node_id: str, target_parent_id: str) -> None:
        """拖拽移动节点。"""
        print(f"[TREE] 移动节点: {node_id} -> {target_parent_id}")
        self._ctrl.on_move_node(node_id, target_parent_id)
        self._load_tree()

    def _handle_soft_delete_node(self, node_id: str) -> None:
        """软删除节点（移入回收站）。"""
        from app.ui.widgets.dialogs import show_confirm_dialog

        def on_confirm() -> None:
            print(f"[TREE] 软删除节点: {node_id}")

            # 检查当前活跃对话是否在待删除子树中
            should_clear = False
            if self._current_session_id and self._current_session_id != node_id:
                # 收集被删节点的所有后代 ID
                tree_nodes = self._ctrl.get_tree()
                children_map: dict[str | None, list] = {}
                for n in tree_nodes:
                    pid = n.parent_id
                    if pid not in children_map:
                        children_map[pid] = []
                    children_map[pid].append(n)

                descendant_ids: set[str] = set()
                queue = [node_id]
                while queue:
                    cur = queue.pop(0)
                    for child in children_map.get(cur, []):
                        descendant_ids.add(child.id)
                        queue.append(child.id)

                if self._current_session_id in descendant_ids:
                    should_clear = True

            self._ctrl.on_soft_delete_node(node_id)
            if node_id == self._current_session_id or should_clear:
                self._current_session_id = ""
                self._clear_message_list()
                self._set_empty_hint(True)
            self._load_tree()

        show_confirm_dialog(
            self._page,
            title="删除确认",
            message="确定要删除吗？将移入回收站，可恢复。",
            on_confirm=on_confirm,
        )

    def _handle_manage_context(self, folder_id: str) -> None:
        """管理目录关联的 ContextBlock。"""
        from app.ui.widgets.dialogs import show_context_block_manager_dialog

        folder_info = self._ctrl.on_get_folder_context(folder_id)
        all_blocks = self._ctrl.on_get_context_blocks()

        def on_save(new_ids: list[str]) -> None:
            print(f"[TREE] 更新上下文块 {folder_id}: {len(new_ids)} 个块")
            self._ctrl.on_update_context_blocks(folder_id, new_ids)
            self._load_tree()

        show_context_block_manager_dialog(
            self._page,
            folder_title=folder_info["title"],
            all_blocks=all_blocks,
            current_block_ids=folder_info["context_block_ids"],
            on_save=on_save,
        )

    def _handle_attach_file(self, folder_id: str) -> None:
        """挂载附件到目录。"""

        def on_result(e: ft.FilePickerResultEvent) -> None:
            if e.files:
                for f in e.files:
                    print(f"[TREE] 挂载附件到 {folder_id}: {f.path}")
                    self._ctrl.on_attach_file(folder_id, f.path)
                self._load_tree()

        fp = ft.FilePicker()
        fp.on_result = on_result
        self._page.overlay.append(fp)
        self._page.update()
        fp.pick_files(allow_multiple=True)

    def _handle_open_trash(self) -> None:
        """打开回收站对话框。"""
        from app.ui.widgets.dialogs import show_recycle_bin_dialog

        entries = self._ctrl.on_list_trash()

        def on_restore(trash_entry_id: str) -> None:
            print(f"[TREE] 恢复: {trash_entry_id}")
            self._ctrl.on_restore_from_trash(trash_entry_id)
            self._load_tree()

        def on_permanent_delete(trash_entry_id: str) -> None:
            print(f"[TREE] 彻底删除: {trash_entry_id}")
            self._ctrl.on_permanently_delete(trash_entry_id)
            self._load_tree()

        def on_clear_all() -> None:
            count = self._ctrl.on_clear_trash()
            print(f"[TREE] 清空回收站: {count} 个条目")
            self._load_tree()

        show_recycle_bin_dialog(
            self._page,
            trash_entries=entries,
            on_restore=on_restore,
            on_permanent_delete=on_permanent_delete,
            on_clear_all=on_clear_all,
        )

    # ──────────────────────────────────────────
    # 消息发送
    # ──────────────────────────────────────────

    def _handle_send(self, text: str, files: list[str]) -> None:
        if not self._current_session_id:
            self._handle_new_conversation()
        if not text and not files:
            return

        self._append_message(ChatMessage(role="user", content=text))
        self._set_empty_hint(False)

        if self._input_area:
            self._input_area.clear()
            self._input_area.set_generating(True)

        if self._page:
            self._page.run_task(
                self._stream_response,
                self._current_session_id,
                text,
                files,
            )

    def _handle_stop(self) -> None:
        self._ctrl.on_stop_generation()
        if self._input_area:
            self._input_area.set_generating(False)
        if self._streaming_message:
            self._streaming_message.finalize_stream()
            self._streaming_message = None

    async def _stream_response(
        self, session_id: str, text: str, files: list[str]
    ) -> None:
        from app.ui.widgets.chat_message import ThinkingBlock

        # 思考块（仅 Reasoner 模式有）
        thinking_block: ThinkingBlock | None = None

        # 助手回复气泡
        _msg_holder: list = []
        def _on_remember(e):
            msg = _msg_holder[0] if _msg_holder else None
            self._page.run_task(self._do_remember, session_id, msg)
        assistant_msg = ChatMessage(
            role="assistant",
            content="",
            on_copy=self._make_copy_handler(),
            on_regenerate=self._make_regenerate_handler(session_id),
            on_remember=_on_remember,
        )
        _msg_holder.append(assistant_msg)
        assistant_msg.start_stream()
        self._streaming_message = assistant_msg
        self._append_message(assistant_msg)

        try:
            async for chunk_vm in self._ctrl.on_send_message(session_id, text, files):
                if chunk_vm.is_done:
                    break

                if chunk_vm.chunk_type == "thinking":
                    if thinking_block is None:
                        thinking_block = ThinkingBlock()
                        self._insert_before_last(thinking_block)
                    thinking_block.append_text(chunk_vm.delta)
                else:
                    assistant_msg.append_stream(chunk_vm.delta)

                if self._page:
                    self._page.update()
                await asyncio.sleep(0)
        finally:
            assistant_msg.finalize_stream()
            self._streaming_message = None
            if self._input_area:
                self._input_area.set_generating(False)
            if self._page:
                self._page.update()
            # 刷新树面板以更新摘要/消息计数
            self._load_tree()

    # ──────────────────────────────────────────
    # 上下文面板
    # ──────────────────────────────────────────

    def _handle_open_context_panel(self) -> None:
        if self._context_panel:
            self._refresh_context_panel()
            self._context_panel.show()

    def _handle_close_context_panel(self) -> None:
        if self._context_panel:
            self._context_panel.hide()

    def _handle_remove_context_block(self, block_id: str) -> None:
        self._ctrl.on_remove_context_block(block_id)
        self._refresh_context_panel()

    def _handle_toggle_context_block(self, block_id: str, enabled: bool) -> None:
        self._ctrl.on_toggle_context_block(block_id, enabled)
        self._refresh_context_panel()

    def _handle_delete_template(self, template_id: str) -> None:
        self._ctrl.on_delete_template(template_id)
        self._refresh_context_panel()

    def _handle_apply_template(self, template_id: str) -> None:
        if template_id.startswith("__save__:"):
            name = template_id[len("__save__:"):]
            self._ctrl.on_save_current_as_template(name)
        else:
            self._ctrl.on_apply_template(template_id)
        self._refresh_context_panel()

    def _handle_add_text_block(self, text: str) -> None:
        self._ctrl.on_add_text_block(text)
        self._refresh_context_panel()

    def _handle_open_kg_panel(self) -> None:
        if self._kg_panel is None:
            return
        self._kg_panel.load_data(
            entities=self._ctrl.on_get_all_entities(),
            relations=self._ctrl.on_get_all_relations(),
            stats=self._ctrl.on_get_kg_stats(),
        )
        self._kg_panel.show()

    def _handle_close_kg_panel(self) -> None:
        if self._kg_panel:
            self._kg_panel.hide()

    def _handle_delete_kg_entity(self, entity_name: str) -> None:
        self._ctrl.on_delete_kg_entity(entity_name)
        self._handle_open_kg_panel()

    def _handle_delete_kg_relation(self, relation_id: str) -> None:
        self._ctrl.on_delete_kg_relation(relation_id)
        self._handle_open_kg_panel()

    def _refresh_context_panel(self) -> None:
        if not self._context_panel:
            return
        from app.controllers.view_models import ContextBlockVM
        blocks_data = self._ctrl.on_get_context_blocks()
        block_vms = [
            ContextBlockVM(
                id=b["id"],
                label=b["label"],
                preview=b["preview"],
                enabled=b["enabled"],
            )
            for b in blocks_data
        ]
        self._context_panel.load_blocks(block_vms)
        preview = self._ctrl.on_get_context_preview()
        self._context_panel.set_preview(preview)
        from app.controllers.view_models import TemplateVM
        templates_data = self._ctrl.on_get_templates()
        template_vms = [
            TemplateVM(id=t["id"], name=t["name"], description=t["description"])
            for t in templates_data
        ]
        self._context_panel.load_templates(template_vms)

    def _insert_before_last(self, control) -> None:
        if not self._message_list_ref.current:
            return
        lst = self._message_list_ref.current.controls
        if lst:
            lst.insert(len(lst) - 1, control)
        else:
            lst.append(control)
        self._message_list_ref.current.update()

    def _append_message(self, msg: ChatMessage) -> None:
        if self._message_list_ref.current:
            self._message_list_ref.current.controls.append(msg)
            self._message_list_ref.current.update()

    def _clear_message_list(self) -> None:
        if self._message_list_ref.current:
            self._message_list_ref.current.controls.clear()
            self._message_list_ref.current.update()

    def _refresh_multi_conv_messages(self) -> None:
        """Phase 5: 刷新聊天区，展示所有启用对话的消息（合并时间线）。"""
        if not self._current_session_id:
            return
        messages = self._ctrl.on_get_multi_conversation_messages()
        self._rebuild_message_list(messages)
        if self._page:
            self._page.update()

    # ── 消息高度估算常量（基于 Flet 默认渲染参数实测校准）──
    # ChatMessage 固定部分（padding + margin + 内部间距 + 角色标签行）
    _MSG_FIXED_USER = 68.0       # 用户消息: padding(24) + inner_gaps(16) + badge(20) + list_spacing(8)
    _MSG_FIXED_ASSISTANT = 104.0 # 助手消息: 上述 + 操作按钮行(36)
    _MSG_LINE_HEIGHT = 21.0      # Markdown 每行渲染高度 (font 14px × 1.5 行距)
    _MSG_CHARS_PER_LINE = 70     # 每行容纳 ASCII 字符数（≈570px 可用宽度 / 8px 均宽）
    _MSG_CODE_LINE_HEIGHT = 17.0 # 代码块每行高度 (mono 12px × 1.4 行距)

    @staticmethod
    def _estimate_message_height(vm) -> float:
        """基于消息内容估算渲染高度（像素）。

        固定开销 = 控件 padding + margin + 内部间距 + 角色标签
        内容高度 = 行数 × 行高，区分普通文本/代码块/CJK 字符
        """
        content = vm.content or ""

        fixed = ChatApp._MSG_FIXED_ASSISTANT if vm.role == "assistant" \
                else ChatApp._MSG_FIXED_USER

        # ── 计算等效行数 ──
        total_lines = 0.0
        in_code_block = False
        for raw_line in content.split("\n"):
            stripped = raw_line.strip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                total_lines += 0.4  # 围栏行很矮
                continue
            text = raw_line if raw_line else " "
            # CJK 字符宽度 ≈ 2 倍 ASCII
            cjk = sum(1 for ch in text if "一" <= ch <= "鿿"
                      or "　" <= ch <= "〿" or "＀" <= ch <= "￯")
            effective_len = len(text) + cjk
            wrap_lines = max(1, int(effective_len / ChatApp._MSG_CHARS_PER_LINE) + 1)
            lh = ChatApp._MSG_CODE_LINE_HEIGHT if in_code_block else ChatApp._MSG_LINE_HEIGHT
            total_lines += wrap_lines * (lh / ChatApp._MSG_LINE_HEIGHT)

        if total_lines < 0.5:
            total_lines = 0.5

        return fixed + total_lines * ChatApp._MSG_LINE_HEIGHT

    def _rebuild_message_list(self, message_vms: list,
                             scroll_to_id: str = "") -> None:
        """重建消息列表；若指定 scroll_to_id 则滚动到对应消息。"""
        if not self._message_list_ref.current:
            return

        # 递增 generation，使旧任务的 finally 块自动跳过
        self._scroll_generation += 1
        gen = self._scroll_generation

        lv = self._message_list_ref.current
        lv.controls.clear()
        self._set_empty_hint(bool(not message_vms))

        # ── 构建消息控件 + 计算累计偏移 ──
        offsets: dict[str, float] = {}
        running = float(Spacing.LG)  # ListView padding.top

        for vm in message_vms:
            msg = ChatMessage(
                role=vm.role, content=vm.content, message_id=vm.id,
                is_thinking=getattr(vm, "is_thinking", False),
                on_copy=self._make_copy_handler(),
                on_regenerate=self._make_regenerate_handler(self._current_session_id),
                on_remember=self._make_remember_handler_simple(self._current_session_id),
            )
            lv.controls.append(msg)
            offsets[vm.id] = running
            running += self._estimate_message_height(vm)

        if scroll_to_id:
            lv.auto_scroll = False

        lv.update()

        if scroll_to_id and self._page:
            target = offsets.get(scroll_to_id)
            if target is not None:
                self._page.run_task(self._scroll_to_message, target, gen)

    async def _scroll_to_message(
        self, target_offset: float, generation: int
    ) -> None:
        """滚动到指定偏移位置；仅当 generation 未过期时恢复 auto_scroll。"""
        await asyncio.sleep(0.15)
        try:
            lv = self._message_list_ref.current
            if lv is not None:
                await lv.scroll_to(offset=target_offset, duration=400)
        except Exception:
            pass
        finally:
            # 仅在未过期时清理状态
            if generation == self._scroll_generation:
                if self._message_list_ref.current:
                    self._message_list_ref.current.auto_scroll = True

    def _set_empty_hint(self, visible: bool) -> None:
        if self._empty_hint_ref.current:
            self._empty_hint_ref.current.visible = visible
            self._empty_hint_ref.current.update()

    async def _do_remember(
        self, session_id: str, msg: "ChatMessage | None"
    ) -> None:
        try:
            result = await self._ctrl.on_remember_conversation(session_id)
            print(f"[KG] {result}")
            success = "提取完成" in result or "✓" in result
        except Exception as ex:
            print(f"[KG] 提取异常: {ex}")
            success = False
        if msg is not None:
            msg.set_remember_done(success)
        if self._page:
            self._page.update()

    def _make_remember_handler_simple(self, session_id: str):
        def _handler(e):
            control = e.control
            msg = None
            parent = getattr(control, 'parent', None)
            while parent is not None:
                from app.ui.widgets.chat_message import ChatMessage as CM
                if isinstance(parent, CM):
                    msg = parent
                    break
                parent = getattr(parent, 'parent', None)
            if self._page:
                self._page.run_task(self._do_remember, session_id, msg)
        return _handler

    def _make_copy_handler(self):
        def _handler(e):
            if isinstance(e.control.parent, ChatMessage):
                self._page.set_clipboard(e.control.parent.current_content)
        return _handler

    def _make_regenerate_handler(self, session_id: str):
        def _handler(e):
            if self._page:
                self._page.run_task(
                    self._stream_regenerate, session_id
                )
        return _handler

    async def _stream_regenerate(self, session_id: str) -> None:
        """流式重新生成最后一条助手回复。"""
        from app.ui.widgets.chat_message import ThinkingBlock

        thinking_block: ThinkingBlock | None = None

        _msg_holder: list = []
        def _on_remember(e):
            msg = _msg_holder[0] if _msg_holder else None
            self._page.run_task(self._do_remember, session_id, msg)
        assistant_msg = ChatMessage(
            role="assistant",
            content="",
            on_copy=self._make_copy_handler(),
            on_regenerate=self._make_regenerate_handler(session_id),
            on_remember=_on_remember,
        )
        _msg_holder.append(assistant_msg)
        assistant_msg.start_stream()
        self._streaming_message = assistant_msg
        self._append_message(assistant_msg)

        try:
            async for chunk_vm in self._ctrl.on_regenerate_message(
                session_id, ""
            ):
                if chunk_vm.is_done:
                    break

                if chunk_vm.chunk_type == "thinking":
                    if thinking_block is None:
                        thinking_block = ThinkingBlock()
                        self._insert_before_last(thinking_block)
                    thinking_block.append_text(chunk_vm.delta)
                else:
                    assistant_msg.append_stream(chunk_vm.delta)

                if self._page:
                    self._page.update()
                await asyncio.sleep(0)
        finally:
            assistant_msg.finalize_stream()
            self._streaming_message = None
            if self._input_area:
                self._input_area.set_generating(False)
            if self._page:
                self._page.update()
            self._load_tree()
