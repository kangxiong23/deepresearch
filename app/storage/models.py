# Layer: Storage (共享领域模型)
# File: app/storage/models.py
# Responsibility: 全项目通用的 Pydantic / dataclass 领域数据模型。
#                 这是各层之间的"通用语言"，Core 编排、Adapter 填充、Storage 持久化
#                 均使用同一套模型，不在各层重复定义。
# Input:  无（纯模型定义）
# Output: 可被任意层 import 的数据容器

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional, Union


def utcnow() -> datetime:
    """返回 naive UTC 时间戳（等价 `datetime.utcnow()`，避免 3.12+ 弃用警告）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ──────────────────────────────────────────────
# 枚举
# ──────────────────────────────────────────────

class Role(str, Enum):
    USER      = "user"
    ASSISTANT = "assistant"
    SYSTEM    = "system"
    THINKING  = "thinking"   # 思考块（DeepSeek reasoner 内部链）


class ChunkType(str, Enum):
    TEXT     = "text"
    THINKING = "thinking"


class ContextSource(str, Enum):
    MANUAL   = "manual"
    FILE     = "file"
    TEMPLATE = "template"


# ──────────────────────────────────────────────
# 消息 & 对话
# ──────────────────────────────────────────────

@dataclass
class Message:
    """单条对话消息，对应数据库一行。"""
    id: str
    conversation_id: str
    role: Role
    content: str
    is_thinking: bool = False
    created_at: datetime = field(default_factory=utcnow)
    token_count: int = 0        # 预估 token 数，由 Core 层写入


@dataclass
class ConversationDetail:
    """完整对话快照，包含节点元数据和消息列表，用于切换对话时加载聊天记录。

    由 ConversationService.switch_conversation() 从 ConversationNode + Message 组装返回。
    """
    id: str
    title: str = "新对话"
    messages: list[Message] = field(default_factory=list)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


# ──────────────────────────────────────────────
# 流式块
# ──────────────────────────────────────────────

@dataclass
class MessageSearchResult:
    """
    Core 层消息搜索结果（领域模型）。
    由 ConversationService.search_messages() 生成，
    供 Controller 映射为 SearchResultVM。
    """
    message_id: str
    conversation_id: str
    role: str
    content: str               # 原始内容（用于 snippet 生成）
    tree_path: str             # 树路径，如 "项目A/对话1/用户消息"
    created_at: datetime


@dataclass
class MessageChunk:
    """LLM 流式输出的单个增量块，由 Adapter 生成，Core 透传，Controller 映射为 VM。"""
    delta: str
    is_done: bool = False
    chunk_type: ChunkType = ChunkType.TEXT
    message_id: str = ""


# ──────────────────────────────────────────────
# 上下文
# ──────────────────────────────────────────────

@dataclass
class ContextBlock:
    """单个上下文块（手动添加 / 来自文件 / 来自模板）。"""
    id: str
    label: str
    content: str
    source: ContextSource = ContextSource.MANUAL
    enabled: bool = True
    order: int = 0              # 拼接顺序

    @property
    def preview(self) -> str:
        """前 80 字预览，供 UI 展示。"""
        return self.content[:80] + ("..." if len(self.content) > 80 else "")


@dataclass
class ContextTemplate:
    """上下文模板，可一键应用到上下文块列表。"""
    id: str
    name: str
    description: str = ""
    blocks: list[ContextBlock] = field(default_factory=list)


# ──────────────────────────────────────────────
# LLM 请求上下文（Core 构建，传给 Adapter）
# ──────────────────────────────────────────────

@dataclass
class LLMContext:
    """
    Core 层组装好的完整 LLM 请求上下文。
    Adapter 不需要知道业务含义，只需按此结构构建 HTTP payload。
    """
    messages: list[dict]          # [{"role": "...", "content": "..."}]
    system_prompt: str = ""
    model_type: str = ""          # 由 Adapter 从 config 读取（此处可为空）
    thinking_enabled: bool = False
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None


# ──────────────────────────────────────────────
# 搜索结果
# ──────────────────────────────────────────────

@dataclass
class SearchResult:
    """单条搜索结果，由 SearchAdapter 返回，由 Core 决定如何注入上下文。"""
    title: str
    url: str
    snippet: str
    source: str = ""              # "arxiv" | "web" 等


@dataclass
class SearchResults:
    """搜索结果集合。"""
    query: str
    results: list[SearchResult] = field(default_factory=list)
    total: int = 0


# ──────────────────────────────────────────────
# 树形结构（Phase 0 — 新建）
# ──────────────────────────────────────────────

class NodeType(str, Enum):
    """树节点类型枚举。"""
    FOLDER       = "folder"
    CONVERSATION = "conversation"
    MESSAGE      = "message"


@dataclass
class TreeNode:
    """树节点基类，包含文件夹、对话和消息节点的共有字段。

    具体节点类型由子类区分：
    - FolderNode → NodeType.FOLDER（目录，可包含子节点）
    - ConversationNode → NodeType.CONVERSATION（对话容器）
    - MessageNode → NodeType.MESSAGE（消息叶子）

    子节点通过 parent_id 字段引用父节点 id 建立层级关系，
    同级节点按 sort_order 升序排列。
    """
    id: str
    parent_id: Optional[str] = None
    sort_order: int = 0
    enabled: Union[bool, Literal["some"]] = True
    title: str = ""
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    # 分叉字段（仅 MessageNode / ConversationNode 有效，FolderNode 恒为默认值）：
    # 分叉点记录其下分支总数 n 与当前分支索引 m（0 起）。
    # 被修改节点无任何标记，通过"分叉点后继节点"定位。
    is_fork_point: bool = False
    fork_branch_count: int = 0
    fork_current_index: int = 0


@dataclass
class FolderNode(TreeNode):
    """目录节点 — 可包含子节点（子文件夹或对话）。

    与 ConversationNode 的区别：
    - 可嵌套（通过 parent_id 指向另一个 FolderNode）
    - 不直接关联 messages 表
    - 可关联上下文块和附件
    """
    context_block_ids: list[str] = field(default_factory=list)
    context_blocks_enabled: bool = True   # 节点上下文块是否启用（禁用后"+"变半透明、不注入）
    attachment_paths: list[str] = field(default_factory=list)
    node_type: NodeType = field(default=NodeType.FOLDER, init=False)


@dataclass
class ConversationNode(TreeNode):
    """对话容器节点 — 通过 id 与 messages 表的 conversation_id 关联。

    id 沿用原 Conversation.id（UUID 格式），确保 messages 表外键不受影响。
    作为目录节点，可包含 MessageNode 子节点（每条 message 表记录对应一个 MessageNode）。
    与 FolderNode 一样支持挂载上下文块和附件。
    """
    summary: str = ""
    message_count: int = 0
    context_block_ids: list[str] = field(default_factory=list)
    context_blocks_enabled: bool = True   # 节点上下文块是否启用
    attachment_paths: list[str] = field(default_factory=list)
    node_type: NodeType = field(default=NodeType.CONVERSATION, init=False)


@dataclass
class MessageNode(TreeNode):
    """消息叶子节点 — 与 messages 表中的一条记录一一对应。

    id 与 message_id 相同，均为 messages 表的主键。
    parent_id 指向所属 ConversationNode 或 FolderNode。
    消息节点始终为叶子节点，不可包含子节点。
    """
    message_id: str = ""          # FK to messages.id
    role: str = ""                # "user" | "assistant" | "thinking" | "system"
    preview: str = ""             # 前 60 字预览，供树节点展示
    incomplete: bool = False      # 未完成（用户停止生成）：构建上下文时跳过，后续可软删除
    thinking_message_id: str | None = None  # assistant 节点: 绑定其思维链 thinking 行 id (thinking 不再是树节点)
    node_type: NodeType = field(default=NodeType.MESSAGE, init=False)


# 联合类型别名 — 用于 tree.json 序列化/反序列化
AnyTreeNode = Union[FolderNode, ConversationNode, MessageNode]


@dataclass
class TreeRoot:
    """树根容器，对应 tree.json 文件的顶层结构。

    采用扁平邻接表（flat adjacency list）组织：nodes 列表包含树中全部节点
    （不限深度），节点之间通过 parent_id 引用建立层级关系。同级节点按
    sort_order 升序排列。
    """
    version: str = "1.0"
    nodes: list[AnyTreeNode] = field(default_factory=list)


@dataclass
class TrashEntry:
    """回收站条目 — 记录被软删除的节点，支持恢复或彻底删除。

    json_path 使用 "root/子目录/对话" 格式记录原节点在树中的路径，
    恢复时可根据该路径重新定位插入位置。
    node_data 保存被删除节点的完整原始数据（dict 形式），
    彻底删除时一并清除。
    """
    id: str
    json_path: str
    node_data: dict
    deleted_at: datetime = field(default_factory=utcnow)
