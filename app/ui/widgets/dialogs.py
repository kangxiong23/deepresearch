# Layer: UI (PySide6) → widgets
# File: app/ui/widgets/dialogs.py
# Responsibility: 可复用的对话框组件 — 重命名、新建文件夹、确认、回收站、上下文块管理。
#                 与 Flet 版本 app/ui_flet_legacy/widgets/dialogs.py 功能完全对等。
# Input:  父窗口 (QWidget) + 回调函数 + 数据
# Output: 通过 exec() 弹出模态对话框，用户操作后调用回调

from __future__ import annotations
from typing import Callable

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QCheckBox,
    QScrollArea,
    QWidget,
    QFrame,
    QInputDialog,
    QMessageBox,
    QSizePolicy,
    QSpacerItem,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from app.ui.theme import Colors, Fonts, Spacing, Radius
from app.controllers.view_models import TrashEntryVM


# ══════════════════════════════════════════════════
# 重命名对话框
# ══════════════════════════════════════════════════

def show_rename_dialog(
    parent: QWidget,
    current_title: str,
    on_confirm: Callable[[str], None],
) -> None:
    """
    弹出重命名对话框。

    Args:
        parent:        父窗口
        current_title: 当前名称（预填）
        on_confirm:    确认回调，传入新名称字符串
    """
    name, ok = QInputDialog.getText(
        parent,
        "重命名",
        "新名称:",
        QLineEdit.EchoMode.Normal,
        current_title,
    )
    if ok and name.strip():
        on_confirm(name.strip())


# ══════════════════════════════════════════════════
# 新建文件夹对话框
# ══════════════════════════════════════════════════

def show_new_folder_dialog(
    parent: QWidget,
    on_confirm: Callable[[str], None],
    default_title: str = "新文件夹",
) -> None:
    """
    弹出新建目录对话框。

    Args:
        parent:        父窗口
        on_confirm:    确认回调，传入目录名称
        default_title: 默认目录名称
    """
    name, ok = QInputDialog.getText(
        parent,
        "新建目录",
        "目录名称:",
        QLineEdit.EchoMode.Normal,
        default_title,
    )
    if ok and name.strip():
        on_confirm(name.strip())


# ══════════════════════════════════════════════════
# 通用确认对话框
# ══════════════════════════════════════════════════

def show_confirm_dialog(
    parent: QWidget,
    title: str,
    message: str,
    on_confirm: Callable[[], None],
    confirm_text: str = "确认",
    danger: bool = True,
) -> None:
    """
    弹出通用确认对话框。

    Args:
        parent:       父窗口
        title:        对话框标题
        message:      对话框内容
        on_confirm:   确认回调
        confirm_text: 确认按钮文字
        danger:       是否为危险操作（确认按钮用红色）
    """
    # 创建自定义 QMessageBox 以支持样式化按钮
    msg_box = QMessageBox(parent)
    msg_box.setWindowTitle(title)
    msg_box.setText(message)
    msg_box.setIcon(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question)
    msg_box.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    msg_box.setDefaultButton(QMessageBox.StandardButton.No)

    # 设置按钮文字
    yes_btn = msg_box.button(QMessageBox.StandardButton.Yes)
    if yes_btn:
        yes_btn.setText(confirm_text)
    no_btn = msg_box.button(QMessageBox.StandardButton.No)
    if no_btn:
        no_btn.setText("取消")

    # 危险按钮样式
    if danger and yes_btn:
        yes_btn.setStyleSheet(f"""
            QPushButton {{
                color: {Colors.ERROR};
                font-weight: bold;
            }}
        """)

    # 整体样式
    msg_box.setStyleSheet(f"""
        QMessageBox {{
            background-color: {Colors.BG_ELEVATED};
        }}
        QLabel {{
            color: {Colors.TEXT_PRIMARY};
            font-size: {Fonts.SIZE_MD}px;
        }}
        QPushButton {{
            background-color: {Colors.BG_OVERLAY};
            color: {Colors.TEXT_PRIMARY};
            border: 1px solid {Colors.BORDER};
            border-radius: {Radius.SM}px;
            padding: 6px 16px;
            font-size: {Fonts.SIZE_SM}px;
        }}
        QPushButton:hover {{
            background-color: {Colors.BG_ELEVATED};
            border-color: {Colors.PRIMARY};
        }}
    """)

    result = msg_box.exec()
    if result == QMessageBox.StandardButton.Yes:
        on_confirm()


