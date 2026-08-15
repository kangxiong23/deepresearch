# Layer: UI (PySide6) → widgets
# File: app/ui/widgets/chat_message.py
# Responsibility: 聊天消息气泡 — 角色标签、Markdown 渲染、操作按钮、流式更新、思考块
# 与 Flet 版本 app/ui_flet_legacy/widgets/chat_message.py 视觉完全一致

from __future__ import annotations

import markdown as _md_lib

from PySide6.QtWidgets import (
    QFrame,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTextBrowser,
    QPushButton,
    QSizePolicy,
    QScrollBar,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor

from app.ui.theme import apply_style, Colors, Fonts, Spacing, Radius


# ──────────────────────────────────────────────
# Markdown → HTML 转换器（深色主题）
# ──────────────────────────────────────────────

_MD_EXTENSIONS = ["fenced_code", "codehilite", "tables", "nl2br"]

# 深色主题 HTML 模板
# Qt QTextBrowser 使用的富文本引擎仅支持 CSS 2.1 子集；
# 所有字号/粗细/颜色必须显式声明，否则回退到 body 默认值。
_HTML_TEMPLATE = """<!DOCTYPE html>
<html><head><style>
body {{
    font-family: "{body_font}";
    font-size: {font_size}px;
    color: {text_color};
    background-color: transparent;
    margin: 0;
    padding: 0;
    line-height: 1.5;
}}
h1 {{
    font-size: {h1_size}px;
    font-weight: bold;
    color: {text_color};
    margin: 12px 0 6px 0;
    padding-bottom: 4px;
    border-bottom: 1px solid {border_color};
}}
h2 {{
    font-size: {h2_size}px;
    font-weight: bold;
    color: {text_color};
    margin: 10px 0 4px 0;
}}
h3 {{
    font-size: {h3_size}px;
    font-weight: bold;
    color: {text_color};
    margin: 8px 0 4px 0;
}}
h4, h5, h6 {{
    font-size: {h4_size}px;
    font-weight: bold;
    color: {text_color};
    margin: 6px 0 2px 0;
}}
strong, b {{ font-weight: bold; }}
em, i {{ font-style: italic; }}
p {{ margin: 4px 0; }}
pre {{
    background-color: {code_bg};
    border: 1px solid {border_color};
    border-radius: 4px;
    padding: 8px 12px;
    overflow-x: auto;
    font-family: "{mono_font}", monospace;
    font-size: {code_font_size}px;
    line-height: 1.4;
}}
code {{
    font-family: "{mono_font}", monospace;
    font-size: {code_font_size}px;
    color: {text_code};
    background-color: {code_bg};
    padding: 1px 4px;
    border-radius: 2px;
}}
pre code {{
    background-color: transparent;
    padding: 0;
    color: {text_code};
}}
blockquote {{
    border-left: 2px solid {accent_color};
    margin: 8px 0;
    padding: 4px 12px;
    color: {text_secondary};
}}
a {{ color: {primary_color}; }}
ul {{
    margin: 4px 0;
    padding-left: 20px;
    list-style-type: disc;
}}
ol {{
    margin: 4px 0;
    padding-left: 20px;
    list-style-type: decimal;
}}
ul ul {{ list-style-type: circle; }}
ul ul ul {{ list-style-type: square; }}
li {{ margin: 2px 0; }}
table {{
    border-collapse: collapse;
    margin: 8px 0;
}}
th, td {{
    border: 1px solid {border_color};
    padding: 4px 8px;
}}
th {{ background-color: {code_bg}; }}
hr {{
    border: none;
    border-top: 1px solid {border_color};
    margin: 12px 0;
}}
</style></head><body>{content}</body></html>"""


def _render_markdown(text: str) -> str:
    """将 markdown 文本转换为深色主题 HTML。"""
    html_body = _md_lib.markdown(
        text,
        extensions=_MD_EXTENSIONS,
    )
    # 标题字号逐级递减：h1=1.6x, h2=1.4x, h3=1.2x, h4+ = 正文尺寸
    base = Fonts.SIZE_MD
    return _HTML_TEMPLATE.format(
        body_font=Fonts.BODY,
        font_size=base,
        h1_size=int(base * 1.6),
        h2_size=int(base * 1.4),
        h3_size=int(base * 1.2),
        h4_size=base,
        text_color=Colors.TEXT_PRIMARY,
        code_bg=Colors.BG_BASE,
        border_color=Colors.BORDER,
        mono_font=Fonts.MONO,
        code_font_size=Fonts.SIZE_SM,
        text_code=Colors.TEXT_CODE,
        accent_color=Colors.ACCENT,
        text_secondary=Colors.TEXT_SECONDARY,
        primary_color=Colors.PRIMARY,
        content=html_body,
    )


# ──────────────────────────────────────────────
# 角色标签
# ──────────────────────────────────────────────


def _role_badge(role: str) -> QLabel:
    """创建角色标签控件。与原 Flet 版本 _role_badge() 视觉完全一致。"""
    label_map = {
        "user":      ("YOU",      Colors.ROLE_USER),
        "assistant": ("DEEP",     Colors.ROLE_ASSISTANT),
        "thinking":  ("THINKING", Colors.ROLE_THINKING),
    }
    text, color = label_map.get(role, ("???", Colors.TEXT_SECONDARY))

    badge = QLabel(text)
    badge.setFont(Fonts.mono(Fonts.SIZE_XS, QFont.Weight.Bold))
    badge.setStyleSheet(f"""
        QLabel {{
            color: {color};
            background-color: transparent;
            border: 1px solid {color};
            border-radius: {Radius.SM}px;
            padding: 2px 6px;
        }}
    """)
    badge.setFixedHeight(22)
    badge.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
    return badge


# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────

MAX_CONTENT_HEIGHT = 2000  # 单条消息最大可见高度 (px)，超出启用内部滚动

# ──────────────────────────────────────────────
# 思考块
# ──────────────────────────────────────────────


class ThinkingBlock(QFrame):
    """
    可折叠的思考内容块。与原 Flet 版本 ThinkingBlock 视觉完全一致。

    流式使用：
        block.start_stream()       # 可选，初始化缓冲
        block.append_text(delta)   # 追加文本，自动刷新
    """

    # 当内容变化时发出信号（用于外部 page.update 等效操作）
    content_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("thinkingBlock")

        self._expanded: bool = False  # 默认折叠（折叠状态不持久化）
        self._buffer: str = ""
        self.message_id: str = ""  # 供滚动定位注册（与 ChatMessage 对齐）

        # ── 折叠按钮 ────────────────────────────
        self._toggle_btn = QPushButton("▾")
        self._toggle_btn.setFont(Fonts.body(Fonts.SIZE_SM))
        self._toggle_btn.setFixedSize(20, 20)
        apply_style(self._toggle_btn, lambda: f"""
            QPushButton {{
                color: {Colors.TEXT_SECONDARY};
                background-color: transparent;
                border: none;
                padding: 0;
            }}
            QPushButton:hover {{
                color: {Colors.TEXT_PRIMARY};
            }}
        """)
        self._toggle_btn.clicked.connect(self._on_toggle)

        # ── 头行：角色标签 + 空格 + 折叠按钮 ──
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(Spacing.SM)
        header_row.addWidget(_role_badge("thinking"))
        header_row.addStretch()
        header_row.addWidget(self._toggle_btn)

        header_widget = QWidget()
        header_widget.setLayout(header_row)
        header_widget.setCursor(Qt.CursorShape.PointingHandCursor)
        header_widget.mousePressEvent = lambda e: self._on_toggle()

        # ── 内容区 ──────────────────────────────
        self._content_browser = QTextBrowser()
        self._content_browser.setOpenExternalLinks(True)
        self._content_browser.setReadOnly(True)
        self._content_browser.setFont(Fonts.mono(Fonts.SIZE_SM))
        apply_style(self._content_browser, lambda: f"""
            QTextBrowser {{
                color: {Colors.TEXT_SECONDARY};
                background-color: transparent;
                border: none;
                padding: 0;
            }}
        """)
        self._content_browser.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        self._content_browser.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._content_browser.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        # ── 整体布局 ────────────────────────────
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            Spacing.MD, Spacing.SM, Spacing.MD, Spacing.SM
        )
        layout.setSpacing(Spacing.SM)
        layout.addWidget(header_widget)
        layout.addWidget(self._content_browser)

        # ── 底部边框 ────────────────────────────
        apply_style(self, lambda: f"""
            ThinkingBlock {{
                background-color: transparent;
                border: none;
                border-bottom: 1px solid {Colors.DIVIDER};
            }}
        """)

        # 初始高度
        self._update_content_height()

    def _on_toggle(self) -> None:
        """切换展开/折叠。"""
        self._expanded = not self._expanded
        self._content_browser.setVisible(self._expanded)
        self._toggle_btn.setText("▾" if self._expanded else "▸")
        self._update_content_height()
        self.content_changed.emit()

    def set_expanded(self, expanded: bool) -> None:
        """程序化设置展开/折叠状态（用于重建时保持原有状态）。"""
        if self._expanded == expanded:
            return
        self._expanded = expanded
        self._content_browser.setVisible(self._expanded)
        self._toggle_btn.setText("▾" if self._expanded else "▸")
        self._update_content_height()
        self.content_changed.emit()

    @property
    def is_expanded(self) -> bool:
        """当前是否展开。"""
        return self._expanded

    def append_text(self, delta: str) -> None:
        """追加文本（流式），自动更新显示。"""
        self._buffer += delta
        self._content_browser.setPlainText(self._buffer)
        self._update_content_height()
        # ★ 同步重绘内容 viewport，保证 thinking 内容也逐块上屏
        self._content_browser.viewport().repaint()

    def start_stream(self) -> None:
        """[兼容] 与 ChatMessage.start_stream 接口统一。"""
        self._buffer = ""
        self._content_browser.setPlainText("")

    @property
    def current_content(self) -> str:
        """当前缓冲的完整文本。"""
        return self._buffer

    def _update_content_height(self) -> None:
        """根据可见性、内容和可用宽度调整高度。"""
        # 折叠时隐藏内容区（与 _on_toggle / set_expanded 的 setVisible 行为一致）
        self._content_browser.setVisible(self._expanded)
        if not self._expanded or not self._buffer:
            self._content_browser.setFixedHeight(0)
            return

        viewport = self._content_browser.viewport()
        if viewport is None:
            return
        available_width = viewport.width()
        if available_width <= 0:
            return  # 尚未布局，等 resizeEvent

        doc = self._content_browser.document()
        doc.setTextWidth(available_width)
        height = int(doc.size().height()) + 8
        self._content_browser.setFixedHeight(max(height, 20))


