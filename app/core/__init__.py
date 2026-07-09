# Layer: Core
# File: app/core/__init__.py
# 公开 Core 层对外接口，Controller 层通过此处导入服务类。

from app.core.conversation_service import ConversationService, ConversationNotFoundError
from app.core.context_service import ContextService
from app.core.search_service import SearchService
from app.core.file_service import FileService
from app.core.protocols import (
    LLMClientProtocol,
    SearchAdapterProtocol,
    FileParserProtocol,
    ConversationRepoProtocol,
    ContextStoreProtocol,
)

__all__ = [
    "ConversationService",
    "ConversationNotFoundError",
    "ContextService",
    "SearchService",
    "FileService",
    "LLMClientProtocol",
    "SearchAdapterProtocol",
    "FileParserProtocol",
    "ConversationRepoProtocol",
    "ContextStoreProtocol",
]
