# Layer: UI → widgets
# File: app/ui/widgets/sidebar.py
# Flet 0.85 兼容版本

from __future__ import annotations
from typing import Callable
import flet as ft
from app.ui.theme import Colors, Fonts, Spacing, Radius, Borders


class ConversationItem(ft.Container):
    def __init__(
        self,
        session_id: str,
        title: str,
        preview: str,
        updated_at: str,
        is_active: bool,
        on_click: Callable[[str], None],
        on_delete: Callable[[str], None],
    ) -> None:
        self.session_id = session_id
        self._delete_visible_ref = ft.Ref[ft.IconButton]()

        bg = Colors.BG_OVERLAY if is_active else "transparent"
        border = (
            ft.Border(
                top=ft.BorderSide(0, "transparent"),
                bottom=ft.BorderSide(0, "transparent"),
                right=ft.BorderSide(0, "transparent"),
                left=ft.BorderSide(2, Colors.PRIMARY),
            )
            if is_active else
            ft.Border(
                top=ft.BorderSide(0, "transparent"),
                bottom=ft.BorderSide(1, Colors.DIVIDER),
                left=ft.BorderSide(2, "transparent"),
                right=ft.BorderSide(0, "transparent"),
            )
        )

        super().__init__(
            content=ft.Row(
                controls=[
                    ft.Column(
                        expand=True,
                        spacing=2,
                        controls=[
                            ft.Text(
                                title or "新对话",
                                size=Fonts.SIZE_MD,
                                color=Colors.TEXT_PRIMARY if is_active else Colors.TEXT_SECONDARY,
                                weight=ft.FontWeight.W_500 if is_active else ft.FontWeight.NORMAL,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Text(
                                preview,
                                size=Fonts.SIZE_XS,
                                color=Colors.TEXT_DISABLED,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                        ],
                    ),
                    ft.Column(
                        spacing=0,
                        horizontal_alignment=ft.CrossAxisAlignment.END,
                        controls=[
                            ft.Text(
                                updated_at,
                                size=Fonts.SIZE_XS,
                                color=Colors.TEXT_DISABLED,
                                font_family=Fonts.MONO,
                            ),
                            ft.IconButton(
                                ref=self._delete_visible_ref,
                                icon=ft.Icons.DELETE_OUTLINE,
                                icon_size=14,
                                icon_color=Colors.ERROR,
                                tooltip="删除对话",
                                visible=True,
                                style=ft.ButtonStyle(
                                    padding=ft.Padding(left=2, right=2, top=2, bottom=2),
                                ),
                                on_click=lambda e: on_delete(session_id),
                            ),
                        ],
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=Spacing.SM,
            ),
            padding=ft.Padding(
                left=Spacing.MD, right=Spacing.SM,
                top=Spacing.SM, bottom=Spacing.SM,
            ),
            bgcolor=bg,
            border=border,
            on_click=lambda e: on_click(session_id),
        )

    def _on_hover(self, e) -> None:
        if self._delete_visible_ref.current:
            self._delete_visible_ref.current.visible = e.data == "true"
            e.control.update()


class Sidebar(ft.Container):
    """
    左侧边栏。

    公开接口：
        load_conversations(items: list[ConversationVM]) -> None
        set_active(session_id: str) -> None
    """

    def __init__(
        self,
        on_new_conversation: Callable[[], None],
        on_switch_conversation: Callable[[str], None],
        on_delete_conversation: Callable[[str], None],
        on_open_context_panel: Callable[[], None],
        on_open_kg_panel: Callable[[], None] | None = None,
    ) -> None:
        self._on_switch = on_switch_conversation
        self._on_delete = on_delete_conversation
        self._active_id: str = ""
        self._list_ref = ft.Ref[ft.ListView]()

        header = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Container(
                        content=ft.Text(
                            "DR",
                            size=Fonts.SIZE_MD,
                            font_family=Fonts.MONO,
                            color=Colors.PRIMARY,
                            weight=ft.FontWeight.BOLD,
                        ),
                        width=32, height=32,
                        border_radius=Radius.SM,
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
                        size=Fonts.SIZE_LG,
                        font_family=Fonts.MONO,
                        color=Colors.TEXT_PRIMARY,
                        weight=ft.FontWeight.W_600,
                    ),
                ],
                spacing=Spacing.SM,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding(
                left=Spacing.LG, right=Spacing.LG,
                top=Spacing.LG, bottom=Spacing.LG,
            ),
            border=Borders.BOTTOM_ONLY,
        )

        new_btn = ft.Container(
            content=ft.ElevatedButton(
                content=ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.ADD, size=16, color=Colors.BG_BASE),
                        ft.Text(
                            "新建对话",
                            size=Fonts.SIZE_MD,
                            color=Colors.BG_BASE,
                            weight=ft.FontWeight.W_500,
                        ),
                    ],
                    tight=True,
                    spacing=Spacing.SM,
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
                style=ft.ButtonStyle(
                    bgcolor=Colors.PRIMARY,
                    overlay_color=Colors.PRIMARY_DIM,
                    shape=ft.RoundedRectangleBorder(radius=8),
                    padding=ft.Padding(left=16, right=16, top=10, bottom=10),
                    elevation=0,
                ),
                on_click=lambda e: on_new_conversation(),
            ),
            padding=ft.Padding(
                left=Spacing.LG, right=Spacing.LG,
                top=Spacing.MD, bottom=Spacing.MD,
            ),
        )

        conversation_list = ft.ListView(
            ref=self._list_ref,
            expand=True,
            spacing=0,
            padding=ft.Padding(left=0, right=0, top=0, bottom=0),
        )

        context_entry = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.LAYERS_OUTLINED, size=16, color=Colors.TEXT_SECONDARY),
                    ft.Text(
                        "上下文管理",
                        size=Fonts.SIZE_SM,
                        color=Colors.TEXT_SECONDARY,
                    ),
                    ft.Container(expand=True),
                    ft.Icon(ft.Icons.CHEVRON_RIGHT, size=14, color=Colors.TEXT_DISABLED),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=Spacing.SM,
            ),
            padding=ft.Padding(
                left=Spacing.LG, right=Spacing.MD,
                top=Spacing.MD, bottom=Spacing.MD,
            ),
            border=ft.Border(top=ft.BorderSide(1, Colors.DIVIDER)),
            on_click=lambda e: on_open_context_panel(),
        )

        kg_entry = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.ACCOUNT_TREE_OUTLINED, size=16,
                            color=Colors.TEXT_SECONDARY),
                    ft.Text(
                        "知识图谱",
                        size=Fonts.SIZE_SM,
                        color=Colors.TEXT_SECONDARY,
                    ),
                    ft.Container(expand=True),
                    ft.Icon(ft.Icons.CHEVRON_RIGHT, size=14,
                            color=Colors.TEXT_DISABLED),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=Spacing.SM,
            ),
            padding=ft.Padding(
                left=Spacing.LG, right=Spacing.MD,
                top=Spacing.MD, bottom=Spacing.MD,
            ),
            border=ft.Border(top=ft.BorderSide(1, Colors.DIVIDER)),
            on_click=lambda e: on_open_kg_panel() if on_open_kg_panel else None,
        )

        super().__init__(
            content=ft.Column(
                expand=True,
                spacing=0,
                controls=[
                    header,
                    new_btn,
                    ft.Container(
                        content=ft.Text(
                            "对话历史",
                            size=Fonts.SIZE_XS,
                            font_family=Fonts.MONO,
                            color=Colors.TEXT_DISABLED,
                        ),
                        padding=ft.Padding(
                            left=Spacing.LG, right=Spacing.LG,
                            top=Spacing.SM, bottom=Spacing.SM,
                        ),
                    ),
                    ft.Container(content=conversation_list, expand=True),
                    context_entry,
                    kg_entry,
                ],
            ),
            width=260,
            bgcolor=Colors.BG_SURFACE,
            border=ft.Border(
                right=ft.BorderSide(1, Colors.BORDER),
                top=ft.BorderSide(0, "transparent"),
                bottom=ft.BorderSide(0, "transparent"),
                left=ft.BorderSide(0, "transparent"),
            ),
        )

    def load_conversations(self, items: list) -> None:
        if not self._list_ref.current:
            return
        self._list_ref.current.controls.clear()
        for vm in items:
            self._list_ref.current.controls.append(
                ConversationItem(
                    session_id=vm.id,
                    title=vm.title,
                    preview=vm.preview,
                    updated_at=vm.updated_at,
                    is_active=(vm.id == self._active_id),
                    on_click=self._on_switch,
                    on_delete=self._on_delete,
                )
            )
        self._list_ref.current.update()

    def set_active(self, session_id: str) -> None:
        self._active_id = session_id
        if not self._list_ref.current:
            return
        for item in self._list_ref.current.controls:
            if isinstance(item, ConversationItem):
                is_active = item.session_id == session_id
                item.bgcolor = Colors.BG_OVERLAY if is_active else "transparent"
                item.border = (
                    ft.Border(
                        left=ft.BorderSide(2, Colors.PRIMARY),
                        top=ft.BorderSide(0, "transparent"),
                        right=ft.BorderSide(0, "transparent"),
                        bottom=ft.BorderSide(0, "transparent"),
                    )
                    if is_active else
                    ft.Border(
                        left=ft.BorderSide(2, "transparent"),
                        top=ft.BorderSide(0, "transparent"),
                        right=ft.BorderSide(0, "transparent"),
                        bottom=ft.BorderSide(1, Colors.DIVIDER),
                    )
                )
        self._list_ref.current.update()