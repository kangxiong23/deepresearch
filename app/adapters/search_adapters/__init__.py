# Layer: Adapter
# File: app/adapters/search_adapters/__init__.py

from app.adapters.search_adapters.base import BaseSearchAdapter
from app.adapters.search_adapters.web_search import DuckDuckGoSearchAdapter
from app.adapters.search_adapters.arxiv import ArxivSearchAdapter

__all__ = [
    "BaseSearchAdapter",
    "DuckDuckGoSearchAdapter",
    "ArxivSearchAdapter",
]
