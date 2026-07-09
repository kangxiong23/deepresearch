# Layer: Controller
# File: app/controllers/app_controller.py
# Responsibility: 主控制器，接收 UI 事件，调用 Core 服务，将领域结果映射为 View Model。
#                 不含任何业务逻辑、不直接访问 adapter/storage/config 业务值。
# Input:  UI 原始参数（str, bool, list[str]）
# Output: View Model 实例或 AsyncGenerator[StreamChunkVM]
# 禁止: 业务判断、配置解析、直接调用 adapter/storage、维护全局状态对象。

from __future__ import annotations
from typing import AsyncGenerator

from app.controllers.command_builder import CommandBuilder
from app.controllers.view_models import (
    ConversationVM,
    ConversationDetailVM,
    MessageVM,
    StreamChunkVM,
)


class AppController:
    """
    主应用控制器。

    通过依赖注入持有 Core 层服务，不自行实例化任何服务。
    每个公开方法对应一个 UI 事件，仅做：
        1. 用 CommandBuilder 打包参数（可选）
        2. 调用 Core 服务对应方法
        3. 将返回的领域对象映射为 View Model

    用法（在 main.py 中）：
        conversation_service = ConversationService(...)
        controller = AppController(conversation_service=conversation_service)
        chat_app = ChatApp(app_controller=controller, ...)
    """

    def __init__(self, conversation_service) -> None:
        """
        Args:
            conversation_service: core.conversation_service.ConversationService 实例
        """
        self._conversation_svc = conversation_service
        # context_service 通过 conversation_service 访问
        self._context_svc = conversation_service._context_svc
        # knowledge_service 后续通过 set_knowledge_service 注入
        self._knowledge_svc = None

    def set_knowledge_service(self, knowledge_service) -> None:
        """
        注入 KnowledgeService。在 main.py 装配完所有服务后调用。
        采用延迟注入而非构造参数，避免循环依赖。
        """
        self._knowledge_svc = knowledge_service

    # ──────────────────────────────────────────
    # 对话生命周期
    # ──────────────────────────────────────────

    def on_new_conversation(self) -> str:
        """
        UI 点击"新建对话"时调用。

        Returns:
            新对话的 session_id（str），UI 用于标记当前激活会话。
        """
        conversation = self._conversation_svc.create_conversation()
        return conversation.id

    def on_switch_conversation(self, session_id: str) -> ConversationDetailVM:
        """
        UI 点击侧边栏某条对话时调用。

        Args:
            session_id: 目标对话 ID

        Returns:
            ConversationDetailVM，包含完整消息列表，UI 据此重建聊天区。
        """
        cmd = CommandBuilder.build_switch_command(session_id)
        detail = self._conversation_svc.switch_conversation(**cmd)
        return self._map_to_detail_vm(detail)

    def on_delete_conversation(self, session_id: str) -> None:
        """
        UI 点击删除对话时调用。

        Args:
            session_id: 要删除的对话 ID
        """
        cmd = CommandBuilder.build_delete_command(session_id)
        self._conversation_svc.delete_conversation(**cmd)

    def on_load_history(self) -> list[ConversationVM]:
        """
        UI 初始化或刷新侧边栏列表时调用。

        Returns:
            list[ConversationVM]，按更新时间倒序。
        """
        conversations = self._conversation_svc.list_conversations()
        return [self._map_to_conversation_vm(c) for c in conversations]

    # ──────────────────────────────────────────
    # 消息生成
    # ──────────────────────────────────────────

    async def on_send_message(
        self,
        session_id: str,
        text: str,
        files: list[str],
    ) -> AsyncGenerator[StreamChunkVM, None]:
        """
        UI 发送消息时调用，返回流式 AsyncGenerator。

        Args:
            session_id: 当前对话 ID
            text:       用户输入的文本内容
            files:      附件文件路径列表（可为空）

        Yields:
            StreamChunkVM — 逐块返回，is_done=True 表示结束
        """
        cmd = CommandBuilder.build_send_command(session_id, text, files)
        async for chunk in self._conversation_svc.send_message(**cmd):
            yield self._map_to_stream_chunk_vm(chunk)

    async def on_regenerate_message(
        self,
        session_id: str,
        message_id: str,
    ) -> AsyncGenerator[StreamChunkVM, None]:
        """
        UI 点击"重新生成"时调用。

        Args:
            session_id: 当前对话 ID
            message_id: 要重新生成的消息 ID

        Yields:
            StreamChunkVM — 流式块，同 on_send_message
        """
        cmd = CommandBuilder.build_regenerate_command(session_id, message_id)
        async for chunk in self._conversation_svc.regenerate_message(**cmd):
            yield self._map_to_stream_chunk_vm(chunk)

    def on_stop_generation(self) -> None:
        """
        UI 点击"停止生成"时调用。
        直接透传给 Core 服务，不含任何逻辑。
        """
        self._conversation_svc.stop_generation()

    # ──────────────────────────────────────────
    # View Model 映射（纯数据转换，无业务判断）
    # ──────────────────────────────────────────

    @staticmethod
    def _map_to_conversation_vm(domain_obj) -> ConversationVM:
        """
        领域对象 Conversation -> ConversationVM。

        预期 domain_obj 字段:
            id: str
            title: str
            last_message_preview: str
            updated_at: datetime
        """
        return ConversationVM(
            id=domain_obj.id,
            title=domain_obj.title or "新对话",
            preview=domain_obj.last_message_preview or "",
            updated_at=_format_datetime(domain_obj.updated_at),
        )

    @staticmethod
    def _map_to_detail_vm(domain_obj) -> ConversationDetailVM:
        """
        领域对象 ConversationDetail -> ConversationDetailVM。

        预期 domain_obj 字段:
            id: str
            title: str
            messages: list[Message]
        """
        return ConversationDetailVM(
            id=domain_obj.id,
            title=domain_obj.title or "新对话",
            messages=[
                AppController._map_to_message_vm(m)
                for m in (domain_obj.messages or [])
            ],
        )

    @staticmethod
    def _map_to_message_vm(domain_obj) -> MessageVM:
        """
        领域对象 Message -> MessageVM。

        预期 domain_obj 字段:
            id: str
            role: str
            content: str
            is_thinking: bool
            created_at: datetime
        """
        return MessageVM(
            id=domain_obj.id,
            role=domain_obj.role,
            content=domain_obj.content,
            is_thinking=getattr(domain_obj, "is_thinking", False),
            created_at=_format_datetime(getattr(domain_obj, "created_at", None)),
        )

    @staticmethod
    def _map_to_stream_chunk_vm(domain_obj) -> StreamChunkVM:
        """
        领域对象 MessageChunk -> StreamChunkVM。

        预期 domain_obj 字段:
            delta: str
            is_done: bool
            chunk_type: str   ("text" | "thinking")
            message_id: str
        """
        return StreamChunkVM(
            delta=getattr(domain_obj, "delta", ""),
            is_done=getattr(domain_obj, "is_done", False),
            chunk_type=getattr(domain_obj, "chunk_type", "text"),
            message_id=getattr(domain_obj, "message_id", ""),
        )


    # ── 上下文块管理 ──────────────────────────────

    def on_add_text_block(self, text: str) -> dict:
        """
        添加自定义文本块到上下文。
        Returns: {"id": str, "label": str, "preview": str}
        """
        block = self._context_svc.add_text_block(
            content=text,
            label=text[:20].replace("\n", " "),
        )
        return {
            "id": block.id,
            "label": block.label,
            "preview": block.content[:60],
            "enabled": block.enabled,
        }

    def on_remove_context_block(self, block_id: str) -> None:
        """删除上下文块。"""
        self._context_svc._store.delete_block(block_id)

    def on_toggle_context_block(self, block_id: str, enabled: bool) -> None:
        """启用/禁用上下文块。"""
        self._context_svc._store.update_block_enabled(block_id, enabled)

    def on_get_context_blocks(self) -> list[dict]:
        """获取所有上下文块列表。"""
        blocks = self._context_svc.get_blocks()
        return [
            {
                "id": b.id,
                "label": b.label,
                "preview": b.content[:60],
                "enabled": b.enabled,
            }
            for b in blocks
        ]

    def on_get_context_preview(self) -> str:
        """获取当前上下文拼接预览文本。"""
        return self._context_svc._build_system_prompt()


    def on_get_templates(self) -> list[dict]:
        """获取所有模板列表。"""
        templates = self._context_svc.get_templates()
        return [
            {"id": t.id, "name": t.name, "description": t.description}
            for t in templates
        ]

    def on_apply_template(self, template_id: str) -> None:
        """应用模板：把模板里的块全部加入当前上下文。"""
        self._context_svc.apply_template(template_id)

    def on_delete_template(self, template_id: str) -> None:
        """删除模板。"""
        from app.storage.context_store import ContextStore
        # 直接操作 store 删除模板
        templates = self._context_svc._store.list_templates()
        remaining = [t for t in templates if t.id != template_id]
        # 重新写入所有模板（暂无 delete_template 方法，用覆盖方式）
        import json
        from pathlib import Path
        import config as app_config
        store_path = Path(app_config.CONTEXT_STORE_PATH) / 'templates.json'
        def _tmpl_to_dict(t):
            return {
                'id': t.id, 'name': t.name, 'description': t.description,
                'blocks': [{
                    'id': b.id, 'label': b.label, 'content': b.content,
                    'source': b.source.value if hasattr(b.source,'value') else str(b.source),
                    'enabled': b.enabled, 'order': b.order,
                } for b in t.blocks]
            }
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with store_path.open('w', encoding='utf-8') as f:
            json.dump([_tmpl_to_dict(t) for t in remaining], f, ensure_ascii=False, indent=2)

    def on_save_current_as_template(self, name: str, description: str = "") -> dict:
        """把当前所有上下文块保存为新模板。"""
        import uuid
        from app.storage.models import ContextTemplate
        blocks = self._context_svc.get_blocks()
        template = ContextTemplate(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            blocks=blocks,
        )
        self._context_svc._store.save_template(template)
        return {"id": template.id, "name": name, "description": description}


    # ──────────────────────────────────────────
    # 知识图谱（KGLite）
    # ──────────────────────────────────────────

    async def on_remember_conversation(self, session_id: str) -> str:
        """
        UI 点击"记住"按钮时调用。
        取当前对话最近 N 条消息，调用 KnowledgeService 提取实体关系写入图谱。

        Args:
            session_id: 当前对话 ID

        Returns:
            提取结果摘要字符串，显示在 UI 上
        """
        if self._knowledge_svc is None:
            return "知识图谱服务未初始化"

        # 从对话历史获取最近消息
        detail = self._conversation_svc.switch_conversation(session_id)
        if detail is None or not detail.messages:
            return "当前对话没有消息可提取"

        # 转为 OpenAI 格式
        messages = [
            {
                "role": msg.role.value if hasattr(msg.role, "value") else str(msg.role),
                "content": msg.content,
            }
            for msg in detail.messages
        ]

        result = await self._knowledge_svc.extract_and_save(
            messages=messages,
            conversation_id=session_id,
        )
        return result

    def on_query_knowledge(self, user_message: str) -> str:
        """
        根据用户消息查询知识图谱，返回格式化的注入文本。
        由 context_service 在 build_llm_context 时调用，不直接暴露给 UI。

        Args:
            user_message: 用户输入的消息文本

        Returns:
            格式化的知识背景文本，空串表示无相关知识
        """
        if self._knowledge_svc is None:
            return ""
        results = self._knowledge_svc.query_for_context(user_message)
        return self._knowledge_svc.format_knowledge_for_injection(results)


    def on_get_all_entities(self) -> list[dict]:
        """获取所有实体，供 KGPanel 展示。"""
        if self._knowledge_svc is None:
            return []
        entities = self._knowledge_svc._store.get_entities()
        return [
            {
                "id": e.id,
                "name": e.name,
                "entity_type": e.entity_type,
                "description": e.description,
                "source_conversation_id": e.source_conversation_id,
            }
            for e in entities
        ]

    def on_get_all_relations(self) -> list[dict]:
        """获取所有关系，供 KGPanel 展示。"""
        if self._knowledge_svc is None:
            return []
        relations = self._knowledge_svc._store.get_relations()
        return [
            {
                "id": r.id,
                "source_entity_name": r.source_entity_name,
                "relation_type": r.relation_type,
                "target_entity_name": r.target_entity_name,
                "description": r.description,
            }
            for r in relations
        ]

    def on_delete_kg_entity(self, entity_name: str) -> None:
        """删除实体（同时删除相关关系）。"""
        if self._knowledge_svc is None:
            return
        self._knowledge_svc._store.delete_entity(entity_name)

    def on_delete_kg_relation(self, relation_id: str) -> None:
        """删除单条关系。"""
        if self._knowledge_svc is None:
            return
        self._knowledge_svc._store.delete_relation(relation_id)

    def on_get_kg_stats(self) -> dict:
        """返回知识图谱统计信息，供 UI 展示。"""
        if self._knowledge_svc is None:
            return {"entity_count": 0, "relation_count": 0}
        return self._knowledge_svc.get_stats()

# ──────────────────────────────────────────────
# 模块级辅助（不含业务知识，仅格式化）
# ──────────────────────────────────────────────

def _format_datetime(dt) -> str:
    """将 datetime 对象格式化为展示字符串，dt 为 None 时返回空串。"""
    if dt is None:
        return ""
    try:
        return dt.strftime("%m-%d %H:%M")
    except AttributeError:
        return str(dt)