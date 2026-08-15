# Layer: UI (PySide6)
# File: app/ui/theme.py
# Responsibility: 主题系统 — 全局颜色/字体/间距/圆角常量 + 多主题注册 + 主题管理器
#
# 设计说明（供后续扩展主题参考）：
#   1. 所有主题的「颜色 token」集合完全一致（见 _TOKENS 与各 Theme.colors）。
#      新增主题 = 在 THEMES 里加一个 Theme 条目即可，无需改动任何控件代码。
#   2. Colors 是「动态调色板」：ThemeManager.apply() 会把当前主题的颜色值
#      写回 Colors 的类属性，因此任何代码 `Colors.PRIMARY` 读到的永远是当前主题。
#   3. 静态样式（只依赖主题 token 的样式）用 apply_style(widget, style_fn) 注册，
#      切主题时 reapply_all_styles() 会重新求值 style_fn（懒求值 → 拿到新颜色）。
#   4. 依赖运行时状态的样式（如按钮 on/off、气泡高亮）用控件自己的
#      refresh_theme() 方法重绘，reapply_all_styles() 会遍历顶层窗口统一调用。
#   5. 消息气泡/思考块/树节点的颜色在「重建」时自然生效（ChatApp 在主题变更
#      信号里 _load_tree() + _load_all_messages()），见 app/ui/app.py。

from __future__ import annotations

import json
import weakref
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtGui import QFont, QColor, QPalette
from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import QApplication, QWidget


# ──────────────────────────────────────────────
# 字体系统
# ──────────────────────────────────────────────


class Fonts:
    """字体常量。"""

    BODY = "Noto Sans SC"
    MONO = "JetBrains Mono"

    # 字号（pt）
    SIZE_XS = 11
    SIZE_SM = 12
    SIZE_MD = 14
    SIZE_LG = 16
    SIZE_XL = 18
    SIZE_XXL = 22

    @staticmethod
    def body(size: int = SIZE_MD, weight: int = QFont.Weight.Normal) -> QFont:
        font = QFont(Fonts.BODY, size)
        font.setWeight(weight)
        return font

    @staticmethod
    def mono(size: int = SIZE_SM, weight: int = QFont.Weight.Normal) -> QFont:
        font = QFont(Fonts.MONO, size)
        font.setWeight(weight)
        font.setStyleHint(QFont.StyleHint.Monospace)
        return font


# ──────────────────────────────────────────────
# 间距 / 圆角系统
# ──────────────────────────────────────────────


class Spacing:
    XS = 4
    SM = 8
    MD = 12
    LG = 16
    XL = 24
    XXL = 32


class Radius:
    SM = 4
    MD = 8
    LG = 12
    XL = 16


# ──────────────────────────────────────────────
# 颜色 token 集合（所有主题共享同一套 key）
# ──────────────────────────────────────────────

_TOKENS: tuple[str, ...] = (
    "BG_BASE",
    "BG_SURFACE",
    "BG_ELEVATED",
    "BG_OVERLAY",
    "USER_BG",
    "PRIMARY",
    "PRIMARY_DIM",
    "PRIMARY_GLOW",
    "ACCENT",
    "TEXT_PRIMARY",
    "TEXT_SECONDARY",
    "TEXT_DISABLED",
    "TEXT_CODE",
    "ROLE_USER",
    "ROLE_ASSISTANT",
    "ROLE_THINKING",
    "SUCCESS",
    "WARNING",
    "ERROR",
    "INFO",
    "CHECKBOX_ENABLED",
    "CHECKBOX_DISABLED",
    "CHECKBOX_SOME",
    "BORDER",
    "BORDER_FOCUS",
    "DIVIDER",
)


