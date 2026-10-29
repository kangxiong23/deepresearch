# Layer: UI (PySide6) → widgets
# File: app/ui/widgets/config_dialog.py
# Responsibility: 应用配置面板——把 config.py 里的可变项做成一张表单，
#                 保存时交给 Controller 校验、落盘、写回 config。
#                 静态部署配置（API 地址、数据目录）只读展示：改它们等于换一套数据。
# Input:  values（当前生效值）/ factory（出厂默认）/ readonly（只读项）/ on_apply
# Output: 通过 exec() 弹模态对话框；关闭后由调用方 deleteLater

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.ui.theme import apply_style, Colors, Fonts, Radius, Spacing
from app.utils.app_settings import EDITABLE, Field


# (分组标题, ((键, 名称, 说明), ...))
_GROUPS: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...] = (
    ("模型与生成", (
        ("model_type", "模型", "输入框下方也能切；这里改完会同步过去"),
        ("thinking_enabled", "思考模式",
         "开启后随请求发送 reasoning_effort，并且不再发送 temperature"),
        ("reasoning_effort", "思考强度", "low / high / max，仅思考模式开启时生效"),
        ("temperature", "温度",
         "0 最确定、2 最发散；仅思考模式关闭时发送，思考开启时由服务端忽略"),
        ("max_tokens", "单次回复上限", "单位 token，太大会拖长等待"),
    )),
    ("历史与上下文", (
        ("max_history_tokens", "历史预算", "超过预算的旧消息会被截断，单位 token"),
    )),
    ("搜索", (
        ("search_enabled", "联网搜索", "输入框下方也能切；这里改完会同步过去"),
        ("search_max_results", "每次结果条数", "注入上下文的最大搜索结果数"),
    )),
    ("知识图谱", (
        ("kg_injection_enabled", "自动注入相关知识", "发消息时按当前问题查图谱并注入"),
        ("kg_max_inject", "注入条目上限", "一次最多注入多少条"),
        ("kg_extract_recent_n", "提取时分析最近 N 条", "复盘消息抽取实体/关系时的窗口"),
    )),
)


