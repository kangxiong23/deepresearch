# Layer: UI (PySide6) → widgets
# File: app/ui/widgets/attachment_dialog.py
# Responsibility: 节点挂载管理面板——列出"这个节点实际会注入哪些文件"，
#                 支持添加、移除、把旧式路径附件就地转快照。
#                 继承来的附件只读（移除会影响所有兄弟对话，必须去源节点删）。
# Input:  节点 ID + 四个回调（取数据 / 添加 / 移除 / 转快照）
# Output: 通过 exec() 弹模态对话框

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.ui.theme import apply_style, Colors, Fonts, Radius, Spacing

# 元信息列固定宽度：各行的名字列因此对齐（长文本省略，悬停看全文）
_META_WIDTH = 330


def _meta_label(text: str) -> QLabel:
    """固定宽度的元信息标签（省略过长内容，悬停显示全文）。"""
    label = QLabel()
    label.setFont(Fonts.body(Fonts.SIZE_XS))
    metrics = QFontMetrics(label.font())
    label.setText(metrics.elidedText(text, Qt.TextElideMode.ElideRight, _META_WIDTH - 8))
    label.setFixedWidth(_META_WIDTH)
    label.setToolTip(text)
    apply_style(label, lambda: f"QLabel {{ color: {Colors.TEXT_SECONDARY}; background-color: transparent; border: none; }}")
    return label


