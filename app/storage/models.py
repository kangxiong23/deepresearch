# Layer: Storage (共享领域模型)
# File: app/storage/models.py
# Responsibility: 全项目通用的 Pydantic / dataclass 领域数据模型。
#                 这是各层之间的"通用语言"，Core 编排、Adapter 填充、Storage 持久化
#                 均使用同一套模型，不在各层重复定义。
# Input:  无（纯模型定义）
# Output: 可被任意层 import 的数据容器

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


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
    created_at: datetime = field(default_factory=datetime.utcnow)
    token_count: int = 0        # 预估 token 数，由 Core 层写入


@dataclass
class Conversation:
    """对话摘要，用于列表展示。"""
    id: str
    title: str = "新对话"
    last_message_preview: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ConversationDetail:
    """完整对话，包含所有消息，用于加载聊天记录。"""
    id: str
    title: str = "新对话"
    messages: list[Message] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


# ──────────────────────────────────────────────
# 流式块
# ──────────────────────────────────────────────

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
