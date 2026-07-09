# Layer: Core
# File: app/core/conversation_service.py
# Responsibility: 对话业务编排主服务。
#                 串联 ContextService、SearchService、FileService、LLMClient、
#                 MessageRepo（消息持久化）、TreeStore（对话元数据 & 层级），
#                 完整编排"发送消息"、"重新生成"、"对话 CRUD"等流程。
#                 是 Controller 层的直接调用目标。
# Input:  来自 AppController 的业务参数（session_id, text, files 等）
# Output: 领域对象（ConversationNode, ConversationDetail, MessageChunk 流）
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
    LLMClientProtocol,
    MessageRepoProtocol,
    TreeStoreProtocol,
)
from app.core.search_service import SearchService
from app.storage.models import (
    ChunkType,
    ConversationDetail,
    ConversationNode,
    FolderNode,
    MessageNode,
    Message,
    MessageChunk,
    NodeType,
    Role,
    TreeRoot,
    TrashEntry,
)


class ConversationService:
    """
    对话编排主服务（Phase 2 — 基于树形结构）。

    生命周期与状态：
    - 自身不持有"当前对话"状态，session_id 由 Controller 在每次调用时传入。
    - _stop_event 是唯一的实例状态，用于跨异步任务的中止信号。
    - 对话元数据全部存储在 TreeStore（tree.json），消息行存储在 MessageRepo（messages 表）。

    编排流程（send_message）：
        1. 验证会话节点存在（TreeStore.get_node）
        2. 文件提取   → FileService.extract_files()
        3. 搜索       → SearchService.search_and_format()
        4. 构建上下文 → ContextService.build_llm_context()
        5. 保存用户消息 → MessageRepo.save_message()
        6. 调用 LLM  → LLMClient.stream_chat()
        7. 流式转发   → yield MessageChunk
        8. 持久化回复 → MessageRepo.save_message()
        9. 更新对话元数据 → TreeStore.update_node()
    """

    def __init__(
        self,
        message_repo: MessageRepoProtocol,
        tree_store: TreeStoreProtocol,
        llm_client: LLMClientProtocol,
        context_service: ContextService,
        search_service: SearchService,
        file_service: FileService,
    ) -> None:
        self._msg_repo = message_repo
        self._tree = tree_store
        self._llm = llm_client
        self._context_svc = context_service
        self._search_svc = search_service
        self._file_svc = file_service
        self._stop_event = asyncio.Event()

    # ──────────────────────────────────────────
    # 对话生命周期（基于 TreeStore）
    # ──────────────────────────────────────────

    def create_conversation(
        self,
        title: str = "新对话",
        parent_id: str | None = None,
    ) -> str:
        """
        新建一个空对话节点。

        Args:
            title:     对话标题
            parent_id: 父节点 ID，None 表示放在默认"未分类"目录下

        Returns:
            新对话节点的 ID（UUID），同时也是 messages 表的 conversation_id
        """
        if parent_id is None:
            parent_id = "root"

        new_id = str(uuid.uuid4())
        now = datetime.utcnow()
        node = ConversationNode(
            id=new_id,
            parent_id=parent_id,
            title=title,
            summary="",
            message_count=0,
            created_at=now,
            updated_at=now,
        )
        self._tree.create_node(node)

        # 新节点可能改变父级的 "some" 状态
        if parent_id:
            self._tree.recompute_ancestors_enabled(new_id)

        return new_id

    def switch_conversation(self, session_id: str) -> ConversationDetail:
        """
        切换到指定对话，返回完整消息历史。

        Args:
            session_id: 目标对话节点 ID

        Returns:
            ConversationDetail（含 messages 列表）

        Raises:
            ConversationNotFoundError: 节点不存在或不是对话节点
        """
        node = self._tree.get_node(session_id)
        if node is None:
            raise ConversationNotFoundError(session_id)
        if not isinstance(node, ConversationNode):
            raise ConversationNotFoundError(
                f"Node {session_id} is not a conversation"
            )

        messages = self._msg_repo.get_messages(session_id)
        return ConversationDetail(
            id=node.id,
            title=node.title,
            messages=messages,
            created_at=node.created_at,
            updated_at=node.updated_at,
        )

    def get_messages_for_node(self, node_id: str) -> list[Message]:
        """
        以树结构为唯一依据，加载任意节点对应的消息列表。

        规则（纯树引用，不依赖 conversation_id 列）：
        - MessageNode  → 加载该 message_id 对应的单条消息
        - ConversationNode → 收集其下所有子 MessageNode.message_id，
                              批量加载全部消息；若无 MessageNode 子节点
                              （Phase 5 迁移前创建的旧对话），回退到
                              conversation_id 查询
        - FolderNode → 递归收集所有后代 MessageNode.message_id，
                       批量加载全部消息

        Args:
            node_id: 目标节点 ID（任意类型）

        Returns:
            list[Message] — 按 created_at 升序排列的消息列表
        """
        node = self._tree.get_node(node_id)
        if node is None:
            print(f"[CORE ] get_messages_for_node: 节点 {node_id} 不存在")
            return []

        # ── 收集该节点覆盖的 message_id 集合 ──
        if isinstance(node, MessageNode):
            message_ids = [node.message_id]
        elif isinstance(node, ConversationNode):
            message_ids = [
                n.message_id
                for n in self._tree.get_descendants(node_id)
                if isinstance(n, MessageNode)
            ]
            # 回退：若无 MessageNode 子节点（Phase 5 迁移前的旧数据），
            # 使用 conversation_id 查询
            if not message_ids:
                print(f"[CORE ] get_messages_for_node: 对话 {node_id} 无 MessageNode，"
                      f"回退到 conversation_id 查询")
                messages = self._msg_repo.get_messages(node_id)
                messages.sort(key=lambda m: m.created_at)
                return messages
        elif isinstance(node, FolderNode):
            message_ids = [
                n.message_id
                for n in self._tree.get_descendants(node_id)
                if isinstance(n, MessageNode)
            ]
        else:
            return []

        if not message_ids:
            print(f"[CORE ] get_messages_for_node: 节点 {node_id} 无 message_id 可查")
            return []

        messages = self._msg_repo.get_messages_by_ids(message_ids)
        messages.sort(key=lambda m: m.created_at)
        print(f"[CORE ] get_messages_for_node: 节点 {node_id} → {len(messages)} 条消息")
        return messages

    def delete_conversation(
        self,
        conversation_id: str,
        mode: str = "recursive",
    ) -> None:
        """
        软删除对话节点，移入回收站。消息数据保留不删（便于恢复）。

        Args:
            conversation_id: 对话节点 ID
            mode:            "recursive" | "raise"（同 TreeStore.delete_node）
        """
        # 记录父节点 ID 以便删除后重新计算
        node = self._tree.get_node(conversation_id)
        parent_id = node.parent_id if node else None

        self._tree.soft_delete_node(conversation_id, mode)

        # 删除后重新计算祖先的 enabled 状态
        if parent_id:
            self._tree.recompute_ancestors_enabled(parent_id)

    def permanently_delete_conversation(self, conversation_id: str) -> None:
        """
        彻底删除对话：清理消息数据 + 从回收站移除。

        Args:
            conversation_id: 对话节点 ID
        """
        # 清理消息
        self._msg_repo.delete_messages_by_conversation(conversation_id)

        # 检查回收站中是否有该节点的条目，若有则移除
        for entry in self._tree.list_trash():
            node_data = entry.node_data
            if node_data.get("id") == conversation_id:
                self._tree.permanently_delete_from_trash(entry.id)
                break

    def restore_conversation(
        self,
        trash_entry_id: str,
        new_parent_id: str | None = None,
    ) -> None:
        """
        从回收站恢复对话节点。

        Args:
            trash_entry_id: 回收站条目 ID
            new_parent_id:  恢复到的父节点 ID，None 则使用原父级
        """
        self._tree.restore_from_trash(trash_entry_id, new_parent_id)

    def list_conversations(self) -> list[ConversationNode]:
        """
        返回所有对话节点（扁平列表），供侧边栏展示。

        Returns:
            list[ConversationNode] — 所有对话叶子节点
        """
        tree = self._tree.get_tree()
        return [
            n for n in tree.nodes
            if isinstance(n, ConversationNode)
        ]

    def get_effective_enabled_messages(self) -> list[Message]:
        """
        收集树中所有有效启用的 MessageNode 对应的消息，按时间合并。

        遍历所有 MessageNode，对每个节点通过 ancestors 级联规则判断
        是否 effectively enabled，如果是则收集其 message_id。最后批量
        从 SQLite 加载实际消息内容，按 created_at 排序。

        回退：若无任何 MessageNode（Phase 5 迁移前），则收集所有有效
        启用的 ConversationNode，通过 conversation_id 批量查询消息。

        Returns:
            list[Message] — 所有有效启用对话的消息，按 created_at 升序
        """
        all_msg_nodes = self._tree.get_all_message_nodes()

        if not all_msg_nodes:
            # 回退：无 MessageNode，使用 ConversationNode + conversation_id 查询
            tree = self._tree.get_tree()
            conv_nodes = [n for n in tree.nodes if isinstance(n, ConversationNode)]
            enabled_conv_ids: list[str] = []
            for cn in conv_nodes:
                if self._context_svc.is_node_effectively_enabled(cn.id):
                    enabled_conv_ids.append(cn.id)
            if not enabled_conv_ids:
                print("[CORE ] get_effective_enabled_messages: 无启用的对话")
                return []
            print(f"[CORE ] get_effective_enabled_messages: 回退模式，"
                  f"{len(enabled_conv_ids)} 个启用对话")
            messages = self._msg_repo.get_messages_by_conversation_ids(enabled_conv_ids)
            messages.sort(key=lambda m: m.created_at)
            return messages

        # 逐个判断 effective enabled（考虑祖先级联）
        enabled_message_ids: list[str] = []
        for node in all_msg_nodes:
            if self._context_svc.is_node_effectively_enabled(node.id):
                enabled_message_ids.append(node.message_id)

        if not enabled_message_ids:
            print("[CORE ] get_effective_enabled_messages: 无有效启用的 MessageNode")
            return []

        messages = self._msg_repo.get_messages_by_ids(enabled_message_ids)
        messages.sort(key=lambda m: m.created_at)
        print(f"[CORE ] get_effective_enabled_messages: "
              f"{len(enabled_message_ids)} 个 MessageNode → {len(messages)} 条消息")
        return messages

    def get_tree(self) -> TreeRoot:
        """
        返回完整树结构，供 UI 树形展示。

        Returns:
            TreeRoot — 深拷贝，外部修改不影响内部状态
        """
        return self._tree.get_tree()

    def move_conversation(
        self,
        conversation_id: str,
        new_parent_id: str | None = None,
        position: int | None = None,
    ) -> None:
        """
        移动对话到新父级下。

        Args:
            conversation_id: 对话节点 ID
            new_parent_id:   新父节点 ID
            position:        插入位置索引
        """
        self._tree.move_node(conversation_id, new_parent_id, position)

    def update_conversation_meta(
        self,
        conversation_id: str,
        title: str | None = None,
        summary: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        """
        更新对话节点的元数据字段。

        Args:
            conversation_id: 对话节点 ID
            title:           新标题
            summary:         新摘要
            enabled:         启用/禁用状态
        """
        updates: dict = {}
        if title is not None:
            updates["title"] = title
        if summary is not None:
            updates["summary"] = summary
        if enabled is not None:
            updates["enabled"] = enabled
        if updates:
            self._tree.update_node(conversation_id, **updates)

    def get_path(self, conversation_id: str) -> str:
        """
        返回对话节点在树中的路径。

        Args:
            conversation_id: 对话节点 ID

        Returns:
            str — 如 "root/未分类/我的对话"
        """
        return self._tree.get_path(conversation_id)

    # ──────────────────────────────────────────
    # 树节点管理（Phase 4 — 目录/对话通用操作）
    # ──────────────────────────────────────────

    def create_folder(
        self,
        title: str = "新文件夹",
        parent_id: str | None = None,
    ) -> FolderNode:
        """
        创建一个新目录节点。

        Args:
            title:     目录名称
            parent_id: 父节点 ID，None 表示放在根目录下

        Returns:
            新创建的 FolderNode
        """
        if parent_id is None:
            parent_id = "root"

        now = datetime.utcnow()
        folder = FolderNode(
            id=str(uuid.uuid4()),
            parent_id=parent_id,
            title=title,
            created_at=now,
            updated_at=now,
        )
        self._tree.create_node(folder)
        print(f"[TREE] 创建目录: {title} (id={folder.id})")

        # 新节点可能改变父级的 "some" 状态
        if parent_id:
            self._tree.recompute_ancestors_enabled(folder.id)

        return folder

    def rename_node(self, node_id: str, new_title: str) -> None:
        """
        重命名任意节点（目录或对话）。

        Args:
            node_id:   目标节点 ID
            new_title: 新名称
        """
        if not new_title.strip():
            return
        self._tree.update_node(node_id, title=new_title.strip())
        print(f"[TREE] 重命名节点 {node_id} -> {new_title}")

    def toggle_enabled(self, node_id: str) -> None:
        """
        切换节点的启用状态（含完整双向级联）：
        1. 确定新目标状态：True → False，False/"some" → True
        2. Top-down：递归设置所有后代为同一新状态
        3. Bottom-up：自底向上重新计算所有祖先的 enabled 状态

        Args:
            node_id: 目标节点 ID
        """
        node = self._tree.get_node(node_id)
        if node is None:
            return

        new_state = node.enabled is not True  # True→False, False/"some"→True

        # Step 1: Top-down 级联到所有后代
        affected = self._tree.set_node_enabled_cascade_down(node_id, new_state)

        # Step 2: Bottom-up 重新计算祖先
        changed_ancestors = self._tree.recompute_ancestors_enabled(node_id)

        state_label = "True" if new_state else "False"
        print(
            f"[TREE] 级联切换 {node_id}: -> {state_label}"
            f"（受影响: {len(affected)} 个节点, 祖先变化: {len(changed_ancestors)}）"
        )

    def update_folder_context(
        self,
        folder_id: str,
        context_block_ids: list[str],
    ) -> None:
        """
        更新目录关联的 ContextBlock ID 列表。

        Args:
            folder_id:          目录节点 ID
            context_block_ids:  新的 ContextBlock ID 列表
        """
        node = self._tree.get_node(folder_id)
        if node is None:
            return
        if not isinstance(node, FolderNode):
            print(f"[TREE] update_folder_context: {folder_id} 不是目录节点")
            return
        self._tree.update_node(folder_id, context_block_ids=context_block_ids)
        print(f"[TREE] 更新目录上下文块 {folder_id}: {len(context_block_ids)} 个块")

    def attach_file(self, folder_id: str, file_path: str) -> None:
        """
        将文件路径挂载到目录节点。

        Args:
            folder_id: 目录节点 ID
            file_path: 文件绝对路径
        """
        node = self._tree.get_node(folder_id)
        if node is None:
            return
        if not isinstance(node, FolderNode):
            print(f"[TREE] attach_file: {folder_id} 不是目录节点")
            return
        paths: list[str] = list(node.attachment_paths)
        if file_path not in paths:
            paths.append(file_path)
            self._tree.update_node(folder_id, attachment_paths=paths)
            print(f"[TREE] 挂载附件到 {folder_id}: {file_path}")

    def detach_file(self, folder_id: str, file_path: str) -> None:
        """
        从目录节点移除文件路径。

        Args:
            folder_id: 目录节点 ID
            file_path: 要移除的文件路径
        """
        node = self._tree.get_node(folder_id)
        if node is None:
            return
        if not isinstance(node, FolderNode):
            print(f"[TREE] detach_file: {folder_id} 不是目录节点")
            return
        paths: list[str] = list(node.attachment_paths)
        if file_path in paths:
            paths.remove(file_path)
            self._tree.update_node(folder_id, attachment_paths=paths)
            print(f"[TREE] 移除附件从 {folder_id}: {file_path}")

    def get_trash_entries(self) -> list[TrashEntry]:
        """
        返回回收站中所有条目。

        Returns:
            list[TrashEntry]
        """
        return self._tree.list_trash()

    def get_node(self, node_id: str):
        """
        按 ID 获取任意节点（目录或对话）。

        Args:
            node_id: 节点唯一 ID

        Returns:
            AnyTreeNode | None
        """
        return self._tree.get_node(node_id)

    def permanently_delete_from_trash(self, trash_entry_id: str) -> None:
        """
        从回收站彻底删除条目（含关联消息数据清理）。

        对于对话节点：先清理 messages 表中数据，再移除回收站条目。
        对于目录节点：直接移除回收站条目（子节点的回收站条目需单独处理）。

        Args:
            trash_entry_id: 回收站条目 ID
        """
        tree = self._tree
        trash_list = tree.list_trash()
        for entry in trash_list:
            if entry.id == trash_entry_id:
                node_data = entry.node_data
                if node_data.get("node_type") == "conversation":
                    conv_id = node_data.get("id", "")
                    if conv_id:
                        try:
                            self._msg_repo.delete_messages_by_conversation(conv_id)
                        except Exception as ex:
                            print(f"[TREE] 清理消息失败 {conv_id}: {ex}")
                tree.permanently_delete_from_trash(trash_entry_id)
                return
        # 未找到匹配条目（可能已被并发删除），静默忽略
        tree.permanently_delete_from_trash(trash_entry_id)

    def clear_trash(self) -> int:
        """
        清空回收站（永久删除所有条目及其关联消息数据）。

        先清空回收站文件（确保即使后续消息清理失败，回收站状态已更新），
        再清理关联的消息数据（消息清理失败仅打印日志，不影响整体流程）。

        Returns:
            int — 清除的条目数量
        """
        trash = self._tree.list_trash()
        count = self._tree.clear_trash()
        # 清空回收站后清理消息数据
        for entry in trash:
            node_data = entry.node_data
            if node_data.get("node_type") == "conversation":
                conv_id = node_data.get("id", "")
                if conv_id:
                    try:
                        self._msg_repo.delete_messages_by_conversation(conv_id)
                    except Exception as ex:
                        print(f"[TREE] 清理消息失败 {conv_id}: {ex}")
        return count

    # ──────────────────────────────────────────
    # 消息生成（保留流式异步编排）
    # ──────────────────────────────────────────

    async def send_message(
        self,
        session_id: str,
        text: str,
        files: list[str],
    ) -> AsyncGenerator[MessageChunk, None]:
        """
        发送用户消息并流式返回 LLM 回复。

        Args:
            session_id: 当前对话节点 ID
            text:       用户输入文本
            files:      附件文件路径列表（可为空）

        Yields:
            MessageChunk — 流式块，is_done=True 为结束信号

        Raises:
            ConversationNotFoundError: 会话节点不存在或不是对话节点
        """
        self._stop_event.clear()

        # ── Step 0: 验证会话节点存在且为对话类型 ──
        node = self._tree.get_node(session_id)
        if node is None:
            raise ConversationNotFoundError(session_id)
        if not isinstance(node, ConversationNode):
            raise ConversationNotFoundError(
                f"Node {session_id} is not a conversation"
            )

        # ── Step 1: 文件提取 ─────────────────────
        file_text = ""
        if files:
            extracted = await self._file_svc.extract_files(files)
            parts = []
            for filename, content in extracted:
                if content.strip():
                    parts.append(f"[文件：{filename}]\n{content.strip()}")
            if parts:
                file_text = "\n\n".join(parts)

        # ── Step 2: 拼合文件内容到用户文本 ──────
        full_user_text = text
        if file_text:
            full_user_text = (
                f"{file_text}\n\n[用户问题]\n{text}" if text else file_text
            )

        # ── Step 3: 搜索 ─────────────────────────
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
            token_count=len(text) // 4,
        )
        self._msg_repo.save_message(user_msg)

        # Phase 5: 创建对应的 MessageNode 到树中
        user_msg_node = MessageNode(
            id=user_msg.id,
            parent_id=session_id,
            message_id=user_msg.id,
            role=Role.USER.value,
            preview=text[:60] if text else "",
            title=f"User: {text[:30]}" if text else "User message",
            enabled=True,
        )
        self._tree.create_node(user_msg_node)

        # ── Step 6 & 7: 调用 LLM，流式转发 ──────
        assistant_msg_id = str(uuid.uuid4())
        full_content_parts: list[str] = []
        thinking_parts: list[str] = []

        async for chunk in self._llm.stream_chat(llm_context):
            if self._stop_event.is_set():
                yield MessageChunk(
                    delta="", is_done=True, message_id=assistant_msg_id
                )
                break

            chunk.message_id = assistant_msg_id

            if chunk.chunk_type == ChunkType.THINKING:
                thinking_parts.append(chunk.delta)
            else:
                full_content_parts.append(chunk.delta)

            if chunk.is_done:
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
                    self._msg_repo.save_message(assistant_msg)

                    # Phase 5: 创建 assistant MessageNode
                    assistant_preview = full_content[:60] if full_content else ""
                    asst_node = MessageNode(
                        id=assistant_msg_id,
                        parent_id=session_id,
                        message_id=assistant_msg_id,
                        role=Role.ASSISTANT.value,
                        preview=assistant_preview,
                        title=f"Asst: {assistant_preview[:30]}" if assistant_preview else "Assistant message",
                        enabled=True,
                    )
                    self._tree.create_node(asst_node)

                    if thinking_parts:
                        thinking_content = "".join(thinking_parts)
                        thinking_msg = Message(
                            id=str(uuid.uuid4()),
                            conversation_id=session_id,
                            role=Role.THINKING,
                            content=thinking_content,
                            is_thinking=True,
                            created_at=datetime.utcnow(),
                            token_count=len(thinking_content) // 4,
                        )
                        self._msg_repo.save_message(thinking_msg)

                        # Phase 5: 创建 thinking MessageNode
                        thinking_preview = thinking_content[:60]
                        think_node = MessageNode(
                            id=thinking_msg.id,
                            parent_id=session_id,
                            message_id=thinking_msg.id,
                            role=Role.THINKING.value,
                            preview=thinking_preview,
                            title=f"Think: {thinking_preview[:30]}" if thinking_preview else "Thinking",
                            enabled=True,
                        )
                        self._tree.create_node(think_node)

                # Step 9: 更新对话元数据（标题 & 摘要）
                self._update_meta_after_reply(
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

        Args:
            session_id: 当前对话节点 ID
            message_id: 要替换的消息 ID（可为空，自动取最后一条 assistant 消息）

        Yields:
            MessageChunk — 同 send_message
        """
        self._stop_event.clear()

        # 定位并删除最后一条 assistant 消息（含 MessageNode）
        target_id = message_id or self._find_last_assistant_message(session_id)
        if target_id:
            self._msg_repo.delete_message(target_id)
            # Phase 5: 也从树中删除对应的 MessageNode
            try:
                target_node = self._tree.get_node(target_id)
                if target_node is not None:
                    self._tree.soft_delete_node(target_id, mode="recursive")
            except Exception:
                pass  # 节点可能已不存在（容错）

        # 取倒数第一条用户消息作为重新生成的输入
        messages = self._msg_repo.get_messages(session_id)
        last_user = next(
            (m for m in reversed(messages) if m.role == Role.USER), None
        )
        user_text = last_user.content if last_user else ""

        # 复用 send_message 的编排流程
        llm_context = self._context_svc.build_llm_context(
            conversation_id=session_id,
            new_user_message=user_text,
            injected_search_text="",
        )

        new_msg_id = str(uuid.uuid4())
        full_parts: list[str] = []

        async for chunk in self._llm.stream_chat(llm_context):
            if self._stop_event.is_set():
                yield MessageChunk(
                    delta="", is_done=True, message_id=new_msg_id
                )
                break
            chunk.message_id = new_msg_id
            full_parts.append(chunk.delta)
            yield chunk
            if chunk.is_done:
                break

        full_content = "".join(full_parts)
        if full_content:
            self._msg_repo.save_message(Message(
                id=new_msg_id,
                conversation_id=session_id,
                role=Role.ASSISTANT,
                content=full_content,
                created_at=datetime.utcnow(),
                token_count=len(full_content) // 4,
            ))

            # Phase 5: 为新生成的回复创建 MessageNode
            preview = full_content[:60]
            regen_node = MessageNode(
                id=new_msg_id,
                parent_id=session_id,
                message_id=new_msg_id,
                role=Role.ASSISTANT.value,
                preview=preview,
                title=f"Asst: {preview[:30]}" if preview else "Assistant message",
                enabled=True,
            )
            self._tree.create_node(regen_node)

        self._update_meta_after_reply(session_id, user_text, full_content)

    def stop_generation(self) -> None:
        """
        设置中止信号，流式生成循环在下一次 yield 前检测并退出。
        同时调用 LLM 客户端的 abort() 关闭底层 HTTP 连接。
        """
        self._stop_event.set()
        self._llm.abort()

    # ──────────────────────────────────────────
    # 内部编排辅助
    # ──────────────────────────────────────────

    def _find_last_assistant_message(self, session_id: str) -> str | None:
        """返回最后一条 assistant 消息的 ID，找不到返回 None。"""
        messages = self._msg_repo.get_messages(session_id)
        for msg in reversed(messages):
            if msg.role == Role.ASSISTANT:
                return msg.id
        return None

    def _update_meta_after_reply(
        self,
        session_id: str,
        user_text: str,
        assistant_text: str,
    ) -> None:
        """
        回复后更新对话节点的元数据：
        - 首次对话：用用户输入前 20 字作为标题
        - 摘要：用助手最新回复的前 100 字
        - 消息计数：重新统计
        """
        node = self._tree.get_node(session_id)
        if node is None or not isinstance(node, ConversationNode):
            return

        updates: dict = {}

        # 首次对话：自动生成标题
        if node.title in ("新对话", "") and user_text:
            updates["title"] = (
                user_text[:20] + ("..." if len(user_text) > 20 else "")
            )

        # 更新摘要
        preview = (assistant_text or user_text)[:100]
        preview = preview + ("..." if len(preview) == 100 else "")
        updates["summary"] = preview

        # 更新消息计数
        messages = self._msg_repo.get_messages(session_id)
        updates["message_count"] = len(messages)

        self._tree.update_node(session_id, **updates)


# ──────────────────────────────────────────────
# 领域异常
# ──────────────────────────────────────────────

class ConversationNotFoundError(Exception):
    """对话不存在时抛出，由 Controller 层捕获并处理。"""
    def __init__(self, session_id: str) -> None:
        super().__init__(f"Conversation not found: {session_id}")
        self.session_id = session_id
