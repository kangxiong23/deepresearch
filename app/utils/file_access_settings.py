# Layer: app/utils（独立工具，不在 Stack 内）
# File: app/utils/file_access_settings.py
# Responsibility: 本地文件访问的**全局授权**持久化——把"是否允许"与"授权目录"
#                 写入 data 目录下的 file_access.json，启动时读回并覆盖 config。
#                 节点级授权（文件夹/对话）存在 tree.json 的节点字段上，不在这里。
# Input:  无（路径由 config.DB_PATH 推导，测试用 ConfigScope 自然隔离）
# Output: dict{enabled, roots}
# 禁止: 导入 UI 库；读写用户文件（这里只碰本应用自己的 data 目录）

from __future__ import annotations

import json
import os
from pathlib import Path

import config as app_config
from app.storage.json_io import write_json_atomic

_FILENAME = "file_access.json"


def normalize_root(path: str) -> str:
    """
    授权目录的规范形式：去空白/引号、统一分隔符、去掉结尾分隔符。

    拖进来的路径、对话框选的路径、手写配置里的路径形态各不相同，
    不归一化就会出现"同一个目录授权两次"（去重失效）或"路径尾斜杠看不出来"。
    """
    text = str(path or "").strip().strip('"').strip("'")
    if not text:
        return ""
    try:
        return os.path.normpath(text)
    except (OSError, ValueError):   # Windows 保留设备名等极端情况
        return text


def root_key(path: str) -> str:
    """去重用的比较键：Windows 下大小写不敏感（同一个目录的不同大小写写法算一条）。"""
    return os.path.normcase(normalize_root(path))


class FileAccessSettings:
    """
    授权的持久化。

    放在 DB_PATH 同级：真实运行是 data/file_access.json，
    测试里 ConfigScope 把 DB_PATH 指到临时目录，这里自动跟着隔离，
    不会污染真实配置。
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else (
            Path(app_config.DB_PATH).parent / _FILENAME
        )

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict:
        """读取授权配置；文件缺失或损坏一律回退到"全部关闭"。"""
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return {"enabled": False, "roots": []}

        if not isinstance(data, dict):
            return {"enabled": False, "roots": []}

        roots = [
            str(r) for r in (data.get("roots") or [])
            if isinstance(r, str) and r.strip()
        ]
        return {"enabled": bool(data.get("enabled")), "roots": roots}

    def save(self, enabled: bool, roots: list[str]) -> None:
        """原子写入——半截文件不会被下次启动读到。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(
            str(self._path),
            {"enabled": bool(enabled), "roots": [str(r) for r in roots if r]},
        )

    def apply_to_config(self) -> dict:
        """把持久化值写进 config（启动时调用一次）。"""
        data = self.load()
        app_config.file_access_enabled = data["enabled"]
        # 整体赋值：不要原地 append，_defaults 里的列表是跨 scope 共享的
        app_config.file_access_roots = data["roots"]
        return data