class Colors:
    """
    动态颜色调色板。

    类属性随 ThemeManager.apply() 更新；所有控件在任意时刻读取
    Colors.X 都得到当前主题值。QColor 缓存会在切主题时清空。
    """

    # 默认主题（深空工业）的初始值——应用启动时会被持久化主题覆盖
    BG_BASE = "#0D0F14"
    BG_SURFACE = "#13161D"
    BG_ELEVATED = "#1A1E28"
    BG_OVERLAY = "#1F2433"
    USER_BG = "#15203A"
    PRIMARY = "#4A9EFF"
    PRIMARY_DIM = "#2E6FCC"
    PRIMARY_GLOW = "#4A9EFF22"
    ACCENT = "#64FFDA"
    TEXT_PRIMARY = "#E8EAF0"
    TEXT_SECONDARY = "#7A8099"
    TEXT_DISABLED = "#3D4255"
    TEXT_CODE = "#A8C4E8"
    ROLE_USER = "#4A9EFF"
    ROLE_ASSISTANT = "#64FFDA"
    ROLE_THINKING = "#9C7FE8"
    SUCCESS = "#4CAF82"
    WARNING = "#E8A838"
    ERROR = "#F06B6B"
    INFO = "#4A9EFF"
    CHECKBOX_ENABLED = "#4CAF82"
    CHECKBOX_DISABLED = "#3D4255"
    CHECKBOX_SOME = "#E8A838"
    BORDER = "#252A38"
    BORDER_FOCUS = "#4A9EFF"
    DIVIDER = "#1E2230"

    _cache: dict[str, QColor] = {}

    @classmethod
    def _activate(cls, palette: dict[str, str]) -> None:
        """把 palette（token→hex）写入类属性，并清空 QColor 缓存。"""
        for key in _TOKENS:
            if key in palette:
                setattr(cls, key, palette[key])
        cls._cache.clear()

    @classmethod
    def qcolor(cls, hex_str: str) -> QColor:
        """返回 hex 字符串对应的 QColor，带缓存。"""
        if hex_str not in cls._cache:
            cls._cache[hex_str] = QColor(hex_str)
        return cls._cache[hex_str]


# ──────────────────────────────────────────────
# 主题定义
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class Theme:
    """一个主题：id、显示名、说明、是否暗色、颜色 palette、预览色板。"""

    id: str
    name: str
    description: str
    colors: dict[str, str]
    dark: bool = True
    preview: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        # 预览色板：背景 / 面板 / 主色 / 强调色
        object.__setattr__(
            self,
            "preview",
            (
                self.colors.get("BG_BASE", "#000000"),
                self.colors.get("BG_SURFACE", "#000000"),
                self.colors.get("PRIMARY", "#000000"),
                self.colors.get("ACCENT", "#000000"),
            ),
        )


