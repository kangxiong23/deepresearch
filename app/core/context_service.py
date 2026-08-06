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
    MessageNode,
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
        *,
        history_until: str | None = None,
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
            history_until:        历史截断边界消息 ID（不含该消息及其之后，
                                  供重新生成场景使用——只保留目标消息之前的上下文）

        Returns:
            LLMContext — 可直接传给 LLMClientProtocol.stream_chat()
        """
        # ── Phase 3: 收集树形上下文资源（仅附件；上下文块改为整树前序）──
        tree_attachment_paths: list[str] = []
        if self._tree is not None:
            _, tree_attachment_paths = self._collect_context_resources(conversation_id)

        # 1. 构建系统提示（含整树前序上下文块 + 附件 + 知识图谱注入）
        system_prompt = self._build_system_prompt(
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
        #    ⚠️ 以 tree.json 为唯一数据源：仅收集有效启用的 MessageNode，
        #    保证删除/禁用的消息不会进入 LLM 历史（与前端展示一致）。
        #    ⚠️ 去除 THINKING（LLM 草稿）：不进历史、不占 token 预算，
        #    保持上下文轻量。
        history = self.get_enabled_history_messages()
        history = [m for m in history if m.role != Role.THINKING]
        if history_until:
            # 重新生成场景:只保留目标消息之前的上下文(4.1)
            cut: list[Message] = []
            for m in history:
                if m.id == history_until:
                    break
                cut.append(m)
            history = cut
        truncated = self._truncate_history(
            history,
            budget_tokens=app_config.max_history_tokens,
        )

        # 3. 组装 messages 列表（OpenAI 格式）
        messages: list[dict] = []
        for msg in truncated:
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

    def reorder_block(self, block_id: str, new_index: int) -> None:
        """将上下文块移动到 new_index 位置（面板拖拽排序）。

        get_blocks() 按 order 正序返回，即当前展示顺序；
        从中移除目标块再插入到 new_index，最后按新顺序重写 0..N-1 的 order。
        """
        blocks = self._store.get_blocks()
        ids = [b.id for b in blocks]
        if block_id not in ids:
            return
        ids.remove(block_id)
        new_index = max(0, min(new_index, len(ids)))
        ids.insert(new_index, block_id)
        self._store.reorder_blocks(ids)

    def get_blocks(self) -> list[ContextBlock]:
        """获取所有上下文块（按 order 排序）。"""
        return self._store.get_blocks()

    def get_block(self, block_id: str) -> ContextBlock | None:
        """按 ID 获取单个上下文块。"""
        for b in self._store.get_blocks():
            if b.id == block_id:
                return b
        return None

    def clone_block(self, block_id: str) -> ContextBlock | None:
        """克隆上下文块：生成内容/标题完全相同的块，插到原块的下一个位置。"""
        blocks = self._store.get_blocks()
        ids = [b.id for b in blocks]
        if block_id not in ids:
            return None
        src = next(b for b in blocks if b.id == block_id)
        idx = ids.index(block_id)
        clone = ContextBlock(
            id=str(uuid.uuid4()),
            label=src.label,
            content=src.content,
            source=src.source,
            enabled=src.enabled,
            order=0,  # 由 reorder_blocks 重排
        )
        self._store.save_block(clone)
        new_ids = ids[: idx + 1] + [clone.id] + ids[idx + 1 :]
        self._store.reorder_blocks(new_ids)
        return clone

    def update_block(self, block_id: str, content: str, label: str | None = None) -> None:
        """更新上下文块内容（及可选标题，编辑保存）。"""
        for b in self._store.get_blocks():
            if b.id == block_id:
                b.content = content
                if label is not None:
                    b.label = label
                self._store.save_block(b)
                return

    def apply_template(self, template_id: str, mode: str = "replace") -> list[ContextBlock]:
        """应用模板。

        Args:
            template_id: 模板 ID
            mode:
                "replace" — 整体替换：删除当前所有上下文块，再用模板块填充；
                "add"     — 增量追加：在现有块之后追加模板块。

        Returns:
            新建的 ContextBlock 列表
        """
        template = self._store.get_template(template_id)
        if template is None:
            return []
        if mode == "replace":
            for b in self._store.get_blocks():
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

    def _preorder_context_blocks(self) -> list[ContextBlock]:
        """按树前序遍历（root → 子节点，DFS）收集所有已挂靠且生效的 ContextBlock。

        ## 模型约定（重要，勿误解为"按对话隔离"）
        - 对话并非隔离关系：所有启用的消息共同组成一份历史，上下文块也必须全部组织在一起。
        - 每个上下文块都必须挂靠到某个目录/对话节点才能存在，不存在"未挂靠的全局块"。
        - 判断某节点挂靠的块是否进 system prompt，看的是该节点**自身** enabled 值：
            * true 或 "some" → 其下存在启用消息 → 必须加入；
            * false → 其下无启用消息 → 不加。
          （级联切换会把状态写回子节点字段，故直接看自身值即可。）
        - 另有 per-node 开关 context_blocks_enabled：为 False 时该节点挂靠的块也不加入。
        - 任何对话的 system prompt 一致（整树前序的全部块）。
        """
        if self._tree is None:
            return []
        all_blocks_by_id = {b.id: b for b in self._store.get_blocks()}
        tree_root = self._tree.get_tree()
        nodes_by_parent: dict[str | None, list] = {}
        for node in tree_root.nodes:
            nodes_by_parent.setdefault(node.parent_id, []).append(node)
        for children in nodes_by_parent.values():
            children.sort(key=lambda n: n.sort_order)

        result: list[ContextBlock] = []

        def visit(node) -> None:
            if isinstance(node, (FolderNode, ConversationNode)):
                # 自身 enabled 为 true/"some" 且上下文块未禁用 → 加入其挂靠块
                if node.enabled is not False and node.context_blocks_enabled:
                    for bid in node.context_block_ids:
                        block = all_blocks_by_id.get(bid)
                        if block is not None and block.enabled and block.content.strip():
                            result.append(block)
            for child in nodes_by_parent.get(node.id, []):
                visit(child)

        for top in nodes_by_parent.get(None, []):
            visit(top)
        return result

    def _collect_context_resources(
        self,
        conversation_id: str,
    ) -> tuple[list[str], list[str]]:
        """
        从对话节点向上遍历所有父目录，收集 context_block_ids 和
        attachment_paths（去重，保持由近到远的顺序）。

        注意：上下文块的 system prompt 注入已改为整树前序（_preorder_context_blocks），
        本方法返回的 block_ids 已不再用于拼接，仅保留供测试/扩展；附件仍按当前对话链收集。

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

    def get_enabled_history_messages(
        self,
        include_incomplete: bool = False,
        include_thinking: bool = False,
    ) -> list[Message]:
        """
        收集历史消息，以 tree.json 为唯一数据源。

        规则（与前端 `get_effective_enabled_messages` 一致）：
        - 遍历树中所有 MessageNode，逐个通过祖先级联规则判断是否 effectively enabled
        - 仅收集 enabled 的 message_id，批量从 messages 表加载实际内容
        - 按 DFS 前序遍历（树结构顺序）排列
        - 默认排除 THINKING（Phase 6 起 thinking 绑定在 assistant 上，
          通过 include_thinking=True 仅在前端展示时穿插，LLM 上下文永不含 thinking）

        include_incomplete:
            False（默认）— 跳过标记为未完成（incomplete）的节点，
                           用于 LLM 上下文（不污染、不占 token）。
            True — 包含 incomplete 节点，用于前端展示（与侧边栏一致，
                   暂停轮次的 user 消息仍显示）。

        include_thinking:
            False（默认）— 用于 LLM 上下文（thinking 是草稿，不进上下文）。
            True — 将 assistant 节点绑定的 thinking 行穿插到对应 assistant 之前，
                   用于前端聚合时间线展示。

        回退：若树中无任何 MessageNode（Phase 5 迁移前创建的旧对话），
        则收集所有有效启用的 ConversationNode，通过 conversation_id 批量查询。

        Returns:
            list[Message] — 所有有效启用对话的消息，按 DFS 前序遍历排列
        """
        if self._tree is None:
            return []

        all_msg_nodes = self._tree.get_all_message_nodes()

        if not all_msg_nodes:
            # 回退：无 MessageNode，使用 ConversationNode + conversation_id 查询
            tree = self._tree.get_tree()
            conv_nodes = [n for n in tree.nodes if isinstance(n, ConversationNode)]
            enabled_conv_ids: list[str] = [
                cn.id for cn in conv_nodes
                if self.is_node_effectively_enabled(cn.id)
            ]
            if not enabled_conv_ids:
                print("[CTX ] get_enabled_history_messages: 无启用的对话（回退模式）")
                return []
            messages = self._repo.get_messages_by_conversation_ids(enabled_conv_ids)
            if include_thinking:
                messages = self._interleave_bound_thinking(messages)
            print(f"[CTX ] get_enabled_history_messages: 回退模式，"
                  f"{len(enabled_conv_ids)} 个启用对话 → {len(messages)} 条消息")
            return messages

        # 逐个判断 effective enabled（考虑祖先级联）
        # 默认跳过 incomplete 节点（不进 LLM 上下文）；前端展示时传入 True 包含
        enabled_message_ids: set[str] = set()
        for node in all_msg_nodes:
            if node.incomplete and not include_incomplete:
                continue
            if self._is_node_enabled(node.id):
                enabled_message_ids.add(node.message_id)

        if not enabled_message_ids:
            print("[CTX ] get_enabled_history_messages: 无有效启用的 MessageNode")
            return []

        # ── 按 DFS 前序遍历排序 ──
        ordered_ids = self._tree.get_message_ids_in_tree_order()
        id_order: dict[str, int] = {mid: i for i, mid in enumerate(ordered_ids)}

        messages = self._repo.get_messages_by_ids(list(enabled_message_ids))
        messages.sort(key=lambda m: id_order.get(m.id, 999999))

        if include_thinking:
            # Phase 6: 展示模式附带绑定 thinking（LLM 上下文保持排除）
            messages = self._interleave_bound_thinking(messages)

        print(f"[CTX ] get_enabled_history_messages: "
              f"{len(enabled_message_ids)} 个 MessageNode → {len(messages)} 条消息（树序遍历）")
        return messages

    def _interleave_bound_thinking(self, messages: list[Message]) -> list[Message]:
        """
        Phase 6: 将 assistant 节点绑定的 thinking 行穿插到对应 assistant 之前。

        仅用于前端展示；LLM 上下文调用方不传 include_thinking。
        """
        if not messages:
            return messages
        all_msg_nodes = self._tree.get_all_message_nodes()
        bound = {
            n.message_id: n.thinking_message_id
            for n in all_msg_nodes
            if isinstance(n, MessageNode) and n.thinking_message_id
        }
        if not bound:
            return messages
        thinking_ids = [tid for tid in bound.values() if tid]
        thinking_msgs = self._repo.get_messages_by_ids(thinking_ids)
        by_id = {t.id: t for t in thinking_msgs}
        result: list[Message] = []
        for m in messages:
            if m.role == Role.ASSISTANT and m.id in bound:
                t = by_id.get(bound[m.id])
                if t is not None:
                    result.append(t)
            result.append(m)
        return result

    def build_continue_context(
        self,
        partial_content: str,
        partial_thinking: str = "",
    ) -> LLMContext:
        """
        构建"继续生成"的 LLM 上下文（DeepSeek Beta 前缀续写）。

        历史来自 tree.json（与正常发送一致，剔除 THINKING），
        末尾追加部分 assistant 内容作为 prefix（prefix=True），让模型补全其余内容。

        Args:
            partial_content:  未完成消息已有的部分文本（续写起点）
            partial_thinking: 未完成消息已有的部分思考内容（若有，作为 reasoning_content）

        Returns:
            LLMContext — 传给 LLMClient.stream_prefix_continue
        """
        system_prompt = self._build_system_prompt()

        # 历史与正常发送一致：tree.json 唯一数据源，剔除 thinking
        history = self.get_enabled_history_messages()
        history = [m for m in history if m.role != Role.THINKING]
        truncated = self._truncate_history(
            history, budget_tokens=app_config.max_history_tokens
        )

        messages: list[dict] = []
        for msg in truncated:
            messages.append({
                "role": msg.role.value if hasattr(msg.role, "value") else msg.role,
                "content": msg.content,
            })

        # 末尾追加部分 assistant 作为 prefix（续写起点）
        prefix_msg: dict = {
            "role": "assistant",
            "content": partial_content or "",
            "prefix": True,
        }
        if partial_thinking:
            prefix_msg["reasoning_content"] = partial_thinking
        messages.append(prefix_msg)

        return LLMContext(
            messages=messages,
            system_prompt=system_prompt,
            model_type=app_config.model_type,
            thinking_enabled=app_config.thinking_enabled,
            max_tokens=app_config.max_tokens,
            temperature=app_config.temperature,
        )

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
        tree_attachment_paths: list[str] | None = None,
    ) -> str:
        """
        构建完整的 system prompt，按以下顺序拼接：

        1. 所有已挂靠的 ContextBlock —— 按**树前序遍历**（root → 子节点，DFS）顺序
           收集整棵树上挂靠到目录/对话节点的启用块，全部拼入（任何对话一致）
        2. 树目录附件文件内容
        3. 默认系统提示（_DEFAULT_SYSTEM_PROMPT，仅当无任何内容时兜底）

        模型约定：每个上下文块都必须挂靠到某个目录或对话节点才能存在；
        不存在"未挂靠的全局块"。

        Args:
            tree_attachment_paths: 树收集到的附件文件路径列表

        Returns:
            拼接完成的 system prompt 字符串
        """
        if tree_attachment_paths is None:
            tree_attachment_paths = []

        parts: list[str] = []

        # ── 1. 所有已挂靠块，按树前序遍历顺序拼接 ──
        for block in self._preorder_context_blocks():
            parts.append(block.content.strip())

        # ── 2. 树目录附件文件 ───────────────
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

        # ── 3. 默认系统提示（兜底）───────────
        # 仅当没有任何上下文内容时追加；只要存在已启用的上下文块 /
        # 附件，默认英文提示便不参与拼接
        # （"可被上下文块覆盖"的兜底语义，与拼接预览保持一致）。
        if not parts:
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
