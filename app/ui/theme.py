# Layer: UI (PySide6)
# File: app/ui/theme.py
# Responsibility: 全局颜色、字体、间距、样式常量 — 与 Flet 版本视觉完全一致
# Input:  无
# Output: 供所有 PySide6 UI 模块 import 的常量

from __future__ import annotations

from PySide6.QtGui import QFont, QColor, QFontDatabase
from PySide6.QtCore import Qt


# ──────────────────────────────────────────────
# 色彩系统（工业精密风 · 深色）
# 与 Flet 版本 app/ui/theme.py 完全一致
# ──────────────────────────────────────────────


class Colors:
    """全局色彩常量。所有值与原 Flet 版本 hex 完全一致。"""

    # 背景层次
    BG_BASE = "#0D0F14"       # 最底层背景
    BG_SURFACE = "#13161D"    # 卡片 / 面板背景
    BG_ELEVATED = "#1A1E28"   # 悬浮元素 / 输入框
    BG_OVERLAY = "#1F2433"    # hover / 选中态
    USER_BG = "#15203A"       # 用户消息气泡背景（深蓝）

    # 主色 & 强调
    PRIMARY = "#4A9EFF"        # 冷钢蓝 - 主交互色
    PRIMARY_DIM = "#2E6FCC"    # 按压态
    PRIMARY_GLOW = "#4A9EFF22" # 光晕背景（低透明度）
    ACCENT = "#64FFDA"         # 薄荷绿 - 思考模式 / 特殊高亮

    # 文字层次
    TEXT_PRIMARY = "#E8EAF0"   # 主要文字
    TEXT_SECONDARY = "#7A8099" # 次要 / 占位符
    TEXT_DISABLED = "#3D4255"  # 禁用态
    TEXT_CODE = "#A8C4E8"      # 代码块 / 等宽内容

    # 角色标识
    ROLE_USER = "#4A9EFF"      # 用户气泡标识
    ROLE_ASSISTANT = "#64FFDA" # 助手气泡标识
    ROLE_THINKING = "#9C7FE8"  # 思考块标识（紫）

    # 状态色
    SUCCESS = "#4CAF82"
    WARNING = "#E8A838"
    ERROR = "#F06B6B"
    INFO = "#4A9EFF"

    # 复选框状态色（树节点 enabled 指示器）
    CHECKBOX_ENABLED = "#4CAF82"   # 绿色 — 已启用
    CHECKBOX_DISABLED = "#3D4255"  # 暗色 — 已禁用
    CHECKBOX_SOME = "#E8A838"      # 琥珀色 — 部分启用

    # 边框 & 分割线
    BORDER = "#252A38"         # 普通边框
    BORDER_FOCUS = "#4A9EFF"   # 聚焦态边框
    DIVIDER = "#1E2230"        # 分割线

    # ── QColor 缓存（避免重复构造）──

    _cache: dict[str, QColor] = {}

    @classmethod
    def qcolor(cls, hex_str: str) -> QColor:
        """返回 hex 字符串对应的 QColor，带缓存。"""
        if hex_str not in cls._cache:
            cls._cache[hex_str] = QColor(hex_str)
        return cls._cache[hex_str]


# ──────────────────────────────────────────────
# 字体系统
# ──────────────────────────────────────────────


class Fonts:
    """字体常量，与原 Flet 版本完全一致。"""

    BODY = "Noto Sans SC"
    MONO = "JetBrains Mono"

    # 字号（pt）
    SIZE_XS = 11
    SIZE_SM = 12
    SIZE_MD = 14
    SIZE_LG = 16
    SIZE_XL = 18
    SIZE_XXL = 22

    # ── QFont 工厂方法 ──

    @staticmethod
    def body(size: int = SIZE_MD, weight: int = QFont.Weight.Normal) -> QFont:
        """创建 body 字体。"""
        font = QFont(Fonts.BODY, size)
        font.setWeight(weight)
        return font

    @staticmethod
    def mono(size: int = SIZE_SM, weight: int = QFont.Weight.Normal) -> QFont:
        """创建等宽字体。"""
        font = QFont(Fonts.MONO, size)
        font.setWeight(weight)
        # JetBrains Mono 通常是等宽的，但显式设置确保
        font.setStyleHint(QFont.StyleHint.Monospace)
        return font


# ──────────────────────────────────────────────
# 间距系统
# ──────────────────────────────────────────────


class Spacing:
    """间距常量（px），与原 Flet 版本完全一致。"""

    XS = 4
    SM = 8
    MD = 12
    LG = 16
    XL = 24
    XXL = 32


# ──────────────────────────────────────────────
# 圆角系统
# ──────────────────────────────────────────────


class Radius:
    """圆角常量（px），与原 Flet 版本完全一致。"""

    SM = 4
    MD = 8
    LG = 12
    XL = 16


# ──────────────────────────────────────────────
# 全局样式表构建
# ──────────────────────────────────────────────


def build_global_stylesheet() -> str:
    """
    构建应用程序全局样式表。
    为 QApplication 设置深色主题基础样式。
    各控件具体样式通过 inline stylesheet 覆盖。
    """
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
