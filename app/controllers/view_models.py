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
    # 分叉信息（仅被修改节点携带，其余为默认值）：
    conversation_id: str = ""   # 所属对话 ID
    fork_m: int = 0             # 当前分支展示编号（1 起；0 = 非被修改节点）
    fork_n: int = 0             # 分叉点下分支总数
    fork_point_id: str = ""     # 分叉点 ID（<m/n> 控件切换目标）


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


# ──────────────────────────────────────────────
# 树形结构 VM（Phase 4 — 树面板用）
# ──────────────────────────────────────────────
@dataclass
class TreeNodeVM:
    """树形节点视图模型，统一表示目录、对话和消息节点。"""
    id: str
    title: str
    node_type: str                           # "folder" | "conversation" | "message"
    parent_id: str | None = None
    enabled: bool | str = True               # True, False, or "some"
    sort_order: int = 0
    preview: str = ""                        # 对话摘要 / 消息预览 / 目录留空
    updated_at: str = ""                     # 格式化时间字符串
    has_children: bool = False               # 是否有子节点（预计算）
    depth: int = 0                           # 缩进级别（预计算）
    message_count: int = 0                   # 仅对话
    context_block_count: int = 0             # 仅目录/对话
    context_blocks_enabled: bool = True      # 节点上下文块是否启用（仅目录/对话）
    attachment_count: int = 0                # 仅目录
    role: str = ""                           # 仅消息节点: "user" | "assistant" | "thinking"
    fork_display: str = ""                   # 分叉点标题前缀 "<m/n> "（仅信息展示，3.5.2）
    is_modified: bool = False                # 仅消息节点: 被修改节点（分叉点后继，拖拽判定用 3.6）


@dataclass
class TrashEntryVM:
    """回收站条目视图模型。"""
    id: str                                  # TrashEntry ID
    node_id: str                             # 原节点 ID
    title: str
    node_type: str                           # "folder" | "conversation"
    json_path: str
    deleted_at: str                          # 格式化时间字符串


@dataclass
class SearchResultVM:
    """消息搜索结果视图模型。"""
    message_id: str
    conversation_id: str       # 所属对话 ID
    role: str                  # "user" | "assistant" | "thinking" | "system"
    snippet: str               # 关键词上下文片段（~30 chars）
    tree_path: str             # 树路径，如 "项目A/对话1/用户消息"
    created_at: str            # 格式化时间字符串
