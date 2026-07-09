# Layer: Core
# File: app/core/conversation_service.py
# Responsibility: 对话业务编排主服务。
#                 串联 ContextService、SearchService、FileService、LLMClient、
#                 ConversationRepo，完整编排"发送消息"、"重新生成"、"切换对话"等流程。
#                 是 Controller 层的直接调用目标。
# Input:  来自 AppController 的业务参数（session_id, text, files 等）
# Output: 领域对象（Conversation, ConversationDetail, MessageChunk 流）
# 禁止: 直接实现 HTTP/SQL/文件解析；导入 UI 库。

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import AsyncGenerator

import config as app_config
from app.core.context_service import ContextService
from app.core.file_service import FileService
from app.core.protocols import (
    ConversationRepoProtocol,
    LLMClientProtocol,
)
from app.core.search_service import SearchService
from app.storage.models import (
    ChunkType,
    Conversation,
    ConversationDetail,
    Message,
    MessageChunk,
    Role,
)


class ConversationService:
    """
    对话编排主服务。

    生命周期与状态：
    - 自身不持有"当前对话"状态，session_id 由 Controller 在每次调用时传入。
    - _stop_event 是唯一的实例状态，用于跨异步任务的中止信号。
    - 所有持久化状态存储在 ConversationRepo。

    编排流程（send_message）：
        1. 文件提取   → FileService.extract_files()
        2. 注入上下文 → ContextService.add_file_block()
        3. 搜索       → SearchService.search_and_format()（config.search_enabled 控制）
        4. 构建上下文 → ContextService.build_llm_context()
        5. 保存用户消息 → ConversationRepo.save_message()
        6. 调用 LLM  → LLMClient.stream_chat()
        7. 流式转发   → yield MessageChunk（Core 层不缓冲，直接透传）
        8. 持久化回复 → ConversationRepo.save_message()
        9. 更新对话摘要 → ConversationRepo.update_conversation_meta()
    """

    def __init__(
        self,
        conversation_repo: ConversationRepoProtocol,
        llm_client: LLMClientProtocol,
        context_service: ContextService,
        search_service: SearchService,
        file_service: FileService,
    ) -> None:
        self._repo = conversation_repo
        self._llm = llm_client
        self._context_svc = context_service
        self._search_svc = search_service
        self._file_svc = file_service
        self._stop_event = asyncio.Event()

    # ──────────────────────────────────────────
    # 对话生命周期
    # ──────────────────────────────────────────

    def create_conversation(self) -> Conversation:
        """
        新建一个空对话。

        Returns:
            新建的 Conversation 领域对象（已持久化）
        """
        conversation = Conversation(
            id=str(uuid.uuid4()),
            title="新对话",
            last_message_preview="",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self._repo.save_conversation(conversation)
        return conversation

    def switch_conversation(self, session_id: str) -> ConversationDetail:
        """
        切换到指定对话，返回完整消息历史。

        Args:
            session_id: 目标对话 ID

        Returns:
            ConversationDetail（含 messages 列表）

        Raises:
            ValueError: 对话不存在
        """
        detail = self._repo.get_conversation_detail(session_id)
        if detail is None:
            raise ConversationNotFoundError(session_id)
        return detail

    def delete_conversation(self, session_id: str) -> None:
        """
        删除对话及其所有消息。

        Args:
            session_id: 要删除的对话 ID
        """
        self._repo.delete_conversation(session_id)

    def list_conversations(self) -> list[Conversation]:
        """
        返回所有对话摘要，按 updated_at 倒序。

        Returns:
            list[Conversation]
        """
        return self._repo.list_conversations()

    # ──────────────────────────────────────────
    # 消息生成
    # ──────────────────────────────────────────

    async def send_message(
        self,
        session_id: str,
        text: str,
        files: list[str],
    ) -> AsyncGenerator[MessageChunk, None]:
        """
        发送用户消息并流式返回 LLM 回复。

        编排步骤见类文档。

        Args:
            session_id: 当前对话 ID
            text:       用户输入文本
            files:      附件文件路径列表（可为空）

        Yields:
            MessageChunk — 流式块，is_done=True 为结束信号
        """
        self._stop_event.clear()

        # ── Step 1: 文件提取，内容直接拼入本次消息 ─
        file_text = ""
        if files:
            extracted = await self._file_svc.extract_files(files)
            parts = []
            for filename, content in extracted:
                if content.strip():
                    parts.append(f"[文件：{filename}]\n{content.strip()}")
            if parts:
                file_text = "\n\n".join(parts)

        # ── Step 2: 把文件内容拼到用户文本前面 ──
        full_user_text = text
        if file_text:
            full_user_text = f"{file_text}\n\n[用户问题]\n{text}" if text else file_text

        # ── Step 3: 搜索（由 config.search_enabled 控制）──
        search_text = await self._search_svc.search_and_format(text)

        # ── Step 4: 构建 LLM 上下文 ─────────────
        llm_context = self._context_svc.build_llm_context(
            conversation_id=session_id,
            new_user_message=full_user_text,
            injected_search_text=search_text,
        )

        # ── Step 5: 持久化用户消息 ───────────────
        user_msg = Message(
            id=str(uuid.uuid4()),
            conversation_id=session_id,
            role=Role.USER,
            content=text,
            created_at=datetime.utcnow(),
            token_count=len(text) // 4,   # 粗估，Adapter 可覆写
        )
        self._repo.save_message(user_msg)

        # ── Step 6 & 7: 调用 LLM，流式转发 ──────
        assistant_msg_id = str(uuid.uuid4())
        full_content_parts: list[str] = []   # 累积完整回复，用于持久化
        thinking_parts: list[str] = []

        async for chunk in self._llm.stream_chat(llm_context):
            # 检查中止信号
            if self._stop_event.is_set():
                yield MessageChunk(delta="", is_done=True, message_id=assistant_msg_id)
                break

            # 打上 message_id 后透传
            chunk.message_id = assistant_msg_id

            if chunk.chunk_type == ChunkType.THINKING:
                thinking_parts.append(chunk.delta)
            else:
                full_content_parts.append(chunk.delta)

            if chunk.is_done:
                # ── Step 8: 先持久化，再 yield done ─
                full_content = "".join(full_content_parts)
                if full_content or thinking_parts:
                    assistant_msg = Message(
                        id=assistant_msg_id,
                        conversation_id=session_id,
                        role=Role.ASSISTANT,
                        content=full_content,
                        is_thinking=bool(thinking_parts),
                        created_at=datetime.utcnow(),
                        token_count=len(full_content) // 4,
                    )
                    self._repo.save_message(assistant_msg)

                    if thinking_parts:
                        thinking_msg = Message(
                            id=str(uuid.uuid4()),
                            conversation_id=session_id,
                            role=Role.THINKING,
                            content="".join(thinking_parts),
                            is_thinking=True,
                            created_at=datetime.utcnow(),
                            token_count=len("".join(thinking_parts)) // 4,
                        )
                        self._repo.save_message(thinking_msg)

                # ── Step 9: 更新对话元数据 ───────────
                self._update_conversation_meta(
                    session_id, text, "".join(full_content_parts)
                )
                yield chunk
                break

            yield chunk

    async def regenerate_message(
        self,
        session_id: str,
        message_id: str,
    ) -> AsyncGenerator[MessageChunk, None]:
        """
        重新生成指定消息之后的最后一条助手回复。

        流程：
        1. 删除最后一条 assistant 消息（message_id 为空时自动定位）
        2. 重新构建 LLM 上下文（历史中不含被删消息）
        3. 流式生成新回复并持久化

        Args:
            session_id: 当前对话 ID
            message_id: 要替换的消息 ID（可为空，自动取最后一条 assistant 消息）

        Yields:
            MessageChunk — 同 send_message
        """
        self._stop_event.clear()

        # 定位并删除最后一条 assistant 消息
        target_id = message_id or self._find_last_assistant_message(session_id)
        if target_id:
            self._repo.delete_message(target_id)

        # 取倒数第一条用户消息作为重新生成的输入
        messages = self._repo.get_messages(session_id)
        last_user = next(
            (m for m in reversed(messages) if m.role == Role.USER), None
        )
        user_text = last_user.content if last_user else ""

        # 复用 send_message 的编排流程（不含文件提取，不含搜索）
        llm_context = self._context_svc.build_llm_context(
            conversation_id=session_id,
            new_user_message=user_text,
            injected_search_text="",
        )

        new_msg_id = str(uuid.uuid4())
        full_parts: list[str] = []

        async for chunk in self._llm.stream_chat(llm_context):
            if self._stop_event.is_set():
                yield MessageChunk(delta="", is_done=True, message_id=new_msg_id)
                break
            chunk.message_id = new_msg_id
            full_parts.append(chunk.delta)
            yield chunk
            if chunk.is_done:
                break

        full_content = "".join(full_parts)
        if full_content:
            self._repo.save_message(Message(
                id=new_msg_id,
                conversation_id=session_id,
                role=Role.ASSISTANT,
                content=full_content,
                created_at=datetime.utcnow(),
                token_count=len(full_content) // 4,
            ))

        self._update_conversation_meta(session_id, user_text, full_content)

    def stop_generation(self) -> None:
        """
        设置中止信号，流式生成循环在下一次 yield 前检测并退出。
        线程安全：asyncio.Event 可在同一事件循环的任意协程中设置。
        同时调用 LLM 客户端的 abort() 关闭底层 HTTP 连接。
        """
        self._stop_event.set()
        self._llm.abort()

    # ──────────────────────────────────────────
    # 内部编排辅助
    # ──────────────────────────────────────────

    def _find_last_assistant_message(self, session_id: str) -> str | None:
        """返回最后一条 assistant 消息的 ID，找不到返回 None。"""
        messages = self._repo.get_messages(session_id)
        for msg in reversed(messages):
            if msg.role == Role.ASSISTANT:
                return msg.id
        return None

    def _update_conversation_meta(
        self,
        session_id: str,
        user_text: str,
        assistant_text: str,
    ) -> None:
        """
        更新对话标题（首次对话时用用户输入前 20 字）和摘要预览。
        仅在有内容时更新，避免空消息覆盖。
        """
        conversation = self._repo.get_conversation(session_id)
        if conversation is None:
            return

        # 首次对话：自动生成标题
        new_title: str | None = None
        if conversation.title == "新对话" and user_text:
            new_title = user_text[:20] + ("..." if len(user_text) > 20 else "")

        # 更新摘要为助手最新回复的前 40 字
        preview = (assistant_text or user_text)[:40]
        preview = preview + ("..." if len(preview) == 40 else "")

        self._repo.update_conversation_meta(
            conversation_id=session_id,
            title=new_title,
            last_message_preview=preview,
        )


# ──────────────────────────────────────────────
# 领域异常
# ──────────────────────────────────────────────

class ConversationNotFoundError(Exception):
    """对话不存在时抛出，由 Controller 层捕获并处理。"""
    def __init__(self, session_id: str) -> None:
        super().__init__(f"Conversation not found: {session_id}")
        self.session_id = session_id