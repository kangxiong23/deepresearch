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

from app.ui.theme import apply_style, Colors, Fonts, Spacing, Radius
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
        apply_style(yes_btn, lambda: f"""
            QPushButton {{
                color: {Colors.ERROR};
                font-weight: bold;
            }}
        """)

    # 整体样式
    apply_style(msg_box, lambda: f"""
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


# ──────────────────────────────────────────────
# 节点上下文管理相关确认对话框
# ──────────────────────────────────────────────


def _style_msg_box(msg_box: QMessageBox) -> None:
    """统一 QMessageBox 深色样式。"""
    apply_style(msg_box, lambda: f"""
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


def show_template_overwrite_confirm_dialog(parent: QWidget, name: str) -> str:
    """
    模板标题已存在时的三选一确认。

    Returns:
        "cancel"       — 取消本次保存（保留输入）
        "no_overwrite" — 不覆盖（保留输入，用户可改名重试）
        "overwrite"    — 覆盖同名模板
    """
    msg_box = QMessageBox(parent)
    msg_box.setWindowTitle("模板已存在")
    msg_box.setText(
        f"模板库中已存在标题为「{name}」的模板，\n保存将覆盖该模板。是否继续？"
    )
    msg_box.setIcon(QMessageBox.Icon.Question)
    cancel_btn = msg_box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    no_btn = msg_box.addButton("不覆盖", QMessageBox.ButtonRole.DestructiveRole)
    overwrite_btn = msg_box.addButton("覆盖", QMessageBox.ButtonRole.AcceptRole)
    msg_box.setDefaultButton(no_btn)
    _style_msg_box(msg_box)
    msg_box.exec()
    clicked = msg_box.clickedButton()
    if clicked is overwrite_btn:
        return "overwrite"
    if clicked is no_btn:
        return "no_overwrite"
    return "cancel"


def show_node_apply_confirm_dialog(parent: QWidget, node_name: str) -> str:
    """
    节点模式下关闭面板且有未应用修改时的三选一确认。

    Returns:
        "cancel"  — 不关闭，继续编辑
        "discard" — 不应用，直接关闭
        "apply"   — 应用后关闭
    """
    msg_box = QMessageBox(parent)
    msg_box.setWindowTitle("确认是否应用")
    msg_box.setText(
        f"节点「{node_name}」的上下文块有未应用的修改。\n\n是否应用？"
    )
    msg_box.setIcon(QMessageBox.Icon.Question)
    cancel_btn = msg_box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    discard_btn = msg_box.addButton("不应用", QMessageBox.ButtonRole.DestructiveRole)
    apply_btn = msg_box.addButton("应用", QMessageBox.ButtonRole.AcceptRole)
    msg_box.setDefaultButton(apply_btn)
    _style_msg_box(msg_box)
    msg_box.exec()
    clicked = msg_box.clickedButton()
    if clicked is apply_btn:
        return "apply"
    if clicked is discard_btn:
        return "discard"
    return "cancel"


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
        apply_style(scroll, lambda: f"""
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
            apply_style(empty_label, lambda: f"""
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
            apply_style(clear_btn, lambda: f"""
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
        apply_style(close_btn, lambda: f"""
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
        apply_style(row, lambda: f"""
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
        apply_style(title_label, lambda: f"""
            QLabel {{
                color: {Colors.TEXT_PRIMARY};
                border: none;
                background: transparent;
            }}
        """)

        # 路径
        path_label = QLabel(entry.json_path)
        path_label.setFont(Fonts.mono(Fonts.SIZE_XS))
        apply_style(path_label, lambda: f"""
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
        apply_style(time_label, lambda: f"""
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
        apply_style(restore_btn, lambda: f"""
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
        apply_style(delete_btn, lambda: f"""
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
        apply_style(self, lambda: f"""
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