THEMES: dict[str, Theme] = {
    "deepspace": Theme(
        id="deepspace",
        name="深空工业",
        description="冷钢蓝 · 深色",
        colors={
            "BG_BASE": "#0D0F14", "BG_SURFACE": "#13161D",
            "BG_ELEVATED": "#1A1E28", "BG_OVERLAY": "#1F2433",
            "USER_BG": "#15203A", "PRIMARY": "#4A9EFF",
            "PRIMARY_DIM": "#2E6FCC", "PRIMARY_GLOW": "#4A9EFF22",
            "ACCENT": "#64FFDA", "TEXT_PRIMARY": "#E8EAF0",
            "TEXT_SECONDARY": "#7A8099", "TEXT_DISABLED": "#3D4255",
            "TEXT_CODE": "#A8C4E8", "ROLE_USER": "#4A9EFF",
            "ROLE_ASSISTANT": "#64FFDA", "ROLE_THINKING": "#9C7FE8",
            "SUCCESS": "#4CAF82", "WARNING": "#E8A838", "ERROR": "#F06B6B",
            "INFO": "#4A9EFF", "CHECKBOX_ENABLED": "#4CAF82",
            "CHECKBOX_DISABLED": "#3D4255", "CHECKBOX_SOME": "#E8A838",
            "BORDER": "#252A38", "BORDER_FOCUS": "#4A9EFF", "DIVIDER": "#1E2230",
        },
    ),
    "light": Theme(
        id="light",
        name="月白浅色",
        description="清爽明亮 · 浅色",
        dark=False,
        colors={
            "BG_BASE": "#F5F7FA", "BG_SURFACE": "#FFFFFF",
            "BG_ELEVATED": "#FFFFFF", "BG_OVERLAY": "#E9EEF4",
            "USER_BG": "#E3EDFB", "PRIMARY": "#2563EB",
            "PRIMARY_DIM": "#1D4ED8", "PRIMARY_GLOW": "#2563EB1A",
            "ACCENT": "#059669", "TEXT_PRIMARY": "#1F2430",
            "TEXT_SECONDARY": "#5B6472", "TEXT_DISABLED": "#A6AEBB",
            "TEXT_CODE": "#2563EB", "ROLE_USER": "#2563EB",
            "ROLE_ASSISTANT": "#059669", "ROLE_THINKING": "#7C3AED",
            "SUCCESS": "#16A34A", "WARNING": "#D97706", "ERROR": "#DC2626",
            "INFO": "#2563EB", "CHECKBOX_ENABLED": "#16A34A",
            "CHECKBOX_DISABLED": "#C3C9D4", "CHECKBOX_SOME": "#D97706",
            "BORDER": "#D8DEE8", "BORDER_FOCUS": "#2563EB", "DIVIDER": "#E5EAF1",
        },
    ),
    "dracula": Theme(
        id="dracula",
        name="德古拉暗紫",
        description="经典 Dracula 配色 · 暗紫绿",
        colors={
            "BG_BASE": "#282A36", "BG_SURFACE": "#21222C",
            "BG_ELEVATED": "#31333F", "BG_OVERLAY": "#3B3D4F",
            "USER_BG": "#2E2A3F", "PRIMARY": "#BD93F9",
            "PRIMARY_DIM": "#A579E8", "PRIMARY_GLOW": "#BD93F922",
            "ACCENT": "#50FA7B", "TEXT_PRIMARY": "#F8F8F2",
            "TEXT_SECONDARY": "#9AA0B5", "TEXT_DISABLED": "#555A6E",
            "TEXT_CODE": "#F1FA8C", "ROLE_USER": "#FF79C6",
            "ROLE_ASSISTANT": "#50FA7B", "ROLE_THINKING": "#BD93F9",
            "SUCCESS": "#50FA7B", "WARNING": "#FFB86C", "ERROR": "#FF5555",
            "INFO": "#8BE9FD", "CHECKBOX_ENABLED": "#50FA7B",
            "CHECKBOX_DISABLED": "#555A6E", "CHECKBOX_SOME": "#FFB86C",
            "BORDER": "#44475A", "BORDER_FOCUS": "#BD93F9", "DIVIDER": "#3B3D4F",
        },
    ),
    "forest": Theme(
        id="forest",
        name="森绿墨夜",
        description="森林绿 · 荧光点缀",
        colors={
            "BG_BASE": "#0F1A12", "BG_SURFACE": "#152419",
            "BG_ELEVATED": "#1B2E20", "BG_OVERLAY": "#233826",
            "USER_BG": "#1D2E2A", "PRIMARY": "#3EB489",
            "PRIMARY_DIM": "#2F8A68", "PRIMARY_GLOW": "#3EB48922",
            "ACCENT": "#A3E635", "TEXT_PRIMARY": "#E6EFE8",
            "TEXT_SECONDARY": "#8AA390", "TEXT_DISABLED": "#45544A",
            "TEXT_CODE": "#9BE8C0", "ROLE_USER": "#3EB489",
            "ROLE_ASSISTANT": "#A3E635", "ROLE_THINKING": "#7C9CF5",
            "SUCCESS": "#4CAF82", "WARNING": "#E8A838", "ERROR": "#F06B6B",
            "INFO": "#3EB489", "CHECKBOX_ENABLED": "#4CAF82",
            "CHECKBOX_DISABLED": "#45544A", "CHECKBOX_SOME": "#E8A838",
            "BORDER": "#273A2D", "BORDER_FOCUS": "#3EB489", "DIVIDER": "#1D2E23",
        },
    ),
    "amber": Theme(
        id="amber",
        name="暖阳琥珀",
        description="暖棕底 · 琥珀橙 · 青绿点缀",
        colors={
            "BG_BASE": "#1A1512", "BG_SURFACE": "#241D18",
            "BG_ELEVATED": "#2E241D", "BG_OVERLAY": "#3A2E24",
            "USER_BG": "#33281E", "PRIMARY": "#F4A259",
            "PRIMARY_DIM": "#D98A44", "PRIMARY_GLOW": "#F4A25922",
            "ACCENT": "#7FD8D0", "TEXT_PRIMARY": "#F0E8E0",
            "TEXT_SECONDARY": "#A08D7B", "TEXT_DISABLED": "#554A40",
            "TEXT_CODE": "#F2C48D", "ROLE_USER": "#F4A259",
            "ROLE_ASSISTANT": "#7FD8D0", "ROLE_THINKING": "#C39BF0",
            "SUCCESS": "#7BC47F", "WARNING": "#F4A259", "ERROR": "#F06B6B",
            "INFO": "#F4A259", "CHECKBOX_ENABLED": "#7BC47F",
            "CHECKBOX_DISABLED": "#554A40", "CHECKBOX_SOME": "#F4A259",
            "BORDER": "#3A2E24", "BORDER_FOCUS": "#F4A259", "DIVIDER": "#2A211B",
        },
    ),
    "mono": Theme(
        id="mono",
        name="极简灰白",
        description="黑白灰 · 克制极简",
        dark=False,
        colors={
            "BG_BASE": "#F4F4F5", "BG_SURFACE": "#FFFFFF",
            "BG_ELEVATED": "#FFFFFF", "BG_OVERLAY": "#E4E4E7",
            "USER_BG": "#E9E9EC", "PRIMARY": "#18181B",
            "PRIMARY_DIM": "#3F3F46", "PRIMARY_GLOW": "#18181B14",
            "ACCENT": "#52525B", "TEXT_PRIMARY": "#18181B",
            "TEXT_SECONDARY": "#52525B", "TEXT_DISABLED": "#A1A1AA",
            "TEXT_CODE": "#3F3F46", "ROLE_USER": "#18181B",
            "ROLE_ASSISTANT": "#52525B", "ROLE_THINKING": "#71717A",
            "SUCCESS": "#3F3F46", "WARNING": "#A1A1AA", "ERROR": "#DC2626",
            "INFO": "#18181B", "CHECKBOX_ENABLED": "#3F3F46",
            "CHECKBOX_DISABLED": "#D4D4D8", "CHECKBOX_SOME": "#A1A1AA",
            "BORDER": "#D4D4D8", "BORDER_FOCUS": "#18181B", "DIVIDER": "#E4E4E7",
        },
    ),
}

