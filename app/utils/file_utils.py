# Layer: Utils（跨层通用）
# File: app/utils/file_utils.py
# Responsibility: 文件路径、扩展名、编码检测等通用工具。
#                 供 FileService 和 FileParser 共用，不含业务逻辑。
# Input:  文件路径字符串
# Output: 布尔值、字符串等简单类型

from __future__ import annotations

import os
from pathlib import Path


# ──────────────────────────────────────────────
# 扩展名工具
# ──────────────────────────────────────────────

def get_extension(filename: str) -> str:
    """
    返回小写扩展名（含点），如 'Report.PDF' → '.pdf'。
    无扩展名时返回空串。
    """
    return os.path.splitext(filename)[1].lower()


def is_text_file(filename: str) -> bool:
    """判断是否为纯文本类型（txt/md/rst/json/csv 等）。"""
    return get_extension(filename) in {
        ".txt", ".md", ".markdown", ".rst",
        ".json", ".jsonl", ".csv", ".tsv", ".text",
    }


def is_document_file(filename: str) -> bool:
    """判断是否为文档类型（docx/pdf 等需要专用解析器）。"""
    return get_extension(filename) in {".docx", ".doc", ".pdf"}


# ──────────────────────────────────────────────
# 文件名安全处理
# ──────────────────────────────────────────────

def safe_filename(name: str, max_len: int = 128) -> str:
    """
    将任意字符串转为安全文件名：
    - 替换路径分隔符和特殊字符为下划线
    - 截断到 max_len 字符

    Args:
        name:    原始名称
        max_len: 最大长度

    Returns:
        安全文件名（不含扩展名）
    """
    import re
    safe = re.sub(r'[\\/:*?"<>|\s]', "_", name)
    return safe[:max_len]


# ──────────────────────────────────────────────
# 路径工具
# ──────────────────────────────────────────────

def ensure_dir(path: str | Path) -> Path:
    """
    确保目录存在，不存在则递归创建。

    Returns:
        Path 对象
    """
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def file_size_str(path: str | Path) -> str:
    """
    返回人类可读的文件大小，如 '1.2 MB'、'34 KB'。
    文件不存在时返回 '—'。
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return "—"

    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# ──────────────────────────────────────────────
# 编码检测（轻量版，不依赖 chardet）
# ──────────────────────────────────────────────

def read_text_safe(path: str | Path, max_bytes: int = 10 * 1024 * 1024) -> str:
    """
    安全读取文本文件，依次尝试 UTF-8 → GBK → Latin-1。
    超过 max_bytes 的部分截断（默认 10 MB）。

    Returns:
        文件文本内容，失败时返回空串
    """
    p = Path(path)
    if not p.exists():
        return ""

    raw: bytes
    try:
        raw = p.read_bytes()[:max_bytes]
    except OSError:
        return ""

    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue

    return raw.decode("latin-1", errors="replace")
