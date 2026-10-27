# Layer: UI (PySide6) → widgets
# File: app/ui/widgets/file_access_dialog.py
# Responsibility: 本地文件访问的授权界面——开关 + 目录列表 + 增删。
#                 改动即时生效（每次操作都回调），没有"取消"语义。
# Input:  当前 enabled / roots + 变更回调
# Output: 通过 exec() 弹模态对话框

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from app.ui.theme import apply_style, Colors, Fonts, Spacing, Radius


# ══════════════════════════════════════════════════
# 本地文件访问授权
# ══════════════════════════════════════════════════

class _FileAccessDialog(QDialog):
    """
    管理"模型可读哪些本地目录"。

    改动即时生效（每次增删/切换都回调），没有"取消"语义 —— 与设置面板一致，
    避免用户改了却忘了保存。
    """

    def __init__(
        self,
        parent: QWidget,
        enabled: bool,
        roots: list[str],
        on_change: Callable[[bool, list[str]], None],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("📁 本地文件访问")
        self.setMinimumSize(580, 460)
        self.setModal(True)

        self._on_change = on_change
        self._enabled = bool(enabled)
        self._roots = list(roots)

        self._setup_ui()
        self._refresh()

    # ── UI ───────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        layout.setSpacing(Spacing.MD)

        title = QLabel("允许模型读取本地文件")
        title.setFont(Fonts.body(Fonts.SIZE_LG, QFont.Weight.Bold))
        apply_style(title, lambda: f"QLabel {{ color: {Colors.TEXT_PRIMARY}; }}")
        layout.addWidget(title)

        desc = QLabel(
            "开启后，模型可以按需检索并读取下列目录中的文件（txt / md / json / "
            "jsonl / docx），用于回答你的问题。\n"
            "全程只读：不会修改、重命名或删除任何文件，也不会打开外部程序。\n"
            "不在列表中的路径一律拒绝；云端占位文件（未下载）会被跳过。"
        )
        desc.setWordWrap(True)
        desc.setFont(Fonts.body(Fonts.SIZE_SM))
        apply_style(desc, lambda: f"QLabel {{ color: {Colors.TEXT_SECONDARY}; }}")
        layout.addWidget(desc)

        self._toggle = QCheckBox("启用本地文件访问")
        self._toggle.setChecked(self._enabled)
        self._toggle.setFont(Fonts.body(Fonts.SIZE_MD))
        apply_style(self._toggle, lambda: f"QCheckBox {{ color: {Colors.TEXT_PRIMARY}; }}")
        self._toggle.toggled.connect(self._on_toggle)
        layout.addWidget(self._toggle)

        self._list = QListWidget()
        self._list.setFont(Fonts.body(Fonts.SIZE_SM))
        apply_style(self._list, lambda: f"""
            QListWidget {{
                background-color: {Colors.BG_BASE};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: {Radius.MD}px;
                padding: {Spacing.SM}px;
            }}
            QListWidget::item {{
                padding: 6px 4px;
                border-bottom: 1px solid {Colors.DIVIDER};
            }}
            QListWidget::item:selected {{
                background-color: {Colors.BG_OVERLAY};
                color: {Colors.TEXT_PRIMARY};
            }}
        """)
        layout.addWidget(self._list, 1)

        self._empty_hint = QLabel("尚未授权任何目录，模型看不到任何本地文件。")
        self._empty_hint.setFont(Fonts.body(Fonts.SIZE_SM))
        apply_style(self._empty_hint, lambda: f"QLabel {{ color: {Colors.TEXT_DISABLED}; }}")
        layout.addWidget(self._empty_hint)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(Spacing.SM)

        self._add_btn = QPushButton("＋ 添加目录")
        self._add_btn.clicked.connect(self._on_add)
        self._remove_btn = QPushButton("－ 移除选中")
        self._remove_btn.clicked.connect(self._on_remove)
        for b in (self._add_btn, self._remove_btn):
            b.setFont(Fonts.body(Fonts.SIZE_SM))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            apply_style(b, lambda: f"""
                QPushButton {{
                    color: {Colors.TEXT_PRIMARY};
                    background-color: {Colors.BG_OVERLAY};
                    border: 1px solid {Colors.BORDER};
                    border-radius: {Radius.SM}px;
                    padding: 6px 12px;
                }}
                QPushButton:hover {{ background-color: {Colors.PRIMARY}33; }}
                QPushButton:disabled {{ color: {Colors.TEXT_DISABLED}; }}
            """)
            btn_row.addWidget(b)

        btn_row.addStretch()

        close_btn = QPushButton("关闭")
        close_btn.setFont(Fonts.body(Fonts.SIZE_SM))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        apply_style(close_btn, lambda: f"""
            QPushButton {{
                color: {Colors.BG_BASE};
                background-color: {Colors.PRIMARY};
                border: none;
                border-radius: {Radius.SM}px;
                padding: 6px 18px;
            }}
        """)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    # ── 逻辑 ─────────────────────────────────

    def _refresh(self) -> None:
        """重建列表并同步控件可用状态。"""
        self._list.clear()
        for root in self._roots:
            self._list.addItem(root)

        self._empty_hint.setVisible(not self._roots)
        active = self._enabled
        self._list.setEnabled(active)
        self._add_btn.setEnabled(active)
        self._remove_btn.setEnabled(active and bool(self._roots))

    def _emit(self) -> None:
        self._on_change(self._enabled, list(self._roots))

    def _on_toggle(self, checked: bool) -> None:
        self._enabled = bool(checked)
        self._refresh()
        self._emit()

    def _on_add(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择允许访问的目录")
        if not path:
            return
        normalized = path.replace("/", "\\").rstrip("\\")
        if normalized in self._roots:
            return
        self._roots.append(normalized)
        self._refresh()
        self._emit()

    def _on_remove(self) -> None:
        row = self._list.currentRow()
        if row < 0 or row >= len(self._roots):
            return
        self._roots.pop(row)
        self._refresh()
        self._emit()


def show_file_access_dialog(
    parent: QWidget,
    enabled: bool,
    roots: list[str],
    on_change: Callable[[bool, list[str]], None],
) -> None:
    """弹出本地文件访问授权对话框。"""
    dialog = _FileAccessDialog(parent, enabled, roots, on_change)
    dialog.exec()
