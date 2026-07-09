# Layer: Core
# File: app/core/context_service.py
# Responsibility: 上下文编排服务。
#                 决定"如何构建发送给 LLM 的完整上下文"——
#                 包括历史消息截断策略、系统提示拼接、上下文块插入。
#                 不直接读写数据库，通过 ConversationRepo / ContextStore 协议操作。
# Input:  conversation_id, 上下文块列表, 新消息
# Output: LLMContext（组装好的完整请求上下文，交给 ConversationService 传给 LLM）
# 禁止: HTTP 调用、数据库 SQL、UI 导入

from __future__ import annotations

import config as app_config
from app.core.protocols import ConversationRepoProtocol, ContextStoreProtocol
from app.storage.models import (
    ContextBlock,
    ContextTemplate,
    ContextSource,
    LLMContext,
    Message,
    Role,
)


# ──────────────────────────────────────────────
# 系统提示模板（静态默认值，可被上下文块覆盖）
# ──────────────────────────────────────────────
_DEFAULT_SYSTEM_PROMPT = (
    "You are DeepResearch, an advanced AI research assistant. "
    "You provide thorough, well-reasoned answers with citations where appropriate. "
    "When you don't know something, say so clearly."
)


class ContextService:
    """
    上下文构建服务。

    编排职责：
    1. 从 ContextStore 读取用户配置的上下文块（系统提示、背景材料等）
    2. 从 ConversationRepo 读取历史消息，按 token 预算截断
    3. 将以上内容组装为 LLMContext，供 ConversationService 传给 LLM 客户端

    不直接操作数据库或外部 API。
    """

    def __init__(
        self,
        conversation_repo: ConversationRepoProtocol,
        context_store: ContextStoreProtocol,
        knowledge_service=None,
    ) -> None:
        self._repo = conversation_repo
        self._store = context_store
        self._knowledge_svc = knowledge_service  # 可选，延迟注入

    def set_knowledge_service(self, knowledge_service) -> None:
        """延迟注入 KnowledgeService，避免循环依赖。"""
        self._knowledge_svc = knowledge_service

    # ──────────────────────────────────────────
    # 主接口：构建 LLM 请求上下文
    # ──────────────────────────────────────────

    def build_llm_context(
        self,
        conversation_id: str,
        new_user_message: str,
        injected_search_text: str = "",
    ) -> LLMContext:
        """
        为一次 LLM 调用组装完整上下文。

        流程：
        1. 读取并合并用户上下文块（system prompt 块优先，其余按 order 拼接）
        2. 读取历史消息并按 token 预算截断
        3. 若有搜索结果注入，拼接到用户消息之前
        4. 追加本次新用户消息
        5. 从 config 读取模型配置并填入 LLMContext

        Args:
            conversation_id:      当前对话 ID
            new_user_message:     用户本次输入的文本
            injected_search_text: 搜索结果文本（由 SearchService 提供，可为空）

        Returns:
            LLMContext — 可直接传给 LLMClientProtocol.stream_chat()
        """
        # 1. 构建系统提示（含知识图谱注入）
        system_prompt = self._build_system_prompt()
        if self._knowledge_svc and app_config.kg_injection_enabled:
            kg_results = self._knowledge_svc.query_for_context(new_user_message)
            kg_text = self._knowledge_svc.format_knowledge_for_injection(kg_results)
            if kg_text:
                print(f"[KG] 注入知识 {len(kg_results)} 条到 system prompt")
                system_prompt = kg_text + "\n\n" + system_prompt
            else:
                print(f"[KG] 无匹配知识可注入（图谱关系数: "
                      f"{self._knowledge_svc.get_stats().get('relation_count',0)}）")

        # 2. 获取历史消息并截断
        history = self._repo.get_messages(conversation_id)
        truncated = self._truncate_history(
            history,
            budget_tokens=app_config.max_history_tokens,
        )

        # 3. 组装 messages 列表（OpenAI 格式）
        messages: list[dict] = []
        for msg in truncated:
            # 思考块不发给 LLM（仅展示用）
            if msg.role == Role.THINKING:
                continue
            messages.append({
                "role": msg.role.value if hasattr(msg.role, "value") else msg.role,
                "content": msg.content,
            })

        # 4. 构建本次用户消息（可能含搜索注入）
        user_content = self._compose_user_message(
            new_user_message, injected_search_text
        )
        messages.append({"role": Role.USER.value, "content": user_content})

        return LLMContext(
            messages=messages,
            system_prompt=system_prompt,
            model_type=app_config.model_type,
            thinking_enabled=app_config.thinking_enabled,
            max_tokens=app_config.max_tokens,
            temperature=app_config.temperature,
        )

    # ──────────────────────────────────────────
    # 上下文块管理接口
    # ──────────────────────────────────────────

    def add_text_block(self, content: str, label: str = "") -> ContextBlock:
        """
        手动添加一个文本上下文块。

        Args:
            content: 文本内容
            label:   可选显示标签

        Returns:
            新建的 ContextBlock（已持久化）
        """
        import uuid
        block = ContextBlock(
            id=str(uuid.uuid4()),
            label=label or content[:20],
            content=content,
            source=ContextSource.MANUAL,
            enabled=True,
            order=self._next_order(),
        )
        self._store.save_block(block)
        return block

    def add_file_block(self, content: str, filename: str) -> ContextBlock:
        """
        将解析后的文件内容作为上下文块添加。
        由 FileService 提取内容后调用此方法。

        Args:
            content:  文件提取出的文本
            filename: 原始文件名（用作 label）

        Returns:
            新建的 ContextBlock（已持久化）
        """
        import uuid
        block = ContextBlock(
            id=str(uuid.uuid4()),
            label=filename,
            content=content,
            source=ContextSource.FILE,
            enabled=True,
            order=self._next_order(),
        )
        self._store.save_block(block)
        return block

    def remove_block(self, block_id: str) -> None:
        """移除上下文块。"""
        self._store.delete_block(block_id)

    def toggle_block(self, block_id: str, enabled: bool) -> None:
        """启用/禁用某个上下文块。"""
        self._store.update_block_enabled(block_id, enabled)

    def get_blocks(self) -> list[ContextBlock]:
        """获取所有上下文块（按 order 排序）。"""
        return self._store.get_blocks()

    def apply_template(self, template_id: str) -> list[ContextBlock]:
        """
        应用模板：将模板中的块全部追加到当前上下文块列表。

        Args:
            template_id: 目标模板 ID

        Returns:
            新追加的上下文块列表
        """
        template = self._store.get_template(template_id)
        if template is None:
            return []
        # 先清除所有已有的模板来源块（避免叠加）
        existing = self._store.get_blocks()
        for b in existing:
            if b.source == ContextSource.TEMPLATE:
                self._store.delete_block(b.id)
        import uuid
        base_order = self._next_order()
        new_blocks: list[ContextBlock] = []
        for i, tmpl_block in enumerate(template.blocks):
            block = ContextBlock(
                id=str(uuid.uuid4()),
                label=tmpl_block.label,
                content=tmpl_block.content,
                source=ContextSource.TEMPLATE,
                enabled=True,
                order=base_order + i,
            )
            self._store.save_block(block)
            new_blocks.append(block)
        return new_blocks

    def get_templates(self) -> list[ContextTemplate]:
        """获取所有可用模板。"""
        return self._store.list_templates()

    def get_assembled_preview(self) -> str:
        """
        返回当前已启用上下文块拼接后的预览文本。
        供 UI 上下文面板展示用，不影响实际 LLM 调用。
        """
        blocks = [b for b in self._store.get_blocks() if b.enabled]
        return self._assemble_context_blocks(blocks)

    # ──────────────────────────────────────────
    # 内部编排方法
    # ──────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        """
        将用户添加的所有启用上下文块直接拼入 system prompt。
        用户块的内容会覆盖/补充默认指令，AI 会优先遵守。
        """
        blocks = self._store.get_blocks()
        enabled = [b for b in blocks if b.enabled]

        parts: list[str] = []

        # 用户自定义块优先（放在最前面，AI 会优先遵守）
        for block in enabled:
            if block.content.strip():
                parts.append(block.content.strip())

        # 默认提示兜底（放在末尾）
        parts.append(_DEFAULT_SYSTEM_PROMPT)

        return "\n\n".join(p for p in parts if p.strip())

    def _assemble_context_blocks(self, blocks: list[ContextBlock]) -> str:
        """将上下文块按 order 拼接为单一文本（用于系统提示外注入或预览）。"""
        sorted_blocks = sorted(blocks, key=lambda b: b.order)
        return "\n\n".join(
            f"[{b.label}]\n{b.content}" for b in sorted_blocks if b.content.strip()
        )

    def _truncate_history(
        self,
        messages: list[Message],
        budget_tokens: int,
    ) -> list[Message]:
        """
        从最新消息向前截取，保证总 token 数不超过 budget_tokens。
        token 数直接使用 Message.token_count（由存储时预估写入）。
        若 token_count 为 0，降级用字符数 / 4 估算。
        始终保留最近一条消息，避免空列表。
        """
        if not messages:
            return []

        # 从末尾向前累计
        selected: list[Message] = []
        total = 0
        for msg in reversed(messages):
            estimated = msg.token_count if msg.token_count > 0 else len(msg.content) // 4
            if total + estimated > budget_tokens and selected:
                break
            selected.append(msg)
            total += estimated

        selected.reverse()
        return selected

    def _compose_user_message(
        self,
        user_text: str,
        search_text: str,
    ) -> str:
        """
        将用户原始输入与搜索结果拼接为完整用户消息内容。
        搜索结果以结构化前缀注入。
        """
        if not search_text:
            return user_text
        return (
            f"[搜索参考资料]\n{search_text}\n\n"
            f"[用户问题]\n{user_text}"
        )

    def _next_order(self) -> int:
        """计算下一个上下文块的排序值。"""
        blocks = self._store.get_blocks()
        if not blocks:
            return 0
        return max(b.order for b in blocks) + 1