# Layer: Adapter
# File: app/adapters/search_adapters/base.py
# Responsibility: 定义搜索适配器的抽象基类（ABC）。
#                 所有具体搜索适配器（DuckDuckGo、Arxiv 等）继承此类，
#                 保证接口统一，便于 SearchService 多源聚合。
# Input:  query: str, max_results: int
# Output: SearchResults

from __future__ import annotations

from abc import ABC, abstractmethod

from app.storage.models import SearchResults


class BaseSearchAdapter(ABC):
    """
    搜索适配器抽象基类。

    所有具体适配器必须实现 search() 方法。
    SearchService 以此类型持有适配器列表，调用时无需关心具体实现。
    """

    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> SearchResults:
        """
        执行搜索并返回结果。

        Args:
            query:       搜索关键词
            max_results: 最多返回条目数

        Returns:
            SearchResults — 若搜索失败应返回空结果，不抛出异常

        Contract:
            - 不抛出网络异常（内部捕获，返回空 SearchResults）
            - results 列表长度 ≤ max_results
        """
        ...

    @property
    def name(self) -> str:
        """适配器名称，用于日志标识。默认取类名。"""
        return self.__class__.__name__
