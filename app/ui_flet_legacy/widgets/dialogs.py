# Layer: UI → widgets
# File: app/ui/widgets/dialogs.py
# Responsibility: 可复用的对话框组件（自由函数，非类）。
#                 Phase 4 — 为树形面板提供重命名、新建目录、确认删除、
#                 上下文块管理、回收站管理等对话框。
# Input:  ft.Page + 回调函数
# Output: 通过 page.show_dialog(dlg) 弹出 AlertDialog

from __future__ import annotations
from typing import Callable

import flet as ft

from app.ui.theme import Colors, Fonts, Spacing


# ──────────────────────────────────────────────
# 重命名对话框
# ──────────────────────────────────────────────

def show_rename_dialog(
    page: ft.Page,
    current_title: str,
    on_confirm: Callable[[str], None],
) -> None:
    """
    弹出重命名对话框。

    Args:
        page:          Flet Page 实例
        current_title: 当前名称（预填）
        on_confirm:    确认回调，传入新名称字符串
    """
    text_field = ft.TextField(
        value=current_title,
        label="新名称",
        autofocus=True,
        border_color=Colors.PRIMARY,
        text_size=Fonts.SIZE_MD,
    )

    def _confirm(e, tf, dlg, cb):
        page.pop_dialog()
        new_val = tf.value.strip() if tf.value else ""
        if new_val:
            cb(new_val)

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("重命名", color=Colors.TEXT_PRIMARY, size=Fonts.SIZE_LG),
        content=text_field,
        actions=[
            ft.TextButton(
                "取消",
                on_click=lambda e: page.pop_dialog(),
            ),
            ft.TextButton(
                "确认",
                on_click=lambda e: _confirm(e, text_field, dlg, on_confirm),
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.show_dialog(dlg)


# ──────────────────────────────────────────────
# 新建目录对话框
# ──────────────────────────────────────────────

def show_new_folder_dialog(
    page: ft.Page,
    on_confirm: Callable[[str], None],
    default_title: str = "新文件夹",
) -> None:
    """
    弹出新建目录对话框。

    Args:
        page:          Flet Page 实例
        on_confirm:    确认回调，传入目录名称
        default_title: 默认目录名称
    """
    text_field = ft.TextField(
        value=default_title,
        label="目录名称",
        autofocus=True,
        border_color=Colors.PRIMARY,
        text_size=Fonts.SIZE_MD,
    )

    def _confirm(e, tf, dlg, cb):
        page.pop_dialog()
        new_val = tf.value.strip() if tf.value else ""
        if new_val:
            cb(new_val)

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("新建目录", color=Colors.TEXT_PRIMARY, size=Fonts.SIZE_LG),
        content=text_field,
        actions=[
            ft.TextButton(
                "取消",
                on_click=lambda e: page.pop_dialog(),
            ),
            ft.TextButton(
                "创建",
                on_click=lambda e: _confirm(e, text_field, dlg, on_confirm),
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.show_dialog(dlg)


# ──────────────────────────────────────────────
# 通用确认对话框
# ──────────────────────────────────────────────

def show_confirm_dialog(
    page: ft.Page,
    title: str,
    message: str,
    on_confirm: Callable[[], None],
    confirm_text: str = "确认",
    danger: bool = True,
) -> None:
    """
    弹出通用确认对话框。

    Args:
        page:         Flet Page 实例
        title:        对话框标题
        message:      对话框内容
        on_confirm:   确认回调
        confirm_text: 确认按钮文字
        danger:       是否为危险操作（确认按钮用红色）
    """
    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text(title, color=Colors.TEXT_PRIMARY, size=Fonts.SIZE_LG),
        content=ft.Text(message, color=Colors.TEXT_SECONDARY, size=Fonts.SIZE_MD),
        actions=[
            ft.TextButton(
                "取消",
                on_click=lambda e: page.pop_dialog(),
            ),
            ft.TextButton(
                confirm_text,
                on_click=lambda e: (_confirm(e, dlg, on_confirm)),
                style=ft.ButtonStyle(
                    color=Colors.ERROR if danger else Colors.PRIMARY,
                ),
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.show_dialog(dlg)


def _confirm(e, dlg: ft.AlertDialog, cb: Callable[[], None]) -> None:
    """内部辅助：关闭对话框并调用回调。"""
    # 从 event 获取 page 不太可靠，直接传 None 给 close 即可
    dlg.open = False
    if dlg.page:
        dlg.page.pop_dialog()
    cb()


# ──────────────────────────────────────────────
# 回收站对话框
# ──────────────────────────────────────────────

def show_recycle_bin_dialog(
    page: ft.Page,
    trash_entries: list,
    on_restore: Callable[[str], None],
    on_permanent_delete: Callable[[str], None],
    on_clear_all: Callable[[], None] | None = None,
) -> None:
    """
    弹出回收站对话框，列出所有已删除条目。

    Args:
        page:                Flet Page 实例
        trash_entries:       TrashEntryVM 列表
        on_restore:          恢复回调，传入 trash_entry_id
        on_permanent_delete: 彻底删除回调，传入 trash_entry_id
        on_clear_all:        清空回收站回调
    """
    entries_column = ft.Column(
        spacing=Spacing.SM,
        scroll=ft.ScrollMode.AUTO,
    )

    if not trash_entries:
        entries_column.controls.append(
            ft.Container(
                content=ft.Text(
                    "回收站为空",
                    size=Fonts.SIZE_SM,
                    color=Colors.TEXT_DISABLED,
                ),
                padding=ft.Padding(
                    left=Spacing.MD, right=Spacing.MD,
                    top=Spacing.XL, bottom=Spacing.XL,
                ),
                alignment=ft.Alignment(0, 0),
            )
        )
    else:
        for entry in trash_entries:
            icon = "📁" if entry.node_type == "folder" else "📄"
            row = ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Text(icon, size=14),
                        ft.Text(
                            f"{entry.title}",
                            size=Fonts.SIZE_SM,
                            color=Colors.TEXT_PRIMARY,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            expand=True,
                        ),
                        ft.Text(
                            entry.deleted_at,
                            size=Fonts.SIZE_XS,
                            color=Colors.TEXT_DISABLED,
                            font_family=Fonts.MONO,
                        ),
                        ft.IconButton(
                            icon=ft.Icons.RESTORE,
                            icon_size=16,
                            icon_color=Colors.SUCCESS,
                            tooltip="恢复",
                            on_click=lambda e, eid=entry.id: _trash_action(
                                page, dlg_ref[0], lambda: on_restore(eid)
                            ),
                        ),
                        ft.IconButton(
                            icon=ft.Icons.DELETE_FOREVER,
                            icon_size=16,
                            icon_color=Colors.ERROR,
                            tooltip="彻底删除",
                            on_click=lambda e, eid=entry.id: _trash_action(
                                page, dlg_ref[0], lambda: on_permanent_delete(eid)
                            ),
                        ),
                    ],
                    spacing=Spacing.SM,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.Padding(
                    left=Spacing.MD, right=Spacing.MD,
                    top=Spacing.SM, bottom=Spacing.SM,
                ),
                border=ft.Border(
                    bottom=ft.BorderSide(1, Colors.DIVIDER),
                ),
            )
            entries_column.controls.append(row)

    dlg_ref: list[ft.AlertDialog | None] = [None]

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("🗑️ 回收站", color=Colors.TEXT_PRIMARY, size=Fonts.SIZE_LG),
        content=ft.Container(
            content=entries_column,
            height=400,
            width=500,
        ),
        actions=[
            ft.TextButton(
                "清空回收站",
                on_click=lambda e: _trash_clear_all(page, dlg_ref[0], on_clear_all),
                style=ft.ButtonStyle(color=Colors.ERROR),
            ) if on_clear_all else ft.Container(),
            ft.Container(expand=True),
            ft.TextButton(
                "关闭",
                on_click=lambda e: page.pop_dialog(),
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    dlg_ref[0] = dlg
    page.show_dialog(dlg)


def _trash_action(
    page: ft.Page,
    dlg: ft.AlertDialog | None,
    action: Callable[[], None],
) -> None:
    """内部辅助：执行回收站操作并刷新对话框。"""
    action()
    if dlg and dlg.page:
        dlg.page.pop_dialog()


def _trash_clear_all(
    page: ft.Page,
    dlg: ft.AlertDialog | None,
    action: Callable[[], None] | None,
) -> None:
    """内部辅助：清空回收站并关闭对话框。"""
    if action:
        action()
    if dlg and dlg.page:
        dlg.page.pop_dialog()


# ──────────────────────────────────────────────
# 上下文块管理对话框
# ──────────────────────────────────────────────

def show_context_block_manager_dialog(
    page: ft.Page,
    folder_title: str,
    all_blocks: list[dict],
    current_block_ids: list[str],
    on_save: Callable[[list[str]], None],
) -> None:
    """
    弹出上下文块管理对话框，用复选框选择目录关联的 ContextBlock。

    Args:
        page:              Flet Page 实例
        folder_title:      目录名称（用于标题）
        all_blocks:        所有可用的 ContextBlock（dict 格式，含 id/label/preview/enabled）
        current_block_ids: 当前已选中的 block ID 列表
        on_save:           保存回调，传入新的选中 ID 列表
    """
    # 使用可变容器让内部回调可以修改选中集合
    selected_ids: set[str] = set(current_block_ids)

    checkboxes: list[ft.Checkbox] = []
    block_column = ft.Column(
        spacing=Spacing.XS,
        scroll=ft.ScrollMode.AUTO,
    )

    if not all_blocks:
        block_column.controls.append(
            ft.Text(
                "暂无可用上下文块",
                size=Fonts.SIZE_SM,
                color=Colors.TEXT_DISABLED,
            )
        )
    else:
        for block in all_blocks:
            bid = block["id"]
            label = block.get("label", "")
            preview = block.get("preview", "")[:40]

            cb = ft.Checkbox(
                label=f"{label} — {preview}",
                value=bid in selected_ids,
                label_style=ft.TextStyle(
                    size=Fonts.SIZE_SM,
                    color=Colors.TEXT_PRIMARY,
                ),
                fill_color=Colors.PRIMARY,
                on_change=lambda e, blk_id=bid: _toggle_block(
                    blk_id, e.control.value, selected_ids
                ),
            )
            checkboxes.append(cb)
            block_column.controls.append(cb)

    def _save(e, dlg, cb):
        page.pop_dialog()
        cb(list(selected_ids))

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text(
            f"管理上下文 — {folder_title}",
            color=Colors.TEXT_PRIMARY,
            size=Fonts.SIZE_LG,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
        content=ft.Container(
            content=block_column,
            height=350,
            width=480,
        ),
        actions=[
            ft.TextButton(
                "取消",
                on_click=lambda e: page.pop_dialog(),
            ),
            ft.TextButton(
                "保存",
                on_click=lambda e: _save(e, dlg, on_save),
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.show_dialog(dlg)


def _toggle_block(
    block_id: str,
    checked: bool | None,
    selected_ids: set[str],
) -> None:
    """内部辅助：切换上下文块的选中状态。"""
    if checked:
        selected_ids.add(block_id)
    else:
        selected_ids.discard(block_id)