DEFAULT_THEME_ID = "deepspace"

_THEME_ORDER: tuple[str, ...] = (
    "deepspace", "light", "dracula", "forest", "amber", "mono",
)


def theme_order() -> list[Theme]:
    """按展示顺序返回主题列表（供选择器使用）。"""
    return [THEMES[tid] for tid in _THEME_ORDER if tid in THEMES]


# ──────────────────────────────────────────────
# 全局样式表 / 调色板构建
# ──────────────────────────────────────────────


def build_global_stylesheet() -> str:
    """构建应用程序全局样式表（读取当前 Colors 动态值）。"""
    return f"""
    QMainWindow {{
        background-color: {Colors.BG_BASE};
    }}
    QWidget {{
        background-color: {Colors.BG_BASE};
        color: {Colors.TEXT_PRIMARY};
        font-family: "{Fonts.BODY}";
        font-size: {Fonts.SIZE_MD}px;
    }}
    QScrollBar:vertical {{
        background: {Colors.BG_BASE};
        width: 8px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {Colors.BORDER};
        border-radius: 4px;
        min-height: 30px;
    }}
    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QScrollBar::add-page:vertical,
    QScrollBar::sub-page:vertical {{
        background: none;
    }}
    QScrollBar:horizontal {{
        background: {Colors.BG_BASE};
        height: 8px;
        margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {Colors.BORDER};
        border-radius: 4px;
        min-width: 30px;
    }}
    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}
    QScrollBar::add-page:horizontal,
    QScrollBar::sub-page:horizontal {{
        background: none;
    }}
    QToolTip {{
        background-color: {Colors.BG_ELEVATED};
        color: {Colors.TEXT_PRIMARY};
        border: 1px solid {Colors.BORDER};
        padding: 4px 8px;
        font-size: {Fonts.SIZE_XS}px;
    }}
    """


def build_palette() -> QPalette:
    """根据当前 Colors 构建 QPalette（原生控件：树展开箭头等）。"""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(Colors.BG_BASE))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(Colors.TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Base, QColor(Colors.BG_SURFACE))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(Colors.BG_ELEVATED))
    palette.setColor(QPalette.ColorRole.Text, QColor(Colors.TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Button, QColor(Colors.BG_ELEVATED))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(Colors.TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(Colors.PRIMARY))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(Colors.BG_BASE))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(Colors.BG_ELEVATED))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(Colors.TEXT_PRIMARY))
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,
        QColor(Colors.TEXT_DISABLED),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText,
        QColor(Colors.TEXT_DISABLED),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText,
        QColor(Colors.TEXT_DISABLED),
    )
    return palette


