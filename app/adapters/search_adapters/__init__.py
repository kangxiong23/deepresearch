# Layer: Adapter
# File: app/adapters/search_adapters/__init__.py
#
# 搜索后端一览：
#   DeepSeekSearchAdapter —— DeepSeek 官方 Search 接口（需要 API Key，结构化结果）
#   ArxivSearchAdapter    —— Arxiv 学术检索（免 Key，只在网页结果不够时补位）

from app.adapters.search_adapters.base import BaseSearchAdapter
from app.adapters.search_adapters.deepseek_search import (
    DeepSeekFetchAdapter,
    DeepSeekSearchAdapter,
)
from app.adapters.search_adapters.arxiv import ArxivSearchAdapter

__all__ = [
    "BaseSearchAdapter",
    "DeepSeekSearchAdapter",
    "DeepSeekFetchAdapter",
    "ArxivSearchAdapter",
]
