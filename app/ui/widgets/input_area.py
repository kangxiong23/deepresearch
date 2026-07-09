# Layer: UI → widgets
# File: app/ui/widgets/input_area.py
# Responsibility: 底部输入区复合体——输入框、模型切换、思考/搜索开关、文件上传、发送/停止按钮
# Flet 0.85 兼容版本

from __future__ import annotations
from typing import Callable
import threading
import flet as ft
import config as app_config
from app.ui.theme import Colors, Fonts, Spacing, Radius


class _ToggleChip(ft.Container):
    def __init__(
        self,
        label: str,
        icon: str,
        active_color: str,
        initial: bool = False,
        on_change: Callable[[bool], None] | None = None,
    ) -> None:
        self._active = initial
        self._active_color = active_color
        self._on_change = on_change
        self._label_ref = ft.Ref[ft.Text]()
        self._icon_ref = ft.Ref[ft.Icon]()

        super().__init__(
            content=ft.Row(
                controls=[
                    ft.Icon(
                        icon,
                        ref=self._icon_ref,
                        size=14,
                        color=active_color if initial else Colors.TEXT_DISABLED,
                    ),
                    ft.Text(
                        ref=self._label_ref,
                        value=label,
                        size=Fonts.SIZE_XS,
                        font_family=Fonts.MONO,
                        color=active_color if initial else Colors.TEXT_DISABLED,
                    ),
                ],
                tight=True,
                spacing=4,
            ),
            padding=ft.Padding(left=8, right=8, top=4, bottom=4),
            border=ft.Border(
                top=ft.BorderSide(1, active_color if initial else Colors.BORDER),
                bottom=ft.BorderSide(1, active_color if initial else Colors.BORDER),
                left=ft.BorderSide(1, active_color if initial else Colors.BORDER),
                right=ft.BorderSide(1, active_color if initial else Colors.BORDER),
            ),
            border_radius=Radius.SM,
            bgcolor=f"{active_color}22" if initial else "transparent",
            on_click=self._toggle,
        )

    def _toggle(self, e) -> None:
        self._active = not self._active
        color = self._active_color if self._active else Colors.TEXT_DISABLED
        border_color = self._active_color if self._active else Colors.BORDER
        self.bgcolor = f"{self._active_color}22" if self._active else "transparent"
        self.border = ft.Border(
            top=ft.BorderSide(1, border_color),
            bottom=ft.BorderSide(1, border_color),
            left=ft.BorderSide(1, border_color),
            right=ft.BorderSide(1, border_color),
        )
        if self._icon_ref.current:
            self._icon_ref.current.color = color
        if self._label_ref.current:
            self._label_ref.current.color = color
        e.control.update()
        if self._on_change:
            self._on_change(self._active)

    @property
    def value(self) -> bool:
        return self._active

    def set_value(self, val: bool) -> None:
        if self._active != val:
            self._active = val
            color = self._active_color if val else Colors.TEXT_DISABLED
            border_color = self._active_color if val else Colors.BORDER
            self.bgcolor = f"{self._active_color}22" if val else "transparent"
            self.border = ft.Border(
                top=ft.BorderSide(1, border_color),
                bottom=ft.BorderSide(1, border_color),
                left=ft.BorderSide(1, border_color),
                right=ft.BorderSide(1, border_color),
            )
            if self._icon_ref.current:
                self._icon_ref.current.color = color
            if self._label_ref.current:
                self._label_ref.current.color = color


class _ModelSelector(ft.Container):
    """下拉模型选择，写入 app_config.model_type"""

    _MODEL_OPTIONS = [
        ("deepseek-v4-flash", "Flash"),
        ("deepseek-v4-pro", "Pro"),
    ]

    def __init__(self, on_change: Callable[[str], None] | None = None) -> None:
        self._on_change = on_change
        current = getattr(app_config, "model_type", "deepseek-v4-flash")

        options = [
            ft.dropdown.Option(key=k, text=v)
            for k, v in self._MODEL_OPTIONS
        ]

        self._dd = ft.Dropdown(
            value=current,
            options=options,
            width=150,
            text_size=Fonts.SIZE_SM,
            text_style=ft.TextStyle(
                font_family=Fonts.MONO,
                color=Colors.TEXT_PRIMARY,
            ),
            bgcolor=Colors.BG_ELEVATED,
            border_color=Colors.BORDER,
            focused_border_color=Colors.PRIMARY,
            border_radius=8,
            content_padding=ft.Padding(left=10, right=10, top=6, bottom=6),
        )
        self._dd.on_change = self._handle_change
        super().__init__(content=self._dd)

    def _handle_change(self, e) -> None:
        app_config.model_type = e.control.value
        if self._on_change:
            self._on_change(e.control.value)


