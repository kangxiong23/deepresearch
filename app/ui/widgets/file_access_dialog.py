# Layer: UI (PySide6) → widgets
# File: app/ui/widgets/file_access_dialog.py
# Responsibility: 本地文件夹访问权限的统一管理界面 —— 全局授权、各文件夹节点、
#                 各对话节点集中在一处查看 / 添加 / 移除。
#                 改动即时生效（每次操作都回调），没有"取消"语义。
# Input:  fetch() 取当前授权快照 + 三个回调（全局写盘 / 节点添加 / 节点移除）
# Output: 通过 exec() 弹模态对话框

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui.theme import apply_style, Colors, Fonts, Radius, Spacing
from app.utils.file_access_settings import normalize_root, root_key

# 树项的三种角色：分组标题 / 节点 / 具体目录
_ROLE_KIND = int(Qt.ItemDataRole.UserRole)
_ROLE_SCOPE = _ROLE_KIND + 1
_ROLE_PATH = _ROLE_KIND + 2

_KIND_GROUP = "group"
_KIND_NODE = "node"
_KIND_PATH = "path"

_SCOPE_GLOBAL = "global"
_SCOPE_NODE_GROUP = "node-group"

_DESC = (
    "模型可以按需检索并读取下列目录中的文件（txt / md / json / jsonl / docx），"
    "用来回答你的问题。全程只读：不修改、不重命名、不删除，也不会打开外部程序。\n"
    "生效范围：全局授权 + 当前对话所在文件夹链 + 该对话自身。"
    "一个目录都没有授权时，请求里不会带上文件工具。"
)


def _dim_color() -> QBrush:
    return QBrush(QColor(Colors.TEXT_DISABLED))


