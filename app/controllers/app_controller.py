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
    SearchResultVM,
    StreamChunkVM,
    TreeNodeVM,
    TrashEntryVM,
)
from app.storage.models import FolderNode, ConversationNode, MessageNode


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

    def on_new_conversation(self, parent_id: str | None = None) -> str:
        """
        UI 点击"新建对话"时调用。

        Args:
            parent_id: 可选父节点 ID，None 表示放在默认"未分类"目录下

        Returns:
            新对话的 session_id（str），UI 用于标记当前激活会话。
        """
        return self._conversation_svc.create_conversation(parent_id=parent_id)

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

    def on_load_messages_for_node(self, node_id: str) -> list[MessageVM]:
        """
        以树结构为依据，加载任意节点对应的消息列表。

        不依赖 conversation_id，直接使用节点中存储的 message_id 引用。

        Args:
            node_id: 目标节点 ID（MessageNode / ConversationNode / FolderNode）

        Returns:
            list[MessageVM] — 该节点对应的消息
        """
        print(f"[CTRL ] on_load_messages_for_node: node={node_id}")
        messages = self._conversation_svc.get_messages_for_node(node_id)
        result = [self._map_to_message_vm(m) for m in messages]
        print(f"[CTRL ] on_load_messages_for_node: 返回 {len(result)} 个 MessageVM")
        return result

    def on_get_multi_conversation_messages(self) -> list[MessageVM]:
        """
        获取所有有效启用对话的消息，按时间排序合并。

        被修改节点附带分叉信息（fork_m/fork_n/fork_point_id），
        供消息气泡下方的 <m/n> 切换控件使用（3.5.1）。

        Returns:
            list[MessageVM] — 所有启用对话的消息
        """
        print("[CTRL ] on_get_multi_conversation_messages: 查询所有启用消息")
        messages = self._conversation_svc.get_effective_enabled_messages()
        fork_map = self._conversation_svc.get_fork_info_map()
        result: list[MessageVM] = []
        for m in messages:
            vm = self._map_to_message_vm(m)
            info = fork_map.get(m.id)
            if info is not None:
                vm.conversation_id, vm.fork_m, vm.fork_n, vm.fork_point_id = info
            result.append(vm)
        print(f"[CTRL ] on_get_multi_conversation_messages: 返回 {len(result)} 个 MessageVM")
        return result

    def on_switch_branch(self, fork_point_id: str, delta: int) -> None:
        """
        UI 点击 <m/n> 控件的 < / > 时调用（3.5）。

        Args:
            fork_point_id: 分叉点 ID（消息节点或对话节点）
            delta:         +1 下一个分支 / -1 上一个分支
        """
        node = self._conversation_svc.get_node(fork_point_id)
        if node is None or not isinstance(node, (ConversationNode, MessageNode)):
            return
        if not node.is_fork_point:
            return
        target = node.fork_current_index + delta
        if target < 0 or target >= node.fork_branch_count:
            return  # 越界防御（UI 边界按钮已禁用）
        conv_id = node.id if isinstance(node, ConversationNode) else node.parent_id
        self._conversation_svc.switch_branch(conv_id, fork_point_id, target)

    def find_last_enabled_conversation_id(self) -> str | None:
        """
        返回树中最后一个有效启用消息所在的对话 id。

        供 UI 在发送消息前确定插入目标（新内容在聚合时间线末尾继续）。

        Returns:
            str | None — 目标对话 id；无启用消息时为 None
        """
        return self._conversation_svc.find_last_enabled_conversation_id()

    def should_continue_in_new_conversation(self, conv_id: str | None) -> bool:
        """
        判断是否应强制在新（空）对话中开始（不重定向）。

        条件：conv_id 是有效启用的空对话，且其位置位于所有已启用消息之后（DFS 前序）。
        供 UI 发送前决定是否重定向到最后一个启用消息所在对话。

        Args:
            conv_id: 对话节点 id（可为 None）

        Returns:
            bool — True 表示应使用该新对话，不重定向
        """
        return self._conversation_svc.should_continue_in_new_conversation(conv_id)

    def is_conversation_usable(self, conv_id: str | None) -> bool:
        """
        判断 conv_id 是否是可接收消息的对话节点（存在、是 ConversationNode、且有效启用）。

        用于「无任何启用消息」场景：若当前会话不可用（空 / 已禁用 / 不存在），
        则在根目录自动新建对话，确保新对话被记录并可见。

        Args:
            conv_id: 对话节点 id（可为 None）

        Returns:
            bool — True 表示该对话可用作消息插入目标
        """
        return self._conversation_svc.is_conversation_usable(conv_id)

    def find_latest_user_message_id(self, session_id: str) -> str | None:
        """
        返回指定对话下最近一条 user MessageNode 的 id（未完成轮次的标记目标）。

        Args:
            session_id: 对话节点 ID

        Returns:
            str | None — 最近一条 user 消息的 id
        """
        return self._conversation_svc.find_latest_user_message_id(session_id)

    def cleanup_incomplete_nodes(self) -> int:
        """软删除所有标记为未完成的 MessageNode（移至回收站）。"""
        return self._conversation_svc.cleanup_incomplete_nodes()

    def mark_incomplete(self, node_id: str) -> None:
        """标记消息节点为未完成（用户停止生成）。"""
        self._conversation_svc.mark_incomplete(node_id)

    def clear_incomplete_mark(self, node_id: str) -> None:
        """清除消息节点的未完成标记（被"重新生成"/"继续生成"抢救）。"""
        self._conversation_svc.clear_incomplete_mark(node_id)

    def on_search(self, keywords_text: str) -> list[SearchResultVM]:
        """
        UI 调用：执行消息关键词搜索。

        Args:
            keywords_text: 空格分隔的搜索关键词

        Returns:
            list[SearchResultVM] — 按时间倒序排列的搜索结果
        """
        if not keywords_text.strip():
            return []

        keywords = [
            kw.strip().lower()
            for kw in keywords_text.split()
            if kw.strip()
        ]
        results = self._conversation_svc.search_messages(keywords)
        vms: list[SearchResultVM] = []
        for r in results:
            snippet = self._build_snippet(r.content, keywords)
            vms.append(SearchResultVM(
                message_id=r.message_id,
                conversation_id=r.conversation_id,
                role=r.role,
                snippet=snippet,
                tree_path=r.tree_path,
                created_at=_format_datetime(r.created_at),
            ))
        return vms

    @staticmethod
    def _build_snippet(content: str, keywords: list[str]) -> str:
        """
        围绕消息中最早匹配的关键词构建上下文片段。
        在所有关键词中找到最早出现位置，显示该位置前后各 10 字符。
        """
        from app.utils.markdown_utils import strip_for_search

        plain = strip_for_search(content)
        if not keywords or not plain:
            return plain[:30] if plain else ""

        plain_lower = plain.lower()
        best_idx: int | None = None
        best_kw: str = ""
        for kw in keywords:
            idx = plain_lower.find(kw)
            if idx != -1 and (best_idx is None or idx < best_idx):
                best_idx = idx
                best_kw = kw

        if best_idx is None:
            return plain[:30] + ("..." if len(plain) > 30 else "")

        kw_len = len(best_kw)
        start = max(0, best_idx - 10)
        end = min(len(plain), best_idx + kw_len + 10)
        snippet = plain[start:end]
        if start > 0:
            snippet = "..." + snippet
        if end < len(plain):
            snippet = snippet + "..."
        return snippet

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

    async def on_resend_edited_message(
        self,
        session_id: str,
        original_user_id: str,
        text: str,
        files: list[str],
    ) -> AsyncGenerator[StreamChunkVM, None]:
        """
        修改并重发送 user 消息（分叉功能，spec 5.3）。

        发送时立即创建分支，随后流式输出 assistant 回复。

        Yields:
            StreamChunkVM — 同 on_send_message
        """
        async for chunk in self._conversation_svc.resend_edited_message(
            session_id, original_user_id, text, files
        ):
            yield self._map_to_stream_chunk_vm(chunk)

    async def on_continue_message(
        self,
        session_id: str,
        partial_content: str,
        partial_thinking: str = "",
    ) -> AsyncGenerator[StreamChunkVM, None]:
        """
        继续生成未完成的助手消息（DeepSeek Beta 前缀续写）。

        Args:
            session_id:       当前对话节点 ID
            partial_content:  未完成消息已有的部分文本
            partial_thinking: 未完成消息已有的部分思考内容（若有）

        Yields:
            StreamChunkVM — 流式续写块，同 on_send_message
        """
        async for chunk in self._conversation_svc.continue_message(
            session_id, partial_content, partial_thinking
        ):
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
        ConversationNode → ConversationVM（Phase 5 — 移除旧 Conversation 兼容）。
        """
        return ConversationVM(
            id=domain_obj.id,
            title=domain_obj.title or "新对话",
            preview=getattr(domain_obj, "summary", ""),
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

    def on_add_text_block(self, text: str, title: str = "") -> dict:
        """
        添加自定义文本块到上下文。
        Args:
            text:  内容文本
            title: 标题；空缺时默认取内容前 15 个中文字符
        Returns: {"id", "label", "content", "preview", "enabled"}
        """
        block = self._context_svc.add_text_block(
            content=text,
            label=_default_block_title(text, title),
        )
        return {
            "id": block.id,
            "label": block.label,
            "content": block.content,
            "preview": block.content[:60],
            "enabled": block.enabled,
        }

    def on_get_blocks_by_ids(self, block_ids: list[str]) -> list[dict]:
        """按给定 ID 顺序返回上下文块完整信息（含 content/preview），供节点模式使用。"""
        by_id = {b.id: b for b in self._context_svc.get_blocks()}
        result: list[dict] = []
        for bid in block_ids:
            b = by_id.get(bid)
            if b is not None:
                result.append(
                    {
                        "id": b.id,
                        "label": b.label,
                        "content": b.content,
                        "preview": b.content[:60],
                        "enabled": b.enabled,
                    }
                )
        return result

    def on_remove_context_block(self, block_id: str) -> None:
        """删除上下文块。"""
        self._context_svc._store.delete_block(block_id)

    def on_clone_context_block(self, block_id: str) -> dict:
        """克隆上下文块到原块下一个位置，返回新块。"""
        clone = self._context_svc.clone_block(block_id)
        if clone is None:
            return {}
        return {
            "id": clone.id,
            "label": clone.label,
            "content": clone.content,
            "preview": clone.content[:60],
            "enabled": clone.enabled,
        }

    def on_toggle_context_block(self, block_id: str, enabled: bool) -> None:
        """启用/禁用上下文块。"""
        self._context_svc._store.update_block_enabled(block_id, enabled)

    def on_reorder_context_block(self, block_id: str, new_index: int) -> None:
        """拖拽调整上下文块顺序。"""
        self._context_svc.reorder_block(block_id, new_index)

    def on_get_context_block_content(self, block_id: str) -> str:
        """获取单个上下文块的完整内容（编辑模式填充用）。"""
        block = self._context_svc.get_block(block_id)
        return block.content if block else ""

    def on_get_context_block(self, block_id: str) -> dict:
        """获取单个上下文块信息（内容 + 标题，编辑模式填充用）。"""
        block = self._context_svc.get_block(block_id)
        if block is None:
            return {"id": "", "label": "", "content": "", "enabled": True}
        return {
            "id": block.id,
            "label": block.label,
            "content": block.content,
            "enabled": block.enabled,
        }

    def on_update_context_block(
        self, block_id: str, content: str, title: str = ""
    ) -> str:
        """更新上下文块内容与标题（编辑保存），返回新标题。"""
        label = _default_block_title(content, title)
        self._context_svc.update_block(block_id, content, label)
        return label

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

    def on_apply_template(self, template_id: str, mode: str = "replace") -> list[dict]:
        """应用模板：mode="replace" 整体替换当前上下文块；"add" 增量追加。
        返回新建的块（节点模式暂存用）。"""
        blocks = self._context_svc.apply_template(template_id, mode)
        return [
            {
                "id": b.id,
                "label": b.label,
                "content": b.content,
                "preview": b.content[:60],
                "enabled": b.enabled,
            }
            for b in blocks
        ]

    def on_delete_template(self, template_id: str) -> None:
        """删除模板。"""
        self._context_svc.delete_template(template_id)

    def on_find_template_by_name(self, name: str) -> dict | None:
        """按标题精确查找模板（重名检测用）。"""
        for t in self._context_svc.get_templates():
            if t.name == name:
                return {"id": t.id, "name": t.name, "description": t.description}
        return None

    def on_overwrite_template(
        self, template_id: str, name: str, blocks: list[dict] | None = None
    ) -> dict:
        """用当前块覆盖指定 ID 的模板（保留 ID 与描述）。

        Args:
            template_id: 被覆盖的模板 ID
            name:        模板标题
            blocks:      新的块列表；None 时用全局上下文块
        """
        import uuid
        from app.storage.models import ContextBlock, ContextSource, ContextTemplate
        existing = self._context_svc._store.get_template(template_id)
        if existing is None:
            # 目标不存在 → 退化为新建
            if blocks is None:
                return self.on_save_current_as_template(name)
            return self.on_save_blocks_as_template(name, blocks)
        if blocks is None:
            src_blocks = self._context_svc.get_blocks()
        else:
            src_blocks = [
                ContextBlock(
                    id=b.get("id", ""),
                    label=b.get("label", ""),
                    content=b.get("content", ""),
                    source=ContextSource.MANUAL,
                    enabled=b.get("enabled", True),
                    order=i,
                )
                for i, b in enumerate(blocks)
            ]
        template = ContextTemplate(
            id=template_id,
            name=name,
            description=existing.description,
            blocks=src_blocks,
        )
        self._context_svc._store.save_template(template)
        return {"id": template_id, "name": name, "description": existing.description}

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

    def on_save_blocks_as_template(self, name: str, blocks: list[dict]) -> dict:
        """把指定块列表保存为新模板（节点模式下保存节点挂靠块）。"""
        import uuid
        from app.storage.models import ContextBlock, ContextSource, ContextTemplate
        tmpl_blocks = [
            ContextBlock(
                id=b.get("id", ""),
                label=b.get("label", ""),
                content=b.get("content", ""),
                source=ContextSource.MANUAL,
                enabled=b.get("enabled", True),
                order=i,
            )
            for i, b in enumerate(blocks)
        ]
        template = ContextTemplate(
            id=str(uuid.uuid4()), name=name, description="", blocks=tmpl_blocks
        )
        self._context_svc._store.save_template(template)
        return {"id": template.id, "name": name, "description": ""}


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

    # ──────────────────────────────────────────
    # 树形结构管理（Phase 4）
    # ──────────────────────────────────────────

    def _build_tree_vms_plain(self) -> list[TreeNodeVM]:
        """
        返回完整树结构的 DFS 排序平面列表，depth 和 has_children 预计算
        （不含分叉展示前缀，由 get_tree 统一附加）。

        Returns:
            list[TreeNodeVM] — 按 DFS 遍历顺序排列的节点视图模型
        """
        tree = self._conversation_svc.get_tree()
        nodes = tree.nodes
        if not nodes:
            return []

        # 构建 children 映射
        child_map: dict[str | None, list] = {}
        for n in nodes:
            pid = n.parent_id
            if pid not in child_map:
                child_map[pid] = []
            child_map[pid].append(n)

        # 按 sort_order 排序每组
        for pid in child_map:
            child_map[pid].sort(key=lambda n: n.sort_order)

        result: list[TreeNodeVM] = []

        def dfs(node, depth: int) -> None:
            children = child_map.get(node.id, [])
            has_children = len(children) > 0

            if isinstance(node, MessageNode):
                vm = TreeNodeVM(
                    id=node.id,
                    title=node.preview or node.title or node.role,
                    node_type="message",
                    parent_id=node.parent_id,
                    enabled=node.enabled,
                    sort_order=node.sort_order,
                    preview=node.preview,
                    updated_at=_format_datetime(node.updated_at),
                    has_children=False,  # MessageNode 永远是叶子
                    depth=depth,
                    role=node.role,
                    is_modified=(
                        self._conversation_svc.is_modified_node(node.id)
                    ),
                )
            elif isinstance(node, FolderNode):
                vm = TreeNodeVM(
                    id=node.id,
                    title=node.title or "新文件夹",
                    node_type="folder",
                    parent_id=node.parent_id,
                    enabled=node.enabled,
                    sort_order=node.sort_order,
                    preview="",
                    updated_at=_format_datetime(node.updated_at),
                    has_children=has_children,
                    depth=depth,
                    context_block_count=len(getattr(node, "context_block_ids", [])),
                    attachment_count=len(getattr(node, "attachment_paths", [])),
                )
            else:  # ConversationNode
                vm = TreeNodeVM(
                    id=node.id,
                    title=node.title or "新对话",
                    node_type="conversation",
                    parent_id=node.parent_id,
                    enabled=node.enabled,
                    sort_order=node.sort_order,
                    preview=getattr(node, "summary", ""),
                    updated_at=_format_datetime(node.updated_at),
                    has_children=has_children,
                    depth=depth,
                    message_count=getattr(node, "message_count", 0),
                    context_block_count=len(getattr(node, "context_block_ids", [])),
                    attachment_count=len(getattr(node, "attachment_paths", [])),
                )
            result.append(vm)

            for child in children:
                dfs(child, depth + 1)

        # 从根节点开始 DFS
        roots = child_map.get(None, [])
        for root_node in roots:
            dfs(root_node, 0)

        return result

    def get_tree(self) -> list[TreeNodeVM]:
        """
        返回完整树结构的 DFS 排序平面列表，depth 和 has_children 预计算。

        分叉标记（<m/n>）显示在被修改节点上（用户拍板）：标记存储于
        分叉点，但展示时位于分叉点的后继（被修改节点），与聊天区控件一致。

        Returns:
            list[TreeNodeVM] — 按 DFS 遍历顺序排列的节点视图模型
        """
        vms = self._build_tree_vms_plain()
        # 分叉展示信息：{message_id: (conv, m, n, fork_point_id)}
        fork_map = self._conversation_svc.get_fork_info_map()
        for vm in vms:
            info = fork_map.get(vm.id)
            if info is not None:
                vm.fork_display = f"<{info[1]}/{info[2]}> "
        return vms

    def on_create_folder(
        self,
        parent_id: str | None,
        title: str,
    ) -> TreeNodeVM:
        """
        UI 调用：创建新目录。

        Args:
            parent_id: 父节点 ID，None 表示根目录
            title:     目录名称

        Returns:
            TreeNodeVM — 新创建的目录节点视图模型
        """
        folder = self._conversation_svc.create_folder(title, parent_id)
        return TreeNodeVM(
            id=folder.id,
            title=folder.title,
            node_type="folder",
            parent_id=folder.parent_id,
            enabled=folder.enabled,
            sort_order=folder.sort_order,
            updated_at=_format_datetime(folder.updated_at),
            has_children=False,
            depth=0,
        )

    def on_rename_node(self, node_id: str, new_title: str) -> None:
        """UI 调用：重命名节点。"""
        self._conversation_svc.rename_node(node_id, new_title)

    def on_toggle_enabled(self, node_id: str) -> None:
        """UI 调用：切换节点启用/禁用状态。"""
        self._conversation_svc.toggle_enabled(node_id)

    def on_move_node(
        self,
        node_id: str,
        target_parent_id: str,
        position: int | None = None,
    ) -> None:
        """UI 调用：移动节点到目标父级下。"""
        self._conversation_svc.move_conversation(node_id, target_parent_id, position)

    def on_move_node_branch_aware(
        self,
        node_id: str,
        target_parent_id: str | None,
        position: int | None = None,
        prev_id: str | None = None,
        next_id: str | None = None,
    ) -> bool:
        """
        UI 调用：分支感知的拖拽移动（3.6 组合表）。

        Returns:
            True 执行成功；False 被拒绝（被修改节点同对话移动等）
        """
        return self._conversation_svc.move_message_with_fork(
            node_id, target_parent_id, position, prev_id, next_id
        )

    def get_node(self, node_id: str):
        """UI 调用：返回节点领域对象（None 表示不存在）。"""
        return self._conversation_svc.get_node(node_id)

    def on_get_fork_info(self, fork_point_id: str):
        """
        UI 调用：返回分叉点的当前展示 (m, n)；非分叉点返回 None。

        用于后端 create_branch 完成后增量刷新 <m/n> 控件（无需全量重建）。
        """
        node = self._conversation_svc.get_node(fork_point_id)
        if node is None or not node.is_fork_point:
            return None
        return (node.fork_current_index + 1, node.fork_branch_count)

    def on_get_fork_preview(self, message_id: str):
        """
        UI 调用：修改重发送发送前预估被修改消息的分叉展示 (m, n, fork_point_id)。

        分叉点 = 被修改消息的前驱（第一条消息 → 对话节点）。
        首次分叉 → (2, 2)；已有分叉 → (count+1, count+1)。
        供发送后立即显示 <m/n>（临时禁用点击，create_branch 完成后启用）。
        """
        svc = self._conversation_svc
        node = svc.get_node(message_id)
        if node is None:
            return None
        conv_id = node.parent_id
        pred = svc.get_predecessor(conv_id, message_id)
        if pred is not None:
            fp_id = pred.id
            count = pred.fork_branch_count if pred.is_fork_point else 0
        else:
            fp_id = conv_id
            conv_node = svc.get_node(conv_id)
            count = (
                conv_node.fork_branch_count
                if conv_node is not None and conv_node.is_fork_point
                else 0
            )
        m = n = (count + 1) if count >= 1 else 2
        return (m, n, fp_id)

    def on_soft_delete_node(self, node_id: str, mode: str = "recursive") -> None:
        """UI 调用：软删除节点（移入回收站；被修改节点为硬删除，3.3.1）。"""
        self._conversation_svc.delete_conversation(node_id, mode)

    def is_modified_node(self, node_id: str) -> bool:
        """
        UI 调用：节点是否为"被修改节点"（分叉点后继，3.3.1 硬删除判定）。

        删除确认框据此展示"永久删除、不进回收站"的提示。
        """
        return self._conversation_svc.is_modified_node(node_id)

    def on_update_context_blocks(
        self,
        folder_id: str,
        block_ids: list[str],
    ) -> None:
        """UI 调用：更新目录关联的 ContextBlock 列表。"""
        self._conversation_svc.update_folder_context(folder_id, block_ids)

    def on_get_folder_context(self, folder_id: str) -> dict:
        """
        UI 调用：获取节点（目录或对话）的上下文块信息（供管理对话框使用）。

        Returns:
            {"title": str, "context_block_ids": list[str]}
        """
        node = self._conversation_svc.get_node(folder_id)
        if node is None:
            return {"title": "节点", "context_block_ids": []}
        return {
            "title": getattr(node, "title", "节点"),
            "context_block_ids": list(getattr(node, "context_block_ids", [])),
        }

    def on_attach_file(self, folder_id: str, file_path: str) -> None:
        """UI 调用：挂载附件到目录。"""
        self._conversation_svc.attach_file(folder_id, file_path)

    def on_detach_file(self, folder_id: str, file_path: str) -> None:
        """UI 调用：从目录移除附件。"""
        self._conversation_svc.detach_file(folder_id, file_path)

    def on_list_trash(self) -> list[TrashEntryVM]:
        """UI 调用：列出回收站所有条目。"""
        entries = self._conversation_svc.get_trash_entries()
        return [
            TrashEntryVM(
                id=entry.id,
                node_id=entry.node_data.get("id", ""),
                title=entry.node_data.get("title", "未命名"),
                node_type=entry.node_data.get("node_type", "conversation"),
                json_path=entry.json_path,
                deleted_at=_format_datetime(entry.deleted_at),
            )
            for entry in entries
        ]

    def on_restore_from_trash(
        self,
        trash_entry_id: str,
        new_parent_id: str | None = None,
    ) -> None:
        """UI 调用：从回收站恢复节点。"""
        self._conversation_svc.restore_conversation(trash_entry_id, new_parent_id)

    def on_permanently_delete(self, trash_entry_id: str) -> None:
        """UI 调用：从回收站彻底删除节点及关联消息。"""
        self._conversation_svc.permanently_delete_from_trash(trash_entry_id)

    def on_clear_trash(self) -> int:
        """UI 调用：清空回收站。返回清除的条目数。"""
        return self._conversation_svc.clear_trash()

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


def _default_block_title(content: str, title: str = "") -> str:
    """计算文本块标题：用户输入优先；空缺时取内容前 15 个中文字符
    （每 2 个 ASCII 字符计 1 个中文字符，忽略换行）。"""
    source = title.strip() if title and title.strip() else content
    return _truncate_to_cn_chars(source, 15)


def _truncate_to_cn_chars(text: str, max_cn: int = 15) -> str:
    """按"中文字符"权重截断：ASCII 计 0.5，其余计 1；忽略换行；不超过 max_cn。"""
    cleaned = text.replace("\r", "").replace("\n", "")
    out: list[str] = []
    weight = 0.0
    for ch in cleaned:
        w = 0.5 if ord(ch) < 128 else 1.0
        if weight + w > max_cn:
            break
        out.append(ch)
        weight += w
    return "".join(out)