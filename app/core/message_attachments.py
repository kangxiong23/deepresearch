# Layer: Core
# File: app/core/message_attachments.py
# Responsibility: 消息附件的统一规则——上限裁剪、展开格式、token 记账。
#                 附件文本直接存在 messages.attachments 列里（内容随消息行走），
#                 content 列只放用户自己写的字；只有构建 LLM 上下文时才展开。
# Input:  [(文件名, 提取文本)] / [{"name", "text"}]
# Output: 裁剪后的附件列表、展开后的消息文本、token 估算值
# 禁止: 文件 IO、解析器调用、UI 导入（本模块是纯函数）

from __future__ import annotations

# ── 上限 ─────────────────────────────────────
# 单文件 2 万字、单条消息合计 5 万字。超限截断并显式标注，绝不静默丢内容，
# 也不拒绝发送（拒绝会让用户困惑）；界面提示"建议改用节点挂载"。
MAX_FILE_CHARS = 20_000
MAX_MESSAGE_CHARS = 50_000

TRUNCATE_MARK = "\n…（超出上限，已截断 {n} 字，建议改用节点挂载）"


def normalize(
    pairs: list[tuple[str, str]],
    *,
    max_file_chars: int = MAX_FILE_CHARS,
    max_message_chars: int = MAX_MESSAGE_CHARS,
) -> tuple[list[dict], list[str]]:
    """
    把解析结果裁剪成可落库的附件列表。

    Args:
        pairs: [(文件名, 提取文本)]，解析失败的项已由调用方过滤

    Returns:
        (attachments, warnings)
        attachments — [{"name": str, "text": str}]
        warnings    — 人话提示（截断/丢弃），供日志与界面使用
    """
    attachments: list[dict] = []
    warnings: list[str] = []
    used = 0

    for name, text in pairs:
        name = name or "附件"
        text = (text or "").strip()
        if not text:
            continue

        remaining = max_message_chars - used
        if remaining <= 0:
            warnings.append("「" + name + "」超出单条消息附件总量上限，未附加")
            continue

        budget = min(max_file_chars, remaining)
        if len(text) > budget:
            removed = len(text) - budget
            text = text[:budget].rstrip() + TRUNCATE_MARK.format(n=removed)
            if budget < max_file_chars:
                warnings.append(
                    "「" + name + "」超出单条消息附件总量上限，已截断 " + str(removed) + " 字"
                )
            else:
                warnings.append(
                    "「" + name + "」超出单文件上限，已截断 " + str(removed) + " 字"
                )

        attachments.append({"name": name, "text": text})
        used += len(text)

    return attachments, warnings


def expand(content: str, attachments: list[dict] | None) -> str:
    """
    把附件文本拼到消息正文后面——**唯一**的展开点。

    只允许在构建 LLM 上下文时调用。其他任何地方（编辑、复制、预览、
    搜索、token 记账以外的展示）都应使用 content 原值，否则会出现
    "文档正文灌进输入框" / "regenerate 重复注入" 这类回归。
    """
    body = content or ""
    if not attachments:
        return body

    parts: list[str] = [body] if body else []
    for att in attachments:
        text = (att.get("text") or "")
        if not text.strip():
            continue
        parts.append("[附件：" + str(att.get("name") or "附件") + "]\n" + text)
    return "\n\n".join(parts)


def estimate_tokens(content: str, attachments: list[dict] | None = None) -> int:
    """
    消息的 token 估算值（正文 + 展开后的附件）。

    必须把附件算进去：否则一条挂了 30 万字附件的消息会被 _truncate_history
    当成轻量消息放行，请求直接 400。
    """
    text = expand(content, attachments) if attachments else (content or "")
    return len(text) // 4


def names(attachments: list[dict] | None) -> list[str]:
    """提取附件文件名列表（UI chip 展示用）。"""
    return [str(a.get("name") or "附件") for a in (attachments or [])]


def char_counts(attachments: list[dict] | None) -> list[int]:
    """提取附件字符数列表（与 names 一一对应）。"""
    return [len(a.get("text") or "") for a in (attachments or [])]
