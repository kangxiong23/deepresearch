# Layer: Core
# File: app/core/context_service.py
# Responsibility: 上下文编排服务。
#                 决定"如何构建发送给 LLM 的完整上下文"——
#                 包括历史消息截断策略、系统提示拼接、上下文块插入、
#                 树形目录上下文收集（Phase 3）。
#                 不直接读写数据库，通过 MessageRepo / ContextStore / TreeStore 协议操作。
# Input:  conversation_id, 上下文块列表, 新消息
# Output: LLMContext（组装好的完整请求上下文，交给 ConversationService 传给 LLM）
# 禁止: HTTP 调用、数据库 SQL、UI 导入

from __future__ import annotations

import uuid
from pathlib import Path

import config as app_config
from app.core.protocols import (
    ContextStoreProtocol,
    MessageRepoProtocol,
    TreeStoreProtocol,
)
from app.storage.models import (
    ContextBlock,
    ContextSource,
    ContextTemplate,
    ConversationNode,
    FolderNode,
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

# 附件文件读取大小上限（字节）
_MAX_ATTACHMENT_BYTES = 1_000_000


class ContextService:
    """
    上下文构建服务（Phase 3 — 集成树形上下文收集）。

    编排职责：
    1. 从 ContextStore 读取用户全局配置的上下文块
    2. 从 TreeStore 沿父目录链收集树关联的 ContextBlock 和附件
    3. 从 MessageRepo 读取历史消息，按 token 预算截断
    4. 将以上内容组装为 LLMContext，供 ConversationService 传给 LLM 客户端

    不直接操作数据库或外部 API。
    """

    def __init__(
        self,
        message_repo: MessageRepoProtocol,
        context_store: ContextStoreProtocol,
        tree_store: TreeStoreProtocol | None = None,
        knowledge_service=None,
    ) -> None:
        self._repo = message_repo
        self._store = context_store
        self._tree = tree_store                 # Phase 3: 树形上下文收集
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
        1. 收集树形上下文资源（父目录的 ContextBlock + 附件文件）
        2. 构建系统提示（全局块 → 树块 → 附件内容 → 知识图谱 → 默认提示）
        3. 读取历史消息并按 token 预算截断
        4. 若有搜索结果，拼接到用户消息之前
        5. 追加本次新用户消息
        6. 从 config 读取模型配置并填入 LLMContext

        Args:
            conversation_id:      当前对话节点 ID
            new_user_message:     用户本次输入的文本
            injected_search_text: 搜索结果文本（由 SearchService 提供，可为空）

        Returns:
            LLMContext — 可直接传给 LLMClientProtocol.stream_chat()
        """
        # ── Phase 3: 收集树形上下文资源 ────────
        tree_block_ids: list[str] = []
        tree_attachment_paths: list[str] = []
        if self._tree is not None:
            tree_block_ids, tree_attachment_paths = (
                self._collect_context_resources(conversation_id)
            )

        # 1. 构建系统提示（含树上下文 + 知识图谱注入）
        system_prompt = self._build_system_prompt(
            tree_block_ids=tree_block_ids,
            tree_attachment_paths=tree_attachment_paths,
        )
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
    # 上下文块管理接口（不变）
    # ──────────────────────────────────────────

    def add_text_block(self, content: str, label: str = "") -> ContextBlock:
        """手动添加一个文本上下文块。"""
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
        """将解析后的文件内容作为上下文块添加。"""
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
        """应用模板：将模板中的块全部追加到当前上下文块列表。"""
        template = self._store.get_template(template_id)
        if template is None:
            return []
        existing = self._store.get_blocks()
        for b in existing:
            if b.source == ContextSource.TEMPLATE:
                self._store.delete_block(b.id)
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

    def delete_template(self, template_id: str) -> None:
        """删除模板。"""
        self._store.delete_template(template_id)

    def get_assembled_preview(self) -> str:
        """返回当前已启用上下文块拼接后的预览文本。"""
        blocks = [b for b in self._store.get_blocks() if b.enabled]
        return self._assemble_context_blocks(blocks)

    # ──────────────────────────────────────────
    # Phase 3: 树形上下文收集
    # ──────────────────────────────────────────

    def _collect_context_resources(
        self,
        conversation_id: str,
    ) -> tuple[list[str], list[str]]:
        """
        从对话节点向上遍历所有父目录，收集 context_block_ids 和
        attachment_paths（去重，保持由近到远的顺序）。

        仅收集 effectively-enabled 的 FolderNode 的资源。

        优化：单次 O(D) 遍历——收集祖先链后，从上到下（root→node）扫描，
        维护运行中的 effective 状态，无需为每个祖先重复调用 _is_node_enabled。

        Args:
            conversation_id: 当前对话节点 ID

        Returns:
            (去重的 context_block_id 列表, 去重的 attachment_path 列表)
        """
        if self._tree is None:
            return ([], [])

        node = self._tree.get_node(conversation_id)
        if node is None:
            return ([], [])

        # 第一步：收集祖先链（从近到远，即 node→root 方向）
        # 包括 ConversationNode 自身（它也可能挂载了上下文块和附件）
        ancestors: list = []  # list of FolderNode | ConversationNode, bottom-up order
        # 首先检查节点自身（如果是 ConversationNode，它自己的资源也要收集）
        if isinstance(node, ConversationNode):
            ancestors.append(node)
        current_id: str | None = node.parent_id
        while current_id is not None:
            parent = self._tree.get_node(current_id)
            if parent is None:
                break
            if isinstance(parent, (FolderNode, ConversationNode)):
                ancestors.append(parent)
            current_id = parent.parent_id

        # 第二步：从上到下（root→node）单次扫描，维护运行中的 effective 状态
        # 规则：最近的（离 root 更近的）非 "some" 祖先覆盖所有后代
        block_ids: list[str] = []
        attachment_paths: list[str] = []
        effective = True  # 默认启用（无祖先约束时）

        for ancestor in reversed(ancestors):  # root first, then down
            if ancestor.enabled is True:
                effective = True
            elif ancestor.enabled is False:
                effective = False
            # ancestor.enabled == "some": 保持当前 effective 不变

            if effective:
                for bid in ancestor.context_block_ids:
                    if bid not in block_ids:
                        block_ids.append(bid)
                for ap in ancestor.attachment_paths:
                    if ap not in attachment_paths:
                        attachment_paths.append(ap)

        return (block_ids, attachment_paths)

    def is_node_effectively_enabled(self, node_id: str) -> bool:
        """
        公开方法：判断节点是否实际启用（考虑父节点级联覆盖规则）。

        供 ConversationService 等外部调用方获取节点的 effective enabled 状态。

        Args:
            node_id: 要检查的节点 ID

        Returns:
            bool — 节点是否实际启用
        """
        return self._is_node_enabled(node_id)

    def _is_node_enabled(self, node_id: str) -> bool:
        """
        判断节点是否实际启用（考虑父节点覆盖规则）。

        规则（自顶向下级联，高层祖先优先）：
        - 从节点父级向上遍历所有祖先，遇到 True/False 就更新有效状态
        - 最高层（最接近根）的非 "some" 祖先最终决定
        - 若所有祖先均为 "some"，则使用节点自身的 enabled 值

        示例：root(some) → A(False) → B(True) → C(some)
        - 从 C 向上：B=True（effective=True），A=False（覆盖！effective=False）
        - 最终 C 为 False（A 的 False 级联覆盖了 B 的 True）

        Args:
            node_id: 要检查的节点 ID

        Returns:
            bool — 节点是否实际启用
        """
        if self._tree is None:
            return True

        node = self._tree.get_node(node_id)
        if node is None:
            return False

        # 节点自身值作为初始默认
        effective = node.enabled is not False

        # 从父级向上遍历，高层祖先覆盖低层
        current_id: str | None = node.parent_id
        while current_id is not None:
            parent = self._tree.get_node(current_id)
            if parent is None:
                break
            if parent.enabled is True:
                effective = True
            elif parent.enabled is False:
                effective = False
            # parent.enabled == "some": 不改变 effective
            current_id = parent.parent_id

        return effective

    def _read_attachment_content(self, path: str) -> str:
        """
        读取单个附件文件内容，带大小限制和异常处理。

        Args:
            path: 文件绝对路径

        Returns:
            文件文本内容，失败或过大时返回空字符串或占位提示
        """
        try:
            file_path = Path(path)
            if not file_path.exists():
                print(f"[CTX] 附件不存在: {path}")
                return ""

            file_size = file_path.stat().st_size
            if file_size > _MAX_ATTACHMENT_BYTES:
                print(f"[CTX] 附件过大 ({file_size} bytes)，跳过: {path}")
                return f"[附件过大未加载：{file_path.name} ({file_size} bytes)]"

            with file_path.open("r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return content
        except OSError as e:
            print(f"[CTX] 读取附件失败 {path}: {e}")
            return ""
        except Exception as e:
            print(f"[CTX] 附件异常 {path}: {e}")
            return ""

    # ──────────────────────────────────────────
    # 内部编排方法
    # ──────────────────────────────────────────

    def _build_system_prompt(
        self,
        tree_block_ids: list[str] | None = None,
        tree_attachment_paths: list[str] | None = None,
    ) -> str:
        """
        构建完整的 system prompt，按以下顺序拼接：

        1. 全局用户上下文块（手动添加，source=MANUAL / FILE / TEMPLATE）
        2. 树目录收集的 ContextBlock（按父目录由近到远，去重）
        3. 树目录附件文件内容
        4. 默认系统提示（_DEFAULT_SYSTEM_PROMPT）

        Args:
            tree_block_ids:        树收集到的 ContextBlock ID 列表
            tree_attachment_paths: 树收集到的附件文件路径列表

        Returns:
            拼接完成的 system prompt 字符串
        """
        if tree_block_ids is None:
            tree_block_ids = []
        if tree_attachment_paths is None:
            tree_attachment_paths = []

        parts: list[str] = []
        tree_id_set: set[str] = set(tree_block_ids) if tree_block_ids else set()

        # ── 1. 全局上下文块（排除已在树中的）─
        all_blocks = self._store.get_blocks()
        global_enabled = [
            b for b in all_blocks
            if b.enabled and b.id not in tree_id_set
        ]
        for block in global_enabled:
            if block.content.strip():
                parts.append(block.content.strip())

        # ── 2. 树目录 ContextBlock ──────────
        if tree_block_ids and self._tree is not None:
            all_blocks_by_id = {b.id: b for b in all_blocks}
            tree_blocks: list[ContextBlock] = []
            for bid in tree_block_ids:
                block = all_blocks_by_id.get(bid)
                if block is None:
                    print(f"[CTX] 树 ContextBlock 未找到 (全局 storage): {bid}")
                    continue
                if block.enabled and block.content.strip():
                    tree_blocks.append(block)

            if tree_blocks:
                print(f"[CTX] 注入 {len(tree_blocks)} 个树目录 ContextBlock")
                tree_text = self._assemble_context_blocks(tree_blocks)
                if tree_text.strip():
                    parts.append(tree_text.strip())

        # ── 3. 树目录附件文件 ───────────────
        if tree_attachment_paths:
            loaded = 0
            for ap in tree_attachment_paths:
                content = self._read_attachment_content(ap)
                if content.strip():
                    label = Path(ap).name
                    parts.append(f"[附件：{label}]\n{content}")
                    loaded += 1
            if loaded:
                print(f"[CTX] 注入 {loaded} 个树附件文件")

        # ── 4. 默认系统提示（兜底）───────────
        parts.append(_DEFAULT_SYSTEM_PROMPT)

        return "\n\n".join(p for p in parts if p.strip())

    def _assemble_context_blocks(self, blocks: list[ContextBlock]) -> str:
        """将上下文块按 order 拼接为单一文本。"""
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

        selected: list[Message] = []
        total = 0
        for msg in reversed(messages):
            estimated = (
                msg.token_count if msg.token_count > 0 else len(msg.content) // 4
            )
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
        """将用户原始输入与搜索结果拼接为完整用户消息内容。"""
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
