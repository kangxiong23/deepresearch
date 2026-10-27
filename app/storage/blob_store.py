# Layer: Storage
# File: app/storage/blob_store.py
# Responsibility: 节点挂载文件的内容寻址快照仓库（data/files/<sha256>）。
#                 只负责字节的复制/校验/读取，不做解析、不做业务判断。
# Input:  源文件绝对路径
# Output: 挂载记录 dict（blob_id / name / size / mtime / sha256 / added_at）
# 禁止: 调用解析器、读写 tree.json、UI 导入

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import config as app_config


# 快照大小上限（单文件 / 单节点累计）。超限拒绝并给用户明确提示，
# 不做"无上限静默复制"——这类功能最常见的翻车点。
MAX_BLOB_BYTES = 20 * 1024 * 1024          # 20 MB / 文件
MAX_NODE_BYTES = 200 * 1024 * 1024         # 200 MB / 节点累计

_CHUNK = 1024 * 1024


class BlobTooLarge(Exception):
    """文件超过快照上限。"""


class FileBlobStore:
    """
    内容寻址的文件快照仓库。

    同一份文件挂到多个节点只存一份（按内容 hash 去重）；
    节点上只存 blob_id 与展示元数据，文件被删被移都不影响已挂载内容。
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self._root_override = Path(root) if root else None

    # ──────────────────────────────────────────
    # 路径
    # ──────────────────────────────────────────

    @property
    def root(self) -> Path:
        """仓库根目录（每次读取 config，保证 ConfigScope 生效）。"""
        base = self._root_override or Path(app_config.FILES_STORE_PATH)
        base.mkdir(parents=True, exist_ok=True)
        return base

    def blob_path(self, blob_id: str) -> Path:
        """blob 原始字节路径。"""
        return self.root / _safe_name(blob_id)

    def text_path(self, blob_id: str) -> Path:
        """blob 的解析文本缓存路径（<sha256>.txt）。"""
        return self.root / (_safe_name(blob_id) + ".txt")

    def exists(self, blob_id: str) -> bool:
        return bool(blob_id) and self.blob_path(blob_id).exists()

    # ──────────────────────────────────────────
    # 写入
    # ──────────────────────────────────────────

    def put(
        self,
        source_path: str,
        *,
        max_bytes: int = MAX_BLOB_BYTES,
    ) -> dict:
        """
        把源文件快照进仓库，返回挂载记录。

        Raises:
            BlobTooLarge: 文件超过单文件上限
            OSError: 文件不可读
        """
        src = Path(source_path)
        stat = src.stat()
        size = stat.st_size
        if size > max_bytes:
            raise BlobTooLarge(
                f"{src.name} 超过单文件快照上限 "
                f"({size / 1024 / 1024:.1f} MB > {max_bytes / 1024 / 1024:.0f} MB)"
            )

        sha = _sha256_of(src)
        dest = self.blob_path(sha)
        if not dest.exists():
            # 先写临时文件再原子替换：复制到一半崩溃不会留下半个 blob
            fd, tmp = tempfile.mkstemp(dir=str(self.root), prefix=".tmp_", suffix=".part")
            os.close(fd)
            try:
                shutil.copyfile(src, tmp)
                os.replace(tmp, dest)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

        return {
            "blob_id": sha,
            "sha256": sha,
            "name": src.name,
            "size": size,
            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "added_at": datetime.now().isoformat(timespec="seconds"),
            "source_path": str(src.resolve()),
        }

    # ──────────────────────────────────────────
    # 读取 / 删除
    # ──────────────────────────────────────────

    def read_bytes(self, blob_id: str) -> bytes | None:
        """读 blob 原始字节（不存在返回 None）。"""
        path = self.blob_path(blob_id)
        try:
            return path.read_bytes()
        except OSError:
            return None

    def read_cached_text(self, blob_id: str) -> str | None:
        """读解析文本缓存（不存在返回 None）。"""
        path = self.text_path(blob_id)
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def write_cached_text(self, blob_id: str, text: str) -> None:
        """写解析文本缓存。blob 内容不可变，缓存无需失效逻辑。"""
        path = self.text_path(blob_id)
        try:
            path.write_text(text, encoding="utf-8")
        except OSError as e:
            print(f"[BLOB] 写解析缓存失败 {blob_id[:12]}: {e}")

    def delete(self, blob_id: str) -> bool:
        """删除 blob 与其解析缓存（引用已断开后由维护脚本调用）。"""
        removed = False
        for path in (self.blob_path(blob_id), self.text_path(blob_id)):
            try:
                path.unlink()
                removed = True
            except OSError:
                pass
        return removed

    def all_blob_ids(self) -> list[str]:
        """列出仓库中所有 blob_id（GC 用）。"""
        try:
            entries = list(self.root.iterdir())
        except OSError:
            return []
        return [
            p.name for p in entries
            if p.is_file() and not p.name.endswith(".txt") and not p.name.startswith(".")
        ]

    def total_bytes(self) -> int:
        """仓库占用字节数（诊断用）。"""
        total = 0
        try:
            for p in self.root.iterdir():
                if p.is_file():
                    total += p.stat().st_size
        except OSError:
            pass
        return total


# ──────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────

def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(blob_id: str) -> str:
    """
    只接受纯十六进制 blob_id，杜绝路径逃逸。

    刻意不做"过滤掉非法字符后继续用"——那会让 ../../etc/passwd 之类
    变成另一个合法名字，读到不属于它的文件。不合规就报错。
    """
    cleaned = "".join(c for c in (blob_id or "") if c in "0123456789abcdef")
    if not cleaned or cleaned != blob_id:
        raise ValueError(f"非法 blob_id: {blob_id!r}")
    return cleaned


def node_mounts_bytes(mounts: list[dict] | None) -> int:
    """节点已挂载内容的总字节数（去重后）。"""
    seen: set[str] = set()
    total = 0
    for mount in mounts or []:
        blob_id = str((mount or {}).get("blob_id") or "")
        if not blob_id or blob_id in seen:
            continue
        seen.add(blob_id)
        total += int((mount or {}).get("size") or 0)
    return total
