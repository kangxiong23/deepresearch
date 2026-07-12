# Layer: UI (PySide6) → widgets
# File: app/ui/widgets/kg_panel.py
# Responsibility: 知识图谱管理面板（右侧滑出抽屉）— 查看实体、关系，支持删除操作。
#                 使用 QStackedWidget 实现实体/关系标签页切换。
#                 与 Flet 版本 app/ui_flet_legacy/widgets/kg_panel.py 功能完全对等。

from __future__ import annotations
from typing import Callable

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QSizePolicy,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from app.ui.theme import Colors, Fonts, Spacing, Radius


# ──────────────────────────────────────────────
# 实体条目控件
# ──────────────────────────────────────────────


class _EntityRow(QFrame):
    """实体条目行：类型标签 + 名称 + 描述 + 删除按钮。"""

    delete_requested = Signal(str)  # entity_name

    def __init__(
        self,
        name: str,
        entity_type: str,
        description: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._name = name

        # ── 类型标签 ──────────────────────────────
        type_badge = QLabel(entity_type)
        type_badge.setFont(Fonts.mono(Fonts.SIZE_XS))
        type_badge.setStyleSheet(f"""
            QLabel {{
                color: {Colors.ACCENT};
                background-color: transparent;
                border: 1px solid {Colors.ACCENT};
                border-radius: 2px;
                padding: 2px 4px;
            }}
        """)
        type_badge.setFixedHeight(20)

        # ── 名称 ──────────────────────────────────
        name_label = QLabel(name)
        name_label.setFont(Fonts.body(Fonts.SIZE_SM, QFont.Weight.Medium))
        name_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_PRIMARY};
                background: transparent;
                border: none;
            }}
        """)

        # ── 描述 ──────────────────────────────────
        desc_label = QLabel(description or "—")
        desc_label.setFont(Fonts.body(Fonts.SIZE_XS))
        desc_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_DISABLED};
                background: transparent;
                border: none;
            }}
        """)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        text_col.addWidget(name_label)
        text_col.addWidget(desc_label)

        # ── 删除按钮 ──────────────────────────────
        delete_btn = QPushButton("🗑")
        delete_btn.setFont(Fonts.body(10))
        delete_btn.setFixedSize(22, 22)
        delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_btn.setToolTip("删除实体（同时删除相关关系）")
        delete_btn.setStyleSheet(f"""
            QPushButton {{
                color: {Colors.ERROR};
                background: transparent;
                border: none;
                padding: 2px;
            }}
            QPushButton:hover {{
                background-color: {Colors.ERROR}22;
                border-radius: 3px;
            }}
        """)
        delete_btn.clicked.connect(lambda: self.delete_requested.emit(name))

        # ── 行布局 ────────────────────────────────
        row_layout = QHBoxLayout(self)
        row_layout.setContentsMargins(Spacing.MD, Spacing.SM, Spacing.SM, Spacing.SM)
        row_layout.setSpacing(Spacing.SM)
        row_layout.addWidget(type_badge)
        row_layout.addLayout(text_col, stretch=1)
        row_layout.addWidget(delete_btn)

        self.setStyleSheet(f"""
            _EntityRow {{
                background-color: transparent;
                border-bottom: 1px solid {Colors.DIVIDER};
            }}
        """)


# ──────────────────────────────────────────────
# 关系条目控件
# ──────────────────────────────────────────────