class _FileAccessDialog(QDialog):
    """
    本地文件夹权限的统一管理。

    三层作用域在同一棵树里列出来：
        全局授权   —— 所有对话可用（有自己的总开关）
        文件夹节点 —— 该目录下所有对话可用（含子文件夹）
        对话节点   —— 只有该对话可用
    节点级授权只能在树里右键添加，这里负责查看与撤销；选中某个节点后
    "添加目录"按钮会把目录加到那个节点上，选中全局组则加到全局。
    """

    def __init__(
        self,
        parent: QWidget,
        fetch: Callable[[], dict],
        on_global: Callable[[bool, list[str]], None],
        on_add_node_root: Callable[[str, str], str],
        on_remove_node_root: Callable[[str, str], str],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("📁 本地文件夹权限")
        self.setMinimumSize(700, 600)
        self.setModal(True)

        self._fetch = fetch
        self._on_global = on_global
        self._on_add_node_root = on_add_node_root
        self._on_remove_node_root = on_remove_node_root

        self._data: dict = {}
        self._global_item: QTreeWidgetItem | None = None
        self._folder_group: QTreeWidgetItem | None = None
        self._conv_group: QTreeWidgetItem | None = None

        self._setup_ui()
        self.reload()

    # ── UI ───────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        layout.setSpacing(Spacing.MD)

        title = QLabel("📁 本地文件夹权限")
        title.setFont(Fonts.body(Fonts.SIZE_LG, QFont.Weight.Bold))
        apply_style(title, lambda: f"QLabel {{ color: {Colors.TEXT_PRIMARY}; }}")
        layout.addWidget(title)

        desc = QLabel(_DESC)
        desc.setWordWrap(True)
        desc.setFont(Fonts.body(Fonts.SIZE_SM))
        apply_style(desc, lambda: f"QLabel {{ color: {Colors.TEXT_SECONDARY}; }}")
        layout.addWidget(desc)

        self._toggle = QCheckBox("启用全局授权（对所有对话生效）")
        self._toggle.setFont(Fonts.body(Fonts.SIZE_MD))
        apply_style(self._toggle, lambda: f"QCheckBox {{ color: {Colors.TEXT_PRIMARY}; }}")
        self._toggle.toggled.connect(self._on_toggle)
        layout.addWidget(self._toggle)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setExpandsOnDoubleClick(True)
        self._tree.setFont(Fonts.body(Fonts.SIZE_SM))
        apply_style(self._tree, lambda: f"""
            QTreeWidget {{
                background-color: {Colors.BG_BASE};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: {Radius.MD}px;
                padding: {Spacing.XS}px;
            }}
            QTreeWidget::item {{ padding: 3px 2px; }}
            QTreeWidget::item:hover {{ background-color: {Colors.BG_ELEVATED}; }}
            QTreeWidget::item:selected {{
                background-color: {Colors.BG_OVERLAY};
                color: {Colors.TEXT_PRIMARY};
            }}
        """)
        self._tree.itemSelectionChanged.connect(self._refresh_buttons)
        layout.addWidget(self._tree, 1)

        self._empty_hint = QLabel(
            "尚未授权任何目录，模型看不到任何本地文件。"
            "节点级授权在左侧树里右键文件夹或对话添加。"
        )
        self._empty_hint.setWordWrap(True)
        self._empty_hint.setFont(Fonts.body(Fonts.SIZE_SM))
        apply_style(self._empty_hint, lambda: f"QLabel {{ color: {Colors.TEXT_DISABLED}; }}")
        layout.addWidget(self._empty_hint)

        self._status = QLabel("选中哪个作用域，点「添加目录」就加到哪个作用域。")
        self._status.setWordWrap(True)
        self._status.setFont(Fonts.body(Fonts.SIZE_SM))
        apply_style(self._status, lambda: f"QLabel {{ color: {Colors.TEXT_SECONDARY}; }}")
        layout.addWidget(self._status)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(Spacing.SM)

        self._add_btn = self._make_button("＋ 添加目录")
        self._add_btn.clicked.connect(self._on_add)
        self._remove_btn = self._make_button("－ 移除选中")
        self._remove_btn.clicked.connect(self._on_remove)
        btn_row.addWidget(self._add_btn)
        btn_row.addWidget(self._remove_btn)
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
                padding: 6px 12px;
            }}
            QPushButton:hover {{ background-color: {Colors.PRIMARY}33; }}
            QPushButton:disabled {{ color: {Colors.TEXT_DISABLED}; }}
        """)
        return btn

    # ── 数据 ─────────────────────────────────

    def reload(self) -> None:
        """重新拉取快照并重建整棵树（保留原选中项）。"""
        key = self._selection_key()
        self._data = self._fetch() or {}

        self._toggle.blockSignals(True)
        self._toggle.setChecked(bool(self._data.get("enabled")))
        self._toggle.blockSignals(False)

        self._rebuild(key)

    def _rebuild(self, key) -> None:
        roots = [str(r) for r in (self._data.get("global_roots") or []) if r]
        nodes = list(self._data.get("nodes") or [])

        self._tree.clear()
        self._global_item = self._add_group(self._global_label(), _SCOPE_GLOBAL)
        for path in roots:
            self._add_path_item(self._global_item, _SCOPE_GLOBAL, path)
        if not roots:
            self._add_placeholder(self._global_item, "（暂无）")

        self._folder_group = self._add_group(
            "文件夹节点 · 对其下所有对话生效", _SCOPE_NODE_GROUP
        )
        self._conv_group = self._add_group(
            "对话节点 · 只对该对话生效", _SCOPE_NODE_GROUP
        )
        counters = {"folder": 0, "conversation": 0}
        for node in nodes:
            is_folder = node.get("node_type") == "folder"
            target = self._folder_group if is_folder else self._conv_group
            self._add_node_item(target, node)
            counters["folder" if is_folder else "conversation"] += 1
        if not counters["folder"]:
            self._add_placeholder(self._folder_group, "（暂无，右键左侧树节点添加）")
        if not counters["conversation"]:
            self._add_placeholder(self._conv_group, "（暂无，右键左侧树节点添加）")

        for group in (self._global_item, self._folder_group, self._conv_group):
            group.setExpanded(True)

        has_any = bool(roots) or bool(nodes)
        self._empty_hint.setVisible(not has_any)
        self._restore_selection(key)
        self._refresh_buttons()

    def _add_group(self, text: str, scope: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(self._tree, [text])
        item.setData(0, _ROLE_KIND, _KIND_GROUP)
        item.setData(0, _ROLE_SCOPE, scope)
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        item.setForeground(0, QBrush(QColor(Colors.TEXT_SECONDARY)))
        return item

    def _add_placeholder(self, parent: QTreeWidgetItem, text: str) -> None:
        item = QTreeWidgetItem(parent, [text])
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setForeground(0, _dim_color())

    def _add_node_item(self, parent: QTreeWidgetItem, node: dict) -> None:
        title = str(node.get("title") or "未命名")
        roots = [str(r) for r in (node.get("roots") or []) if r]
        icon = "📁 " if node.get("node_type") == "folder" else "💬 "
        label = f"{icon}{title} · {len(roots)} 个目录"
        if not node.get("enabled", True):
            label += " · 已禁用（授权不生效）"

        item = QTreeWidgetItem(parent, [label])
        item.setData(0, _ROLE_KIND, _KIND_NODE)
        item.setData(0, _ROLE_SCOPE, str(node.get("id") or ""))
        item.setToolTip(0, "位置：" + str(node.get("path") or ""))
        if not node.get("enabled", True):
            item.setForeground(0, _dim_color())
        item.setExpanded(True)

        for path in roots:
            self._add_path_item(item, str(node.get("id") or ""), path)

    def _add_path_item(
        self, parent: QTreeWidgetItem, scope: str, path: str
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [str(path)])
        item.setData(0, _ROLE_KIND, _KIND_PATH)
        item.setData(0, _ROLE_SCOPE, scope)
        item.setData(0, _ROLE_PATH, str(path))
        item.setToolTip(0, str(path))
        return item

    def _global_label(self) -> str:
        suffix = "" if self._toggle.isChecked() else " · 已关闭"
        return "全局授权 · 对所有对话生效" + suffix

    # ── 选中项 ───────────────────────────────

    def _selection_key(self):
        item = self._tree.currentItem()
        while item is not None:
            kind = item.data(0, _ROLE_KIND)
            if kind == _KIND_PATH:
                return (
                    _KIND_PATH,
                    str(item.data(0, _ROLE_SCOPE) or ""),
                    str(item.data(0, _ROLE_PATH) or ""),
                )
            if kind == _KIND_NODE:
                return (_KIND_NODE, str(item.data(0, _ROLE_SCOPE) or ""))
            item = item.parent()
        return None

    def _restore_selection(self, key) -> None:
        found: QTreeWidgetItem | None = None

        def walk(item: QTreeWidgetItem) -> None:
            nonlocal found
            if found is not None:
                return
            kind = item.data(0, _ROLE_KIND)
            scope = str(item.data(0, _ROLE_SCOPE) or "")
            if (
                key is not None
                and key[0] == _KIND_PATH
                and kind == _KIND_PATH
                and scope == key[1]
                and str(item.data(0, _ROLE_PATH) or "") == key[2]
            ):
                found = item
                return
            if (
                key is not None
                and key[0] == _KIND_NODE
                and kind == _KIND_NODE
                and scope == key[1]
            ):
                found = item
                return
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self._tree.topLevelItemCount()):
            walk(self._tree.topLevelItem(i))
        self._tree.setCurrentItem(found or self._global_item)

    def _target(self) -> tuple[str, str, str]:
        """当前选中项对应的添加目标：(kind, node_id, 显示名)。"""
        item = self._tree.currentItem()
        while item is not None:
            kind = item.data(0, _ROLE_KIND)
            scope = str(item.data(0, _ROLE_SCOPE) or "")
            if kind in (_KIND_PATH, _KIND_NODE):
                if scope == _SCOPE_GLOBAL or not scope:
                    return ("global", "", "全局")
                return ("node", scope, self._scope_title(scope))
            if kind == _KIND_GROUP:
                if scope == _SCOPE_GLOBAL:
                    return ("global", "", "全局")
                return ("none", "", "")
            item = item.parent()
        return ("global", "", "全局")

    def _scope_title(self, node_id: str) -> str:
        for node in (self._data.get("nodes") or []):
            if str(node.get("id") or "") == node_id:
                return str(node.get("title") or "节点")
        return "节点"

    def _refresh_buttons(self) -> None:
        """按钮文案跟着选中项走：加到哪里、能不能加。"""
        kind, _node_id, name = self._target()
        if kind == "none":
            self._add_btn.setText("＋ 添加目录")
            self._add_btn.setEnabled(False)
            self._add_btn.setToolTip("先选中全局授权或某个节点")
        elif kind == "global":
            self._add_btn.setText("＋ 添加到全局")
            active = self._toggle.isChecked()
            self._add_btn.setEnabled(active)
            self._add_btn.setToolTip("" if active else "全局授权已关闭，勾选上方开关后再添加")
        else:
            self._add_btn.setText("＋ 添加到「" + self._elide(name) + "」")
            self._add_btn.setEnabled(True)
            self._add_btn.setToolTip("只对这个节点生效")

        item = self._tree.currentItem()
        is_path = item is not None and item.data(0, _ROLE_KIND) == _KIND_PATH
        scope = str(item.data(0, _ROLE_SCOPE) or "") if item is not None else ""
        global_locked = scope == _SCOPE_GLOBAL and not self._toggle.isChecked()
        self._remove_btn.setEnabled(bool(is_path) and not global_locked)
        self._remove_btn.setToolTip(
            "" if not global_locked else "全局授权已关闭，勾选上方开关后再移除"
        )

    @staticmethod
    def _elide(text: str, limit: int = 14) -> str:
        return text if len(text) <= limit else text[: limit - 1] + "…"

    # ── 操作 ─────────────────────────────────

    def _on_toggle(self, checked: bool) -> None:
        self._on_global(
            bool(checked), [str(r) for r in (self._data.get("global_roots") or []) if r]
        )
        self.reload()

    def _on_add(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择允许模型读取的目录")
        if path:
            self._add_path(path)

    def _add_path(self, path: str) -> None:
        """把目录加到当前选中项（全局或某个节点）。"""
        kind, node_id, name = self._target()
        if kind == "none":
            return
        target = normalize_root(path)
        if not target:
            return

        if kind == "global":
            current = [str(r) for r in (self._data.get("global_roots") or []) if r]
            if any(root_key(r) == root_key(target) for r in current):
                self._set_status("该目录已在全局授权中：" + target)
                return
            current.append(target)
            self._on_global(bool(self._toggle.isChecked()), current)
            self.reload()
            self._set_status("已添加到全局授权：" + target)
            return

        err = self._on_add_node_root(node_id, target)
        self.reload()
        if err:
            self._set_status("⚠ " + err, error=True)
        else:
            self._set_status("已授权给「" + name + "」：" + target)

    def _on_remove(self) -> None:
        item = self._tree.currentItem()
        if item is None or item.data(0, _ROLE_KIND) != _KIND_PATH:
            return
        scope = str(item.data(0, _ROLE_SCOPE) or "")
        path = str(item.data(0, _ROLE_PATH) or "")
        if not path:
            return

        if scope == _SCOPE_GLOBAL or not scope:
            current = [
                str(r) for r in (self._data.get("global_roots") or [])
                if r and root_key(r) != root_key(path)
            ]
            self._on_global(bool(self._toggle.isChecked()), current)
            self.reload()
            self._set_status("已从全局授权移除：" + path)
            return

        err = self._on_remove_node_root(scope, path)
        self.reload()
        if err:
            self._set_status("⚠ " + err, error=True)
        else:
            self._set_status("已移除：" + path)

    def _set_status(self, text: str, error: bool = False) -> None:
        self._status.setText(text)
        color = Colors.ERROR if error else Colors.TEXT_SECONDARY
        apply_style(self._status, lambda: f"QLabel {{ color: {color}; }}")


def show_file_access_dialog(
    parent: QWidget,
    fetch: Callable[[], dict],
    on_global: Callable[[bool, list[str]], None],
    on_add_node_root: Callable[[str, str], str],
    on_remove_node_root: Callable[[str, str], str],
) -> None:
    """弹出本地文件夹权限管理对话框。"""
    dialog = _FileAccessDialog(
        parent, fetch, on_global, on_add_node_root, on_remove_node_root
    )
    dialog.exec()
