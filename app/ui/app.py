# Layer: UI
# File: app/ui/app.py
# Flet 0.85 兼容版本

from __future__ import annotations
import asyncio
import flet as ft

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

        self._sidebar: Sidebar | None = None
        self._input_area: InputArea | None = None
        self._context_panel: ContextPanel | None = None
        self._kg_panel: KGPanel | None = None
        self._message_list_ref = ft.Ref[ft.ListView]()
        self._empty_hint_ref = ft.Ref[ft.Container]()
        self._page: ft.Page | None = None

    def build(self, page: ft.Page) -> None:
        self._page = page
        self._configure_page(page)

        self._sidebar = Sidebar(
            on_new_conversation=self._handle_new_conversation,
            on_switch_conversation=self._handle_switch_conversation,
            on_delete_conversation=self._handle_delete_conversation,
            on_open_context_panel=self._handle_open_context_panel,
            on_open_kg_panel=self._handle_open_kg_panel,
        )

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
                empty_hint,
                ft.Container(content=message_list, expand=True),
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

        self._load_history()

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

    def _load_history(self) -> None:
        conversations = self._ctrl.on_load_history()
        if self._sidebar:
            self._sidebar.load_conversations(conversations)

    def _handle_new_conversation(self) -> None:
        session_id = self._ctrl.on_new_conversation()
        self._current_session_id = session_id
        self._clear_message_list()
        self._set_empty_hint(True)
        if self._sidebar:
            self._sidebar.set_active(session_id)
        self._load_history()

    def _handle_switch_conversation(self, session_id: str) -> None:
        self._current_session_id = session_id
        result = self._ctrl.on_switch_conversation(session_id)
        self._rebuild_message_list(result.messages if result else [])
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
        self._load_history()
        if self._page:
            self._page.update()

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
        # on_remember 用列表包裹 msg 引用，等 msg 创建后赋值
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
                    # 第一个 thinking chunk：创建 ThinkingBlock 插到气泡前面
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
        """删除模板并刷新面板。"""
        self._ctrl.on_delete_template(template_id)
        self._refresh_context_panel()

    def _handle_apply_template(self, template_id: str) -> None:
        """应用模板或保存当前为模板。"""
        if template_id.startswith("__save__:"):
            # 保存当前块为新模板
            name = template_id[len("__save__:"):]
            self._ctrl.on_save_current_as_template(name)
        else:
            self._ctrl.on_apply_template(template_id)
        self._refresh_context_panel()

    def _handle_add_text_block(self, text: str) -> None:
        self._ctrl.on_add_text_block(text)
        self._refresh_context_panel()

    def _handle_open_kg_panel(self) -> None:
        """打开知识图谱管理面板，刷新数据。"""
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
        self._handle_open_kg_panel()  # 刷新面板

    def _handle_delete_kg_relation(self, relation_id: str) -> None:
        self._ctrl.on_delete_kg_relation(relation_id)
        self._handle_open_kg_panel()  # 刷新面板

    def _refresh_context_panel(self) -> None:
        """刷新上下文面板的块列表和预览。"""
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
        # 加载模板列表
        from app.controllers.view_models import TemplateVM
        templates_data = self._ctrl.on_get_templates()
        template_vms = [
            TemplateVM(id=t["id"], name=t["name"], description=t["description"])
            for t in templates_data
        ]
        self._context_panel.load_templates(template_vms)

    def _insert_before_last(self, control) -> None:
        """在消息列表最后一个控件之前插入（用于在气泡前插入 ThinkingBlock）。"""
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

    def _rebuild_message_list(self, message_vms: list) -> None:
        if not self._message_list_ref.current:
            return
        self._message_list_ref.current.controls.clear()
        has_messages = bool(message_vms)
        self._set_empty_hint(not has_messages)
        for vm in message_vms:
            self._message_list_ref.current.controls.append(
                ChatMessage(
                    role=vm.role,
                    content=vm.content,
                    message_id=vm.id,
                    is_thinking=getattr(vm, "is_thinking", False),
                    on_copy=self._make_copy_handler(),
                    on_regenerate=self._make_regenerate_handler(
                        self._current_session_id
                    ),
                    on_remember=self._make_remember_handler_simple(
                        self._current_session_id
                    ),
                )
            )
        self._message_list_ref.current.update()

    def _set_empty_hint(self, visible: bool) -> None:
        if self._empty_hint_ref.current:
            self._empty_hint_ref.current.visible = visible
            self._empty_hint_ref.current.update()

    async def _do_remember(
        self, session_id: str, msg: "ChatMessage | None"
    ) -> None:
        """异步执行知识提取，完成后更新按钮状态。"""
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
        """给已有消息（切换对话时重建）用的 remember handler。"""
        def _handler(e):
            # 找到触发按钮所在的 ChatMessage
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
            self._ctrl.on_regenerate_message(session_id, "")
        return _handler