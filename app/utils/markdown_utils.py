# Layer: Utils（跨层通用）
# File: app/utils/markdown_utils.py
# Responsibility: Markdown 相关工具函数，供 UI 层和 Core 层共用。
# Input:  字符串
# Output: 处理后的字符串

from __future__ import annotations

import re


def extract_plain_text(markdown: str) -> str:
    """
    从 Markdown 文本中提取纯文本（用于预览摘要）。
    去除标题符号、代码块、链接语法、粗体斜体标记等。

    Args:
        markdown: 原始 Markdown 字符串

    Returns:
        纯文本字符串，多余空白已折叠
    """
    text = markdown

    # 删除代码块（``` ... ```）
    text = re.sub(r"```[\s\S]*?```", "", text)

    # 删除行内代码（`...`）
    text = re.sub(r"`[^`]*`", "", text)

    # 删除图片 ![alt](url)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)

    # 链接 [text](url) → text
    text = re.sub(r"\[([^\]]*)\]\([^\)]*\)", r"\1", text)

    # 标题 # ## ### → 去掉 # 号
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)

    # 粗体 **text** / __text__
    text = re.sub(r"\*{1,2}([^\*]+)\*{1,2}", r"\1", text)
    text = re.sub(r"_{1,2}([^_]+)_{1,2}", r"\1", text)

    # 删除水平线
    text = re.sub(r"^\s*[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)

    # 折叠多余空白
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    return text


def truncate_for_preview(text: str, max_chars: int = 60) -> str:
    """
    截取文本前 max_chars 字作为预览，若截断则追加省略号。

    Args:
        text:      输入文本（建议先经过 extract_plain_text）
        max_chars: 最大字符数

    Returns:
        预览字符串
    """
    text = text.replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def estimate_token_count(text: str) -> int:
    """
    粗略估算文本的 token 数（用于上下文窗口管理）。
    规则：中文字符约 1 char = 1 token；英文约 4 chars = 1 token。
    不依赖 tiktoken，避免重量级依赖。

    Args:
        text: 输入文本

    Returns:
        估算 token 数（整数）
    """
    if not text:
        return 0

    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other_chars = len(text) - chinese_chars
    return chinese_chars + max(1, other_chars // 4)


def split_thinking_and_content(raw: str) -> tuple[str, str]:
    """
    从包含 <think>...</think> 标签的原始文本中分离思考内容和正文。
    部分模型以此格式输出推理链。

    Args:
        raw: 原始文本，可能含 <think> 块

    Returns:
        (thinking_content, main_content) — 任一可能为空串
    """
    pattern = re.compile(r"<think>([\s\S]*?)</think>", re.IGNORECASE)
    match = pattern.search(raw)
    if not match:
        return "", raw.strip()

    thinking = match.group(1).strip()
    main = pattern.sub("", raw).strip()
    return thinking, main