class _RelationRow(QFrame):
    """关系条目行：SourceName [REL_TYPE] TargetName + 描述 + 删除按钮。"""

    delete_requested = Signal(str)  # relation_id

    def __init__(
        self,
        relation_id: str,
        source: str,
        relation_type: str,
        target: str,
        description: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._relation_id = relation_id

        # ── 关系行 ────────────────────────────────
        relation_row = QHBoxLayout()
        relation_row.setContentsMargins(0, 0, 0, 0)
        relation_row.setSpacing(4)

        src_label = QLabel(source)
        src_label.setFont(Fonts.body(Fonts.SIZE_SM, QFont.Weight.Medium))
        src_label.setStyleSheet(f"color: {Colors.PRIMARY}; background: transparent; border: none;")

        rel_label = QLabel(f"[{relation_type}]")
        rel_label.setFont(Fonts.mono(Fonts.SIZE_XS))
        rel_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; background: transparent; border: none;")

        tgt_label = QLabel(target)
        tgt_label.setFont(Fonts.body(Fonts.SIZE_SM, QFont.Weight.Medium))
        tgt_label.setStyleSheet(f"color: {Colors.ACCENT}; background: transparent; border: none;")

        relation_row.addWidget(src_label)
        relation_row.addWidget(rel_label)
        relation_row.addWidget(tgt_label)
        relation_row.addStretch()

        # ── 描述（可选）────────────────────────────
        desc_label = QLabel(description)
        desc_label.setFont(Fonts.body(Fonts.SIZE_XS))
        desc_label.setStyleSheet(f"color: {Colors.TEXT_DISABLED}; background: transparent; border: none;")
        desc_label.setVisible(bool(description))

        # ── 文本列 ────────────────────────────────
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        text_col.addLayout(relation_row)
        text_col.addWidget(desc_label)

        # ── 删除按钮 ──────────────────────────────
        delete_btn = QPushButton("🗑")
        delete_btn.setFont(Fonts.body(10))
        delete_btn.setFixedSize(22, 22)
        delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_btn.setToolTip("删除此关系")
        delete_btn.setStyleSheet(f"""
            QPushButton {{
                color: {Colors.ERROR};
                background: transparent;
                border: none;
                padding: 2px;
            }}
            QPushButton:hover {{
                background-color: {Colors.ERROR}22;
                border-radius: 3px;
            }}
        """)
        delete_btn.clicked.connect(
            lambda: self.delete_requested.emit(relation_id)
        )

        # ── 行布局 ────────────────────────────────
        row_layout = QHBoxLayout(self)
        row_layout.setContentsMargins(Spacing.MD, Spacing.SM, Spacing.SM, Spacing.SM)
        row_layout.setSpacing(Spacing.SM)
        row_layout.addLayout(text_col, stretch=1)
        row_layout.addWidget(delete_btn)

        self.setStyleSheet(f"""
            _RelationRow {{
                background-color: transparent;
                border-bottom: 1px solid {Colors.DIVIDER};
            }}
        """)


# ──────────────────────────────────────────────
# 空状态提示
# ──────────────────────────────────────────────


def _empty_hint(text: str) -> QLabel:
    """创建居中空状态提示标签。"""
    label = QLabel(text)
    label.setFont(Fonts.body(Fonts.SIZE_SM))
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet(f"""
        QLabel {{
            color: {Colors.TEXT_DISABLED};
            background: transparent;
            border: none;
            padding: {Spacing.XL}px;
        }}
    """)
    return label


# ──────────────────────────────────────────────
# 知识图谱管理面板主体
# ──────────────────────────────────────────────


class KGPanel(QWidget):
    """
    知识图谱管理面板（右侧滑出抽屉，380px 宽）。

    公开接口：
        show_panel()                                  — 显示面板
        hide_panel()                                  — 隐藏面板
        load_data(entities, relations, stats)          — 加载全部数据

    信号：
        delete_entity(str)     — 删除实体（entity_name）
        delete_relation(str)   — 删除关系（relation_id）
        close_requested()      — 关闭面板
    """

    delete_entity = Signal(str)
    delete_relation = Signal(str)
    close_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("kgPanel")
        self.setFixedWidth(380)

        self._active_tab: str = "entity"

        # ── 头部 ──────────────────────────────────
        header = self._build_header()

        # ── 自定义 Tab 切换栏 ─────────────────────
        tab_bar = self._build_tab_bar()

        # ── 内容区（Stacked Widget）───────────────
        self._stack = QStackedWidget()

        # 实体列表
        self._entities_container = QWidget()
        self._entities_layout = QVBoxLayout(self._entities_container)
        self._entities_layout.setContentsMargins(0, 0, 0, 0)
        self._entities_layout.setSpacing(0)

        entities_scroll = QScrollArea()
        entities_scroll.setWidgetResizable(True)
        entities_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        entities_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        entities_scroll.setWidget(self._entities_container)

        # 关系列表
        self._relations_container = QWidget()
        self._relations_layout = QVBoxLayout(self._relations_container)
        self._relations_layout.setContentsMargins(0, 0, 0, 0)
        self._relations_layout.setSpacing(0)

        relations_scroll = QScrollArea()
        relations_scroll.setWidgetResizable(True)
        relations_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        relations_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        relations_scroll.setWidget(self._relations_container)

        self._stack.addWidget(entities_scroll)   # index 0 — 实体
        self._stack.addWidget(relations_scroll)  # index 1 — 关系
        self._stack.setCurrentIndex(0)

        # ── 整体布局 ──────────────────────────────
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(header)
        main_layout.addWidget(tab_bar)
        main_layout.addWidget(self._stack, stretch=1)

        # ── 整体样式 ──────────────────────────────
        self.setStyleSheet(f"""
            KGPanel {{
                background-color: {Colors.BG_SURFACE};
                border-left: 1px solid {Colors.BORDER};
            }}
        """)

        # 默认隐藏
        self.setVisible(False)

    # ── 头部 ──────────────────────────────────────

    def _build_header(self) -> QWidget:
        """构建面板头部：图标 + 标题 + 统计 + 关闭按钮。"""
        header = QWidget()
        header.setStyleSheet(f"""
            QWidget {{
                background-color: transparent;
                border-bottom: 1px solid {Colors.DIVIDER};
            }}
        """)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(Spacing.LG, Spacing.MD, Spacing.MD, Spacing.MD)
        layout.setSpacing(Spacing.SM)

        icon = QLabel("🔗")
        icon.setFont(Fonts.body(18))
        icon.setStyleSheet("background: transparent; border: none;")

        title = QLabel("知识图谱")
        title.setFont(Fonts.mono(Fonts.SIZE_LG, QFont.Weight.DemiBold))
        title.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent; border: none;")

        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addStretch()

        self._stats_label = QLabel("")
        self._stats_label.setFont(Fonts.mono(Fonts.SIZE_XS))
        self._stats_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_SECONDARY};
                background: transparent;
                border: none;
            }}
        """)
        layout.addWidget(self._stats_label)

        close_btn = QPushButton("✕")
        close_btn.setFont(Fonts.body(14))
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setToolTip("关闭")
        close_btn.setStyleSheet(f"""
            QPushButton {{
                color: {Colors.TEXT_SECONDARY};
                background: transparent;
                border: none;
                padding: 4px;
            }}
            QPushButton:hover {{
                color: {Colors.TEXT_PRIMARY};
                background-color: {Colors.BG_OVERLAY};
                border-radius: 4px;
            }}
        """)
        close_btn.clicked.connect(self.close_requested.emit)
        layout.addWidget(close_btn)

        return header

    # ── Tab 切换栏 ───────────────────────────────

    def _build_tab_bar(self) -> QWidget:
        """构建自定义 Tab 切换栏（实体 | 关系）。"""
        tab_bar = QWidget()
        tab_bar.setStyleSheet(f"""
            QWidget {{
                background-color: transparent;
                border-bottom: 1px solid {Colors.DIVIDER};
            }}
        """)

        layout = QHBoxLayout(tab_bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._entity_tab_btn = QPushButton("实体")
        self._entity_tab_btn.setFont(Fonts.body(Fonts.SIZE_SM))
        self._entity_tab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._entity_tab_btn.setFlat(True)
        self._entity_tab_btn.clicked.connect(lambda: self._switch_tab("entity"))
        self._apply_tab_style(self._entity_tab_btn, active=True)

        self._relation_tab_btn = QPushButton("关系")
        self._relation_tab_btn.setFont(Fonts.body(Fonts.SIZE_SM))
        self._relation_tab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._relation_tab_btn.setFlat(True)
        self._relation_tab_btn.clicked.connect(lambda: self._switch_tab("relation"))
        self._apply_tab_style(self._relation_tab_btn, active=False)

        layout.addWidget(self._entity_tab_btn)
        layout.addWidget(self._relation_tab_btn)
        layout.addStretch()

        return tab_bar

    def _apply_tab_style(self, btn: QPushButton, active: bool) -> None:
        """应用 Tab 按钮的激活/非激活样式。"""
        color = Colors.PRIMARY if active else Colors.TEXT_SECONDARY
        btn.setStyleSheet(f"""
            QPushButton {{
                color: {color};
                background-color: transparent;
                border: none;
                border-bottom: 2px solid {color if active else 'transparent'};
                padding: 8px 16px;
                font-size: {Fonts.SIZE_SM}px;
            }}
            QPushButton:hover {{
                color: {Colors.PRIMARY};
            }}
        """)

    def _switch_tab(self, tab: str) -> None:
        """切换实体/关系标签页。"""
        self._active_tab = tab
        is_entity = (tab == "entity")
        self._stack.setCurrentIndex(0 if is_entity else 1)
        self._apply_tab_style(self._entity_tab_btn, active=is_entity)
        self._apply_tab_style(self._relation_tab_btn, active=not is_entity)

    # ──────────────────────────────────────────────
    # 公开接口
    # ──────────────────────────────────────────────

    def show_panel(self) -> None:
        """显示面板。"""
        self.setVisible(True)

    def hide_panel(self) -> None:
        """隐藏面板。"""
        self.setVisible(False)

    def is_visible(self) -> bool:
        """面板是否可见。"""
        return self.isVisible()

    def load_data(
        self,
        entities: list[dict],
        relations: list[dict],
        stats: dict,
    ) -> None:
        """
        加载知识图谱数据。

        Args:
            entities: 实体列表，每项含 name, entity_type, description
            relations: 关系列表，每项含 id, source_entity_name, relation_type,
                       target_entity_name, description
            stats: 统计信息，含 entity_count, relation_count
        """
        # ── 更新统计 ──────────────────────────────
        ec = stats.get("entity_count", 0)
        rc = stats.get("relation_count", 0)
        self._stats_label.setText(f"{ec} 实体 · {rc} 关系")

        # ── 重建实体列表 ──────────────────────────
        self._clear_layout(self._entities_layout)
        if not entities:
            self._entities_layout.addWidget(
                _empty_hint("暂无实体\n点击 AI 回复下方的书签按钮开始提取")
            )
        else:
            for e in entities:
                row = _EntityRow(
                    name=e.get("name", ""),
                    entity_type=e.get("entity_type", ""),
                    description=e.get("description", ""),
                )
                row.delete_requested.connect(self.delete_entity.emit)
                self._entities_layout.addWidget(row)
        self._entities_layout.addStretch()

        # ── 重建关系列表 ──────────────────────────
        self._clear_layout(self._relations_layout)
        if not relations:
            self._relations_layout.addWidget(_empty_hint("暂无关系"))
        else:
            for r in relations:
                row = _RelationRow(
                    relation_id=r.get("id", ""),
                    source=r.get("source_entity_name", ""),
                    relation_type=r.get("relation_type", ""),
                    target=r.get("target_entity_name", ""),
                    description=r.get("description", ""),
                )
                row.delete_requested.connect(self.delete_relation.emit)
                self._relations_layout.addWidget(row)
        self._relations_layout.addStretch()

    @staticmethod
    def _clear_layout(layout) -> None:
        """清空布局中的所有子控件。"""
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            sub_layout = item.layout()
            if sub_layout:
                KGPanel._clear_layout(sub_layout)
                sub_layout.deleteLater()
