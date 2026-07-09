# Layer: Adapter
# File: app/adapters/__init__.py

from app.adapters.deepseek_client import DeepSeekClient, LLMAPIError
from app.adapters.file_parsers import default_parsers
from app.adapters.search_adapters import DuckDuckGoSearchAdapter

__all__ = [
    "DeepSeekClient",
    "LLMAPIError",
    "default_parsers",
    "DuckDuckGoSearchAdapter",
]