# ──────────────────────────────────────────────
# 聊天消息气泡
# ──────────────────────────────────────────────


class _ForkControl(QFrame):
    """
    分支切换控件（spec 3.5.1）：嵌入消息操作栏。

    三元素：「<」按钮 + "m/n" 标签 + 「>」按钮（元素间距约 1 个英文字符）。
    m=1 时「<」禁用；m=n 时「>」禁用。
    set_interactive(False)：临时禁用切换（后端 create_branch 未就绪时）。
    """

    fork_nav = Signal(int)  # delta: -1 上一个 / +1 下一个

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)  # 约 1 个英文字符

        self._m: int = 0
        self._n: int = 0
        self._interactive: bool = True

        self._prev_btn = QPushButton("‹")
        self._label = QLabel("1/1")
        self._next_btn = QPushButton("›")

        for btn in (self._prev_btn, self._next_btn):
            btn.setFont(Fonts.body(Fonts.SIZE_MD))
            btn.setFixedSize(24, 20)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    color: {Colors.TEXT_SECONDARY};
                    background-color: transparent;
                    border: 1px solid {Colors.BORDER};
                    border-radius: 4px;
                }}
                QPushButton:hover {{
                    color: {Colors.PRIMARY};
                    background-color: {Colors.BG_OVERLAY};
                }}
                QPushButton:disabled {{
                    color: {Colors.TEXT_DISABLED if hasattr(Colors, 'TEXT_DISABLED') else '#3D4255'};
                    border-color: transparent;
                }}
            """)
        self._label.setFont(Fonts.body(Fonts.SIZE_SM))
        apply_style(self._label, lambda: f"color: {Colors.TEXT_SECONDARY}; background: transparent;")

        self._prev_btn.clicked.connect(lambda: self.fork_nav.emit(-1))
        self._next_btn.clicked.connect(lambda: self.fork_nav.emit(1))
        layout.addWidget(self._prev_btn)
        layout.addWidget(self._label)
        layout.addWidget(self._next_btn)

    def set_state(self, m: int, n: int) -> None:
        """设置展示编号（1 起）与总数，并更新边界按钮可用性。"""
        self._m, self._n = m, n
        self._label.setText(f"{m}/{n}")
        self._apply_enabled()

    def set_interactive(self, interactive: bool) -> None:
        """
        启用/禁用切换按钮。

        编辑重发送发送后后端 create_branch 尚未执行完成，先显示
        <m/n> 但禁用点击；后端就绪后启用。
        """
        self._interactive = interactive
        self._apply_enabled()

    def _apply_enabled(self) -> None:
        if not self._interactive:
            self._prev_btn.setEnabled(False)
            self._next_btn.setEnabled(False)
            return
        self._prev_btn.setEnabled(self._m > 1)
        self._next_btn.setEnabled(self._m < self._n)


class ChatMessage(QFrame):
    """
    单条消息气泡。与原 Flet 版本 ChatMessage 视觉完全一致。

    流式使用：
        msg.start_stream()
        msg.append_stream(delta)
        msg.finalize_stream()

    信号：
        copy_requested      — 用户点击复制按钮
        regenerate_requested — 用户点击重新生成按钮
        remember_requested   — 用户点击记住按钮
    """

    copy_requested = Signal(str)        # content text
    regenerate_requested = Signal(str)  # message_id（重新生成完整 assistant，分叉模式）
    remember_requested = Signal()       # no args needed
    continue_requested = Signal()       # 继续生成（未完成消息）
    resend_requested = Signal(str)      # 重新发送（用户消息）
    edit_requested = Signal(str)        # message_id（修改并重发送 user 消息，5.1）
    fork_nav = Signal(int)              # delta（<m/n> 控件切换分支，3.5.1）
    abandon_requested = Signal()        # 放弃本次修改（修改重发送暂停态，3.1.6）

    def __init__(
        self,
        role: str,
        content: str,
        message_id: str = "",
        is_thinking: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("chatMessage")

        self.role = role
        self.message_id = message_id
        self._is_streaming: bool = False
        self._is_incomplete: bool = False  # 未完成（用户停止生成）
        self._show_resend: bool = False    # 用户消息：是否显示"重新发送"
        self._locked: bool = False         # 预分支/修改状态下操作按钮锁定（3.1.3/5.2）
        self._translucent: bool = False    # 修改状态半透明（5.1.3/5.1.4）
        self._opacity_effect = None        # 半透明效果对象（QGraphicsOpacityEffect）
        self._show_abandon: bool = False   # 修改重发送暂停态："放弃本次修改"按钮
        self._stream_buffer: str = content
        self._updating_height: bool = False  # 防重入
        self._fork_m: int = 0              # 分叉展示信息（0 = 非被修改节点）
        self._fork_n: int = 0
        self._fork_point_id: str = ""

        is_user = (role == "user")

        # ── 角色标签行 ──────────────────────────
        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        badge_row.setSpacing(Spacing.SM)
        badge_row.addWidget(_role_badge(role))
        badge_row.addStretch()

        # ── 内容区（Markdown 渲染）───────────────
        self._content_browser = QTextBrowser()
        self._content_browser.setOpenExternalLinks(True)
        self._content_browser.setReadOnly(True)
        self._content_browser.setFont(Fonts.body(Fonts.SIZE_MD))
        apply_style(self._content_browser, lambda: f"""
            QTextBrowser {{
                color: {Colors.TEXT_PRIMARY};
                background-color: transparent;
                border: none;
                padding: 0;
            }}
        """)
        self._content_browser.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._content_browser.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._content_browser.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )

        # 设置初始内容 — 高度在 showEvent / resizeEvent 中计算
        # （此时 viewport().width() 为 0，计算出的高度无效）
        if content:
            self._content_browser.setHtml(_render_markdown(content))

        # ── 操作按钮行（单行；user 靠右，assistant 靠左）─────
        self._action_widget = QWidget()
        # 控件栏背景与气泡框颜色一致
        action_bg = Colors.USER_BG if is_user else Colors.BG_SURFACE
        self._action_widget.setStyleSheet(
            f"QWidget {{ background-color: {action_bg}; }}"
        )
        action_layout = QHBoxLayout(self._action_widget)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(Spacing.XS)

        # "复制"按钮（所有消息，效果一致）
        self._copy_btn = self._make_icon_button("📋", "复制", Colors.TEXT_SECONDARY)
        self._copy_btn.clicked.connect(self._on_copy)

        # "重新发送"按钮（仅首字延迟停止的用户消息场景显示）
        self._resend_btn = self._make_icon_button("↻", "重新发送", Colors.TEXT_SECONDARY)
        self._resend_btn.clicked.connect(self._on_resend)
        self._resend_btn.setVisible(False)

        # "修改"按钮（user 消息：修改并重发送，5.1）
        self._edit_btn = self._make_icon_button("✏️", "修改这条消息并重新发送", Colors.TEXT_SECONDARY)
        self._edit_btn.clicked.connect(self._on_edit)
        self._edit_btn.setVisible(False)

        # "放弃本次修改"按钮（修改重发送暂停态，3.1.6/5.4）
        self._abandon_btn = self._make_icon_button("✕", "放弃本次修改", Colors.TEXT_SECONDARY)
        self._abandon_btn.clicked.connect(self._on_abandon)
        self._abandon_btn.setVisible(False)

        # ── 分叉切换控件（<m/n>，3.5.1）──────────
        self._fork_control = _ForkControl()
        self._fork_control.fork_nav.connect(self.fork_nav)
        self._fork_control.setVisible(False)

        # 组装（从左到右）：复制、重新发送、修改、放弃、
        #   [assistant] 复制/重新生成/记住/继续、最后 <m/n>
        action_layout.addWidget(self._copy_btn)
        action_layout.addWidget(self._resend_btn)
        action_layout.addWidget(self._edit_btn)
        action_layout.addWidget(self._abandon_btn)
        if not is_user:
            self._regen_btn = self._make_icon_button("🔄", "重新生成", Colors.TEXT_SECONDARY)
            self._regen_btn.clicked.connect(self._on_regenerate)

            self._remember_btn = self._make_icon_button("🔖", "记住这段对话（写入知识图谱）", Colors.TEXT_SECONDARY)
            self._remember_btn.clicked.connect(self._on_remember)
            self._remember_btn._default_icon = "🔖"
            self._remember_btn._default_color = Colors.TEXT_SECONDARY

            self._continue_btn = self._make_icon_button("▶", "继续生成", Colors.TEXT_SECONDARY)
            self._continue_btn.clicked.connect(self._on_continue)

            action_layout.addWidget(self._regen_btn)
            action_layout.addWidget(self._remember_btn)
            action_layout.addWidget(self._continue_btn)
        action_layout.addWidget(self._fork_control)
        # user 靠右排列（stretch 在末尾）；assistant 靠左（同样在末尾）
        action_layout.addStretch()

        self._action_widget.setVisible(not is_user)

        # ── 内部布局 ────────────────────────────
        inner = QVBoxLayout()
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(Spacing.SM)
        inner.addLayout(badge_row)
        inner.addWidget(self._content_browser)
        inner.addWidget(self._action_widget)

        # ── 气泡外层 ────────────────────────────
        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            Spacing.LG, Spacing.MD, Spacing.LG, Spacing.MD
        )
        outer.setSpacing(0)
        outer.addLayout(inner)

        # ── 气泡样式 ────────────────────────────
        self._is_user = is_user
        bg_color = Colors.USER_BG if is_user else Colors.BG_SURFACE
        if is_user:
            self._normal_border = f"""
                border: 1px solid {Colors.BORDER};
                border-left: 1px solid {Colors.ROLE_USER}55;
                border-top-left-radius: {Radius.LG}px;
                border-top-right-radius: {Radius.LG}px;
                border-bottom-left-radius: {Radius.LG}px;
                border-bottom-right-radius: {2}px;
            """
        else:
            self._normal_border = f"""
                border: 1px solid {Colors.BORDER};
                border-left: 2px solid {Colors.ROLE_ASSISTANT};
                border-top-left-radius: {Radius.LG}px;
                border-top-right-radius: {Radius.LG}px;
                border-bottom-left-radius: {2}px;
                border-bottom-right-radius: {Radius.LG}px;
            """

        self._bg_color = bg_color
        self._apply_stylesheet(highlighted=False)

        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )

        # 初始状态（完成态）：复制/重新生成/记住显示，继续隐藏
        self._update_action_visibility()

    # ── 操作按钮工厂 ──────────────────────────

    @staticmethod
    def _make_icon_button(text: str, tooltip: str, color: str) -> QPushButton:
        """创建小图标按钮。"""
        btn = QPushButton(text)
        btn.setFont(Fonts.body(12))
        btn.setToolTip(tooltip)
        btn.setFixedSize(28, 28)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                color: {color};
                background-color: transparent;
                border: none;
                border-radius: 4px;
                padding: 2px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BG_OVERLAY};
            }}
        """)
        return btn

    # ── 高亮效果 ──────────────────────────────

    def _apply_stylesheet(self, highlighted: bool = False) -> None:
        """根据高亮状态重新应用样式表。"""
        if highlighted:
            border = f"""
                border: 2px solid {Colors.ACCENT};
                border-left: 3px solid {Colors.ACCENT};
                border-top-left-radius: {Radius.LG}px;
                border-top-right-radius: {Radius.LG}px;
                border-bottom-left-radius: {2 if not self._is_user else Radius.LG}px;
                border-bottom-right-radius: {Radius.LG if not self._is_user else 2}px;
            """
            bg = f"{Colors.ACCENT}18"
        else:
            border = self._normal_border
            bg = self._bg_color

        self.setStyleSheet(f"""
            ChatMessage {{
                background-color: {bg};
                {border}
                margin: {Spacing.XS}px 0px;
            }}
        """)

    def set_highlighted(self, highlighted: bool) -> None:
        """应用或移除高亮发光效果。"""
        self._apply_stylesheet(highlighted=highlighted)

    # ── 高度自适应 ──────────────────────────

    def showEvent(self, event) -> None:
        """首次显示时计算正确高度（此时 viewport 宽度已确定）。"""
        super().showEvent(event)
        self._update_content_height()

    def resizeEvent(self, event) -> None:
        """
        窗口 / 容器宽度变化时重新计算内容高度。

        仅响应宽度变化：宽度改变 → 文本重新换行 → 高度需更新。
        高度变化不触发重算（防止 _update_content_height 的
        setFixedHeight 导致无限 resizeEvent 循环）。
        """
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self._update_content_height()

    def _update_content_height(self) -> None:
        """
        根据文档内容在可用宽度下的自然高度设置 QTextBrowser 高度。

        - natural_height <= MAX_CONTENT_HEIGHT：禁用滚动，固定为自然高度
        - natural_height >  MAX_CONTENT_HEIGHT：启用滚动，固定为 MAX_CONTENT_HEIGHT
        - available_width <= 0：尚无布局（等待 resizeEvent / showEvent），不操作
        """
        if self._updating_height:
            return
        self._updating_height = True
        try:
            viewport = self._content_browser.viewport()
            if viewport is None:
                return
            available_width = viewport.width()
            if available_width <= 0:
                return

            doc = self._content_browser.document()
            doc.setTextWidth(available_width)
            natural = int(doc.size().height()) + 8  # 8px 内边距

            if natural > MAX_CONTENT_HEIGHT:
                self._content_browser.setVerticalScrollBarPolicy(
                    Qt.ScrollBarPolicy.ScrollBarAsNeeded
                )
                self._content_browser.setFixedHeight(MAX_CONTENT_HEIGHT)
            else:
                self._content_browser.setVerticalScrollBarPolicy(
                    Qt.ScrollBarPolicy.ScrollBarAlwaysOff
                )
                self._content_browser.setFixedHeight(max(natural, 24))
        finally:
            self._updating_height = False

    # ── 按钮事件 ──────────────────────────────

    def _on_copy(self) -> None:
        """复制当前内容到剪贴板。"""
        self.copy_requested.emit(self.current_content)

    def _on_regenerate(self) -> None:
        """请求重新生成（携带 message_id，分叉模式定位目标）。"""
        self.regenerate_requested.emit(self.message_id)

    def _on_edit(self) -> None:
        """请求修改并重发送（5.1）。"""
        self.edit_requested.emit(self.message_id)

    def _on_abandon(self) -> None:
        """放弃本次修改（3.1.6：硬删除新分支并回退）。"""
        self.abandon_requested.emit()

    def _on_remember(self) -> None:
        """请求提取知识图谱。"""
        self.remember_requested.emit()

    def _on_continue(self) -> None:
        """请求继续生成（未完成消息）。"""
        self.continue_requested.emit()

    def _on_resend(self) -> None:
        """请求重新发送（用户消息）。"""
        self.resend_requested.emit(self.current_content)

    def show_resend_button(self) -> None:
        """在用户消息上显示'重新发送'按钮（首字延迟停止场景）。"""
        self._show_resend = True
        self._update_action_visibility()

    def hide_resend_button(self) -> None:
        """隐藏'重新发送'按钮。"""
        self._show_resend = False
        self._update_action_visibility()

    # ── 记住按钮状态 ──────────────────────────

    def set_remember_loading(self) -> None:
        """设置记住按钮为加载状态。"""
        btn = getattr(self, "_remember_btn", None)
        if btn is None:
            return
        btn.setText("⏳")
        btn.setStyleSheet(btn.styleSheet().replace(
            f"color: {Colors.TEXT_SECONDARY}",
            f"color: {Colors.WARNING}",
        ))
        btn.setEnabled(False)

    def set_remember_done(self, success: bool = True) -> None:
        """提取完成后更新按钮状态。"""
        btn = getattr(self, "_remember_btn", None)
        if btn is None:
            return
        btn.setEnabled(True)
        if success:
            btn.setText("✅")
            btn.setToolTip("已记住")
            btn.setStyleSheet(btn.styleSheet().replace(
                f"color: {Colors.WARNING}",
                f"color: {Colors.SUCCESS}",
            ))
        else:
            btn.setText("🔖")
            btn.setToolTip("提取失败，可重试")
            btn.setStyleSheet(btn.styleSheet().replace(
                f"color: {Colors.WARNING}",
                f"color: {Colors.ERROR}",
            ))

    # ── 流式支持 ──────────────────────────────

    def start_stream(self) -> None:
        """开始流式接收。清空缓冲并重置内容。"""
        self._is_streaming = True
        self._is_incomplete = False
        self._stream_buffer = ""
        self._update_action_visibility()

    def append_stream(self, delta: str) -> None:
        """追加流式文本块，自动渲染 Markdown 并更新高度。"""
        self._stream_buffer += delta
        self._content_browser.setHtml(_render_markdown(self._stream_buffer))
        self._update_content_height()
        # 流式输出期间自动滚动到文本底部
        vsb = self._content_browser.verticalScrollBar()
        if vsb.isVisible():
            vsb.setValue(vsb.maximum())
        # ★ 同步重绘内容 viewport：setHtml 只安排异步 update，交给事件循环后
        #   paint 事件会被合并，导致文本最后一次性出现。直接 repaint() 内容
        #   区域可立即上屏（同步、不处理其他事件，不会重入）。
        self._content_browser.viewport().repaint()

    def finalize_stream(self) -> None:
        """结束流式接收（正常完成）。"""
        self._is_streaming = False
        self._is_incomplete = False
        self._update_action_visibility()

    def mark_incomplete(self) -> None:
        """标记消息为未完成状态（用户停止生成），显示重新生成 + 继续按钮。"""
        self._is_streaming = False
        self._is_incomplete = True
        self._update_action_visibility()

    def restart_stream_from(self, content: str) -> None:
        """从已有部分内容恢复流式追加状态（继续生成用）。"""
        self._is_streaming = True
        self._is_incomplete = False
        self._stream_buffer = content or ""
        self._content_browser.setHtml(_render_markdown(self._stream_buffer))
        self._update_content_height()
        self._update_action_visibility()

    def _update_action_visibility(self) -> None:
        """根据当前状态更新操作按钮可见性。"""
        fork_visible = self._fork_n > 0
        if self._is_streaming:
            # 流式中：仅 <m/n> 预览可见（预分支流式期间的预估标记，
            # 其余按钮隐藏）
            self._copy_btn.setVisible(False)
            self._resend_btn.setVisible(False)
            self._edit_btn.setVisible(False)
            self._abandon_btn.setVisible(False)
            if not self._is_user:
                self._regen_btn.setVisible(False)
                self._remember_btn.setVisible(False)
                self._continue_btn.setVisible(False)
            self._action_widget.setVisible(fork_visible)
            return
        if self._is_user:
            # 用户消息：复制 / 重新发送 / 修改 / <m/n> 按状态显示
            self._resend_btn.setVisible(self._show_resend)
            # 修改按钮：完整的 user 消息且未锁定（5.1/3.2.1）
            can_edit = (
                bool(self.message_id)
                and not self._is_incomplete
                and not self._locked
            )
            self._edit_btn.setVisible(can_edit)
            self._abandon_btn.setVisible(self._show_abandon)
            self._action_widget.setVisible(
                self._show_resend or can_edit
                or self._show_abandon or fork_visible
            )
            return
        if self._is_incomplete:
            # 未完成：重新生成 + 继续 +（修改重发送暂停态时）放弃本次修改
            self._action_widget.setVisible(True)
            self._copy_btn.setVisible(False)
            self._remember_btn.setVisible(False)
            self._regen_btn.setVisible(True)
            self._continue_btn.setVisible(True)
            self._resend_btn.setVisible(False)
            self._edit_btn.setVisible(False)
            self._abandon_btn.setVisible(self._show_abandon)
            return
        # 正常完成：复制/重新生成/记住显示，继续隐藏
        self._action_widget.setVisible(True)
        self._copy_btn.setVisible(True)
        self._remember_btn.setVisible(True)
        self._regen_btn.setVisible(True)
        self._continue_btn.setVisible(False)
        self._resend_btn.setVisible(False)
        self._edit_btn.setVisible(False)
        self._abandon_btn.setVisible(False)

    # ── 分叉 / 修改状态（P3）──────────────────

    def set_fork_info(
        self, m: int, n: int, fork_point_id: str, interactive: bool = True
    ) -> None:
        """被修改消息设置分叉展示信息并显示 <m/n> 切换控件（3.5.1）。

        m/n 为 1 起展示编号;fork_point_id 为分叉点(切换目标)。
        interactive=False 时临时禁用切换(后端 create_branch 未就绪)。
        """
        self._fork_m, self._fork_n, self._fork_point_id = m, n, fork_point_id
        if n > 0:
            self._fork_control.set_state(m, n)
            self._fork_control.set_interactive(interactive)
            self._fork_control.setVisible(True)
        else:
            self._fork_control.setVisible(False)
        # <m/n> 嵌入操作栏后,可见性影响操作栏整体显示
        self._update_action_visibility()

    def set_content(self, content: str) -> None:
        """更新气泡内容（修改重发送后立即显示新文本，5.3.3）。"""
        self._stream_buffer = content or ""
        self._content_browser.setHtml(_render_markdown(self._stream_buffer))
        self._update_content_height()

    def set_translucent(self, translucent: bool) -> None:
        """半透明状态（修改状态下的气泡与文字，5.1.3/5.1.4）。

        用 QGraphicsOpacityEffect 实现——气泡背景与气泡内文字（含
        Markdown 渲染的 HTML 内容）整体半透明；stylesheet 的 color 规则
        无法覆盖 HTML 内联样式，仅靠 QSS 会导致文字不透明。
        """
        self._translucent = translucent
        if translucent:
            # 每次进入都重建：setGraphicsEffect(None) 会销毁 C++ 对象，
            # 残留引用再复用会触发 "already deleted" RuntimeError
            from PySide6.QtWidgets import QGraphicsOpacityEffect
            self._opacity_effect = QGraphicsOpacityEffect(self)
            self._opacity_effect.setOpacity(0.45)
            self.setGraphicsEffect(self._opacity_effect)
        else:
            self.setGraphicsEffect(None)
            self._opacity_effect = None  # Qt 已销毁该 C++ 对象，丢弃引用

    def set_actions_locked(self, locked: bool) -> None:
        """锁定/解锁操作按钮（预分支与修改状态下，3.1.3/5.2）。"""
        self._locked = locked
        self._update_action_visibility()

    def show_abandon_button(self, show: bool) -> None:
        """显示/隐藏"放弃本次修改"按钮（修改重发送暂停态，3.1.6）。"""
        self._show_abandon = show
        self._update_action_visibility()

    # ── 属性 ──────────────────────────────────

    @property
    def current_content(self) -> str:
        """当前完整文本内容。"""
        return self._stream_buffer

    @property
    def fork_point_id(self) -> str:
        """分叉点 ID（无分叉信息时为空串）。"""
        return self._fork_point_id

    @property
    def fork_m(self) -> int:
        return self._fork_m

    @property
    def fork_n(self) -> int:
        return self._fork_n

