# Layer: Controller
# File: app/controllers/view_models.py
# Responsibility: 所有 View Model 数据容器定义，供 UI 层和 Controller 层共同使用
# Input:  无（纯数据容器，无业务逻辑）
# Output: dataclass 实例，由 Controller 构造后传给 UI
# 注意: VM 只含展示所需字段，不暴露任何领域内部结构

from __future__ import annotations
from dataclasses import dataclass, field


# ──────────────────────────────────────────────
# 对话列表 VM（侧边栏用）
# ──────────────────────────────────────────────
@dataclass
class ConversationVM:
    """单条对话摘要，用于侧边栏列表渲染。"""
    id: str
    title: str
    preview: str          # 最后一条消息的前 N 字
    updated_at: str       # 格式化后的时间字符串，如 "05-13 14:32"


# ──────────────────────────────────────────────
# 单条消息 VM
# ──────────────────────────────────────────────
@dataclass
class MessageVM:
    """单条消息，用于消息列表渲染。"""
    id: str
    role: str             # "user" | "assistant" | "thinking"
    content: str
    is_thinking: bool = False
    created_at: str = ""  # 格式化时间字符串


# ──────────────────────────────────────────────
# 对话详情 VM（切换对话时返回）
# ──────────────────────────────────────────────
@dataclass
class ConversationDetailVM:
    """完整对话详情，包含消息列表，用于切换对话后重建聊天区。"""
    id: str
    title: str
    messages: list[MessageVM] = field(default_factory=list)


# ──────────────────────────────────────────────
# 流式块 VM
# ──────────────────────────────────────────────
@dataclass
class StreamChunkVM:
    """
    流式生成的单个增量块。

    UI 消费方式：
        async for chunk in controller.on_send_message(...):
            if chunk.is_done:
                break
            if chunk.chunk_type == "thinking":
                thinking_block.append_text(chunk.delta)
            else:
                assistant_msg.append_stream(chunk.delta)
            page.update()
    """
    delta: str
    is_done: bool = False
    chunk_type: str = "text"      # "text" | "thinking"
    message_id: str = ""


# ──────────────────────────────────────────────
# 上下文块 VM（上下文面板用）
# ──────────────────────────────────────────────
@dataclass
class ContextBlockVM:
    """单个上下文块，用于上下文管理面板渲染。"""
    id: str
    label: str
    preview: str          # 前 N 字预览
    enabled: bool = True
    source: str = ""      # "manual" | "file" | "template"


# ──────────────────────────────────────────────
# 模板 VM
# ──────────────────────────────────────────────
@dataclass
class TemplateVM:
    """上下文模板条目。"""
    id: str
    name: str
    description: str = ""