class _ConfigDialog(QDialog):
    """
    应用配置。

    改动只有点「保存」才生效（与本地文件面板的即时生效不同：这里一次改多项，
    中途的半成品值（比如温度调到一半）不该立刻影响下一轮请求）。
    """

    def __init__(
        self,
        parent: QWidget,
        values: dict,
        factory: dict,
        readonly: list[tuple[str, str]],
        on_apply: Callable[[dict], dict],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("⚙️ 应用配置")
        self.setMinimumSize(620, 620)
        self.setModal(True)

        self._factory = dict(factory or {})
        self._readonly = list(readonly or [])
        self._on_apply = on_apply
        self._widgets: dict[str, QWidget] = {}

        self._setup_ui()
        self._fill(values or {})

    # ── UI ───────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        layout.setSpacing(Spacing.MD)

        title = QLabel("⚙️ 应用配置")
        title.setFont(Fonts.body(Fonts.SIZE_LG, QFont.Weight.Bold))
        apply_style(title, lambda: f"QLabel {{ color: {Colors.TEXT_PRIMARY}; }}")
        layout.addWidget(title)

        desc = QLabel(
            "这些是 config.py 里的可变项。改动存在 data/app_settings.json，"
            "下次启动仍然生效。带「工具栏也有」的项保存后会同步到输入框下方。"
            "路径与接口地址属于部署配置，只读——要改请动 .env，"
            "运行时换数据目录会让旧数据看起来「消失」。"
        )
        desc.setWordWrap(True)
        desc.setFont(Fonts.body(Fonts.SIZE_SM))
        apply_style(desc, lambda: f"QLabel {{ color: {Colors.TEXT_SECONDARY}; }}")
        layout.addWidget(desc)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        apply_style(scroll, lambda: "QScrollArea { background-color: transparent; }")

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, Spacing.SM, 0)
        body_layout.setSpacing(Spacing.MD)

        for group_title, fields in _GROUPS:
            body_layout.addWidget(self._make_section(group_title))
            body_layout.addLayout(self._make_form(fields))

        if self._readonly:
            body_layout.addWidget(
                self._make_section("只读 · 部署配置（改 .env）")
            )
            body_layout.addLayout(self._make_readonly_rows())

        body_layout.addStretch(1)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setFont(Fonts.body(Fonts.SIZE_SM))
        apply_style(self._status, lambda: f"QLabel {{ color: {Colors.TEXT_SECONDARY}; }}")
        layout.addWidget(self._status)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(Spacing.SM)

        reset_btn = self._make_button("恢复默认")
        reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch(1)

        cancel_btn = self._make_button("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("保存")
        save_btn.setFont(Fonts.body(Fonts.SIZE_SM))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        apply_style(save_btn, lambda: f"""
            QPushButton {{
                color: {Colors.BG_BASE};
                background-color: {Colors.PRIMARY};
                border: none;
                border-radius: {Radius.SM}px;
                padding: 6px 18px;
            }}
        """)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    @staticmethod
    def _make_section(text: str) -> QLabel:
        label = QLabel(text)
        label.setFont(Fonts.body(Fonts.SIZE_XS, QFont.Weight.Bold))
        apply_style(label, lambda: f"QLabel {{ color: {Colors.TEXT_SECONDARY}; }}")
        return label

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
        """)
        return btn

    def _make_form(self, fields) -> QFormLayout:
        form = QFormLayout()
        form.setContentsMargins(Spacing.SM, 0, 0, 0)
        form.setSpacing(Spacing.SM)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        for key, label_text, hint in fields:
            spec = EDITABLE.get(key)
            if spec is None:
                continue
            widget = self._make_editor(key, spec, hint)
            self._widgets[key] = widget

            label = QLabel(label_text)
            label.setFont(Fonts.body(Fonts.SIZE_SM))
            label.setToolTip(hint)
            apply_style(label, lambda: f"QLabel {{ color: {Colors.TEXT_PRIMARY}; }}")
            form.addRow(label, widget)
        return form

    def _make_editor(self, key: str, spec: Field, hint: str) -> QWidget:
        if spec.kind == "bool":
            widget: QWidget = QCheckBox("启用")
        elif spec.kind == "int":
            widget = QSpinBox()
            widget.setRange(int(spec.minimum), int(spec.maximum))
            widget.setGroupSeparatorShown(True)
            # 去掉上下箭头：QSS 一旦接管 ::up-button 就不再画箭头，留着只是
            # 一小块看不见的点击区。直接输入 + 滚轮/↑↓ 微调更符合这套扁平风格。
            widget.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        elif spec.kind == "float":
            widget = QDoubleSpinBox()
            widget.setDecimals(2)
            widget.setSingleStep(0.05)
            widget.setRange(float(spec.minimum), float(spec.maximum))
            widget.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        else:
            widget = QComboBox()
            for option in spec.options:
                widget.addItem(option, option)

        widget.setFont(Fonts.body(Fonts.SIZE_SM))
        widget.setToolTip(hint)
        # 固定宽度上限：表单的 AllNonFixedFieldsGrow 会把控件拉满整行，
        # 一个 8,192 的输入框撑到 600px 宽既难看也没必要
        widget.setFixedWidth(200 if spec.kind == "choice" else 170)
        apply_style(widget, self._editor_style)
        return widget

    @staticmethod
    def _editor_style() -> str:
        return f"""
            QSpinBox, QDoubleSpinBox, QComboBox {{
                color: {Colors.TEXT_PRIMARY};
                background-color: {Colors.BG_ELEVATED};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 4px 8px;
            }}
            QCheckBox {{ color: {Colors.TEXT_PRIMARY}; }}
            QComboBox QAbstractItemView {{
                background-color: {Colors.BG_ELEVATED};
                color: {Colors.TEXT_PRIMARY};
                selection-background-color: {Colors.BG_OVERLAY};
            }}
        """

    def _make_readonly_rows(self) -> QFormLayout:
        form = QFormLayout()
        form.setContentsMargins(Spacing.SM, 0, 0, 0)
        form.setSpacing(Spacing.XS)
        for name, value in self._readonly:
            key_label = QLabel(name)
            key_label.setFont(Fonts.mono(Fonts.SIZE_XS))
            apply_style(key_label, lambda: f"QLabel {{ color: {Colors.TEXT_SECONDARY}; }}")
            value_label = QLabel(str(value))
            value_label.setFont(Fonts.mono(Fonts.SIZE_XS))
            value_label.setWordWrap(False)
            value_label.setToolTip(str(value))
            apply_style(value_label, lambda: f"QLabel {{ color: {Colors.TEXT_DISABLED}; }}")
            form.addRow(key_label, value_label)
        return form

    # ── 取值 / 回填 ───────────────────────────

    def _fill(self, values: dict) -> None:
        for key, widget in self._widgets.items():
            value = values.get(key)
            if value is None:
                continue
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(value))
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(value))
            elif isinstance(widget, QComboBox):
                index = widget.findData(str(value))
                if index >= 0:
                    widget.setCurrentIndex(index)

    def _collect(self) -> dict:
        out: dict = {}
        for key, widget in self._widgets.items():
            if isinstance(widget, QCheckBox):
                out[key] = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                out[key] = int(widget.value())
            elif isinstance(widget, QDoubleSpinBox):
                out[key] = round(float(widget.value()), 2)
            elif isinstance(widget, QComboBox):
                out[key] = str(widget.currentData())
        return out

    # ── 操作 ─────────────────────────────────

    def _on_save(self) -> None:
        try:
            applied = self._on_apply(self._collect())
        except Exception as exc:      # 落盘失败也要说清楚，不能静默关掉
            self._set_status(f"⚠ 保存失败：{exc}", error=True)
            return
        if not applied:
            self._set_status("⚠ 保存失败：没有可保存的配置项", error=True)
            return
        self.accept()

    def _on_reset(self) -> None:
        if not self._factory:
            self._set_status("⚠ 没有可用的出厂默认值", error=True)
            return
        self._fill(self._factory)
        self._set_status("已填入出厂默认值，点「保存」生效。")

    def _set_status(self, text: str, error: bool = False) -> None:
        self._status.setText(text)
        color = Colors.ERROR if error else Colors.TEXT_SECONDARY
        apply_style(self._status, lambda: f"QLabel {{ color: {color}; }}")


def show_config_dialog(
    parent: QWidget,
    values: dict,
    factory: dict,
    readonly: list[tuple[str, str]],
    on_apply: Callable[[dict], dict],
) -> None:
    """弹出应用配置对话框。exec() 之后立刻销毁，不留给父窗口收养。"""
    dialog = _ConfigDialog(parent, values, factory, readonly, on_apply)
    dialog.exec()
    dialog.deleteLater()
