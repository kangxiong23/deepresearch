# Layer: app/utils（独立工具，不在 Stack 内）
# File: app/utils/app_settings.py
# Responsibility: 用户偏好（config.py 里的可变项）的持久化与校验——
#                 把设置面板改的值存进 data/app_settings.json，启动时读回并写进 config。
# Input:  无（路径由 config.DB_PATH 推导，测试用 ConfigScope 自然隔离）
# Output: dict{key: value}（只含白名单里的项）
# 禁止: 导入 UI 库；改静态部署配置（API Key / 数据目录只在 .env 里改）

from __future__ import annotations

import json
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

import config as app_config
from app.storage.json_io import write_json_atomic

_FILENAME = "app_settings.json"


@dataclass(frozen=True)
class Field:
    """一个可改项的类型与取值范围。"""

    kind: str                                  # "bool" | "int" | "float" | "choice"
    minimum: float = 0
    maximum: float = 0
    options: tuple[str, ...] = dc_field(default_factory=tuple)


# 白名单：只有这里列出的才允许从界面改、才允许落盘。
# 范围写在这一处，界面直接拿它建控件——不然 SpinBox 允许 999999、config 其实只认
# 64..65536，就成了两套规则。
EDITABLE: dict[str, Field] = {
    "model_type": Field("choice", options=("deepseek-v4-flash", "deepseek-v4-pro")),
    "thinking_enabled": Field("bool"),
    "reasoning_effort": Field("choice", options=("low", "high", "max")),
    "temperature": Field("float", 0.0, 2.0),
    "max_tokens": Field("int", 64, 65536),
    "max_history_tokens": Field("int", 1000, 2_000_000),
    "search_enabled": Field("bool"),
    "search_max_results": Field("int", 1, 20),
    "kg_injection_enabled": Field("bool"),
    "kg_max_inject": Field("int", 1, 50),
    "kg_extract_recent_n": Field("int", 2, 50),
}

# 静态部署配置：设置面板只展示，不允许改。改了 DB_PATH / 存储目录会让旧数据
# “凭空消失”（其实只是读到另一个空库），这类迁移必须由人显式决定。
READONLY: tuple[str, ...] = (
    "DEEPSEEK_BASE_URL",
    "DB_PATH",
    "TREE_STORE_PATH",
    "CONTEXT_STORE_PATH",
    "KG_STORE_PATH",
    "FILES_STORE_PATH",
)


def coerce(key: str, value: Any) -> tuple[bool, Any]:
    """
    把一个外部值收敛到白名单项的类型与范围内。

    Returns:
        (是否接受, 收敛后的值)。类型完全不对时返回 (False, None)，
        越界的数字钳到边界而不是拒绝——用户手滑写 200 的温度，钳到 2.0 更有用。
    """
    spec = EDITABLE.get(key)
    if spec is None:
        return False, None

    if spec.kind == "bool":
        if isinstance(value, bool):
            return True, value
        if isinstance(value, int) and value in (0, 1):
            return True, bool(value)
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return True, value.lower() == "true"
        return False, None

    if spec.kind == "choice":
        text = str(value or "").strip()
        return (True, text) if text in spec.options else (False, None)

    if spec.kind in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return False, None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False, None
        if number != number:          # NaN
            return False, None
        number = min(max(number, spec.minimum), spec.maximum)
        return True, (int(round(number)) if spec.kind == "int" else round(number, 2))

    return False, None


class AppSettings:
    """
    用户偏好的持久化。

    放在 DB_PATH 同级：真实运行是 data/app_settings.json，测试里 ConfigScope 把
    DB_PATH 指到临时目录，这里自动跟着隔离。
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else (
            Path(app_config.DB_PATH).parent / _FILENAME
        )

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict:
        """读取偏好；文件缺失/损坏/字段非法一律跳过（回落到 config 当前值）。"""
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}

        out: dict = {}
        for key, value in data.items():
            ok, parsed = coerce(key, value)
            if ok:
                out[key] = parsed
        return out

    def save(self, values: dict) -> None:
        """原子写入——半截文件不会被下次启动读到。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(str(self._path), dict(values))

    def merge_and_save(self, values: dict) -> dict:
        """
        合并保存：只接受白名单内的键，非法的丢弃，返回落盘后的完整值。
        """
        merged = self.load()
        for key, value in (values or {}).items():
            ok, parsed = coerce(key, value)
            if ok:
                merged[key] = parsed
        self.save(merged)
        return merged

    def current(self) -> dict:
        """当前生效值（含 .env 默认与本次运行内改过的）。"""
        return {key: getattr(app_config, key, None) for key in EDITABLE}

    @staticmethod
    def factory_defaults() -> dict:
        """出厂默认（config.py 的 _factory_defaults 快照，不受用户偏好影响）。"""
        defaults = getattr(app_config, "_factory_defaults", {})
        return {key: defaults[key] for key in EDITABLE if key in defaults}

    def apply_to_config(self) -> dict:
        """把持久化值写进 config（启动时调用一次）。"""
        data = self.load()
        for key, value in data.items():
            setattr(app_config, key, value)
        return data
