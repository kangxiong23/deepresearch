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
    Message,
    MessageChunk,
    LLMContext,
    SearchResults,
    ContextBlock,
    ContextTemplate,
    AnyTreeNode,
    MessageNode,
    TrashEntry,
    TreeRoot,
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
# 消息持久化接口
# ──────────────────────────────────────────────

@runtime_checkable
class MessageRepoProtocol(Protocol):
    """
    消息持久化抽象（仅 messages 表，对话元数据由 TreeStore 接管）。
    实现者：app/storage/message_repo.py

    Phase 2+ 将 ConversationRepoProtocol 精简为此接口，
    对话 CRUD 操作全部通过 TreeStore 完成。
    """

    def save_message(self, message: Message) -> None:
        """保存一条消息（UPSERT）。"""
        ...

    def get_messages(self, conversation_id: str) -> list[Message]:
        """获取某对话的全部消息，按 created_at 正序。"""
        ...

    def delete_message(self, message_id: str) -> None:
        """删除单条消息（重新生成时使用）。"""
        ...

    def delete_messages_by_conversation(self, conversation_id: str) -> int:
        """
        删除指定对话 ID 下的所有消息（彻底删除时使用）。

        Returns:
            删除的消息行数
        """
        ...

    def get_messages_by_ids(self, message_ids: list[str]) -> list[Message]:
        """按 ID 批量获取消息，按 created_at 升序排列。"""
        ...

    def get_messages_by_conversation_ids(
        self, conversation_ids: list[str]
    ) -> list[Message]:
        """按对话 ID 集合批量获取消息，按 created_at 升序排列。"""
        ...

    def get_all_messages(self) -> list[Message]:
        """返回数据库中所有消息，按 created_at 降序排列。"""
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

    def delete_template(self, template_id: str) -> None:
        """删除模板。"""
        ...


# ──────────────────────────────────────────────
# 树形结构存储接口
# ──────────────────────────────────────────────

@runtime_checkable
class TreeStoreProtocol(Protocol):
    """
    树形结构持久化抽象。
    实现者：app/storage/tree_store.py

    Phase 1 先定义协议，Phase 2+ 通过此接口注入 Core 层。
    """

    def get_tree(self) -> TreeRoot:
        """返回当前树根（深拷贝）。"""
        ...

    def get_node(self, node_id: str) -> AnyTreeNode | None:
        """按 ID 查找节点。"""
        ...

    def get_children(self, parent_id: str | None) -> list[AnyTreeNode]:
        """返回直接子节点，按 sort_order 排序。"""
        ...

    def get_path(self, node_id: str) -> str:
        """返回从根到节点的路径字符串。"""
        ...

    def create_node(self, node: AnyTreeNode) -> None:
        """插入新节点。"""
        ...

    def update_node(self, node_id: str, **updates) -> None:
        """更新节点字段。"""
        ...

    def move_node(
        self,
        node_id: str,
        new_parent_id: str | None = None,
        position: int | None = None,
    ) -> None:
        """移动节点到新父级下。"""
        ...

    def delete_node(
        self,
        node_id: str,
        mode: str = "recursive",
    ) -> tuple[list[AnyTreeNode], list[TrashEntry]]:
        """从树中删除节点（不自动保存回收站）。"""
        ...

    def soft_delete_node(
        self,
        node_id: str,
        mode: str = "recursive",
    ) -> None:
        """软删除：移除节点并存入回收站。"""
        ...

    def restore_from_trash(
        self,
        entry_id: str,
        new_parent_id: str | None = None,
        new_position: int | None = None,
    ) -> None:
        """从回收站恢复节点。"""
        ...

    def permanently_delete_from_trash(self, entry_id: str) -> None:
        """从回收站彻底移除。"""
        ...

    def list_trash(self) -> list[TrashEntry]:
        """返回回收站所有条目。"""
        ...

    def clear_trash(self) -> int:
        """清空回收站，返回清除的条目数量。"""
        ...

    def get_all_message_nodes(self) -> list[MessageNode]:
        """返回树中所有 MessageNode 实例。"""
        ...

    def get_message_ids_in_tree_order(self, root_id: str | None = None) -> list[str]:
        """以 DFS 前序遍历收集树中的 MessageNode.message_id，按树结构排序。"""
        ...

    def get_all_node_ids_in_tree_order(self) -> list[str]:
        """以 DFS 前序遍历收集所有节点 id（目录/对话/消息），按树结构排序。"""
        ...

    def get_descendants(self, node_id: str) -> list[AnyTreeNode]:
        """收集节点的所有后代（BFS，不包含节点自身）。"""
        ...

    def set_node_enabled_cascade_down(
        self, node_id: str, enabled: bool
    ) -> list[str]:
        """设置节点 enabled 状态并递归级联到所有后代，返回受影响的节点 ID。"""
        ...

    def recompute_ancestors_enabled(self, node_id: str) -> list[str]:
        """自底向上重新计算祖先节点的 enabled 状态，返回变化的祖先 ID。"""
        ...
