# Layer: app/utils（独立工具，不在 Stack 内）
# File: app/utils/file_access_settings.py
# Responsibility: 本地文件访问授权的持久化——把"是否允许"与"授权目录"写入
#                 data 目录下的 file_access.json，启动时读回并覆盖 config。
# Input:  无（路径由 config.DB_PATH 推导，测试用 ConfigScope 自然隔离）
# Output: dict{enabled, roots}
# 禁止: 导入 UI 库；读写用户文件（这里只碰本应用自己的 data 目录）

from __future__ import annotations

import json
from pathlib import Path

import config as app_config
from app.storage.json_io import write_json_atomic

_FILENAME = "file_access.json"


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
