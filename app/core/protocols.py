# Layer: Core
# File: app/core/protocols.py
# Responsibility: 定义 Core 层依赖的所有底层能力抽象接口（Protocol）。
#                 Adapter 层通过实现这些协议与 Core 解耦。
#                 Core 服务只依赖这些协议，不依赖任何具体实现类。
# Input:  无（纯接口定义）
# Output: 可被 Core 服务注入、被 Adapter 实现的 Protocol 类

from __future__ import annotations
from typing import AsyncGenerator, Protocol, runtime_checkable

from app.storage.models import (
    Conversation,
    ConversationDetail,
    Message,
    MessageChunk,
    LLMContext,
    SearchResults,
    ContextBlock,
    ContextTemplate,
)


# ──────────────────────────────────────────────
# LLM 客户端接口
# ──────────────────────────────────────────────

@runtime_checkable
class LLMClientProtocol(Protocol):
    """
    LLM 推理客户端抽象。
    Core 层通过此接口调用语言模型，不关心具体 API 实现。
    实现者：app/adapters/deepseek_client.py
    """

    async def stream_chat(
        self,
        context: LLMContext,
    ) -> AsyncGenerator[MessageChunk, None]:
        """
        流式对话推理。

        Args:
            context: Core 层组装好的完整请求上下文

        Yields:
            MessageChunk — 逐块返回，is_done=True 表示结束
        """
        ...

    def abort(self) -> None:
        """中止当前正在进行的流式请求。"""
        ...


# ──────────────────────────────────────────────
# 搜索适配器接口
# ──────────────────────────────────────────────

@runtime_checkable
class SearchAdapterProtocol(Protocol):
    """
    搜索后端抽象。
    实现者：app/adapters/search_adapters/arxiv.py 等
    """

    async def search(
        self,
        query: str,
        max_results: int = 5,
    ) -> SearchResults:
        """
        执行搜索并返回结构化结果。

        Args:
            query:       搜索查询字符串
            max_results: 最多返回条目数

        Returns:
            SearchResults
        """
        ...


# ──────────────────────────────────────────────
# 文件解析接口
# ──────────────────────────────────────────────

@runtime_checkable
class FileParserProtocol(Protocol):
    """
    文件内容提取抽象。
    实现者：app/adapters/file_parsers.py
    """

    def can_parse(self, filename: str) -> bool:
        """判断是否支持解析此文件类型。"""
        ...

    async def extract_text(self, file_path: str) -> str:
        """
        提取文件中的纯文本内容。

        Args:
            file_path: 文件绝对路径

        Returns:
            提取到的文本字符串
        """
        ...


# ──────────────────────────────────────────────
# 对话存储接口
# ──────────────────────────────────────────────

@runtime_checkable
class ConversationRepoProtocol(Protocol):
    """
    对话历史持久化抽象。
    实现者：app/storage/conversation_repo.py
    """

    def save_conversation(self, conversation: Conversation) -> None:
        """新建或更新对话元数据。"""
        ...

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        """按 ID 获取对话摘要，不存在返回 None。"""
        ...

    def get_conversation_detail(
        self, conversation_id: str
    ) -> ConversationDetail | None:
        """按 ID 获取完整对话（含消息列表），不存在返回 None。"""
        ...

    def list_conversations(self) -> list[Conversation]:
        """获取所有对话摘要，按 updated_at 倒序。"""
        ...

    def delete_conversation(self, conversation_id: str) -> None:
        """删除对话及其所有消息。"""
        ...

    def save_message(self, message: Message) -> None:
        """保存一条消息。"""
        ...

    def get_messages(self, conversation_id: str) -> list[Message]:
        """获取某对话的全部消息，按 created_at 正序。"""
        ...

    def delete_message(self, message_id: str) -> None:
        """删除单条消息（重新生成时使用）。"""
        ...

    def update_conversation_meta(
        self,
        conversation_id: str,
        title: str | None = None,
        last_message_preview: str | None = None,
    ) -> None:
        """更新对话标题或摘要预览。"""
        ...


# ──────────────────────────────────────────────
# 上下文存储接口
# ──────────────────────────────────────────────

@runtime_checkable
class ContextStoreProtocol(Protocol):
    """
    上下文块 & 模板持久化抽象。
    实现者：app/storage/context_store.py
    """

    def save_block(self, block: ContextBlock) -> None:
        """保存或更新上下文块。"""
        ...

    def get_blocks(self) -> list[ContextBlock]:
        """获取所有上下文块，按 order 正序。"""
        ...

    def delete_block(self, block_id: str) -> None:
        """删除上下文块。"""
        ...

    def update_block_enabled(self, block_id: str, enabled: bool) -> None:
        """切换上下文块的启用状态。"""
        ...

    def list_templates(self) -> list[ContextTemplate]:
        """获取所有模板。"""
        ...

    def get_template(self, template_id: str) -> ContextTemplate | None:
        """按 ID 获取模板，不存在返回 None。"""
        ...

    def save_template(self, template: ContextTemplate) -> None:
        """保存或更新模板。"""
        ...
