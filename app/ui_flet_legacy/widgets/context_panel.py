# Layer: UI → widgets
# File: app/ui/widgets/context_panel.py
# Flet 0.85 兼容版本

from __future__ import annotations
from typing import Callable
import flet as ft
from app.ui.theme import Colors, Fonts, Spacing, Radius, Borders


class _ContextBlockItem(ft.Container):
    def __init__(
        self,
        block_id: str,
        label: str,
        preview: str,
        on_remove: Callable[[str], None],
        on_toggle: Callable[[str, bool], None],
        enabled: bool = True,
    ) -> None:
        self._block_id = block_id
        self._toggle_ref = ft.Ref[ft.Switch]()

        sw = ft.Switch(
            ref=self._toggle_ref,
            value=enabled,
            active_color=Colors.PRIMARY,
            scale=0.75,
        )
        sw.on_change = lambda e: on_toggle(block_id, e.control.value)

        super().__init__(
            content=ft.Row(
                controls=[
                    sw,
                    ft.Column(
                        expand=True,
                        spacing=2,
                        controls=[
                            ft.Text(
                                label,
                                size=Fonts.SIZE_SM,
                                color=Colors.TEXT_PRIMARY,
                                weight=ft.FontWeight.W_500,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Text(
                                preview,
                                size=Fonts.SIZE_XS,
                                color=Colors.TEXT_DISABLED,
                                max_lines=2,
                                overflow=ft.TextOverflow.ELLIPSIS,
                                font_family=Fonts.MONO,
                            ),
                        ],
                    ),
                    ft.IconButton(
                        icon=ft.Icons.REMOVE_CIRCLE_OUTLINE,
                        icon_size=16,
                        icon_color=Colors.ERROR,
                        tooltip="移除",
                        style=ft.ButtonStyle(
                            padding=ft.Padding(left=4, right=4, top=4, bottom=4),
                        ),
                        on_click=lambda e: on_remove(block_id),
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=Spacing.SM,
            ),
            padding=ft.Padding(
                left=Spacing.SM, right=Spacing.SM,
                top=Spacing.SM, bottom=Spacing.SM,
            ),
            border=ft.Border(bottom=ft.BorderSide(1, Colors.DIVIDER)),
        )


class _TemplateItem(ft.Container):
    def __init__(
        self,
        template_id: str,
        name: str,
        description: str,
        on_apply: Callable[[str], None],
        on_delete: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.DESCRIPTION_OUTLINED, size=16, color=Colors.PRIMARY),
                    ft.Column(
                        expand=True,
                        spacing=2,
                        controls=[
                            ft.Text(name, size=Fonts.SIZE_SM, color=Colors.TEXT_PRIMARY),
                            ft.Text(
                                description,
                                size=Fonts.SIZE_XS,
                                color=Colors.TEXT_DISABLED,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                        ],
                    ),
                    ft.ElevatedButton(
                        content=ft.Text(
                            "应用",
                            size=Fonts.SIZE_XS,
                            color=Colors.PRIMARY,
                            font_family=Fonts.MONO,
                        ),
                        style=ft.ButtonStyle(
                            bgcolor="transparent",
                            overlay_color=Colors.PRIMARY_GLOW,
                            side=ft.BorderSide(1, Colors.PRIMARY),
                            shape=ft.RoundedRectangleBorder(radius=4),
                            padding=ft.Padding(left=10, right=10, top=4, bottom=4),
                            elevation=0,
                        ),
                        on_click=lambda e: on_apply(template_id),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.DELETE_OUTLINE,
                        icon_size=14,
                        icon_color=Colors.ERROR,
                        tooltip="删除模板",
                        style=ft.ButtonStyle(
                            padding=ft.Padding(left=4, right=4, top=4, bottom=4),
                        ),
                        on_click=lambda e: on_delete(template_id) if on_delete else None,
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=Spacing.SM,
            ),
            padding=ft.Padding(
                left=Spacing.MD, right=Spacing.MD,
                top=Spacing.SM, bottom=Spacing.SM,
            ),
            border=ft.Border(bottom=ft.BorderSide(1, Colors.DIVIDER)),
        )


class ContextPanel(ft.Container):
    """
    上下文管理面板（右侧抽屉）。

    公开接口：show() / hide() / load_blocks() / load_templates() / set_preview()
    """

    def __init__(
        self,
        on_remove_block: Callable[[str], None],
        on_toggle_block: Callable[[str, bool], None],
        on_apply_template: Callable[[str], None],
        on_delete_template: Callable[[str], None] | None = None,
        on_close: Callable[[], None] = lambda: None,
        on_add_text_block: Callable[[str], None] = lambda x: None,
    ) -> None:
        self._on_remove_block = on_remove_block
        self._on_toggle_block = on_toggle_block
        self._on_apply_template = on_apply_template
        self._on_delete_template = on_delete_template
        self._on_close = on_close
        self._blocks_list_ref = ft.Ref[ft.ListView]()
        self._templates_list_ref = ft.Ref[ft.ListView]()
        self._preview_ref = ft.Ref[ft.Text]()
        self._new_block_tf_ref = ft.Ref[ft.TextField]()

        header = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.LAYERS_OUTLINED, size=18, color=Colors.PRIMARY),
                    ft.Text(
                        "上下文管理",
                        size=Fonts.SIZE_LG,
                        color=Colors.TEXT_PRIMARY,
                        weight=ft.FontWeight.W_600,
                        font_family=Fonts.MONO,
                    ),
                    ft.Container(expand=True),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        icon_size=18,
                        icon_color=Colors.TEXT_SECONDARY,
                        tooltip="关闭",
                        on_click=lambda e: on_close(),
                        style=ft.ButtonStyle(
                            padding=ft.Padding(left=6, right=6, top=6, bottom=6),
                        ),
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=Spacing.SM,
            ),
            padding=ft.Padding(
                left=Spacing.LG, right=Spacing.MD,
                top=Spacing.MD, bottom=Spacing.MD,
            ),
            border=Borders.BOTTOM_ONLY,
        )

        blocks_section = ft.Column(
            spacing=0,
            controls=[
                ft.Container(
                    content=ft.Text(
                        "已选上下文块",
                        size=Fonts.SIZE_XS,
                        font_family=Fonts.MONO,
                        color=Colors.TEXT_DISABLED,
                    ),
                    padding=ft.Padding(
                        left=Spacing.LG, right=Spacing.LG,
                        top=Spacing.MD, bottom=Spacing.SM,
                    ),
                ),
                ft.ListView(
                    ref=self._blocks_list_ref,
                    height=200,
                    spacing=0,
                    padding=ft.Padding(
                        left=Spacing.MD, right=Spacing.MD,
                        top=0, bottom=0,
                    ),
                ),
            ],
        )

        new_block_tf = ft.TextField(
            ref=self._new_block_tf_ref,
            hint_text="输入自定义上下文内容...",
            hint_style=ft.TextStyle(color=Colors.TEXT_DISABLED, size=Fonts.SIZE_SM),
            multiline=True,
            min_lines=2,
            max_lines=5,
            expand=True,
            border_color=Colors.BORDER,
            focused_border_color=Colors.PRIMARY,
            bgcolor=Colors.BG_ELEVATED,
            text_style=ft.TextStyle(color=Colors.TEXT_PRIMARY, size=Fonts.SIZE_SM),
            border_radius=8,
            content_padding=ft.Padding(
                left=Spacing.MD, right=Spacing.MD,
                top=Spacing.SM, bottom=Spacing.SM,
            ),
        )

        self._template_name_ref = ft.Ref[ft.TextField]()

        save_template_section = ft.Container(
            content=ft.Row(
                controls=[
                    ft.TextField(
                        ref=self._template_name_ref,
                        hint_text="输入模板名称，保存当前块为模板...",
                        hint_style=ft.TextStyle(color=Colors.TEXT_DISABLED, size=Fonts.SIZE_XS),
                        expand=True,
                        border_color=Colors.BORDER,
                        focused_border_color=Colors.PRIMARY,
                        bgcolor=Colors.BG_ELEVATED,
                        text_style=ft.TextStyle(color=Colors.TEXT_PRIMARY, size=Fonts.SIZE_XS),
                        border_radius=6,
                        content_padding=ft.Padding(left=8, right=8, top=6, bottom=6),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.BOOKMARK_ADD_OUTLINED,
                        icon_color=Colors.ACCENT,
                        icon_size=18,
                        tooltip="保存为模板",
                        on_click=lambda e: self._handle_save_template(on_apply_template),
                    ),
                ],
                spacing=Spacing.SM,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding(left=Spacing.LG, right=Spacing.LG, top=Spacing.SM, bottom=Spacing.SM),
            border=Borders.BOTTOM_ONLY,
        )

        add_block_section = ft.Container(
            content=ft.Column(
                spacing=Spacing.SM,
                controls=[
                    ft.Text(
                        "添加文本块",
                        size=Fonts.SIZE_XS,
                        font_family=Fonts.MONO,
                        color=Colors.TEXT_DISABLED,
                    ),
                    ft.Row(
                        controls=[
                            new_block_tf,
                            ft.IconButton(
                                icon=ft.Icons.ADD_CIRCLE_OUTLINE,
                                icon_color=Colors.PRIMARY,
                                icon_size=20,
                                tooltip="添加",
                                on_click=lambda e: self._handle_add_block(on_add_text_block),
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.END,
                        spacing=Spacing.SM,
                    ),
                ],
            ),
            padding=ft.Padding(
                left=Spacing.LG, right=Spacing.LG,
                top=Spacing.MD, bottom=Spacing.MD,
            ),
            border=Borders.BOTTOM_ONLY,
        )

        preview_section = ft.Column(
            spacing=0,
            controls=[
                ft.Container(
                    content=ft.Text(
                        "拼接预览",
                        size=Fonts.SIZE_XS,
                        font_family=Fonts.MONO,
                        color=Colors.TEXT_DISABLED,
                    ),
                    padding=ft.Padding(
                        left=Spacing.LG, right=Spacing.LG,
                        top=Spacing.MD, bottom=Spacing.SM,
                    ),
                ),
                ft.Container(
                    content=ft.Text(
                        ref=self._preview_ref,
                        value="（尚无上下文）",
                        size=Fonts.SIZE_XS,
                        font_family=Fonts.MONO,
                        color=Colors.TEXT_CODE,
                    ),
                    height=120,
                    padding=ft.Padding(
                        left=Spacing.LG, right=Spacing.LG,
                        top=Spacing.SM, bottom=Spacing.SM,
                    ),
                    bgcolor=Colors.BG_BASE,
                    border=ft.Border(
                        top=ft.BorderSide(1, Colors.DIVIDER),
                        bottom=ft.BorderSide(1, Colors.DIVIDER),
                        left=ft.BorderSide(2, Colors.PRIMARY_DIM),
                        right=ft.BorderSide(1, Colors.DIVIDER),
                    ),
                ),
            ],
        )

        templates_section = ft.Column(
            spacing=0,
            controls=[
                ft.Container(
                    content=ft.Text(
                        "模板库",
                        size=Fonts.SIZE_XS,
                        font_family=Fonts.MONO,
                        color=Colors.TEXT_DISABLED,
                    ),
                    padding=ft.Padding(
                        left=Spacing.LG, right=Spacing.LG,
                        top=Spacing.MD, bottom=Spacing.SM,
                    ),
                ),
                ft.ListView(
                    ref=self._templates_list_ref,
                    height=180,
                    spacing=0,
                    padding=ft.Padding(left=0, right=0, top=0, bottom=0),
                ),
            ],
        )

        super().__init__(
            content=ft.Column(
                controls=[
                    header,
                    blocks_section,
                    save_template_section,
                    add_block_section,
                    preview_section,
                    templates_section,
                ],
                spacing=0,
                scroll=ft.ScrollMode.AUTO,
            ),
            width=380,
            bgcolor=Colors.BG_SURFACE,
            border=ft.Border(
                left=ft.BorderSide(1, Colors.BORDER),
                top=ft.BorderSide(0, "transparent"),
                right=ft.BorderSide(0, "transparent"),
                bottom=ft.BorderSide(0, "transparent"),
            ),
            visible=False,
        )

    def _handle_save_template(self, on_apply) -> None:
        """把当前所有块保存为新模板（名称由用户输入）。"""
        if not self._template_name_ref.current:
            return
        name = self._template_name_ref.current.value.strip()
        if not name:
            return
        # 通知外部（app.py 会调用 controller 保存）
        # 这里通过一个特殊 id 传递保存指令
        on_apply(f"__save__:{name}")
        self._template_name_ref.current.value = ""
        self._template_name_ref.current.update()

    def _handle_add_block(self, callback: Callable[[str], None]) -> None:
        if not self._new_block_tf_ref.current:
            return
        text = self._new_block_tf_ref.current.value.strip()
        if text:
            callback(text)
            self._new_block_tf_ref.current.value = ""
            self._new_block_tf_ref.current.update()

    def show(self) -> None:
        self.visible = True
        self.update()

    def hide(self) -> None:
        self.visible = False
        self.update()

    def load_blocks(self, items: list) -> None:
        if not self._blocks_list_ref.current:
            return
        self._blocks_list_ref.current.controls.clear()
        for vm in items:
            self._blocks_list_ref.current.controls.append(
                _ContextBlockItem(
                    block_id=vm.id,
                    label=vm.label,
                    preview=vm.preview,
                    enabled=getattr(vm, "enabled", True),
                    on_remove=self._on_remove_block,
                    on_toggle=self._on_toggle_block,
                )
            )
        self._blocks_list_ref.current.update()

    def load_templates(self, items: list) -> None:
        if not self._templates_list_ref.current:
            return
        self._templates_list_ref.current.controls.clear()
        for vm in items:
            self._templates_list_ref.current.controls.append(
                _TemplateItem(
                    template_id=vm.id,
                    name=vm.name,
                    description=getattr(vm, "description", ""),
                    on_apply=self._on_apply_template,
                    on_delete=self._on_delete_template,
                )
            )
        self._templates_list_ref.current.update()

    def set_preview(self, text: str) -> None:
        if self._preview_ref.current:
            self._preview_ref.current.value = text or "（尚无上下文）"
            self._preview_ref.current.update()