# ──────────────────────────────────────────────
# 样式注册 / 重应用机制
# ──────────────────────────────────────────────

# (weakref(widget), style_fn) —— style_fn 返回该控件当前的样式表字符串，
# 只依赖主题 token（不捕获控件自身），切主题时统一重新求值。
_STYLE_REGISTRY: list[tuple[weakref.ref, object]] = []


def apply_style(widget: QWidget, style_fn) -> QWidget:
    """应用样式并注册，切主题时自动重新求值。

    style_fn 必须是「无参、返回样式表字符串」的可调用对象，且只引用
    Colors/Fonts/Spacing/Radius 等模块级 token（不要捕获控件自身，
    否则会造成引用环导致内存泄漏）。返回 widget 以便链式调用。
    """
    widget.setStyleSheet(style_fn())
    # 每个控件同一时刻只有一个当前样式：替换掉该控件旧注册，并顺带清理已销毁的控件
    alive: list[tuple[weakref.ref, object]] = []
    for ref, fn in _STYLE_REGISTRY:
        existing = ref()
        if existing is None or existing is widget:
            continue
        alive.append((ref, fn))
    _STYLE_REGISTRY[:] = alive
    _STYLE_REGISTRY.append((weakref.ref(widget), style_fn))
    return widget


def _walk_theme_refresh(widget: QWidget) -> None:
    """递归调用控件树中定义了 refresh_theme() 的控件。"""
    refresh = getattr(widget, "refresh_theme", None)
    if refresh is not None:
        try:
            refresh()
        except RuntimeError:
            pass  # C++ 对象已销毁
    for child in widget.findChildren(QWidget):
        child_refresh = getattr(child, "refresh_theme", None)
        if child_refresh is not None:
            try:
                child_refresh()
            except RuntimeError:
                pass


def reapply_all_styles() -> None:
    """切主题后重应用：全局样式表 + 调色板 + 已注册静态样式 + 控件 refresh_theme。"""
    app = QApplication.instance()
    if app is None:
        return
    app.setStyleSheet(build_global_stylesheet())
    app.setPalette(build_palette())

    alive: list[tuple[weakref.ref, object]] = []
    for ref, fn in _STYLE_REGISTRY:
        widget = ref()
        if widget is None:
            continue
        try:
            widget.setStyleSheet(fn())
            alive.append((ref, fn))
        except RuntimeError:
            continue
    _STYLE_REGISTRY[:] = alive

    for top in app.topLevelWidgets():
        try:
            _walk_theme_refresh(top)
        except RuntimeError:
            continue


# ──────────────────────────────────────────────
# 主题管理器（单例）
# ──────────────────────────────────────────────


class ThemeManager(QObject):
    """主题管理器：切换主题、持久化、广播变更信号。"""

    theme_changed = Signal(str)  # 携带新主题 id

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._current_id: str = self.load() or DEFAULT_THEME_ID

    @property
    def current_id(self) -> str:
        return self._current_id

    @property
    def current_theme(self) -> Theme | None:
        return THEMES.get(self._current_id)

    def apply(self, theme_id: str, save: bool = False) -> bool:
        """应用主题；save=True 时同时持久化。"""
        if theme_id not in THEMES:
            return False
        self._current_id = theme_id
        Colors._activate(THEMES[theme_id].colors)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_global_stylesheet())
            app.setPalette(build_palette())
        reapply_all_styles()
        if save:
            self.save()
        self.theme_changed.emit(theme_id)
        return True

    # ── 持久化 ──────────────────────────────

    @staticmethod
    def _store_path() -> Path:
        # theme.py 位于 app/ui/theme.py → 项目根目录 = 上上上级
        return Path(__file__).resolve().parent.parent.parent / "data" / "theme.json"

    def save(self) -> None:
        try:
            path = self._store_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"theme": self._current_id}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def load(self) -> str | None:
        try:
            path = self._store_path()
            if not path.exists():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            tid = data.get("theme")
            return tid if tid in THEMES else None
        except Exception:
            return None


_manager: ThemeManager | None = None


def get_theme_manager() -> ThemeManager:
    """返回全局唯一的 ThemeManager。"""
    global _manager
    if _manager is None:
        _manager = ThemeManager()
    return _manager
