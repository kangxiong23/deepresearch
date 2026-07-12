# Layer: UI → widgets
# File: app/ui/widgets/sidebar.py
# Flet 0.85 兼容版本
# Phase 4 — 树形面板替代原有的扁平对话列表

from __future__ import annotations
from typing import Callable
import flet as ft
from app.ui.theme import Colors, Fonts, Spacing, Radius, Borders


# ──────────────────────────────────────────────
# ConversationItem（保留，供潜在复用）
# ──────────────────────────────────────────────

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


# ──────────────────────────────────────────────
# Sidebar（Phase 4 — 集成 TreePanel）
# ──────────────────────────────────────────────

class Sidebar(ft.Container):
    """
    左侧边栏（Phase 4 — 树形面板替代扁平对话列表）。

    公开接口：
        load_tree(nodes: list[TreeNodeVM]) -> None
        set_active(session_id: str) -> None
        set_page(page: ft.Page) -> None
    """

    def __init__(
        self,
        on_new_conversation: Callable[[], None],
        on_switch_conversation: Callable[[str], None],
        on_delete_conversation: Callable[[str], None],
        on_open_context_panel: Callable[[], None],
        on_open_kg_panel: Callable[[], None] | None = None,
        # Phase 4 — 树操作回调
        on_create_folder: Callable[[str | None, str], None] | None = None,
        on_new_conversation_in_folder: Callable[[str | None], None] | None = None,
        on_rename_node: Callable[[str], None] | None = None,
        on_delete_node: Callable[[str], None] | None = None,
        on_toggle_enabled: Callable[[str], None] | None = None,
        on_move_node: Callable[[str, str], None] | None = None,
        on_manage_context: Callable[[str], None] | None = None,
        on_attach_file: Callable[[str], None] | None = None,
        on_open_trash: Callable[[], None] | None = None,
    ) -> None:
        self._on_switch = on_switch_conversation
        self._on_delete = on_delete_conversation
        self._active_id: str = ""

        # ── 树形面板 ────────────────────────────
        from app.ui.widgets.tree_panel import TreePanel

        self._tree_panel = TreePanel(
            on_switch_conversation=on_switch_conversation,
            on_new_conversation=on_new_conversation_in_folder
            or (lambda pid: None),
            on_new_folder=(
                (lambda pid: on_create_folder(pid, "新文件夹"))
                if on_create_folder
                else (lambda pid: None)
            ),
            on_rename_node=on_rename_node or (lambda nid: None),
            on_delete_node=on_delete_node or (lambda nid: None),
            on_toggle_enabled=on_toggle_enabled or (lambda nid: None),
            on_move_node=on_move_node or (lambda src, dst: None),
            on_manage_context=on_manage_context or (lambda fid: None),
            on_attach_file=on_attach_file or (lambda fid: None),
        )

        # ── 头部 ──────────────────────────────────
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

        # ── 按钮行（新建对话 + 新建文件夹）──────
        btn_style = ft.ButtonStyle(
            bgcolor=Colors.PRIMARY,
            overlay_color=Colors.PRIMARY_DIM,
            shape=ft.RoundedRectangleBorder(radius=8),
            padding=ft.Padding(left=12, right=12, top=10, bottom=10),
            elevation=0,
        )

        new_btn_row = ft.Container(
            content=ft.Row(
                controls=[
                    ft.ElevatedButton(
                        content=ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.ADD, size=14, color=Colors.BG_BASE),
                                ft.Text(
                                    "对话",
                                    size=Fonts.SIZE_SM,
                                    color=Colors.BG_BASE,
                                    weight=ft.FontWeight.W_500,
                                ),
                            ],
                            tight=True,
                            spacing=Spacing.XS,
                            alignment=ft.MainAxisAlignment.CENTER,
                        ),
                        style=btn_style,
                        on_click=lambda e: on_new_conversation(),
                        expand=True,
                    ),
                    ft.ElevatedButton(
                        content=ft.Row(
                            controls=[
                                ft.Icon(
                                    ft.Icons.CREATE_NEW_FOLDER,
                                    size=14,
                                    color=Colors.BG_BASE,
                                ),
                                ft.Text(
                                    "文件夹",
                                    size=Fonts.SIZE_SM,
                                    color=Colors.BG_BASE,
                                    weight=ft.FontWeight.W_500,
                                ),
                            ],
                            tight=True,
                            spacing=Spacing.XS,
                            alignment=ft.MainAxisAlignment.CENTER,
                        ),
                        style=btn_style,
                        on_click=lambda e: (
                            on_create_folder(None, "新文件夹")
                            if on_create_folder
                            else None
                        ),
                        expand=True,
                    ),
                ],
                spacing=Spacing.SM,
            ),
            padding=ft.Padding(
                left=Spacing.LG, right=Spacing.LG,
                top=Spacing.MD, bottom=Spacing.MD,
            ),
        )

        # ── "对话历史" 标签 ───────────────────
        section_label = ft.Container(
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
        )

        # ── 回收站入口 ────────────────────────
        trash_entry = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(
                        ft.Icons.DELETE_OUTLINE,
                        size=16,
                        color=Colors.TEXT_SECONDARY,
                    ),
                    ft.Text(
                        "🗑️ 回收站",
                        size=Fonts.SIZE_SM,
                        color=Colors.TEXT_SECONDARY,
                    ),
                    ft.Container(expand=True),
                    ft.Icon(
                        ft.Icons.CHEVRON_RIGHT,
                        size=14,
                        color=Colors.TEXT_DISABLED,
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=Spacing.SM,
            ),
            padding=ft.Padding(
                left=Spacing.LG, right=Spacing.MD,
                top=Spacing.MD, bottom=Spacing.MD,
            ),
            border=ft.Border(top=ft.BorderSide(1, Colors.DIVIDER)),
            on_click=lambda e: on_open_trash() if on_open_trash else None,
        )

        # ── 上下文管理入口 ────────────────────
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

        # ── 知识图谱入口 ─────────────────────
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

        # ── 组装 ────────────────────────────
        super().__init__(
            content=ft.Column(
                expand=True,
                spacing=0,
                controls=[
                    header,
                    new_btn_row,
                    section_label,
                    ft.Container(content=self._tree_panel, expand=True),
                    trash_entry,
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

    # ──────────────────────────────────────────
    # 公开接口
    # ──────────────────────────────────────────

    def load_tree(self, nodes: list) -> None:
        """加载/刷新树面板数据。"""
        self._tree_panel.load_tree(nodes)

    def set_active(self, session_id: str) -> None:
        """高亮激活的对话节点。"""
        self._active_id = session_id
        self._tree_panel.set_active(session_id)

    def set_page(self, page: ft.Page) -> None:
        """传递 Page 引用给 TreePanel（用于弹出对话框）。"""
        self._tree_panel.set_page(page)

    # ──────────────────────────────────────────
    # 向后兼容接口（扁平列表模式已弃用）
    # ──────────────────────────────────────────

    def load_conversations(self, items: list) -> None:
        """[已弃用] 使用 load_tree() 代替。"""
        # 转为调用 load_tree 兼容旧代码：将 ConversationVM 转为简单 TreeNodeVM
        from app.controllers.view_models import TreeNodeVM
        tree_nodes = [
            TreeNodeVM(
                id=vm.id,
                title=vm.title,
                node_type="conversation",
                parent_id=None,
                preview=vm.preview,
                updated_at=vm.updated_at,
            )
            for vm in items
        ]
        self._tree_panel.load_tree(tree_nodes)