class _AttachmentBar(ft.Row):
    def __init__(self) -> None:
        super().__init__(spacing=Spacing.SM, wrap=True)
        self._files: list[tuple[str, str]] = []  # (display_name, abs_path)

    def add_file(self, name: str, abs_path: str = "") -> None:
        self._files.append((name, abs_path or name))
        chip = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.ATTACH_FILE, size=12, color=Colors.PRIMARY),
                    ft.Text(
                        name,
                        size=Fonts.SIZE_XS,
                        color=Colors.TEXT_SECONDARY,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        icon_size=10,
                        icon_color=Colors.TEXT_DISABLED,
                        style=ft.ButtonStyle(
                            padding=ft.Padding(left=2, right=2, top=2, bottom=2),
                        ),
                        on_click=lambda e, n=name: self._remove(n),
                    ),
                ],
                tight=True,
                spacing=4,
            ),
            padding=ft.Padding(left=6, right=2, top=3, bottom=3),
            border=ft.Border(
                top=ft.BorderSide(1, Colors.BORDER),
                bottom=ft.BorderSide(1, Colors.BORDER),
                left=ft.BorderSide(1, Colors.BORDER),
                right=ft.BorderSide(1, Colors.BORDER),
            ),
            border_radius=Radius.SM,
        )
        self.controls.append(chip)

    def _remove(self, name: str) -> None:
        self._files = [(n, p) for n, p in self._files if n != name]
        self.controls = [
            c for c in self.controls
            if not (
                isinstance(c, ft.Container)
                and isinstance(c.content, ft.Row)
                and any(
                    isinstance(ctrl, ft.Text) and ctrl.value == name
                    for ctrl in c.content.controls
                )
            )
        ]
        self.update()

    def get_files(self) -> list[str]:
        """返回绝对路径列表，供 Core 层读取文件内容。"""
        return [p for _, p in self._files]

    def clear_files(self) -> None:
        self._files = []
        self.controls.clear()


