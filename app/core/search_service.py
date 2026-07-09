# Layer: Core
# File: app/core/search_service.py
# Responsibility: 搜索编排服务。
#                 决定"是否执行搜索"、"用哪个搜索后端"、"结果如何格式化为可注入文本"。
#                 具体 HTTP 搜索请求委托给 SearchAdapterProtocol 实现。
# Input:  用户查询文本
# Output: 格式化好的搜索摘要文本（str），供 ContextService 注入用户消息
# 禁止: 直接实现 HTTP 请求、UI 导入

from __future__ import annotations

import config as app_config
from app.core.protocols import SearchAdapterProtocol
from app.storage.models import SearchResult, SearchResults


class SearchService:
    """
    搜索编排服务。

    编排职责：
    1. 读取 config.search_enabled 决定是否执行搜索
    2. 调用注入的 SearchAdapter 执行实际搜索
    3. 将结构化结果格式化为可注入 LLM 上下文的纯文本摘要

    支持多适配器（如同时注册 arxiv + web），按注册顺序依次搜索并合并结果。
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
                "1. 标题\n   来源: URL\n   摘要: ...\n\n2. ..."
            若未开启搜索或无结果，返回空串。
        """
        if not app_config.search_enabled:
            return ""
        if not query.strip():
            return ""
        print(f"[SearchService] 开始搜索, query={repr(query[:50])}, adapters={len(self._adapters)}")

        max_results = app_config.search_max_results
        all_results: list[SearchResult] = []

        for adapter in self._adapters:
            results = await adapter.search(
                query=query,
                max_results=max_results,
            )
            all_results.extend(results.results)

        if not all_results:
            print("[SearchService] 所有适配器均无结果")
            return ""

        print(f"[SearchService] 共 {len(all_results)} 条结果，格式化后注入上下文")
        return self._format_results(all_results[:max_results])

    # ──────────────────────────────────────────
    # 内部格式化
    # ──────────────────────────────────────────

    @staticmethod
    def _format_results(results: list[SearchResult]) -> str:
        """
        将搜索结果列表格式化为结构化纯文本，LLM 可直接阅读。

        格式：
            1. {title}
               来源: {url}
               摘要: {snippet}
        """
        lines: list[str] = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.title}")
            if r.url:
                lines.append(f"   来源: {r.url}")
            if r.snippet:
                lines.append(f"   摘要: {r.snippet}")
            lines.append("")  # 空行分隔
        return "\n".join(lines).strip()