# Layer: Utils
# File: app/utils/draft_store.py
# Responsibility: 单槽输入草稿持久化（B1 方案）。
#                 只保存"最后一次未发送的文本 + 所属对话 id"，用于崩溃/退出后恢复。
#                 属于 UI 运行时状态，不是领域数据，因此放在 utils 而非 storage。
# Input:  对话 id / 文本
# Output: Draft 对象或 None
# 禁止: 业务判断、导入 UI 库

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import config as app_config
from app.storage.json_io import write_json_atomic

# 太久以前的草稿不再恢复，避免几周后突然冒出陈年文字
_MAX_AGE = timedelta(days=7)


@dataclass
class Draft:
    conv_id: str
    text: str
    updated_at: str


class DraftStore:
    """
    单槽草稿存储。

    取舍（B1）：只保"最后一次没发出去的话"。若在 A 对话留了草稿后又到 B 对话输入，
    A 的草稿会被覆盖 —— 这是单槽方案的既定语义，不是 bug。
    """

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            path = Path(app_config.TREE_STORE_PATH).parent / "draft.json"
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Draft | None:
        """读取草稿；空文本、过期、损坏一律返回 None。"""
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None

        text = str(data.get("text") or "")
        if not text.strip():
            return None

        updated = str(data.get("updated_at") or "")
        try:
            if datetime.now() - datetime.fromisoformat(updated) > _MAX_AGE:
                return None
        except ValueError:
            pass  # 时间戳坏掉不该连累内容，照常恢复

        return Draft(
            conv_id=str(data.get("conv_id") or ""),
            text=text,
            updated_at=updated,
        )

    def save(self, conv_id: str, text: str) -> None:
        """保存草稿；text 为空等价于清除（避免重启后复活已删内容）。"""
        if not text.strip():
            self.clear()
            return
        write_json_atomic(self._path, {
            "conv_id": conv_id or "",
            "text": text,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })

    def clear(self) -> None:
        try:
            self._path.unlink()
        except OSError:
            pass