class _AttachmentDialog(QDialog):
    """
    节点附件管理。

    改动即时生效（与授权面板一致，没有"取消"语义），每次操作后重新拉取数据。
    """

    def __init__(
        self,
        parent: QWidget,
        node_id: str,
        fetch: Callable[[str], dict],
        on_add: Callable[[str, str], str],
        on_remove: Callable[[str, str], str],
        on_convert: Callable[[str, str], str],
    ) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumSize(640, 480)

        self._node_id = node_id
        self._fetch = fetch
        self._on_add = on_add
        self._on_remove = on_remove
        self._on_convert = on_convert

        self._setup_ui()
        self.reload()

    # ── UI ───────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        layout.setSpacing(Spacing.MD)

        self._title = QLabel("📎 附件")
        self._title.setFont(Fonts.body(Fonts.SIZE_LG, QFont.Weight.Bold))
        apply_style(self._title, lambda: f"QLabel {{ color: {Colors.TEXT_PRIMARY}; background-color: transparent; border: none; }}")
        layout.addWidget(self._title)

        self._desc = QLabel(
            "这些文件会作为常驻资料进入系统提示（有总量预算）。"
            "挂载时已复制快照，源文件被移动或删除都不影响。"
            "继承自上级目录的附件只能到源节点移除。"
        )
        self._desc.setWordWrap(True)
        self._desc.setFont(Fonts.body(Fonts.SIZE_SM))
        apply_style(self._desc, lambda: f"QLabel {{ color: {Colors.TEXT_SECONDARY}; background-color: transparent; border: none; }}")
        layout.addWidget(self._desc)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        apply_style(self._scroll, lambda: f"QScrollArea {{ background-color: transparent; }}")

        self._rows_host = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(Spacing.XS)
        self._rows_layout.addStretch()
        self._scroll.setWidget(self._rows_host)
        layout.addWidget(self._scroll, 1)

        self._empty_hint = QLabel("这个节点还没有挂载任何文件。")
        self._empty_hint.setFont(Fonts.body(Fonts.SIZE_SM))
        apply_style(self._empty_hint, lambda: f"QLabel {{ color: {Colors.TEXT_DISABLED}; background-color: transparent; border: none; }}")
        layout.addWidget(self._empty_hint)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(Spacing.SM)

        add_btn = self._make_button("＋ 添加文件")
        add_btn.clicked.connect(self._on_add_clicked)
        btn_row.addWidget(add_btn)

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

    @staticmethod
    def _make_button(text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFont(Fonts.body(Fonts.SIZE_SM))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_style(btn, lambda: f"""
            QPushButton {{
                color: {Colors.TEXT_PRIMARY};
                background-color: {Colors.BG_OVERLAY};
                border: 1px solid {Colors.BORDER};
                border-radius: {Radius.SM}px;
                padding: 5px 12px;
            }}
            QPushButton:hover {{ background-color: {Colors.PRIMARY}33; }}
            QPushButton:disabled {{ color: {Colors.TEXT_DISABLED}; }}
        """)
        return btn

    # ── 数据 ─────────────────────────────────

    def reload(self) -> None:
        """重新拉取数据并重建列表。"""
        data = self._fetch(self._node_id) or {}
        title = data.get("title") or "节点"
        self.setWindowTitle("📎 附件 — " + title)
        self._title.setText("📎 附件 — " + title)

        # 清空旧行（保留末尾 stretch）
        while self._rows_layout.count() > 1:
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        mounts = data.get("mounts") or []
        legacy = data.get("legacy") or []
        index = 0

        if mounts:
            self._rows_layout.insertWidget(index, self._make_section("快照挂载"))
            index += 1

        for mount in mounts:
            idx = index
            self._rows_layout.insertWidget(idx, self._make_mount_row(mount))
            index += 1

        if legacy:
            self._rows_layout.insertWidget(
                index,
                self._make_section(
                    "旧式路径附件（每次从磁盘重读，源文件移动/删除后内容就没了）"
                ),
            )
            index += 1

        for item in legacy:
            idx = index
            self._rows_layout.insertWidget(idx, self._make_legacy_row(item))
            index += 1

        self._empty_hint.setVisible(not mounts and not legacy)

    # ── 行构造 ───────────────────────────────

    def _make_section(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFont(Fonts.body(Fonts.SIZE_XS, QFont.Weight.Bold))
        apply_style(label, lambda: f"QLabel {{ color: {Colors.TEXT_SECONDARY}; }}")
        return label

    def _row_frame(self) -> tuple[QFrame, QHBoxLayout]:
        frame = QFrame()
        # 必须用 #objectName 限定选择器：QSS 的 QFrame 规则会命中它的 QFrame
        # 子类（QLabel 就是 QFrame 子类），否则每个文字标签都会各自长出一个边框。
        frame.setObjectName("attachmentRow")
        apply_style(frame, lambda: f"""
            QFrame#attachmentRow {{
                background-color: {Colors.BG_OVERLAY};
                border: 1px solid {Colors.BORDER};
                border-radius: {Radius.SM}px;
            }}
        """)
        row = QHBoxLayout(frame)
        row.setContentsMargins(Spacing.SM, 4, Spacing.SM, 4)
        row.setSpacing(Spacing.SM)
        return frame, row

    def _make_mount_row(self, mount: dict) -> QFrame:
        frame, row = self._row_frame()

        name = QLabel("📎 " + str(mount.get("name") or "附件"))
        name.setFont(Fonts.body(Fonts.SIZE_SM))
        apply_style(name, lambda: f"QLabel {{ color: {Colors.TEXT_PRIMARY}; background-color: transparent; border: none; }}")
        row.addWidget(name, 1)

        meta_parts = [
            p for p in (mount.get("size_text"), mount.get("added_text")) if p
        ]
        # 短标记在前：超长省略时先丢"继承自谁"，保住"已禁用"这种关键状态
        if not mount.get("enabled", True):
            meta_parts.append("已禁用")
        if mount.get("inherited"):
            meta_parts.append("继承自「" + str(mount.get("owner_title") or "") + "」")
        meta = _meta_label(" · ".join(meta_parts))
        row.addWidget(meta)

        remove = self._make_button("移除")
        # 继承项不允许在此移除：会影响所有兄弟对话，必须去源节点删
        remove.setEnabled(not mount.get("inherited"))
        if mount.get("inherited"):
            remove.setToolTip("继承自上级目录，请到「" + str(mount.get("owner_title") or "") + "」移除")
        else:
            remove.clicked.connect(
                lambda _=False, blob_id=mount.get("blob_id"): self._remove(blob_id)
            )
        row.addWidget(remove)
        return frame

    def _make_legacy_row(self, item: dict) -> QFrame:
        frame, row = self._row_frame()

        name = QLabel("📄 " + str(item.get("name") or item.get("path") or ""))
        name.setFont(Fonts.body(Fonts.SIZE_SM))
        apply_style(name, lambda: f"QLabel {{ color: {Colors.TEXT_PRIMARY}; background-color: transparent; border: none; }}")
        name.setToolTip(item.get("path") or "")
        row.addWidget(name, 1)

        meta_parts = []
        if not item.get("enabled", True):
            meta_parts.append("已禁用")
        if item.get("inherited"):
            meta_parts.append("继承自「" + str(item.get("owner_title") or "") + "」")
        meta = _meta_label(" · ".join(meta_parts) or "未快照")
        row.addWidget(meta)

        convert = self._make_button("转为快照")
        convert.clicked.connect(
            lambda _=False, owner=item.get("owner_id"), path=item.get("path"):
            self._convert(owner, path)
        )
        row.addWidget(convert)

        remove = self._make_button("移除")
        remove.setEnabled(not item.get("inherited"))
        if not item.get("inherited"):
            remove.clicked.connect(
                lambda _=False, owner=item.get("owner_id"), path=item.get("path"):
                self._remove(path, owner)
            )
        row.addWidget(remove)
        return frame

    # ── 操作 ─────────────────────────────────

    def _on_add_clicked(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择要挂载的文件",
            "",
            "支持的文件 (*.txt *.md *.markdown *.json *.jsonl *.docx *.pdf);;所有文件 (*.*)",
        )
        if not paths:
            return
        failures: list[str] = []
        for path in paths:
            err = self._on_add(self._node_id, path)
            if err:
                failures.append(err)
        self.reload()
        if failures:
            self._show_failures(failures)

    def _remove(self, handle: str, owner_id: str | None = None) -> None:
        err = self._on_remove(owner_id or self._node_id, handle)
        self.reload()
        if err:
            self._show_failures([err])

    def _convert(self, owner_id: str, path: str) -> None:
        err = self._on_convert(owner_id, path)
        self.reload()
        if err:
            self._show_failures([err])

    def _show_failures(self, failures: list[str]) -> None:
        text = "\n".join(dict.fromkeys(failures))
        self._desc.setText("⚠ " + text)
        apply_style(self._desc, lambda: (
            f"QLabel {{ color: {Colors.ERROR};"
            " background-color: transparent; border: none; }}"
        ))


def show_attachment_dialog(
    parent: QWidget,
    node_id: str,
    fetch: Callable[[str], dict],
    on_add: Callable[[str, str], str],
    on_remove: Callable[[str, str], str],
    on_convert: Callable[[str, str], str],
) -> None:
    """弹出节点附件管理对话框。"""
    dialog = _AttachmentDialog(parent, node_id, fetch, on_add, on_remove, on_convert)
    dialog.exec()
    # 同上：不让父窗口收养一整套用完的控件
    dialog.deleteLater()