# ══════════════════════════════════════════════════
# 回收站对话框
# ══════════════════════════════════════════════════


class _RecycleBinDialog(QDialog):
    """回收站对话框 — 列出已删除条目，支持恢复和彻底删除。"""

    def __init__(
        self,
        parent: QWidget,
        trash_entries: list[TrashEntryVM],
        on_restore: Callable[[str], None],
        on_permanent_delete: Callable[[str], None],
        on_clear_all: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("🗑️ 回收站")
        self.setMinimumSize(500, 420)
        self.setModal(True)
        self._on_restore = on_restore
        self._on_permanent_delete = on_permanent_delete
        self._on_clear_all = on_clear_all

        self._setup_ui(trash_entries)
        self._apply_style()

    def _setup_ui(self, entries: list[TrashEntryVM]) -> None:
        """构建对话框 UI。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        layout.setSpacing(Spacing.MD)

        # ── 条目列表区域 ─────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: transparent;
                border: 1px solid {Colors.BORDER};
                border-radius: {Radius.MD}px;
            }}
        """)

        container = QWidget()
        container.setStyleSheet("QWidget { background-color: transparent; }")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        if not entries:
            empty_label = QLabel("回收站为空")
            empty_label.setFont(Fonts.body(Fonts.SIZE_SM))
            empty_label.setStyleSheet(f"""
                QLabel {{
                    color: {Colors.TEXT_DISABLED};
                    padding: {Spacing.XL}px;
                }}
            """)
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            container_layout.addWidget(empty_label)
        else:
            for entry in entries:
                row = self._make_entry_row(entry)
                container_layout.addWidget(row)

        container_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, stretch=1)

        # ── 底部按钮行 ────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(Spacing.SM)

        if self._on_clear_all:
            clear_btn = QPushButton("清空回收站")
            clear_btn.setFont(Fonts.body(Fonts.SIZE_SM))
            clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            clear_btn.setStyleSheet(f"""
                QPushButton {{
                    color: {Colors.ERROR};
                    background-color: transparent;
                    border: 1px solid {Colors.ERROR};
                    border-radius: {Radius.MD}px;
                    padding: 6px 16px;
                }}
                QPushButton:hover {{
                    background-color: {Colors.ERROR}22;
                }}
            """)
            clear_btn.clicked.connect(self._on_clear_clicked)
            btn_layout.addWidget(clear_btn)

        btn_layout.addStretch()

        close_btn = QPushButton("关闭")
        close_btn.setFont(Fonts.body(Fonts.SIZE_SM))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                color: {Colors.TEXT_PRIMARY};
                background-color: {Colors.BG_OVERLAY};
                border: 1px solid {Colors.BORDER};
                border-radius: {Radius.MD}px;
                padding: 6px 16px;
            }}
            QPushButton:hover {{
                border-color: {Colors.PRIMARY};
            }}
        """)
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

    def _make_entry_row(self, entry: TrashEntryVM) -> QFrame:
        """创建单个回收站条目行。"""
        row = QFrame()
        row.setStyleSheet(f"""
            QFrame {{
                background-color: transparent;
                border-bottom: 1px solid {Colors.DIVIDER};
            }}
        """)

        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(Spacing.MD, Spacing.SM, Spacing.SM, Spacing.SM)
        row_layout.setSpacing(Spacing.SM)

        # 图标
        icon_text = "📁" if entry.node_type == "folder" else "📄"
        icon_label = QLabel(icon_text)
        icon_label.setFont(Fonts.body(14))
        icon_label.setFixedWidth(20)
        icon_label.setStyleSheet("QLabel { border: none; background: transparent; }")

        # 标题
        title_label = QLabel(entry.title)
        title_label.setFont(Fonts.body(Fonts.SIZE_SM))
        title_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_PRIMARY};
                border: none;
                background: transparent;
            }}
        """)

        # 路径
        path_label = QLabel(entry.json_path)
        path_label.setFont(Fonts.mono(Fonts.SIZE_XS))
        path_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_DISABLED};
                border: none;
                background: transparent;
            }}
        """)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        text_col.addWidget(title_label)
        text_col.addWidget(path_label)

        # 删除时间
        time_label = QLabel(entry.deleted_at)
        time_label.setFont(Fonts.mono(Fonts.SIZE_XS))
        time_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_DISABLED};
                border: none;
                background: transparent;
            }}
        """)

        # 恢复按钮
        restore_btn = QPushButton("🔄 恢复")
        restore_btn.setFont(Fonts.body(Fonts.SIZE_XS))
        restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        restore_btn.setToolTip("恢复")
        restore_btn.setStyleSheet(f"""
            QPushButton {{
                color: {Colors.SUCCESS};
                background: transparent;
                border: 1px solid {Colors.SUCCESS};
                border-radius: {Radius.SM}px;
                padding: 4px 8px;
            }}
            QPushButton:hover {{
                background-color: {Colors.SUCCESS}22;
            }}
        """)
        eid = entry.id
        restore_btn.clicked.connect(
            lambda checked=False, eid=eid: self._on_restore_clicked(eid)
        )

        # 彻底删除按钮
        delete_btn = QPushButton("🗑️ 删除")
        delete_btn.setFont(Fonts.body(Fonts.SIZE_XS))
        delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_btn.setToolTip("彻底删除")
        delete_btn.setStyleSheet(f"""
            QPushButton {{
                color: {Colors.ERROR};
                background: transparent;
                border: 1px solid {Colors.ERROR};
                border-radius: {Radius.SM}px;
                padding: 4px 8px;
            }}
            QPushButton:hover {{
                background-color: {Colors.ERROR}22;
            }}
        """)
        delete_btn.clicked.connect(
            lambda checked=False, eid=eid: self._on_delete_clicked(eid)
        )

        row_layout.addWidget(icon_label)
        row_layout.addLayout(text_col, stretch=1)
        row_layout.addWidget(time_label)
        row_layout.addWidget(restore_btn)
        row_layout.addWidget(delete_btn)

        return row

    def _on_restore_clicked(self, entry_id: str) -> None:
        """恢复操作：调用回调并关闭对话框。"""
        self._on_restore(entry_id)
        self.accept()

    def _on_delete_clicked(self, entry_id: str) -> None:
        """彻底删除：调用回调并关闭对话框。"""
        self._on_permanent_delete(entry_id)
        self.accept()

    def _on_clear_clicked(self) -> None:
        """清空回收站：弹出二次确认后调用回调。"""
        if self._on_clear_all is None:
            return
        reply = QMessageBox.question(
            self,
            "确认清空",
            "确定要清空回收站吗？此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._on_clear_all()
            self.accept()

    def _apply_style(self) -> None:
        """应用对话框整体样式。"""
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {Colors.BG_SURFACE};
            }}
        """)


def show_recycle_bin_dialog(
    parent: QWidget,
    trash_entries: list[TrashEntryVM],
    on_restore: Callable[[str], None],
    on_permanent_delete: Callable[[str], None],
    on_clear_all: Callable[[], None] | None = None,
) -> None:
    """
    弹出回收站对话框。

    Args:
        parent:               父窗口
        trash_entries:       TrashEntryVM 列表
        on_restore:          恢复回调，传入 trash_entry_id
        on_permanent_delete: 彻底删除回调，传入 trash_entry_id
        on_clear_all:        清空回收站回调
    """
    dlg = _RecycleBinDialog(
        parent, trash_entries, on_restore, on_permanent_delete, on_clear_all
    )
    dlg.exec()


# ══════════════════════════════════════════════════
# 上下文块管理对话框
# ══════════════════════════════════════════════════


class _ContextBlockManagerDialog(QDialog):
    """
    上下文块管理对话框 — 列出所有可用块，用复选框选择关联到目录。
    """

    def __init__(
        self,
        parent: QWidget,
        folder_title: str,
        all_blocks: list[dict],
        current_block_ids: list[str],
        on_save: Callable[[list[str]], None],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"管理上下文 — {folder_title}")
        self.setMinimumSize(480, 380)
        self.setModal(True)
        self._on_save = on_save

        # 选中状态集合
        self._selected_ids: set[str] = set(current_block_ids)
        self._checkboxes: list[tuple[str, QCheckBox]] = []

        self._setup_ui(folder_title, all_blocks)
        self._apply_style()

    def _setup_ui(self, folder_title: str, all_blocks: list[dict]) -> None:
        """构建对话框 UI。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        layout.setSpacing(Spacing.MD)

        # ── 提示标签 ──────────────────────────────
        hint = QLabel("选择要关联到该目录的上下文块:")
        hint.setFont(Fonts.body(Fonts.SIZE_SM))
        hint.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; background: transparent; border: none;")
        layout.addWidget(hint)

        # ── 块列表区域 ────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: transparent;
                border: 1px solid {Colors.BORDER};
                border-radius: {Radius.MD}px;
            }}
        """)

        container = QWidget()
        container.setStyleSheet("QWidget { background-color: transparent; }")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(Spacing.SM, Spacing.SM, Spacing.SM, Spacing.SM)
        container_layout.setSpacing(Spacing.XS)

        if not all_blocks:
            empty_label = QLabel("暂无可用上下文块")
            empty_label.setFont(Fonts.body(Fonts.SIZE_SM))
            empty_label.setStyleSheet(f"color: {Colors.TEXT_DISABLED}; background: transparent; border: none;")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            container_layout.addWidget(empty_label)
        else:
            for block in all_blocks:
                bid = block.get("id", "")
                label_text = block.get("label", "")
                preview = block.get("preview", "")[:40]

                cb = QCheckBox(f"{label_text} — {preview}")
                cb.setFont(Fonts.body(Fonts.SIZE_SM))
                cb.setChecked(bid in self._selected_ids)
                cb.setStyleSheet(f"""
                    QCheckBox {{
                        color: {Colors.TEXT_PRIMARY};
                        spacing: {Spacing.SM}px;
                    }}
                    QCheckBox::indicator {{
                        width: 18px;
                        height: 18px;
                        border: 1px solid {Colors.BORDER};
                        border-radius: 3px;
                        background-color: transparent;
                    }}
                    QCheckBox::indicator:checked {{
                        background-color: {Colors.PRIMARY};
                        border-color: {Colors.PRIMARY};
                    }}
                    QCheckBox::indicator:hover {{
                        border-color: {Colors.PRIMARY};
                    }}
                """)
                cb.toggled.connect(
                    lambda checked, block_id=bid: self._on_toggle(block_id, checked)
                )
                self._checkboxes.append((bid, cb))
                container_layout.addWidget(cb)

        container_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, stretch=1)

        # ── 底部按钮行 ────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(Spacing.SM)
        btn_layout.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setFont(Fonts.body(Fonts.SIZE_SM))
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                color: {Colors.TEXT_PRIMARY};
                background-color: {Colors.BG_OVERLAY};
                border: 1px solid {Colors.BORDER};
                border-radius: {Radius.MD}px;
                padding: 6px 16px;
            }}
            QPushButton:hover {{
                border-color: {Colors.PRIMARY};
            }}
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("保存")
        save_btn.setFont(Fonts.body(Fonts.SIZE_SM, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                color: {Colors.BG_BASE};
                background-color: {Colors.PRIMARY};
                border: none;
                border-radius: {Radius.MD}px;
                padding: 6px 16px;
            }}
            QPushButton:hover {{
                background-color: {Colors.PRIMARY_DIM};
            }}
        """)
        save_btn.clicked.connect(self._on_save_clicked)
        btn_layout.addWidget(save_btn)

        layout.addLayout(btn_layout)

    def _on_toggle(self, block_id: str, checked: bool) -> None:
        """切换复选框时更新选中集合。"""
        if checked:
            self._selected_ids.add(block_id)
        else:
            self._selected_ids.discard(block_id)

    def _on_save_clicked(self) -> None:
        """保存按钮：触发回调并关闭。"""
        self._on_save(list(self._selected_ids))
        self.accept()

    def _apply_style(self) -> None:
        """应用对话框整体样式。"""
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {Colors.BG_SURFACE};
            }}
        """)


def show_context_block_manager_dialog(
    parent: QWidget,
    folder_title: str,
    all_blocks: list[dict],
    current_block_ids: list[str],
    on_save: Callable[[list[str]], None],
) -> None:
    """
    弹出上下文块管理对话框。

    Args:
        parent:            父窗口
        folder_title:      目录名称（用于标题）
        all_blocks:        所有可用的 ContextBlock（dict 格式，含 id/label/preview/enabled）
        current_block_ids: 当前已选中的 block ID 列表
        on_save:           保存回调，传入新的选中 ID 列表
    """
    dlg = _ContextBlockManagerDialog(
        parent, folder_title, all_blocks, current_block_ids, on_save
    )
    dlg.exec()
