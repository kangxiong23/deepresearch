# Layer: UI → widgets
# File: app/ui/widgets/chat_message.py
# Flet 0.85 兼容版本

from __future__ import annotations
import flet as ft
from app.ui.theme import Colors, Fonts, Spacing, Radius, Borders


def _role_badge(role: str) -> ft.Control:
    label_map = {
        "user":      ("YOU",      Colors.ROLE_USER),
        "assistant": ("DEEP",     Colors.ROLE_ASSISTANT),
        "thinking":  ("THINKING", Colors.ROLE_THINKING),
    }
    text, color = label_map.get(role, ("???", Colors.TEXT_SECONDARY))
    return ft.Container(
        content=ft.Text(
            text,
            size=Fonts.SIZE_XS,
            font_family=Fonts.MONO,
            color=color,
            weight=ft.FontWeight.BOLD,
        ),
        padding=ft.Padding(left=6, right=6, top=2, bottom=2),
        border=ft.Border(
            top=ft.BorderSide(1, color),
            bottom=ft.BorderSide(1, color),
            left=ft.BorderSide(1, color),
            right=ft.BorderSide(1, color),
        ),
        border_radius=Radius.SM,
    )


class ThinkingBlock(ft.Column):
    def __init__(self) -> None:
        self._text_ref = ft.Ref[ft.Text]()
        self._content_ref = ft.Ref[ft.Container]()
        self._toggle_icon_ref = ft.Ref[ft.Icon]()
        self._expanded = True
        self._buffer = ""

        super().__init__(
            spacing=0,
            controls=[
                ft.Container(
                    content=ft.Row(
                        controls=[
                            _role_badge("thinking"),
                            ft.Container(expand=True),
                            ft.Icon(
                                ft.Icons.EXPAND_LESS,
                                ref=self._toggle_icon_ref,
                                size=16,
                                color=Colors.TEXT_SECONDARY,
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.Padding(
                        left=Spacing.MD, right=Spacing.MD,
                        top=Spacing.SM, bottom=Spacing.SM,
                    ),
                    border=Borders.BOTTOM_ONLY,
                    on_click=self._on_toggle,
                ),
                ft.Container(
                    ref=self._content_ref,
                    content=ft.Text(
                        ref=self._text_ref,
                        value="",
                        size=Fonts.SIZE_SM,
                        font_family=Fonts.MONO,
                        color=Colors.TEXT_SECONDARY,
                    ),
                    padding=ft.Padding(
                        left=Spacing.MD, right=Spacing.MD,
                        top=Spacing.SM, bottom=Spacing.SM,
                    ),
                    visible=True,
                ),
            ],
        )

    def append_text(self, delta: str) -> None:
        self._buffer += delta
        if self._text_ref.current:
            self._text_ref.current.value = self._buffer

    def _on_toggle(self, e) -> None:
        self._expanded = not self._expanded
        if self._content_ref.current:
            self._content_ref.current.visible = self._expanded
        if self._toggle_icon_ref.current:
            self._toggle_icon_ref.current.name = (
                ft.Icons.EXPAND_LESS if self._expanded else ft.Icons.EXPAND_MORE
            )
        e.control.page.update()


class ChatMessage(ft.Container):
    """
    单条消息气泡，支持流式逐字更新。

    流式使用：
        msg.start_stream()
        msg.append_stream(delta); page.update()
        msg.finalize_stream()
    """

    def __init__(
        self,
        role: str,
        content: str,
        message_id: str = "",
        is_thinking: bool = False,
        on_copy=None,
        on_regenerate=None,
        on_remember=None,
    ) -> None:
        self.role = role
        self.message_id = message_id
        self._is_streaming = False
        self._stream_buffer = content

        self._md_ref = ft.Ref[ft.Markdown]()
        self._action_row_ref = ft.Ref[ft.Row]()
        self._remember_btn_ref = ft.Ref[ft.IconButton]()
        self._on_remember = on_remember

        is_user = (role == "user")
        bg_color = Colors.BG_ELEVATED if is_user else Colors.BG_SURFACE
        border = (
            ft.Border(
                top=ft.BorderSide(1, Colors.BORDER),
                bottom=ft.BorderSide(1, Colors.BORDER),
                left=ft.BorderSide(1, Colors.ROLE_USER + "55"),
                right=ft.BorderSide(1, Colors.BORDER),
            )
            if is_user else
            ft.Border(
                top=ft.BorderSide(1, Colors.BORDER),
                bottom=ft.BorderSide(1, Colors.BORDER),
                left=ft.BorderSide(2, Colors.ROLE_ASSISTANT),
                right=ft.BorderSide(1, Colors.BORDER),
            )
        )
        radius = Radius.BUBBLE_USER if is_user else Radius.BUBBLE_ASST

        action_controls: list[ft.Control] = []
        if not is_user:
            action_controls = [
                ft.IconButton(
                    icon=ft.Icons.CONTENT_COPY_OUTLINED,
                    icon_size=14,
                    icon_color=Colors.TEXT_SECONDARY,
                    tooltip="复制",
                    on_click=on_copy,
                    style=ft.ButtonStyle(
                        padding=ft.Padding(left=4, right=4, top=4, bottom=4),
                    ),
                ),
                ft.IconButton(
                    icon=ft.Icons.REFRESH_OUTLINED,
                    icon_size=14,
                    icon_color=Colors.TEXT_SECONDARY,
                    tooltip="重新生成",
                    on_click=on_regenerate,
                    style=ft.ButtonStyle(
                        padding=ft.Padding(left=4, right=4, top=4, bottom=4),
                    ),
                ),
                ft.IconButton(
                    ref=self._remember_btn_ref,
                    icon=ft.Icons.BOOKMARK_ADD_OUTLINED,
                    icon_size=14,
                    icon_color=Colors.TEXT_SECONDARY,
                    tooltip="记住这段对话（写入知识图谱）",
                    on_click=self._handle_remember,
                    style=ft.ButtonStyle(
                        padding=ft.Padding(left=4, right=4, top=4, bottom=4),
                    ),
                ),
            ]

        inner = ft.Column(
            spacing=Spacing.SM,
            controls=[
                ft.Row(
                    controls=[
                        _role_badge(role),
                        ft.Container(expand=True),
                    ],
                ),
                ft.Markdown(
                    ref=self._md_ref,
                    value=content,
                    selectable=True,
                    extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                    code_theme="atom-one-dark",
                    on_tap_link=lambda e: None,
                ),
                ft.Row(
                    ref=self._action_row_ref,
                    controls=action_controls,
                    visible=bool(action_controls),
                    spacing=Spacing.XS,
                ),
            ],
        )

        super().__init__(
            content=inner,
            padding=ft.Padding(
                left=Spacing.LG,
                right=Spacing.LG,
                top=Spacing.MD,
                bottom=Spacing.MD,
            ),
            border=border,
            border_radius=radius,
            bgcolor=bg_color,
            margin=ft.Margin(
                left=0, right=0,
                top=Spacing.XS, bottom=Spacing.XS,
            ),
        )

    def _handle_remember(self, e) -> None:
        """点击记住按钮：变为加载状态，触发回调，完成后变绿色。"""
        if self._on_remember is None:
            return
        btn = self._remember_btn_ref.current
        if btn is None:
            return
        # 变为加载状态
        btn.icon = ft.Icons.HOURGLASS_EMPTY
        btn.icon_color = Colors.WARNING
        btn.disabled = True
        btn.update()
        # 触发回调（由 app.py 处理异步提取）
        self._on_remember(e)

    def set_remember_done(self, success: bool = True) -> None:
        """提取完成后由外部调用，更新按钮状态。"""
        btn = self._remember_btn_ref.current
        if btn is None:
            return
        btn.icon = ft.Icons.BOOKMARK_ADDED if success else ft.Icons.BOOKMARK_OUTLINED
        btn.icon_color = Colors.SUCCESS if success else Colors.ERROR
        btn.disabled = False
        btn.tooltip = "已记住" if success else "提取失败，可重试"
        btn.update()

    def start_stream(self) -> None:
        self._is_streaming = True
        self._stream_buffer = ""

    def append_stream(self, delta: str) -> None:
        self._stream_buffer += delta
        if self._md_ref.current:
            self._md_ref.current.value = self._stream_buffer

    def finalize_stream(self) -> None:
        self._is_streaming = False

    @property
    def current_content(self) -> str:
        return self._stream_buffer