class InputArea(ft.Container):
    """
    底部输入复合体。

    公开接口：
        set_generating(True/False)  — 切换发送/停止状态
        clear()                     — 发送后清空输入框与附件
        set_enabled(bool)           — 整体启用/禁用
    """

    def __init__(
        self,
        on_send: Callable[[str, list[str]], None],
        on_stop: Callable[[], None],
        on_model_change: Callable[[str], None] | None = None,
        on_thinking_change: Callable[[bool], None] | None = None,
        on_search_change: Callable[[bool], None] | None = None,
    ) -> None:
        self._on_send = on_send
        self._on_stop = on_stop
        self._generating = False

        self._tf_ref = ft.Ref[ft.TextField]()
        self._send_btn_ref = ft.Ref[ft.IconButton]()
        self._attachment_bar = _AttachmentBar()

        self._thinking_chip = _ToggleChip(
            label="THINK",
            icon=ft.Icons.PSYCHOLOGY_OUTLINED,
            active_color=Colors.ROLE_THINKING,
            initial=getattr(app_config, "thinking_enabled", False),
            on_change=self._on_thinking_toggle,
        )
        self._search_chip = _ToggleChip(
            label="SEARCH",
            icon=ft.Icons.TRAVEL_EXPLORE_OUTLINED,
            active_color=Colors.ACCENT,
            initial=getattr(app_config, "search_enabled", False),
            on_change=self._on_search_toggle,
        )

        self._external_thinking_cb = on_thinking_change
        self._external_search_cb = on_search_change

        model_selector = _ModelSelector(on_change=on_model_change)

        # 不使用 Flet FilePicker（0.85 兼容性问题），改用 tkinter 原生对话框
        self._file_picker = None

        text_field = ft.TextField(
            ref=self._tf_ref,
            hint_text="输入消息，Shift+Enter 换行，Enter 发送...",
            hint_style=ft.TextStyle(
                color=Colors.TEXT_DISABLED,
                size=Fonts.SIZE_MD,
            ),
            multiline=True,
            min_lines=1,
            max_lines=8,
            expand=True,
            border=ft.InputBorder.NONE,
            text_style=ft.TextStyle(
                color=Colors.TEXT_PRIMARY,
                size=Fonts.SIZE_MD,
                font_family=Fonts.BODY,
            ),
            cursor_color=Colors.PRIMARY,
            bgcolor="transparent",
            content_padding=ft.Padding(left=0, right=0, top=8, bottom=8),
            on_submit=self._handle_send,
            shift_enter=True,
        )

        send_btn = ft.IconButton(
            ref=self._send_btn_ref,
            icon=ft.Icons.SEND_ROUNDED,
            icon_color=Colors.PRIMARY,
            icon_size=20,
            tooltip="发送 (Enter)",
            style=ft.ButtonStyle(
                bgcolor=Colors.PRIMARY_GLOW,
                shape=ft.CircleBorder(),
                padding=ft.Padding(left=10, right=10, top=10, bottom=10),
            ),
            on_click=self._handle_send,
        )

        toolbar = ft.Row(
            controls=[
                ft.IconButton(
                    icon=ft.Icons.ATTACH_FILE_OUTLINED,
                    icon_size=16,
                    icon_color=Colors.TEXT_SECONDARY,
                    tooltip="上传文件",
                    style=ft.ButtonStyle(
                        padding=ft.Padding(left=6, right=6, top=6, bottom=6),
                    ),
                    on_click=self._open_file_dialog,
                ),
                self._thinking_chip,
                self._search_chip,
                ft.Container(expand=True),
                model_selector,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=Spacing.SM,
        )

        input_row = ft.Row(
            controls=[text_field, send_btn],
            vertical_alignment=ft.CrossAxisAlignment.END,
            spacing=Spacing.SM,
        )

        super().__init__(
            content=ft.Column(
                controls=[
                    self._attachment_bar,
                    input_row,
                    ft.Container(
                        content=toolbar,
                        padding=ft.Padding(left=0, right=0, top=Spacing.SM, bottom=0),
                        border=ft.Border(top=ft.BorderSide(1, Colors.DIVIDER)),
                    ),
                ],
                spacing=Spacing.SM,
            ),
            padding=ft.Padding(
                left=Spacing.LG, right=Spacing.LG,
                top=Spacing.MD, bottom=Spacing.MD,
            ),
            bgcolor=Colors.BG_SURFACE,
            border=ft.Border(
                top=ft.BorderSide(1, Colors.BORDER),
                bottom=ft.BorderSide(0, "transparent"),
                left=ft.BorderSide(0, "transparent"),
                right=ft.BorderSide(0, "transparent"),
            ),
        )

    def _handle_send(self, e) -> None:
        if self._generating:
            return
        text = self._tf_ref.current.value.strip() if self._tf_ref.current else ""
        if not text and not self._attachment_bar.get_files():
            return
        files = self._attachment_bar.get_files()
        self._on_send(text, files)

    def _open_file_dialog(self, e) -> None:
        """在独立线程用 tkinter 打开原生文件选择对话框，避免 Flet FilePicker 兼容问题。"""
        def _pick():
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()          # 隐藏主窗口
                root.wm_attributes('-topmost', True)  # 置顶
                paths = filedialog.askopenfilenames(
                    title="选择文件",
                    filetypes=[
                        ("支持的文件", "*.txt *.md *.json *.docx *.pdf"),
                        ("所有文件", "*.*"),
                    ]
                )
                root.destroy()
                if paths and self.page:
                    import os
                    for p in paths:
                        name = os.path.basename(p)
                        print(f"[FilePicker] 选中: name={repr(name)} path={repr(p)}")
                        self._attachment_bar.add_file(name, p)
                    self.page.run_task(self._refresh_after_pick)
            except Exception as ex:
                print(f"[FilePicker] 错误: {ex}")
        threading.Thread(target=_pick, daemon=True).start()

    async def _refresh_after_pick(self) -> None:
        """文件选择完成后刷新 UI。"""
        self.update()
        if self.page:
            self.page.update()

    def _on_thinking_toggle(self, val: bool) -> None:
        app_config.thinking_enabled = val
        if self._external_thinking_cb:
            self._external_thinking_cb(val)

    def _on_search_toggle(self, val: bool) -> None:
        app_config.search_enabled = val
        if self._external_search_cb:
            self._external_search_cb(val)

    def get_file_picker(self):
        """已废弃，改用 tkinter 原生对话框，返回 None。"""
        return None

    def set_generating(self, generating: bool) -> None:
        self._generating = generating
        if self._send_btn_ref.current:
            self._send_btn_ref.current.icon = (
                ft.Icons.STOP_CIRCLE_OUTLINED if generating else ft.Icons.SEND_ROUNDED
            )
            self._send_btn_ref.current.icon_color = (
                Colors.ERROR if generating else Colors.PRIMARY
            )
            self._send_btn_ref.current.on_click = (
                (lambda e: self._on_stop()) if generating else self._handle_send
            )
            self._send_btn_ref.current.update()

    def clear(self) -> None:
        if self._tf_ref.current:
            self._tf_ref.current.value = ""
            self._tf_ref.current.update()
        self._attachment_bar.clear_files()
        self.update()

    def set_enabled(self, enabled: bool) -> None:
        if self._tf_ref.current:
            self._tf_ref.current.disabled = not enabled
            self._tf_ref.current.update()