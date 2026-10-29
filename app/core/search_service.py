# Layer: Core
# File: app/core/search_service.py
# Responsibility: 搜索编排服务。
#                 决定"是否执行搜索"、"用哪个搜索后端"、"结果如何格式化为可注入文本"。
#                 具体 HTTP 搜索请求委托给 SearchAdapterProtocol 实现。
# Input:  用户查询文本
# Output: 格式化好的搜索摘要文本（str），供 ContextService 注入用户消息
# 禁止: 直接实现 HTTP 请求、UI 导入

from __future__ import annotations

from datetime import datetime

import config as app_config
from app.core.protocols import SearchAdapterProtocol
from app.storage.models import SearchResult, SearchResults
from app.utils.async_utils import get_logger

logger = get_logger("CORE", "search")

# 单条摘要上限：接口的 summary 通常 600~1800 字，太长就截断——
# 与其把 5 条摘要全塞进去，不如把预算留给更多条结果。
_SUMMARY_CHARS = 900
# 注入文本总预算（字符）。超出后不再追加后续结果，并明确告诉模型"还有 N 条省略"。
_TOTAL_CHARS = 14_000


class SearchService:
    """
    搜索编排服务。

    编排职责：
    1. 读取 config.search_enabled 决定是否执行搜索
    2. 按注册顺序调用 SearchAdapter，凑够条数就停（不再空打后面的后端）
    3. 将结构化结果格式化为可注入 LLM 上下文的纯文本摘要

    多适配器（DeepSeek 官方搜索 + Arxiv）按顺序补齐：
    DeepSeek 返回满额时 Arxiv 根本不会被请求，省一次往返。
    """

    def __init__(self, adapters: list[SearchAdapterProtocol]) -> None:
        """
        Args:
            adapters: 搜索后端适配器列表，按注册顺序依次尝试
        """
        self._adapters = adapters

    # ──────────────────────────────────────────
    # 主接口
    # ──────────────────────────────────────────

    async def search_and_format(self, query: str) -> str:
        """
        执行搜索并返回格式化文本，供 ContextService 注入用户消息。

        当 config.search_enabled 为 False 时，直接返回空串，
        ConversationService 不会注入任何搜索内容。

        Args:
            query: 从用户输入中提取的搜索查询（由 ConversationService 传入原文）

        Returns:
            格式化搜索摘要文本，如：
                "1. 标题\n   来源: 站点 URL\n   发布时间: 2026-04-27\n   摘要: ..."
            若未开启搜索、搜索无结果且无错误，返回空串。
            若搜索**失败**，返回一段明确的"未取得资料"说明——静默返回空串会
            让用户以为"模型懒得搜"，也让模型以为"确实没有相关资料"。
        """
        if not app_config.search_enabled:
            return ""
        if not query.strip():
            return ""

        max_results = _positive_int(getattr(app_config, "search_max_results", 5), 5)
        logger.info("开始搜索 query=%r adapters=%d", query[:50], len(self._adapters))

        all_results: list[SearchResult] = []
        errors: list[str] = []

        for adapter in self._adapters:
            if len(all_results) >= max_results:
                break                      # 够了，别再打后面的后端（免一次往返）
            name = str(getattr(adapter, "name", "") or type(adapter).__name__)
            try:
                batch: SearchResults = await adapter.search(
                    query=query,
                    max_results=max_results,
                )
            except Exception as exc:       # 适配器违约抛了异常，不能拖垮整轮对话
                logger.warning("%s 搜索异常: %s", name, exc)
                errors.append(f"{name}：{type(exc).__name__}: {exc}")
                continue

            if batch.error:
                errors.append(f"{name}：{batch.error}")
            all_results.extend(batch.results)

        if not all_results:
            if errors:
                logger.warning("搜索失败: %s", "；".join(errors))
                return self._failure_text(errors)
            logger.info("搜索无结果")
            return ""

        logger.info("共 %d 条结果，格式化后注入上下文", len(all_results))
        text = self._format_results(all_results[:max_results])
        if errors:
            # 部分后端失败但另有来源补上了：结果照用，但必须说明来源不全，
            # 否则"用两条不相关的 Arxiv 论文回答一切问题"没有任何痕迹。
            text += "\n\n" + self._partial_note(errors)
        return text

    # ──────────────────────────────────────────
    # 内部格式化
    # ──────────────────────────────────────────

    @staticmethod
    def _format_results(results: list[SearchResult]) -> str:
        """
        将搜索结果列表格式化为结构化纯文本，LLM 可直接阅读。

        格式：
            1. {title}
               来源: {site} {url}
               发布时间: {YYYY-MM-DD}
               摘要: {summary}
        """
        lines: list[str] = []
        used = 0
        for index, result in enumerate(results, 1):
            block = [f"{index}. {result.title}"]

            source = " ".join(
                part for part in (result.site, result.url) if part
            )
            if source:
                block.append(f"   来源: {source}")

            published = _format_date(result.published)
            if published:
                block.append(f"   发布时间: {published}")

            # 有 summary 就用 summary：它是按 query 提炼过的；没有才退回 snippet
            summary = _clip(result.summary or result.snippet, _SUMMARY_CHARS)
            if summary:
                block.append(f"   摘要: {summary}")

            block.append("")
            size = sum(len(line) for line in block)
            if lines and used + size > _TOTAL_CHARS:
                omitted = len(results) - (index - 1)
                lines.append(f"（其余 {omitted} 条结果因篇幅省略）")
                break

            lines.extend(block)
            used += size

        return "\n".join(lines).strip()

    @staticmethod
    def _partial_note(errors: list[str]) -> str:
        """部分后端失败时的说明（结果仍然可用，但来源不全）。"""
        detail = "；".join(errors[:2])
        return f"（注：部分搜索后端不可用 —— {detail}；以上结果只来自其余来源）"

    @staticmethod
    def _failure_text(errors: list[str]) -> str:
        """
        搜索失败时注入的说明。

        刻意写进上下文而不是只打日志：用户开着"联网搜索"却拿到一个纯记忆回答，
        界面上没有任何痕迹；模型也会以为自己拿到了资料。两句话各管一件事——
        前半句给用户看，后半句约束模型别编引用。
        """
        detail = "；".join(errors[:2])
        return (
            f"（联网搜索未成功：{detail}）\n"
            "本轮没有取得任何实时检索结果，回答时不要编造引用来源；"
            "如果需要最新信息，请提示用户检查联网搜索配置。"
        )


# ──────────────────────────────────────────────
# 小工具
# ──────────────────────────────────────────────

def _clip(text: str, limit: int) -> str:
    """合并空白并截断（摘要里带换行，直接拼进缩进行会破坏格式）。"""
    flat = " ".join(str(text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"


def _format_date(raw: str) -> str:
    """RFC 3339（UTC）→ 本地日期。解析不了就原样给前 10 个字符。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10]
    if moment.tzinfo is not None:
        moment = moment.astimezone()      # UTC → 本地时区（日期可能因此跨一天）
    return moment.strftime("%Y-%m-%d")


def _positive_int(value, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback
