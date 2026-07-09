# Layer: UI → widgets
# File: app/ui/widgets/tree_panel.py
# Responsibility: 树形面板控件 — 使用 ExpansionTile 渲染完整对话目录树，
#                 支持右键菜单、拖拽移动、展开/折叠、启用状态显示。
#                 Phase 4 — 替换侧边栏原有的扁平对话列表。
# Input:  list[TreeNodeVM] — 预计算好的 DFS 排序节点列表
# Output: 通过回调将用户交互事件传递给上层 (Sidebar → ChatApp → Controller)

from __future__ import annotations
from typing import Callable

import flet as ft

from app.controllers.view_models import TreeNodeVM
from app.ui.theme import Colors, Fonts, Spacing


class TreePanel(ft.Container):
    """
    树形面板 — 渲染完整的对话目录树。

    公开方法：
        load_tree(nodes)  — 加载/刷新树数据
        set_active(node_id) — 高亮指定节点
        set_page(page)     — 存储 Page 引用（用于弹出对话框）

    回调（全部通过构造函数注入）：
        on_switch_conversation(node_id)
        on_new_conversation(parent_id)
        on_new_folder(parent_id)
        on_rename_node(node_id)
        on_delete_node(node_id)
        on_toggle_enabled(node_id)
        on_move_node(node_id, target_parent_id)
        on_manage_context(folder_id)
        on_attach_file(folder_id)
    """

    def __init__(
        self,
        on_switch_conversation: Callable[[str], None],
        on_new_conversation: Callable[[str | None], None],
        on_new_folder: Callable[[str | None], None],
        on_rename_node: Callable[[str], None],
        on_delete_node: Callable[[str], None],
        on_toggle_enabled: Callable[[str], None],
        on_move_node: Callable[[str, str], None],
        on_manage_context: Callable[[str], None],
        on_attach_file: Callable[[str], None],
    ) -> None:
        self._on_switch = on_switch_conversation
        self._on_new_conv = on_new_conversation
        self._on_new_folder = on_new_folder
        self._on_rename = on_rename_node
        self._on_delete = on_delete_node
        self._on_toggle = on_toggle_enabled
        self._on_move = on_move_node
        self._on_manage_ctx = on_manage_context
        self._on_attach = on_attach_file

        self._active_id: str = ""
        self._tree_data: list[TreeNodeVM] = []
        self._expanded_ids: set[str] = set()
        self._page: ft.Page | None = None

        # 树内容容器引用
        self._tree_content_ref = ft.Ref[ft.Column]()

        # 初始空状态
        empty_text = ft.Text(
            "暂无对话",
            size=Fonts.SIZE_SM,
            color=Colors.TEXT_DISABLED,
        )

        tree_content = ft.Column(
            ref=self._tree_content_ref,
            spacing=0,
            controls=[empty_text],
        )

        super().__init__(
            content=ft.ListView(
                controls=[tree_content],
                expand=True,
                spacing=0,
                padding=ft.Padding(left=0, right=0, top=0, bottom=0),
            ),
            expand=True,
        )

    # ──────────────────────────────────────────
    # 公开接口
    # ──────────────────────────────────────────

    def load_tree(self, nodes: list[TreeNodeVM]) -> None:
        """
        加载/刷新树数据。

        Args:
            nodes: DFS 排序的 TreeNodeVM 列表
        """
        self._tree_data = nodes
        self._rebuild()

    def set_active(self, session_id: str) -> None:
        """高亮指定节点为激活状态。"""
        self._active_id = session_id
        self._rebuild()

    def set_page(self, page: ft.Page) -> None:
        """存储 Page 引用，用于弹出上下文菜单和对话框。"""
        self._page = page

    # ──────────────────────────────────────────
    # 树重建
    # ──────────────────────────────────────────

    def _rebuild(self) -> None:
        """完全重建树控件。"""
        if not self._tree_content_ref.current:
            return

        column = self._tree_content_ref.current
        column.controls.clear()

        if not self._tree_data:
            column.controls.append(
                ft.Text(
                    "暂无对话",
                    size=Fonts.SIZE_SM,
                    color=Colors.TEXT_DISABLED,
                )
            )
        else:
            widgets = self._build_tree_widgets(self._tree_data)
            for w in widgets:
                column.controls.append(w)

        column.update()

    def _build_tree_widgets(self, nodes: list[TreeNodeVM]) -> list[ft.Control]:
        """
        将 DFS 排序的平面 TreeNodeVM 列表转换为嵌套的 ExpansionTile 控件列表。

        算法：
        1. 构建 child_map: parent_id → children 列表
        2. 递归 build_subtree(parent_id, depth):
           - 对每个子节点，调用 _build_node_row 创建行控件
           - 如果节点有子节点：包裹在 ExpansionTile 中，递归子节点
           - 如果是叶子：直接返回行控件
        """
        if not nodes:
            return []

        # 构建 children 映射
        child_map: dict[str | None, list[TreeNodeVM]] = {}
        for node in nodes:
            pid = node.parent_id
            if pid not in child_map:
                child_map[pid] = []
            child_map[pid].append(node)

        # 按 sort_order 排序
        for pid in child_map:
            child_map[pid].sort(key=lambda n: n.sort_order)

        def build_subtree(parent_id: str | None, depth: int) -> list[ft.Control]:
            pid = parent_id
            children = child_map.get(pid, [])
            result: list[ft.Control] = []

            for node in children:
                has_children = node.has_children
                row = self._build_node_row(node, depth)

                if has_children:
                    child_controls = build_subtree(node.id, depth + 1)
                    initially_expanded = node.id in self._expanded_ids

                    tile = ft.ExpansionTile(
                        title=row,
                        controls=child_controls,
                        expanded=initially_expanded,
                        collapsed_text_color=Colors.TEXT_SECONDARY,
                        text_color=Colors.TEXT_PRIMARY,
                        trailing=ft.Icon(
                            ft.Icons.CHEVRON_RIGHT,
                            size=16,
                            color=Colors.TEXT_DISABLED,
                        ),
                        on_change=lambda e, nid=node.id: self._on_expand_change(
                            nid, e
                        ),
                        controls_padding=ft.Padding(
                            left=0, right=0, top=0, bottom=0
                        ),
                    )
                    result.append(tile)
                else:
                    result.append(row)

            return result

        return build_subtree(None, 0)

    def _on_expand_change(self, node_id: str, e) -> None:
        """跟踪展开/折叠状态。"""
        # Flet ExpansionTile.on_change 传入 ControlEvent，data 为 "true"/"false"
        is_expanded = getattr(e, "data", None)
        if is_expanded == "true":
            self._expanded_ids.add(node_id)
        elif is_expanded == "false":
            self._expanded_ids.discard(node_id)

    # ──────────────────────────────────────────
    # 节点行构建
    # ──────────────────────────────────────────

    def _build_node_row(
        self,
        node: TreeNodeVM,
        depth: int,
    ) -> ft.Container:
        """
        构建单行节点控件（不含 ExpansionTile 包裹）。

        Args:
            node:  节点视图模型
            depth: 缩进深度

        Returns:
            ft.Container — 节点行控件
        """
        is_active = (node.id == self._active_id)

        # ── 节点类型图标 ──
        if node.node_type == "folder":
            type_icon = "📁"
        elif node.node_type == "message":
            role_icons = {
                "user": "👤", "assistant": "🤖",
                "thinking": "🧠", "system": "⚙️",
            }
            type_icon = role_icons.get(node.role, "💬")
        else:
            type_icon = "📄"

        # ── 复选框状态指示器（Phase 5: 替换 emoji 文本）──
        if node.enabled is True:
            checkbox_icon = ft.Icon(
                ft.Icons.CHECK_BOX,
                size=16,
                color=Colors.CHECKBOX_ENABLED,
                tooltip="已启用 — 点击禁用",
            )
        elif node.enabled is False:
            checkbox_icon = ft.Icon(
                ft.Icons.CHECK_BOX_OUTLINE_BLANK,
                size=16,
                color=Colors.CHECKBOX_DISABLED,
                tooltip="已禁用 — 点击启用",
            )
        else:  # "some"
            checkbox_icon = ft.Icon(
                ft.Icons.INDETERMINATE_CHECK_BOX,
                size=16,
                color=Colors.CHECKBOX_SOME,
                tooltip="部分启用 — 点击全部启用",
            )

        # 复选框可点击切换
        checkbox_btn = ft.Container(
            content=checkbox_icon,
            on_click=lambda e, nid=node.id: self._safe_callback(
                "toggle_checkbox", self._on_toggle, nid
            ),
            padding=ft.Padding(left=0, right=4, top=0, bottom=0),
        )

        # ── 工具提示 ──
        tooltip = node.title
        if node.enabled is False:
            tooltip += "\n状态: 已禁用"
        elif node.enabled == "some":
            tooltip += "\n状态: 部分启用"
        else:
            tooltip += "\n状态: ✅ 已启用"

        # ── 消息计数（仅对话节点）──
        count_text = ""
        if node.node_type == "conversation" and node.message_count > 0:
            count_text = f" ({node.message_count})"

        # ── 显示标题（消息节点使用 preview）──
        display_title = node.preview or node.title if node.node_type == "message" else node.title

        # ── 行内容 ──
        row = ft.Row(
            controls=[
                ft.Container(width=max(depth * 18, 0)),  # 缩进
                checkbox_btn,                               # 复选框
                ft.Text(type_icon, size=14),                # 类型图标
                ft.Text(
                    f"{display_title}{count_text}",
                    size=Fonts.SIZE_SM,
                    color=(
                        Colors.TEXT_PRIMARY
                        if is_active
                        else Colors.TEXT_SECONDARY
                    ),
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    expand=True,
                ),
                # 时间戳
                ft.Text(
                    node.updated_at,
                    size=Fonts.SIZE_XS,
                    color=Colors.TEXT_DISABLED,
                    font_family=Fonts.MONO,
                ) if node.updated_at else ft.Container(),
            ],
            spacing=Spacing.SM,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # 左侧强调边框
        border = ft.Border(
            left=ft.BorderSide(
                2, Colors.PRIMARY if is_active else "transparent"
            ),
            top=ft.BorderSide(0, "transparent"),
            bottom=ft.BorderSide(0, "transparent"),
            right=ft.BorderSide(0, "transparent"),
        )

        container = ft.Container(
            content=row,
            padding=ft.Padding(
                left=Spacing.SM, right=Spacing.SM,
                top=6, bottom=6,
            ),
            bgcolor=Colors.BG_OVERLAY if is_active else "transparent",
            border=border,
            tooltip=tooltip,
            on_click=lambda e: self._on_node_click(node),
        )

        # 包裹 ContextMenu（右键菜单）
        wrapped = self._wrap_context_menu(node, container)

        # 消息节点不支持拖拽
        if node.node_type != "message":
            wrapped = self._wrap_drag(node, wrapped)

        return wrapped

    # ──────────────────────────────────────────
    # 右键菜单
    # ──────────────────────────────────────────

    def _wrap_context_menu(
        self,
        node: TreeNodeVM,
        content: ft.Control,
    ) -> ft.Control:
        """为节点行包裹 ContextMenu（右键菜单）。"""
        nid = node.id
        ntype = node.node_type

        items: list[ft.PopupMenuItem] = []

        if ntype == "folder":
            # 目录节点专属操作
            items.append(
                ft.PopupMenuItem(
                    content=ft.Row([
                        ft.Icon(ft.Icons.CREATE_NEW_FOLDER, size=16),
                        ft.Text("新建文件夹", size=Fonts.SIZE_SM),
                    ]),
                    on_click=lambda e, pid=nid: self._safe_callback(
                        "new_folder", self._on_new_folder, pid
                    ),
                )
            )
            items.append(
                ft.PopupMenuItem(
                    content=ft.Row([
                        ft.Icon(ft.Icons.ADD_COMMENT, size=16),
                        ft.Text("新建对话", size=Fonts.SIZE_SM),
                    ]),
                    on_click=lambda e, pid=nid: self._safe_callback(
                        "new_conversation", self._on_new_conv, pid
                    ),
                )
            )
            items.append(ft.PopupMenuItem())  # 分割线

        # 消息节点只有简化菜单
        if ntype == "message":
            # 启用/禁用切换
            items.append(
                ft.PopupMenuItem(
                    content=ft.Row([
                        ft.Icon(
                            ft.Icons.TOGGLE_ON
                            if node.enabled is True
                            else ft.Icons.TOGGLE_OFF,
                            size=16,
                        ),
                        ft.Text(
                            "禁用" if node.enabled is True else "启用",
                            size=Fonts.SIZE_SM,
                        ),
                    ]),
                    on_click=lambda e, nid=nid: self._safe_callback(
                        "toggle", self._on_toggle, nid
                    ),
                )
            )
            items.append(ft.PopupMenuItem())
        else:
            # 文件夹和对话节点的通用操作
            items.append(
                ft.PopupMenuItem(
                    content=ft.Row([
                        ft.Icon(ft.Icons.EDIT, size=16),
                        ft.Text("重命名", size=Fonts.SIZE_SM),
                    ]),
                    on_click=lambda e, nid=nid: self._safe_callback(
                        "rename", self._on_rename, nid
                    ),
                )
            )
            items.append(
                ft.PopupMenuItem(
                    content=ft.Row([
                        ft.Icon(
                            ft.Icons.TOGGLE_ON
                            if node.enabled is True
                            else ft.Icons.TOGGLE_OFF,
                            size=16,
                        ),
                        ft.Text(
                            "禁用" if node.enabled is True else "启用",
                            size=Fonts.SIZE_SM,
                        ),
                    ]),
                    on_click=lambda e, nid=nid: self._safe_callback(
                        "toggle", self._on_toggle, nid
                    ),
                )
            )
            items.append(ft.PopupMenuItem())  # 分割线

        if ntype == "folder":
            items.append(
                ft.PopupMenuItem(
                    content=ft.Row([
                        ft.Icon(ft.Icons.LAYERS_OUTLINED, size=16),
                        ft.Text("管理上下文块", size=Fonts.SIZE_SM),
                    ]),
                    on_click=lambda e, nid=nid: self._safe_callback(
                        "manage_context", self._on_manage_ctx, nid
                    ),
                )
            )
            items.append(
                ft.PopupMenuItem(
                    content=ft.Row([
                        ft.Icon(ft.Icons.ATTACH_FILE, size=16),
                        ft.Text("添加附件", size=Fonts.SIZE_SM),
                    ]),
                    on_click=lambda e, nid=nid: self._safe_callback(
                        "attach_file", self._on_attach, nid
                    ),
                )
            )
            items.append(ft.PopupMenuItem())

        # 删除（所有节点通用）
        items.append(
            ft.PopupMenuItem(
                content=ft.Row([
                    ft.Icon(ft.Icons.DELETE, size=16, color=Colors.ERROR),
                    ft.Text("删除", size=Fonts.SIZE_SM, color=Colors.ERROR),
                ]),
                on_click=lambda e, nid=nid: self._safe_callback(
                    "delete", self._on_delete, nid
                ),
            )
        )

        return ft.ContextMenu(
            content=content,
            secondary_items=items,
        )

    # ──────────────────────────────────────────
    # 拖拽支持
    # ──────────────────────────────────────────

    def _wrap_drag(
        self,
        node: TreeNodeVM,
        content: ft.Control,
    ) -> ft.Control:
        """
        为节点行包裹 Draggable（拖拽源），目录节点额外包裹 DragTarget（投放目标）。
        """
        nid = node.id
        ntype = node.node_type

        # 拖拽反馈控件
        feedback = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Text(
                        "📁" if ntype == "folder" else "📄",
                        size=14,
                    ),
                    ft.Text(
                        node.title,
                        size=Fonts.SIZE_SM,
                        color=Colors.TEXT_PRIMARY,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
                spacing=Spacing.SM,
            ),
            bgcolor=Colors.BG_OVERLAY,
            padding=ft.Padding(
                left=Spacing.SM, right=Spacing.SM,
                top=Spacing.XS, bottom=Spacing.XS,
            ),
            border_radius=ft.BorderRadius(
                top_left=8, top_right=8,
                bottom_left=8, bottom_right=8,
            ),
            opacity=0.85,
        )

        draggable = ft.Draggable(
            group="tree_drag",
            content=content,
            content_feedback=feedback,
            content_when_dragging=ft.Container(
                content=content,
                opacity=0.3,
            ),
            data=nid,
        )

        # 通过闭包捕获 node_id，在 DragTarget.on_accept 中使用
        if ntype == "folder":
            return ft.DragTarget(
                group="tree_drag",
                content=draggable,
                on_accept=lambda e: self._on_drop(e, nid),
                on_will_accept=lambda e: None,
                on_leave=lambda e: None,
            )

        return draggable

    def _on_drop(self, e: ft.DragTargetAcceptEvent, target_folder_id: str) -> None:
        """
        处理拖拽投放：将被拖拽节点移动到目标目录下。

        Args:
            e:                  DragTargetAcceptEvent
            target_folder_id:   目标目录节点 ID
        """
        # 从事件的 data 属性获取源节点 ID（在 _wrap_drag 中设置）
        dragged_id = getattr(e, "data", None)

        if dragged_id is None:
            print("[TREE] 拖拽投放：无法确定源节点 (e.data 为空)")
            return

        if dragged_id == target_folder_id:
            print("[TREE] 拖拽投放：不能将节点移到自身")
            return

        print(f"[TREE] 拖拽移动: {dragged_id} -> 目录 {target_folder_id}")
        self._on_move(dragged_id, target_folder_id)

    # ──────────────────────────────────────────
    # 节点交互
    # ──────────────────────────────────────────

    def _safe_callback(self, name: str, cb: Callable, *args) -> None:
        """安全调用回调，捕获异常并用 SnackBar 提示用户。"""
        try:
            print(f"[TREE] _safe_callback: 调用 {name}({args})")
            cb(*args)
            print(f"[TREE] _safe_callback: {name} 完成")
        except Exception as ex:
            print(f"[TREE] 回调异常 {name}: {ex}")
            import traceback
            traceback.print_exc()
            if self._page:
                self._page.show_snack_bar(
                    ft.SnackBar(
                        content=ft.Text(f"操作失败: {ex}",
                                        color=Colors.TEXT_PRIMARY),
                        bgcolor=Colors.ERROR,
                        duration=4000,
                    )
                )

    def _on_node_click(self, node: TreeNodeVM) -> None:
        """
        节点点击处理：
        - 对话节点 / 消息节点 → 加载该节点对应的消息列表
        - 目录节点 → 切换展开/折叠（由 ExpansionTile 处理）
        """
        if node.node_type in ("conversation", "message"):
            print(f"[TREE] 点击节点加载消息: {node.id} ({node.title[:30] if node.title else ''})")
            self._safe_callback("switch_conversation",
                               self._on_switch, node.id)
        elif node.node_type == "folder":
            # 目录节点的展开/折叠由 ExpansionTile.on_change 自动处理
            # 点击目录也加载其下所有消息
            pass
