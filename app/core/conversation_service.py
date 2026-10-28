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
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import AsyncGenerator

import config as app_config
from app.core.branch_service import BranchService
from app.core.context_service import ContextService
from app.core import image_attachments, message_attachments
from app.core.file_service import FileService
from app.core.protocols import (
    LLMClientProtocol,
    MessageRepoProtocol,
    TreeStoreProtocol,
)
from app.core.search_service import SearchService
from app.storage.blob_store import (
    MAX_BLOB_BYTES,
    MAX_NODE_BYTES,
    BlobTooLarge,
    node_mounts_bytes,
)
from app.storage.branch_store import BranchStore
from app.storage.models import (
    ChunkType,
    ConversationDetail,
    ConversationNode,
    FolderNode,
    LLMContext,
    MessageNode,
    Message,
    MessageChunk,
    MessageSearchResult,
    NodeType,
    Role,
    TreeRoot,
    TrashEntry,
    utcnow,
)


# 单轮工具调用的最大子请求次数。超过后提示模型收尾，避免无限循环。
MAX_TOOL_ROUNDS = 5

# 读进内存前的源文件上限。服务端单图上限 32 MiB，超过它连解码都没必要。
_MAX_SOURCE_IMAGE_BYTES = 32 * 1024 * 1024

# 从文档里"挑"图时的扫描上限（不是发送量，发送量由 MAX_IMAGES_PER_REQUEST 约束）。
# 要能挑出最大的几张，就必须先把候选都读出来看看尺寸。
_MAX_DOCX_IMAGE_SCAN = 40


def _document_image_note(total: int, picked: list[dict]) -> str:
    """
    告诉模型：这份文档里的图，这次发出去几张、还剩几张。

    没有这句话时，模型看到正文里的 "[图片 6]" 却只有几张贴图，很容易顺势编造。
    """
    if not total:
        return ""
    if not picked:
        return (
            f"[图片] 文档里有 {total} 张图片，本次一张都没发送"
            "（格式不受支持，或单条消息的图片额度已用完）"
        )

    names = "、".join(str(a.get("name") or "") for a in picked)
    note = f"[图片] 本次随消息附上 {len(picked)} 张：{names}"
    remaining = total - len(picked)
    if remaining > 0:
        note += (
            f"；另有 {remaining} 张未发送"
            f"（每条消息最多 {image_attachments.MAX_IMAGES_PER_REQUEST} 张，优先发清晰的）"
        )
    return note


def _describe_tool_call(call: dict) -> str:
    """把一次工具调用翻成一行给用户看的文字（只用于展示，不进入正文）。"""
    fn = call.get("function") or {}
    name = fn.get("name") or ""
    try:
        args = json.loads(fn.get("arguments") or "{}")
        if not isinstance(args, dict):
            args = {}
    except (json.JSONDecodeError, ValueError):
        args = {}

    if name == "search_files":
        query = str(args.get("query") or "").strip()
        text = f"🔍 检索「{query}」" if query else "🔍 浏览文件列表"
        if args.get("in_content"):
            text += "（含内容）"
        return text
    if name == "read_file":
        path = str(args.get("path") or "")
        return f"📄 读取 {os.path.basename(path)}" if path else "📄 读取文件"
    return f"🔧 {name or '工具调用'}"


@dataclass
class _TurnBuffer:
    """一次用户轮次的累积结果，跨工具子请求共享。"""
    content: list[str] = field(default_factory=list)
    thinking: list[str] = field(default_factory=list)
    tool_events: list[dict] = field(default_factory=list)
    completed: bool = False


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
        branch_service: BranchService | None = None,
        file_tools=None,
        read_log=None,
        blob_store=None,
    ) -> None:
        self._msg_repo = message_repo
        self._tree = tree_store
        self._llm = llm_client
        self._context_svc = context_service
        self._search_svc = search_service
        self._file_svc = file_service
        self._file_tools = file_tools      # FileToolService | None（未装配则无工具能力）
        # 工具读取记录（D3）：落 SQLite，重启后仍在。未装配时退化为内存列表，
        # 保证只装配部分依赖的测试仍能跑。
        self._read_log = read_log
        self._memory_reads: dict[str, list[dict]] = {}
        # 节点挂载的文件快照仓库（D2）。未注入时挂载退化为"只记路径"。
        self._blobs = blob_store
        self._stop_event = asyncio.Event()
        # 分叉服务（未显式注入时按依赖自动构造，兼容既有测试）
        self._branch_svc = branch_service or BranchService(
            tree_store=tree_store,
            branch_store=BranchStore(),
            message_repo=message_repo,
        )

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
            parent_id: 父节点 ID。
                       None → 树顶层（parent_id=null，真正的根目录）。
                       指定文件夹 id → 在该文件夹内创建（如右键菜单）。
                       注意：id="root" 的"未分类"文件夹只是普通文件夹，不是根目录。

        Returns:
            新对话节点的 ID（UUID），同时也是 messages 表的 conversation_id
        """
        new_id = str(uuid.uuid4())
        now = utcnow()
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

        # 新节点可能改变父级的 "some" 状态（顶层节点 parent_id=None 无祖先可更新）
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

        # 以 tree.json 为准加载消息（已删除/禁用的消息不会出现）
        messages = self.get_messages_for_node(session_id)
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

        消息按 DFS 前序遍历（树结构顺序）排列。

        Args:
            node_id: 目标节点 ID（任意类型）

        Returns:
            list[Message] — 按 DFS 前序遍历排列的消息列表
        """
        node = self._tree.get_node(node_id)
        if node is None:
            print(f"[CORE ] get_messages_for_node: 节点 {node_id} 不存在")
            return []

        # ── 收集该节点覆盖的 message_id 集合 ──
        message_nodes: list[MessageNode] = []
        if isinstance(node, MessageNode):
            message_nodes = [node]
        elif isinstance(node, (ConversationNode, FolderNode)):
            message_nodes = [
                n for n in self._tree.get_descendants(node_id)
                if isinstance(n, MessageNode)
            ]
            # 回退：ConversationNode 无 MessageNode 子节点（Phase 5 迁移前的旧数据），
            # 使用 conversation_id 查询
            if isinstance(node, ConversationNode) and not message_nodes:
                print(f"[CORE ] get_messages_for_node: 对话 {node_id} 无 MessageNode，"
                      f"回退到 conversation_id 查询")
                messages = self._msg_repo.get_messages(node_id)
                return messages  # 旧数据保持原顺序
        else:
            return []

        if not message_nodes:
            print(f"[CORE ] get_messages_for_node: 节点 {node_id} 无 message_id 可查")
            return []

        message_ids = [n.message_id for n in message_nodes]
        # Phase 6: 收集 assistant 节点绑定的 thinking 行 id（其内容仍存 messages 表）
        thinking_ids = [
            n.thinking_message_id for n in message_nodes if n.thinking_message_id
        ]

        # ── 按 DFS 前序遍历排序 ──
        ordered_ids = self._tree.get_message_ids_in_tree_order(node_id)
        id_order: dict[str, int] = {mid: i for i, mid in enumerate(ordered_ids)}

        all_ids = message_ids + thinking_ids
        messages = self._msg_repo.get_messages_by_ids(all_ids)
        by_id = {m.id: m for m in messages}
        # 主消息（user/assistant/遗留 thinking 节点）按树序排列
        main_msgs = [
            by_id[mid]
            for mid in sorted(message_ids, key=lambda i: id_order.get(i, 999999))
            if mid in by_id
        ]
        result = self._interleave_bound_thinking(message_nodes, main_msgs, by_id)
        print(f"[CORE ] get_messages_for_node: 节点 {node_id} → "
              f"{len(main_msgs)} 条消息 + {len(result) - len(main_msgs)} 条绑定 thinking（树序遍历）")
        return result

    @staticmethod
    def _interleave_bound_thinking(
        message_nodes: list[MessageNode],
        ordered_msgs: list[Message],
        by_id: dict[str, Message],
    ) -> list[Message]:
        """
        Phase 6: 将 assistant 节点绑定的 thinking 行穿插到对应 assistant 之前。

        仅调整主消息顺序；无绑定则原样返回。
        """
        bound = {
            n.message_id: n.thinking_message_id
            for n in message_nodes
            if n.thinking_message_id
        }
        if not bound:
            return ordered_msgs
        result: list[Message] = []
        for m in ordered_msgs:
            if m.role == Role.ASSISTANT and m.id in bound:
                t = by_id.get(bound[m.id])
                if t is not None:
                    result.append(t)
            result.append(m)
        return result

    def delete_conversation(
        self,
        conversation_id: str,
        mode: str = "recursive",
    ) -> None:
        """
        软删除节点（移入回收站）。分支感知（spec 3.3）：

        - 消息节点是被修改节点 → 硬删除 + 分支删除级联（不进回收站，拍板 3）
        - 消息节点是分叉点 → 分叉数据转交前驱后按普通节点软删除（3.3.4）
        - 对话/文件夹 → 子树内所有对话的分支数据一并删除（3.3.1 补充说明），
          恢复后成为普通对话

        Args:
            conversation_id: 节点 ID（对话 / 文件夹 / 消息）
            mode:            "recursive" | "raise"（同 TreeStore.delete_node）
        """
        node = self._tree.get_node(conversation_id)
        parent_id = node.parent_id if node else None

        if isinstance(node, MessageNode):
            self._delete_message_node(node)
        elif isinstance(node, (ConversationNode, FolderNode)):
            # 子树中的对话：分支数据一并删除（3.3.1 补充说明）
            subtree = [node] + self._tree.get_descendants(conversation_id)
            for n in subtree:
                if isinstance(n, ConversationNode):
                    self._branch_svc.delete_conversation_branches(n.id)
            self._tree.soft_delete_node(conversation_id, mode)

        # 删除后重新计算祖先的 enabled 状态
        if parent_id:
            self._tree.recompute_ancestors_enabled(parent_id)

    def _delete_message_node(self, node: MessageNode) -> None:
        """
        分支感知的消息节点删除（spec 3.3）。

        - 被修改节点（分叉点后继）：硬删除——分支记录删除 + 链自动切换 +
          消息行物理清理，不进入回收站（3.3.1 删除方式，用户拍板 3）
        - 分叉点：分叉数据整体转交前驱（3.3.4），自身按普通节点软删除
        - 普通节点：走现有软删除流程
        """
        fp = self._tree.find_fork_point_of(node)
        if fp is not None:
            fork_id, kind = fp
            conv_id = node.parent_id
            if kind == "conversation":
                fork_id = conv_id
            fp_node = self._tree.get_node(fork_id)
            branch_index = fp_node.fork_current_index if fp_node else 0
            # 硬删除：分支记录删除 + 链重建（节点随链重建移除）+ 孤儿行物理清理
            self._branch_svc.delete_branch(conv_id, fork_id, branch_index)
            return
        if self._tree.is_fork_point_node(node):
            # 分叉点：分叉身份与数据转交前驱，自身按普通节点删除
            self._branch_svc.delete_fork_point(node.parent_id, node.id)
            self._tree.soft_delete_node(node.id, mode="recursive")
            return
        self._tree.soft_delete_node(node.id, mode="recursive")

    def permanently_delete_conversation(self, conversation_id: str) -> None:
        """
        彻底删除对话：清理消息数据 + 从回收站移除。

        Args:
            conversation_id: 对话节点 ID
        """
        # 清理消息
        self._msg_repo.delete_messages_by_conversation(conversation_id)

        # 清理工具读取记录（软删除不动它：回收站恢复后记录还在）
        if self._read_log is not None:
            try:
                self._read_log.delete_by_conversation(conversation_id)
            except Exception as e:
                print(f"[CORE] 清理读取记录失败: {e}")
        self._memory_reads.pop(conversation_id, None)

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

        恢复的节点清除分叉标记（3.3.1 补充说明：分支数据已在删除时清除，
        恢复后为普通节点/普通对话）。

        Args:
            trash_entry_id: 回收站条目 ID
            new_parent_id:  恢复到的父节点 ID，None 则使用原父级
        """
        # 先查条目，确定恢复节点的 ID（恢复后节点已进树）
        restored_id: str | None = None
        for entry in self._tree.list_trash():
            if entry.id == trash_entry_id:
                restored_id = entry.node_data.get("id")
                break

        self._tree.restore_from_trash(trash_entry_id, new_parent_id)

        if restored_id is not None:
            restored = self._tree.get_node(restored_id)
            if isinstance(restored, (ConversationNode, MessageNode)):
                self._tree.update_node(
                    restored_id,
                    is_fork_point=False,
                    fork_branch_count=0,
                    fork_current_index=0,
                )

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
        收集树中所有有效启用的 MessageNode 对应的消息，按 DFS 前序遍历排列。

        与 `ContextService.get_enabled_history_messages()` 共用同一套
        tree.json 驱动逻辑（唯一数据源），保证前端展示与侧边栏一致。
        此处包含 incomplete 节点：暂停轮次的 user 消息在重绘后仍显示
        （LLM 上下文则通过默认 include_incomplete=False 排除）。
        Phase 6 起同时附带绑定 thinking（include_thinking=True），
        使聚合时间线仍能展示每条 assistant 的思维链。

        Returns:
            list[Message] — 所有有效启用对话的消息，按 DFS 前序遍历排列
        """
        return self._context_svc.get_enabled_history_messages(
            include_incomplete=True, include_thinking=True
        )

    def find_last_enabled_conversation_id(self) -> str | None:
        """
        返回树中最后一个有效启用 MessageNode 所在的 ConversationNode id。

        发送消息时应将新内容插入到该对话结尾，使聚合时间线在最新内容处自然延续。
        若树中无任何有效启用的消息，返回 None。

        Returns:
            str | None — 目标对话 id；无启用消息时为 None
        """
        enabled = self._context_svc.get_enabled_history_messages()
        if not enabled:
            return None
        last_msg = enabled[-1]  # DFS 前序的最后一个 = 树中最后一个启用消息
        node_map = {n.message_id: n for n in self._tree.get_all_message_nodes()}
        node = node_map.get(last_msg.id)
        if node is None or not node.parent_id:
            return None
        parent = self._tree.get_node(node.parent_id)
        if isinstance(parent, ConversationNode):
            return parent.id
        return None

    def find_latest_user_message_id(self, session_id: str) -> str | None:
        """
        返回指定对话下最近一条 user MessageNode 的 id。

        用于标记未完成轮次：停止生成时，树中只有用户消息节点存在，
        该节点即未完成轮次的标记目标。

        Args:
            session_id: 对话节点 ID

        Returns:
            str | None — 最近一条 user 消息的 id
        """
        messages = self.get_messages_for_node(session_id)
        for msg in reversed(messages):
            if msg.role == Role.USER:
                return msg.id
        return None

    # ──────────────────────────────────────────
    # 分叉操作（spec 3.5 等）
    # ──────────────────────────────────────────

    def switch_branch(
        self, conversation_id: str, fork_point_id: str, target_index: int
    ) -> list[MessageNode]:
        """
        切换分支（3.5）：同步当前状态 → 读取目标分支 → 嵌套展开 → 重建链。

        Args:
            conversation_id:  对话节点 ID
            fork_point_id:    分叉点 ID（消息节点或对话节点）
            target_index:     目标分支编号（0 起）

        Returns:
            重建后的完整链路（MessageNode 列表）
        """
        return self._branch_svc.switch_branch(
            conversation_id, fork_point_id, target_index
        )

    def is_modified_node(self, node_id: str) -> bool:
        """节点是否为"被修改节点"（分叉点后继；删除时触发分支删除，3.3.1）。"""
        node = self._tree.get_node(node_id)
        if node is None:
            return False
        return self._tree.find_fork_point_of(node) is not None

    def get_predecessor(
        self, conversation_id: str, node_id: str
    ) -> MessageNode | None:
        """返回节点在同一对话消息链中的前一个节点（无则 None）。"""
        return self._tree.get_predecessor(conversation_id, node_id)

    # ──────────────────────────────────────────
    # 分支感知拖拽（spec 3.6）
    # ──────────────────────────────────────────

    def move_message_with_fork(
        self,
        dragged_id: str,
        new_parent_id: str | None,
        position: int | None,
        prev_id: str | None = None,
        next_id: str | None = None,
    ) -> bool:
        """
        分支感知的消息拖拽移动（3.6 组合表）。

        prev_id/next_id：目标插入点前后的消息节点 ID（TreePanel 计算；
        仅在目标位于消息链中时有效，否则为 None）。

        处理矩阵：
        - 被修改节点（同对话）→ ❌ 拒绝（返回 False，拍板 3）
        - 拖到"分叉点与被修改节点之间" → 🔀 插入节点成为新分叉点，
          原分叉点退化，分支数据迁移（3.6.1 原则 3）
        - 分叉点移走 → ✅ 数据原地保留，re-anchor 到补位后的新前驱（3.6.2）
        - 跨对话被修改节点 → 🔄 整体迁移（X 及尾链，拍板 2；
          目标分叉点 = 目标位置前驱，拍板 1）
        - 普通节点 → ✅ 纯移动（同步时分支记录自动更新）
        - 对话/文件夹 → ✅ 纯移动（分支数据按对话 ID 保留）
        """
        node = self._tree.get_node(dragged_id)
        if node is None:
            return False
        # 对话 / 文件夹：纯移动（分支数据按对话 ID 保留，不随节点移动）
        if not isinstance(node, MessageNode):
            self._tree.move_node(dragged_id, new_parent_id, position)
            return True

        # 消息节点只能存在于对话下：目标必须是对话节点（防御——
        # 验证层已拒绝文件夹/根级目标，此处兜底避免分支数据错乱）
        if new_parent_id is None:
            return False
        target = self._tree.get_node(new_parent_id)
        if target is None or not isinstance(target, ConversationNode):
            return False

        dragged_conv = node.parent_id
        is_modified = self._tree.find_fork_point_of(node) is not None
        target_conv = new_parent_id

        # 被修改节点（同对话）→ 全拒绝（3.6.1，拍板 3）
        if is_modified and target_conv == dragged_conv:
            return False

        # 目标位置上下文："之间" = 前驱是分叉点且后继是它的被修改节点
        between_fork_id = self._between_fork_id(prev_id, next_id)

        if between_fork_id is not None:
            # 🔀 "之间"插入：插入节点成为新分叉点，原分叉点退化（3.6.1 原则 3）
            if is_modified:
                # 跨对话被修改节点 → 🔄 整体迁移到当前分叉
                return self._transfer_modified_group(
                    node, between_fork_id, target_conv, position
                )
            if node.is_fork_point:
                # 分叉点（其他分叉）移入"之间"：原数据 re-anchor 后继承目标分叉数据
                self._reanchor_fork_point_after_move(node, dragged_conv)
            self._move_message_node(dragged_id, target_conv, position)
            self._branch_svc.migrate_branch_data(
                target_conv, between_fork_id, dragged_id
            )
            return True

        # 非"之间"位置
        if is_modified:
            # 跨对话被修改节点 → 🔄 整体迁移（拍板 1：前驱成为新分叉点；
            # 无前驱 → 对话节点承接）
            target_fork = prev_id if prev_id else target_conv
            return self._transfer_modified_group(
                node, target_fork, target_conv, position
            )

        if node.is_fork_point:
            # 分叉点移走：数据原地保留，re-anchor 到补位后的新前驱（3.6.2）
            self._reanchor_fork_point_after_move(node, dragged_conv)
            self._move_message_node(dragged_id, target_conv, position)
            return True

        # 普通消息节点：纯移动
        self._move_message_node(dragged_id, target_conv, position)
        return True

    def _between_fork_id(self, prev_id: str | None, next_id: str | None) -> str | None:
        """
        判定插入点是否位于"分叉点与被修改节点之间"（3.6.1 目的地）。

        条件：插入点前驱是分叉点 F，且后继是 F 的被修改节点。
        """
        if not prev_id or not next_id:
            return None
        prev = self._tree.get_node(prev_id)
        if prev is None or not prev.is_fork_point:
            return None
        next_node = self._tree.get_node(next_id)
        if next_node is None:
            return None
        next_fp = self._tree.find_fork_point_of(next_node)
        if next_fp is not None and next_fp[0] == prev_id:
            return prev_id
        return None

    def _move_message_node(
        self, node_id: str, target_conv: str | None, position: int | None
    ) -> None:
        """移动消息节点并同步分支数据（脏标记由 move_node 自动完成）。"""
        self._tree.move_node(node_id, target_conv, position)
        # 移动改变了链结构：刷新当前分支记录（对涉及对话统一同步）
        self._branch_svc.sync_dirty_conversations()

    def _reanchor_fork_point_after_move(
        self, fork_node: MessageNode, source_conv: str
    ) -> None:
        """分叉点被移走：分支数据原地保留，re-anchor 到补位后的新前驱（3.6.2）。

        新分叉点 = 被修改节点的前驱（补位后紧挨着被修改节点的节点）；
        分叉点是第一条消息时 → 对话节点。
        """
        chain = self._tree.get_conversation_chain(source_conv)
        pos = next(
            (i for i, n in enumerate(chain) if n.id == fork_node.id), None
        )
        if pos is None:
            return
        new_fork_id = chain[pos - 1].id if pos > 0 else source_conv
        self._branch_svc.migrate_branch_data(source_conv, fork_node.id, new_fork_id)

    def _transfer_modified_group(
        self,
        node: MessageNode,
        target_fork_id: str,
        target_conv: str,
        position: int | None,
    ) -> bool:
        """
        跨对话被修改节点整体迁移（3.6.1 🔄，拍板 1/2）。

        - X 及其后同分支尾链一起移入目标对话（拍板 2）
        - 目标分叉点 = 目标位置前驱（普通位置时前驱成为新分叉点，
          拍板 1；"之间"时为该分叉点）
        - 源对话链回退到 X 之前；分支数据整体迁移到目标分叉点名下
        """
        source_conv = node.parent_id
        fp = self._tree.find_fork_point_of(node)
        if fp is None:
            return False
        fork_id, _kind = fp

        src_chain = self._tree.get_conversation_chain(source_conv)
        x_pos = next(
            (i for i, n in enumerate(src_chain) if n.id == node.id), None
        )
        if x_pos is None:
            return False
        tail = src_chain[x_pos:]  # X 及尾链（树副本，携带分叉字段）

        # 1. 源对话链回退到 X 之前
        self._tree.replace_conversation_chain(source_conv, src_chain[:x_pos])
        # 2. 分支数据整体迁移（源 F → 目标分叉点，源侧记录删除）
        self._branch_svc.transfer_branch_group(
            source_conv, fork_id, target_conv, target_fork_id
        )
        # 3. 目标对话链：X + 尾链插入目标位置
        tgt_chain = self._tree.get_conversation_chain(target_conv)
        insert_pos = position if position is not None else len(tgt_chain)
        insert_pos = min(max(insert_pos, 0), len(tgt_chain))
        new_tgt_chain = (
            tgt_chain[:insert_pos] + tail + tgt_chain[insert_pos:]
        )
        self._tree.replace_conversation_chain(target_conv, new_tgt_chain)
        # 4. 统一同步（刷新目标对话所有分叉点的当前分支记录）+ 清脏
        self._branch_svc.sync_dirty_conversations()
        return True

    def get_fork_info_map(self) -> dict[str, tuple[str, int, int, str]]:
        """
        返回全部"被修改节点"的分叉展示信息。

        Returns:
            {message_id: (conversation_id, m, n, fork_point_id)}
            - m 为 1 起的展示编号（存储 0 起 + 1）
            - 仅包含被修改节点（其前驱是分叉点；对话第一条消息时前驱为对话节点）
        """
        result: dict[str, tuple[str, int, int, str]] = {}
        tree = self._tree.get_tree()
        for conv in tree.nodes:
            if not isinstance(conv, ConversationNode):
                continue
            chain = self._tree.get_conversation_chain(conv.id)
            for i, node in enumerate(chain):
                if i == 0:
                    fp = conv if conv.is_fork_point else None
                else:
                    prev = chain[i - 1]
                    fp = prev if prev.is_fork_point else None
                if fp is not None:
                    result[node.id] = (
                        conv.id,
                        fp.fork_current_index + 1,
                        fp.fork_branch_count,
                        fp.id,
                    )
        return result

    def cleanup_incomplete_nodes(self) -> int:
        """
        清理所有标记为未完成的 MessageNode（分支感知，spec 5.5）。

        - incomplete 且为被修改节点（新创建分支的节点）→ 触发分支删除，
          硬删除节点 + 分支记录 + 消息行，链自动切换（3.3）
        - 其余 incomplete 节点 → 现有软删除流程（移至回收站）
        """
        incomplete = [
            n for n in self._tree.get_all_message_nodes() if n.incomplete
        ]
        count = len(incomplete)
        for node in incomplete:
            if self._tree.find_fork_point_of(node) is not None:
                self._delete_message_node(node)
            else:
                self._tree.soft_delete_node(node.id, mode="recursive")
        if count:
            print(f"[CORE ] 清理 {count} 个未完成消息节点（分支感知）")
        return count

    def mark_incomplete(self, node_id: str) -> None:
        """标记消息节点为未完成（用户停止生成）。"""
        self._tree.mark_incomplete(node_id, True)

    def clear_incomplete_mark(self, node_id: str) -> None:
        """清除消息节点的未完成标记（被"重新生成"/"继续生成"抢救）。"""
        self._tree.mark_incomplete(node_id, False)

    def conversation_is_after_all_enabled(self, conv_id: str) -> bool:
        """
        判断对话节点 conv_id 在 DFS 前序中的位置是否位于所有已启用消息之后。

        用于「新建对话」场景：若新建对话位于所有启用消息之后，直接在其中开始
        不会造成后续对话历史顺序混乱。

        Args:
            conv_id: 对话节点 id

        Returns:
            bool — True 表示该对话位于所有已启用消息之后（或无任何启用消息）
        """
        order_ids = self._tree.get_all_node_ids_in_tree_order()
        pos = {nid: i for i, nid in enumerate(order_ids)}
        conv_idx = pos.get(conv_id)
        if conv_idx is None:
            return False

        enabled = self._context_svc.get_enabled_history_messages()
        if not enabled:
            return True  # 无启用消息，空对话必然位于"所有启用消息之后"
        node_map = {n.message_id: n for n in self._tree.get_all_message_nodes()}
        last_node = node_map.get(enabled[-1].id)
        if last_node is None:
            return True
        last_idx = pos.get(last_node.id, -1)
        return conv_idx > last_idx

    def should_continue_in_new_conversation(self, conv_id: str | None) -> bool:
        """
        判断是否应强制在新（空）对话中开始（不重定向）。

        条件：conv_id 是一个**有效启用**、没有任何消息的 ConversationNode，
        且其位置位于所有已启用消息之后（DFS 前序）。满足则允许直接在新对话开始。
        禁用的空对话不会复用（其中的消息不会显示）。

        Args:
            conv_id: 对话节点 id（可为 None）

        Returns:
            bool — True 表示应使用该新对话，不重定向
        """
        if not conv_id:
            return False
        node = self._tree.get_node(conv_id)
        if node is None or not isinstance(node, ConversationNode):
            return False
        # 必须有效启用：禁用的对话即使为空，其中消息也不会显示
        if not self._context_svc.is_node_effectively_enabled(conv_id):
            return False
        # 空对话：无任何 MessageNode 后代
        if any(isinstance(n, MessageNode) for n in self._tree.get_descendants(conv_id)):
            return False
        return self.conversation_is_after_all_enabled(conv_id)

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
        if not conv_id:
            return False
        node = self._tree.get_node(conv_id)
        if node is None or not isinstance(node, ConversationNode):
            return False
        return self._context_svc.is_node_effectively_enabled(conv_id)

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
        移动节点到新父级下（支持所有节点类型：目录/对话/消息）。

        Args:
            conversation_id: 要移动的节点 ID
            new_parent_id:   新父节点 ID（None 或空串 = 根级）
            position:        插入位置索引（None = 末尾）
        """
        parent = new_parent_id if new_parent_id else None
        self._tree.move_node(conversation_id, parent, position)

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

    def search_messages(
        self, keywords: list[str]
    ) -> list[MessageSearchResult]:
        """
        在所有消息中搜索关键词（OR 逻辑，大小写不敏感）。

        Args:
            keywords: 已分割且小写的关键词列表

        Returns:
            list[MessageSearchResult] — 按 DFS 前序遍历排列
        """
        if not keywords:
            return []

        from app.utils.markdown_utils import strip_for_search

        all_messages = self._msg_repo.get_all_messages()
        all_msg_nodes = self._tree.get_all_message_nodes()

        node_map: dict[str, MessageNode] = {}
        for n in all_msg_nodes:
            node_map[n.message_id] = n

        # ── 构建 tree-order 排序索引 ──
        ordered_ids = self._tree.get_message_ids_in_tree_order()
        id_order: dict[str, int] = {mid: i for i, mid in enumerate(ordered_ids)}

        results: list[MessageSearchResult] = []
        for msg in all_messages:
            searchable = strip_for_search(msg.content).lower()
            if any(kw in searchable for kw in keywords):
                tree_node = node_map.get(msg.id)
                if tree_node is not None:
                    tree_path = self._tree.get_path(tree_node.id)
                else:
                    conv_node = self._tree.get_node(msg.conversation_id)
                    if conv_node is not None:
                        tree_path = self._tree.get_path(msg.conversation_id)
                        role_label = (
                            msg.role.value
                            if hasattr(msg.role, "value")
                            else str(msg.role)
                        )
                        tree_path = f"{tree_path}/{role_label}"
                    else:
                        tree_path = f"?/{msg.id[:8]}"

                results.append(MessageSearchResult(
                    message_id=msg.id,
                    conversation_id=msg.conversation_id,
                    role=msg.role.value if hasattr(msg.role, "value") else str(msg.role),
                    content=msg.content,
                    tree_path=tree_path,
                    created_at=msg.created_at,
                ))

        # ── 按 DFS 前序遍历排序 ──
        results.sort(key=lambda r: id_order.get(r.message_id, 999999))
        return results

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

        now = utcnow()
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
        更新节点（目录或对话）关联的 ContextBlock ID 列表。

        Args:
            folder_id:          节点 ID（目录或对话）
            context_block_ids:  新的 ContextBlock ID 列表
        """
        node = self._tree.get_node(folder_id)
        if node is None:
            return
        if not isinstance(node, (FolderNode, ConversationNode)):
            print(f"[TREE] update_folder_context: {folder_id} 不支持上下文块")
            return
        self._tree.update_node(folder_id, context_block_ids=context_block_ids)
        print(f"[TREE] 更新节点上下文块 {folder_id}: {len(context_block_ids)} 个块")

    def toggle_context_blocks_enabled(self, node_id: str) -> bool:
        """
        切换节点上下文块的启用/禁用状态（右键菜单）。

        Args:
            node_id: 目录或对话节点 ID

        Returns:
            切换后的新状态（True=启用，False=禁用）
        """
        node = self._tree.get_node(node_id)
        if node is None or not isinstance(node, (FolderNode, ConversationNode)):
            return True
        new_state = not getattr(node, "context_blocks_enabled", True)
        self._tree.update_node(
            node_id, context_blocks_enabled=new_state
        )
        print(f"[TREE] 切换节点上下文块 {node_id}: -> "
              f"{'启用' if new_state else '禁用'}")
        return new_state

    def attach_file(self, folder_id: str, file_path: str) -> str:
        """
        把文件挂载到节点（目录或对话）。

        新式挂载 = **快照**（D2）：字节复制进 data/files/<sha256>，节点上只存
        引用。源文件之后被移动或删除都不影响已挂载内容——这是"常驻资料"
        该有的语义，也是它和工具读取（实时）的根本区别。

        未注入快照仓库时退化为旧的"只记路径"行为，兼容既有测试。

        Args:
            folder_id: 节点 ID（目录或对话）
            file_path: 文件绝对路径

        Returns:
            空串 = 成功；非空 = 给用户看的失败原因
        """
        node = self._tree.get_node(folder_id)
        if node is None:
            return "节点不存在"
        if not isinstance(node, (FolderNode, ConversationNode)):
            return "只有目录和对话可以挂载文件"

        # ── 兜底：没有快照仓库，退回旧行为 ──
        if self._blobs is None:
            paths: list[str] = list(node.attachment_paths)
            if file_path not in paths:
                paths.append(file_path)
                self._tree.update_node(folder_id, attachment_paths=paths)
                print(f"[TREE] 挂载附件(路径)到 {folder_id}: {file_path}")
            return ""

        mounts: list[dict] = list(getattr(node, "mounts", []) or [])

        try:
            record = self._blobs.put(file_path)
        except BlobTooLarge as e:
            print(f"[TREE] 挂载被拒绝: {e}")
            return str(e)
        except OSError as e:
            print(f"[TREE] 挂载失败 {file_path}: {e}")
            return f"无法读取文件：{e}"

        # 同一份内容（同 sha256）挂多次只留一条
        if any(m.get("blob_id") == record["blob_id"] for m in mounts):
            print(f"[TREE] 已在挂载中，忽略重复: {record['name']}")
            return ""

        used = node_mounts_bytes(mounts)
        if used + int(record["size"]) > MAX_NODE_BYTES:
            return (
                f"该节点挂载总量已达上限"
                f"（{MAX_NODE_BYTES // 1024 // 1024} MB），请先移除一些文件"
            )

        mounts.append(record)
        self._tree.update_node(folder_id, mounts=mounts)
        print(
            f"[TREE] 挂载快照到 {folder_id}: {record['name']} "
            f"({record['size']} bytes, {record['blob_id'][:12]})"
        )
        return ""

    def read_attachment_bytes(self, blob_id: str) -> bytes | None:
        """
        按 blob_id 读附件原始字节（只给界面画预览用）。

        刻意不做解析、不做格式判断：能不能显示是 UI 的事，
        这里只负责把快照取出来；读不到返回 None。
        """
        if self._blobs is None or not blob_id:
            return None
        try:
            return self._blobs.read_bytes(blob_id)
        except OSError as e:
            print(f"[CORE] 读取附件快照失败 {blob_id[:12]}: {e}")
            return None

    def get_attachments_view(self, node_id: str) -> dict:
        """
        返回该节点**实际生效**的附件视图（管理面板用）。

        与 _collect_context_resources 的可见范围一致（祖先链继承、按 blob 去重、
        近的覆盖远的），但额外标注归属，因为界面必须区分"自己的"和"继承来的"：
        继承项不能在这里移除 —— 移除它会影响所有兄弟对话。

        Args:
            node_id: 目录或对话节点 ID

        Returns:
            {"mounts": [...], "legacy": [...]}
            mounts 每项 = 原挂载记录 + owner_id / owner_title / inherited / enabled
            legacy 每项 = {"path", "name", owner_id, owner_title, inherited, enabled}
        """
        node = self._tree.get_node(node_id)
        if node is None:
            return {"mounts": [], "legacy": []}

        chain = self._ancestor_chain(node)
        mounts: list[dict] = []
        seen_blobs: set[str] = set()
        legacy: list[dict] = []
        seen_paths: set[str] = set()

        for owner in chain:
            if not isinstance(owner, (FolderNode, ConversationNode)):
                continue
            inherited = owner.id != node_id
            enabled = self._node_enabled(owner.id)
            for mount in getattr(owner, "mounts", []) or []:
                blob_id = str((mount or {}).get("blob_id") or "")
                if not blob_id or blob_id in seen_blobs:
                    continue
                seen_blobs.add(blob_id)
                entry = dict(mount)
                entry.update({
                    "owner_id": owner.id,
                    "owner_title": owner.title or "未命名",
                    "inherited": inherited,
                    "enabled": enabled,
                })
                mounts.append(entry)
            for path in getattr(owner, "attachment_paths", []) or []:
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                legacy.append({
                    "path": path,
                    "name": os.path.basename(path) or path,
                    "owner_id": owner.id,
                    "owner_title": owner.title or "未命名",
                    "inherited": inherited,
                    "enabled": enabled,
                })

        return {"mounts": mounts, "legacy": legacy}

    def _ancestor_chain(self, node) -> list:
        """从节点自身向上的资源链（近 → 远），只含目录与对话。"""
        chain: list = []
        if isinstance(node, (FolderNode, ConversationNode)):
            chain.append(node)
        current = node.parent_id
        while current is not None:
            parent = self._tree.get_node(current)
            if parent is None:
                break
            if isinstance(parent, (FolderNode, ConversationNode)):
                chain.append(parent)
            current = parent.parent_id
        return chain

    def _node_enabled(self, node_id: str) -> bool:
        """节点的 effective enabled（级联规则由 ContextService 持有）。"""
        try:
            return bool(self._context_svc.is_node_effectively_enabled(node_id))
        except Exception:
            node = self._tree.get_node(node_id)
            return bool(getattr(node, "enabled", True) is not False)

    def convert_legacy_attachment(self, owner_id: str, path: str) -> str:
        """
        把一条旧式路径附件就地转成快照挂载。

        以前只能跑维护脚本批量转；挂载管理面板里给单个文件留一个按钮更顺手。

        Returns:
            空串 = 成功；非空 = 失败原因
        """
        node = self._tree.get_node(owner_id)
        if node is None:
            return "节点不存在"
        if not isinstance(node, (FolderNode, ConversationNode)):
            return "只有目录和对话可以挂载文件"
        if self._blobs is None:
            return "未装配文件仓库，无法转换"

        mounts: list[dict] = list(getattr(node, "mounts", []) or [])
        try:
            record = self._blobs.put(path)
        except BlobTooLarge as e:
            return str(e)
        except OSError as e:
            return f"无法读取文件：{e}"

        if not any(m.get("blob_id") == record["blob_id"] for m in mounts):
            mounts.append(record)
        paths = [p for p in (node.attachment_paths or []) if p != path]
        self._tree.update_node(owner_id, mounts=mounts, attachment_paths=paths)
        print(f"[TREE] 旧式附件已转快照: {record['name']} → {owner_id}")
        return ""

    def detach_file(self, folder_id: str, file_path: str) -> str:
        """
        从节点（目录或对话）移除挂载。

        file_path 可以是挂载的源路径、展示名或 blob_id —— 三处调用方
        （右键菜单、文件面板、内部迁移）各自手上只有其中一种。

        Returns:
            空串 = 成功/本就未挂载；非空 = 失败原因
        """
        node = self._tree.get_node(folder_id)
        if node is None:
            return "节点不存在"
        if not isinstance(node, (FolderNode, ConversationNode)):
            return "只有目录和对话可以挂载文件"

        # 旧式路径附件优先按原样处理
        paths: list[str] = list(node.attachment_paths)
        if file_path in paths:
            paths.remove(file_path)
            self._tree.update_node(folder_id, attachment_paths=paths)
            print(f"[TREE] 移除旧式附件 {folder_id}: {file_path}")

        mounts: list[dict] = list(getattr(node, "mounts", []) or [])
        kept = [
            m for m in mounts
            if not (
                file_path in (
                    str(m.get("source_path") or ""),
                    str(m.get("name") or ""),
                    str(m.get("blob_id") or ""),
                )
            )
        ]
        if len(kept) != len(mounts):
            self._tree.update_node(folder_id, mounts=kept)
            print(f"[TREE] 移除挂载 {folder_id}: {file_path}")
        return ""

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

    def reload_file_access(self) -> None:
        """
        授权变更后重新加载：把持久化配置写回 config，并清空检索索引。

        下一次检索会按新目录重新扫描；已授权的目录不会自动失效。
        """
        from app.utils.file_access_settings import FileAccessSettings

        FileAccessSettings().apply_to_config()
        if self._file_tools is not None:
            self._file_tools.invalidate()

    async def _extract_attachments(self, files: list[str]) -> list[dict]:
        """
        把用户选中的文件解析成可落库的消息附件。

        两条路：

        - **文本**：统一走 FileService 的解析器（docx / pdf / json 都能取出文本），
          不再用裸 UTF-8 读取——那是历史遗留的乱码来源。超限由
          message_attachments 截断并给出可读提示，绝不静默丢内容。
        - **图片**：字节存进内容寻址快照仓库（data/files/<sha256>），消息里只留
          {kind:"image", blob_id, mime, width, height}。刻意不把 base64 塞进
          messages 表——一条消息膨胀几 MB 会拖垮每次读取；只有构建上下文时
          才按 blob_id 把字节读出来。

        顺序：文本附件在前、图片在后。docx 内嵌图片（word/media）跟着它所属的
        文档排在图片组里，正文里的 "[图片 N]" 标记才能和实际发出的图对上。

        解析失败的文件也会带着一句 "[无法解析…]" 说明进入附件列表
        （FileService 保证一个文件都不丢）——用户看得见，模型也读得到。
        """
        if not files:
            return []

        attachments: list[dict] = []
        images: list[dict] = []
        failures: list[dict] = []
        char_budget = message_attachments.MAX_MESSAGE_CHARS
        image_budget = image_attachments.MAX_IMAGES_PER_REQUEST

        # 逐个文件处理（而不是批量解析）：
        # 1) 文档内嵌图片的说明要挂回它所属文档的附件文本上；
        # 2) 字符预算要能跨文件累计。
        for path in files:
            name = os.path.basename(path) or path

            if self._sniff_image(path) is not None:
                entry, reason = self._image_attachment(path)
                if entry is None:
                    print(f"[CORE] 图片未能附加: {path} -> {reason}")
                    failures.append({
                        "name": name,
                        "text": "[这张图片没有发送成功：" + reason + "]",
                    })
                elif image_budget <= 0:
                    failures.append({
                        "name": name,
                        "text": "[图片超出单条消息上限，未发送]",
                    })
                else:
                    images.append(entry)
                    image_budget -= 1
                continue

            pairs = await self._file_svc.extract_files([path])
            text_atts, warnings = message_attachments.normalize(
                pairs, max_message_chars=char_budget
            )
            for warning in warnings:
                print(f"[CORE] 附件超限：{warning}")
            char_budget -= sum(len(a.get("text") or "") for a in text_atts)

            if os.path.splitext(path)[1].lower() == ".docx" and image_budget > 0:
                picked, total = self._pick_docx_images(path, image_budget, name)
                if total:
                    images.extend(picked)
                    image_budget -= len(picked)
                    note = _document_image_note(total, picked)
                    if text_atts:
                        text_atts[0]["text"] = (
                            (text_atts[0].get("text") or "") + "\n\n" + note
                        )
                    else:
                        failures.append({"name": name, "text": note})

            attachments.extend(text_atts)

        attachments.extend(failures)
        if not attachments and not images:
            print(f"[CORE] 附件解析结果为空，未附加：{files}")
        return attachments + images

    def _pick_docx_images(
        self, path: str, budget: int, doc_name: str
    ) -> tuple[list[dict], int]:
        """
        从文档里挑最多 budget 张图，返回 (选中的图片, 文档里可用的图片总数)。

        **按像素面积从大到小挑**，再按原顺序返回。踩过的坑：按文档顺序取前 N 张，
        一份"几张行内公式截图 + 三张真·截图"的文档，前 N 张全是 830×42 这种细条，
        真正的截图一张没发出去，模型自然答不出图里的内容。
        """
        stem = os.path.splitext(doc_name)[0] or doc_name
        candidates: list[tuple[int, dict]] = []

        raw_images = self._file_svc.extract_embedded_images(path, limit=_MAX_DOCX_IMAGE_SCAN)
        for index, raw in enumerate(raw_images, 1):
            entry, reason = self._image_from_bytes(raw, name=f"{stem}-图{index}.png")
            if entry is None:
                print(f"[CORE] 文档内嵌图片跳过: {path}#{index} -> {reason}")
                continue
            candidates.append((index, entry))

        if not candidates:
            return [], 0

        ranked = sorted(
            candidates,
            key=lambda item: (item[1].get("width") or 0) * (item[1].get("height") or 0),
            reverse=True,
        )
        chosen = sorted(ranked[:budget], key=lambda item: item[0])
        return [entry for _, entry in chosen], len(candidates)

    # ──────────────────────────────────────────
    # 图片附件
    # ──────────────────────────────────────────

    @staticmethod
    def _sniff_image(path: str) -> str | None:
        """只看前 16 字节判断是不是图片（服务端也是按内容判断，不看扩展名）。"""
        try:
            with open(path, "rb") as handle:
                return image_attachments.sniff_mime(handle.read(16))
        except OSError:
            return None

    def _image_attachment(self, path: str) -> tuple[dict | None, str]:
        """读取一个图片文件并归一化；失败返回 (None, 人话原因)。"""
        name = os.path.basename(path) or path
        try:
            size = os.path.getsize(path)
        except OSError as e:
            return None, f"读取失败（{e.strerror or '未知错误'}）"
        if size > _MAX_SOURCE_IMAGE_BYTES:
            return None, (
                f"超过 {_MAX_SOURCE_IMAGE_BYTES // (1024 * 1024)} MB 上限"
            )
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as e:
            return None, f"读取失败（{e.strerror or '未知错误'}）"
        return self._image_from_bytes(data, name)

    def _image_from_bytes(self, data: bytes, name: str) -> tuple[dict | None, str]:
        """归一化 + 落快照仓库；返回可落库的附件项。"""
        if self._blobs is None:
            return None, "图片仓库未装配"
        prepared = image_attachments.prepare(data)
        if prepared is None:
            return None, "格式不支持（只支持 JPEG / PNG / GIF / WebP）或压缩后仍然过大"
        try:
            record = self._blobs.put_bytes(prepared["data"], name=name)
        except BlobTooLarge as e:
            return None, str(e)
        except OSError as e:
            return None, f"写入快照失败（{e.strerror or '未知错误'}）"
        return {
            "name": name,
            "kind": "image",
            "blob_id": record["blob_id"],
            "mime": prepared["mime"],
            "width": prepared["width"],
            "height": prepared["height"],
            "size": record["size"],
        }, ""

    def _attachments_of(self, message_id: str) -> list[dict]:
        """取某条消息已有的附件（编辑重发送时用，保证改错字不丢资料）。"""
        try:
            messages = self._msg_repo.get_messages_by_ids([message_id])
        except Exception as e:
            print(f"[CORE] 读取原附件失败: {e}")
            return []
        for message in messages:
            if message.id == message_id:
                return list(getattr(message, "attachments", None) or [])
        return []

    def _remember_reads(self, session_id: str) -> None:
        """
        把本轮读过的文件写进读取记录（工具读取 = 实时，不产生挂载）。

        装配了 SQLite 读取日志时写库（重启后仍在），否则退化为内存列表。
        """
        if self._file_tools is None:
            return
        entries = [
            e for e in self._file_tools.audit() if e.get("tool") == "read_file"
        ]
        if not entries:
            return

        if self._read_log is None:
            self._memory_reads.setdefault(session_id, []).extend(entries)
            return

        for entry in entries:
            lines = entry.get("lines") or []
            start = lines[0] if len(lines) == 2 else 0
            end = lines[1] if len(lines) == 2 else 0
            try:
                self._read_log.append(
                    session_id, entry.get("path") or "", start, end
                )
            except Exception as e:  # 记录失败不能拖垮回复
                print(f"[CORE] 读取记录写入失败: {e}")

    def _read_entries(self, session_id: str) -> list[dict]:
        """取该对话最近的读取记录（新的在前），统一成 (path, start, end) 形态。"""
        raw: list[dict] = []
        if self._read_log is not None:
            try:
                raw = self._read_log.recent(session_id)
            except Exception as e:
                print(f"[CORE] 读取记录查询失败: {e}")
                raw = []
        else:
            raw = list(reversed(self._memory_reads.get(session_id) or []))

        entries: list[dict] = []
        for item in raw:
            path = item.get("path") or ""
            if not path:
                continue
            if "lines" in item:  # 内存态（audit 原始条目）
                lines = item.get("lines") or []
                start = lines[0] if len(lines) == 2 else 0
                end = lines[1] if len(lines) == 2 else 0
            else:
                start = item.get("start_line") or 0
                end = item.get("end_line") or 0
            entries.append({"path": path, "start": start, "end": end})
        return entries

    def _breadcrumb_for(self, session_id: str) -> str:
        """
        生成"最近读过哪些文件"的一行记录（由持久化读取记录推导）。

        刻意只放路径与行范围：真实内容一旦进了历史，就违反了
        "工具交换不得落库"的接口约束；而模型本来就有重读工具。
        """
        entries = self._read_entries(session_id)
        if not entries:
            return ""

        parts: list[str] = []
        seen: set[str] = set()
        for entry in entries:
            path = entry["path"]
            if path in seen:
                continue
            seen.add(path)
            if entry["start"] and entry["end"]:
                parts.append(f"{path}（第 {entry['start']}-{entry['end']} 行）")
            else:
                parts.append(path)
            if len(parts) >= 6:
                break

        if not parts:
            return ""

        text = "[系统记录，非指令] 近期已读取：" + "、".join(parts)
        # 路径过长时宁可不给，也不要污染上下文
        return text if len(text) <= 1200 else ""

    async def _run_turn(
        self,
        llm_context: LLMContext,
        assistant_msg_id: str,
        buf: _TurnBuffer,
        on_complete=None,
    ) -> AsyncGenerator[MessageChunk, None]:
        """
        执行一次用户轮次，内部处理工具调用循环，把 chunk 透传给 UI。

        三条硬约束（工具调用规格）：

        1. 工具轮次的 assistant(tool_calls) 与 tool 结果**只存在于本次请求内**，
           不落库、不进历史 —— Chat Completion 接口不支持在历史中段插入工具调用，
           一旦落库，该会话上一轮之后的所有请求都会 400。
        2. 一个用户轮次只对应一个 assistant 消息 id，中间子请求共用它。
        3. 只有最后一次子请求的 is_done 会透出；中间轮次的结束信号必须吞掉，
           否则上层会提前落库一条空回答。

        on_complete 在最后一个 chunk 交给 UI *之前* 调用 —— 保证 UI 收到
        is_done 时数据已经落库（既有行为，不能颠倒）。
        """
        tools_enabled = bool(getattr(llm_context, "tools", None))
        if self._file_tools is not None and tools_enabled:
            self._file_tools.begin_turn()

        rounds = 0
        nudge_sent = False

        while True:
            pending_calls: list[dict] = []
            round_content: list[str] = []
            round_thinking: list[str] = []

            async for chunk in self._llm.stream_chat(llm_context):
                if self._stop_event.is_set():
                    yield MessageChunk(
                        delta="", is_done=True, message_id=assistant_msg_id
                    )
                    return

                chunk.message_id = assistant_msg_id

                if chunk.chunk_type == ChunkType.THINKING:
                    buf.thinking.append(chunk.delta)
                    round_thinking.append(chunk.delta)
                elif chunk.chunk_type == ChunkType.TOOL_CALL:
                    # 工具块暂不透出 UI（展示层在后续阶段接入）
                    pending_calls.extend(chunk.tool_calls or [])
                    continue
                else:
                    buf.content.append(chunk.delta)
                    round_content.append(chunk.delta)

                if chunk.is_done:
                    buf.completed = True
                    if on_complete is not None:
                        on_complete()
                    yield chunk
                    return

                yield chunk

            # 流意外结束却没收到 is_done：按正常结束收尾，避免界面卡在生成中
            if not pending_calls:
                buf.completed = True
                if on_complete is not None:
                    on_complete()
                yield MessageChunk(delta="", is_done=True, message_id=assistant_msg_id)
                return

            rounds += 1
            if rounds > MAX_TOOL_ROUNDS:
                if nudge_sent:
                    # 已经提示过仍要继续调工具：直接收尾
                    buf.completed = True
                    if on_complete is not None:
                        on_complete()
                    yield MessageChunk(
                        delta="", is_done=True, message_id=assistant_msg_id
                    )
                    return
                # 预算耗尽：不执行本轮调用，提示模型基于已有信息作答。
                # 刻意保留 tools —— 历史里已有 assistant.tool_calls，
                # 此时抽掉 tools 有被服务端判定非法的风险。
                nudge_sent = True
                llm_context.messages.append({
                    "role": "system",
                    "content": "工具调用次数已达上限，请基于已获得的信息直接作答。",
                })
                continue

            # 先透出"要做什么"，再执行 —— 检索可能耗时数秒，否则界面干等
            for call in pending_calls:
                yield MessageChunk(
                    delta=_describe_tool_call(call),
                    chunk_type=ChunkType.TOOL_CALL,
                    message_id=assistant_msg_id,
                )

            self._append_tool_round(
                llm_context, pending_calls, round_content, round_thinking
            )
            self._execute_tools(llm_context, pending_calls, buf)
            continue

    def _append_tool_round(
        self,
        llm_context: LLMContext,
        calls: list[dict],
        content: list[str],
        thinking: list[str],
    ) -> None:
        """
        把本轮 assistant 的 tool_calls 回填进请求消息。

        reasoning_content 必须随行回传：思考模式下的工具调用是在思考过程中
        进行的，缺了它模型无法延续自己的推理链。
        """
        message = {
            "role": "assistant",
            "content": "".join(content),
            "reasoning_content": "".join(thinking),
            "tool_calls": [
                {
                    "id": call.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": (call.get("function") or {}).get("name", ""),
                        "arguments": (call.get("function") or {}).get("arguments", ""),
                    },
                }
                for call in calls
            ],
        }
        llm_context.messages.append(message)

    def _execute_tools(
        self,
        llm_context: LLMContext,
        calls: list[dict],
        buf: _TurnBuffer,
    ) -> None:
        """逐个执行工具调用，把结果作为 role="tool" 追加（永不抛异常）。"""
        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            args = fn.get("arguments") or ""

            if self._file_tools is None:
                result = json.dumps({"error": "工具层未装配"}, ensure_ascii=False)
            else:
                result = self._file_tools.execute(
                    name, args, should_stop=self._stop_event.is_set
                )

            llm_context.messages.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": result,
            })
            buf.tool_events.append({
                "name": name,
                "arguments": args,
                "result_chars": len(result),
            })

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

        # ── Step 1: 文件提取（文本随消息行走，不拼进 content）──
        attachments = await self._extract_attachments(files)

        # ── Step 2: 用户文本（原样；附件在 build 时才展开）──
        full_user_text = text

        # ── Step 3: 搜索 ─────────────────────────
        search_text = await self._search_svc.search_and_format(text)

        # ── Step 4: 构建 LLM 上下文 ─────────────
        # 工具能力：仅在总开关打开且有授权目录时下发；否则请求体里没有 tools，
        # 模型无从得知文件访问的存在。
        tools = self._file_tools.schemas() if self._file_tools is not None else []

        # 面包屑：只带路径与行范围，不带内容。
        # 内容不进历史（Chat Completion 不支持历史中段插入工具调用），
        # 模型需要细节时按路径重读一次，本地 I/O 代价远低于每轮重发正文。
        breadcrumb = self._breadcrumb_for(session_id)
        if breadcrumb:
            full_user_text = breadcrumb + "\n\n" + full_user_text

        llm_context = self._context_svc.build_llm_context(
            conversation_id=session_id,
            new_user_message=full_user_text,
            injected_search_text=search_text,
            tools=tools,
            new_user_attachments=attachments,
        )

        # ── Step 5: 持久化用户消息 ───────────────
        # content 只存用户自己写的字；附件文本存 attachments 列。
        # token_count 必须把附件算进去，否则截断策略会把巨型消息当成轻量放行。
        user_msg = Message(
            id=str(uuid.uuid4()),
            conversation_id=session_id,
            role=Role.USER,
            content=text,
            created_at=utcnow(),
            token_count=message_attachments.estimate_tokens(text, attachments),
            attachments=attachments,
        )
        self._msg_repo.save_message(user_msg)

        # Phase 5: 创建对应的 MessageNode 到树中。
        # 分叉感知：若对话已有分叉点，新节点须同步扩展当前分支记录
        # （否则重启后切换分支会丢失这部分链）。
        user_msg_node = MessageNode(
            id=user_msg.id,
            parent_id=session_id,
            message_id=user_msg.id,
            role=Role.USER.value,
            preview=text[:60] if text else "",
            title=f"User: {text[:30]}" if text else "User message",
            enabled=True,
        )
        self._branch_svc.extend_current_branch(session_id, [user_msg_node])

        # ── Step 6 & 7: 调用 LLM（含工具循环），流式转发 ──────
        assistant_msg_id = str(uuid.uuid4())
        buf = _TurnBuffer()

        def _persist_reply() -> None:
            """最后一个 chunk 交给 UI 之前落库（顺序不可颠倒）。"""
            full_content = "".join(buf.content)
            thinking = "".join(buf.thinking)
            if full_content or thinking:
                # Phase 6: 持久化 assistant + 绑定 thinking（thinking 行存
                #   messages 表，树中只建 assistant 节点，通过绑定关联）
                asst_node = self._save_assistant_with_thinking(
                    session_id, assistant_msg_id, full_content, thinking,
                )
                # 分叉感知：同步扩展当前分支记录（同 user 节点创建）
                self._branch_svc.extend_current_branch(session_id, [asst_node])

            # Step 9: 更新对话元数据（标题 & 摘要）
            self._update_meta_after_reply(session_id, text, full_content)

            # 记录本轮读过什么，供下一轮生成面包屑（空列表 = 下一轮不带面包屑）
            self._remember_reads(session_id)

        try:
            async for chunk in self._run_turn(
                llm_context, assistant_msg_id, buf, _persist_reply
            ):
                yield chunk
        except Exception:
            # 网络/API 异常：本轮 user 消息已落库但无回复，标记未完成，
            # 避免留下无回复的孤儿消息（下次启动会被 cleanup_incomplete_nodes 清理）。
            self._tree.mark_incomplete(user_msg_node.id, True)
            raise

    async def regenerate_message(
        self,
        session_id: str,
        message_id: str,
    ) -> AsyncGenerator[MessageChunk, None]:
        """
        重新生成完整的 assistant 消息（分叉模式，spec 第四章）。

        预分支语义（3.1）：流式完成前**不持久化任何内容**；完成后创建新分支
        （分叉点 = 目标 assistant 的前驱，4.2）。停止生成/出错 → 不持久化，
        由 UI 丢弃回退（4.3，停止生成 = 丢弃回退，不产生 incomplete 标记）。

        注意：仅适用于完整 assistant 节点。未完成轮次的"继续/重新生成"由
        另一条路径（send_message 流程）处理，不受本方法影响。

        Args:
            session_id: 当前对话节点 ID
            message_id: 目标 assistant 消息 ID（可为空，自动取最后一条）

        Yields:
            MessageChunk — 同 send_message
        """
        self._stop_event.clear()

        node = self._tree.get_node(session_id)
        if node is None or not isinstance(node, ConversationNode):
            raise ConversationNotFoundError(session_id)

        # 定位目标 assistant
        target_id = message_id or self._find_last_assistant_message(session_id)
        target = self._tree.get_node(target_id) if target_id else None
        if target is None or not isinstance(target, MessageNode):
            raise ValueError(f"重新生成目标不存在: {target_id}")

        # 分叉点 = 目标的前驱；目标为第一条消息 → 对话节点（4.2）
        predecessor = self._tree.get_predecessor(session_id, target_id)
        fork_point_id = predecessor.id if predecessor else session_id

        # 上下文：目标之前的全部历史 + 目标之前最后一条 user 消息文本
        # （4.1"保留该 assistant 消息之前的所有上下文"）
        user_text = self._user_text_before(session_id, target_id)

        # 工具能力照常下发，但**不注入面包屑**：本次历史被截断在 target 之前，
        # "上一轮读过什么"指的是一条不在本次上下文里的轮次，注入了只会误导。
        tools = self._file_tools.schemas() if self._file_tools is not None else []

        llm_context = self._context_svc.build_llm_context(
            conversation_id=session_id,
            new_user_message=user_text,
            injected_search_text="",
            history_until=target_id,
            tools=tools,
        )

        # 预分支流式：不持久化，完成时才创建分支。
        # 停止/出错（4.3）：零持久化，UI 丢弃回退——必须自然完成才落地。
        new_msg_id = str(uuid.uuid4())
        buf = _TurnBuffer()

        # 工具循环内含在 _run_turn 里：**没有 on_complete**，
        # 因为预分支语义要求"自然完成前不落任何东西"，落库统一放在循环之后。
        async for chunk in self._run_turn(llm_context, new_msg_id, buf, None):
            yield chunk

        full_content = "".join(buf.content)
        thinking_content = "".join(buf.thinking)
        if buf.completed and (full_content or thinking_content):
            # 持久化 assistant + 绑定 thinking
            asst_node = self._save_assistant_with_thinking(
                session_id, new_msg_id, full_content, thinking_content
            )
            # 新链 = 目标之前的部分 + 新 assistant（目标及其后整体替换，1.3）
            old_chain = self._tree.get_conversation_chain(session_id)
            idx = next(
                (i for i, n in enumerate(old_chain) if n.id == target_id),
                len(old_chain),
            )
            new_chain = old_chain[:idx] + [asst_node]
            self._branch_svc.create_branch(session_id, fork_point_id, new_chain)
            self._update_meta_after_reply(session_id, user_text, full_content)
            self._remember_reads(session_id)

    async def resend_edited_message(
        self,
        session_id: str,
        original_user_id: str,
        text: str,
        files: list[str],
    ) -> AsyncGenerator[MessageChunk, None]:
        """
        修改并重发送 user 消息（spec 第五章）。

        分支在**发送时立即落地**（5.3.1，无预分支状态）：
        1. 新 user 节点（新 id —— 旧行必须保留在归档分支）标记 incomplete
        2. create_branch（分叉点 = 原节点前驱；第一条消息 → 对话节点）
        3. 流式输出 assistant
        4. 完成后 extend_current_branch + 清除 incomplete（5.5）

        停止生成 → 正常 incomplete 暂停态（5.4）：user 节点保持 incomplete，
        由"继续生成/重新生成/放弃本次修改"接管（UI 层）。

        Args:
            session_id:        对话节点 ID
            original_user_id:  被修改的原 user 消息 ID
            text:              修改后的文本
            files:             附件路径列表（可为空）

        Yields:
            MessageChunk — 同 send_message
        """
        self._stop_event.clear()

        node = self._tree.get_node(session_id)
        if node is None or not isinstance(node, ConversationNode):
            raise ConversationNotFoundError(session_id)

        # 校验：原节点必须是完整的 user 消息（3.2.1 前提条件）
        original = self._tree.get_node(original_user_id)
        if original is None or not isinstance(original, MessageNode):
            raise ValueError(f"被修改消息不存在: {original_user_id}")
        if original.role != Role.USER.value:
            raise ValueError("只有 user 消息支持修改并重发送")
        if original.incomplete:
            raise ValueError("incomplete 节点不可作为分叉来源（3.2.1）")

        # 附件：用户只改文字，**保留原附件**——改个错字不该把资料弄丢。
        # 只有本次真的重新选了文件时才用新的（那时用户是有意替换）。
        attachments = await self._extract_attachments(files)
        if not attachments:
            attachments = self._attachments_of(original_user_id)

        full_user_text = text

        search_text = await self._search_svc.search_and_format(text)

        # 分叉点 = 原节点前驱；第一条消息 → 对话节点（5.3.2）
        predecessor = self._tree.get_predecessor(session_id, original_user_id)
        fork_point_id = predecessor.id if predecessor else session_id

        # 新 user 消息（新 id：旧内容必须保留在归档分支中）
        new_user_id = str(uuid.uuid4())
        user_msg = Message(
            id=new_user_id,
            conversation_id=session_id,
            role=Role.USER,
            content=text,
            created_at=utcnow(),
            token_count=message_attachments.estimate_tokens(text, attachments),
            attachments=attachments,
        )
        self._msg_repo.save_message(user_msg)
        user_node = MessageNode(
            id=new_user_id,
            parent_id=session_id,
            message_id=new_user_id,
            role=Role.USER.value,
            preview=text[:60] if text else "",
            title=f"User: {text[:30]}" if text else "User message",
            enabled=True,
            incomplete=True,  # 5.4：流式期间保持 incomplete
        )

        # 建分支（发送时立即落地）：新链 = 原节点之前 + 新 user 节点
        old_chain = self._tree.get_conversation_chain(session_id)
        idx = next(
            (i for i, n in enumerate(old_chain) if n.id == original_user_id),
            len(old_chain),
        )
        new_chain = old_chain[:idx] + [user_node]
        self._branch_svc.create_branch(session_id, fork_point_id, new_chain)

        # 工具与普通发送一致；面包屑语义也一致（"上一轮"确实在历史里）
        tools = self._file_tools.schemas() if self._file_tools is not None else []
        breadcrumb = self._breadcrumb_for(session_id)
        if breadcrumb:
            full_user_text = breadcrumb + "\n\n" + full_user_text

        # 上下文基于新链构建（incomplete 的新 user 节点由 new_user_message 传入；
        # 新节点尚未出现在历史里，附件只能从参数带进去）
        llm_context = self._context_svc.build_llm_context(
            conversation_id=session_id,
            new_user_message=full_user_text,
            injected_search_text=search_text,
            tools=tools,
            new_user_attachments=attachments,
        )

        new_msg_id = str(uuid.uuid4())
        buf = _TurnBuffer()

        # 同 regenerate：没有 on_complete，落库统一放在循环之后，
        # 保证"停止 = 保持 incomplete 暂停态"这条语义不被工具轮破坏。
        async for chunk in self._run_turn(llm_context, new_msg_id, buf, None):
            yield chunk

        full_content = "".join(buf.content)
        thinking_content = "".join(buf.thinking)
        # 仅自然完成才持久化与扩展分支（5.4：停止 → 保持 incomplete 暂停态，
        # 部分内容只存在于内存，由继续生成/重新生成/放弃接管）
        if buf.completed and (full_content or thinking_content):
            asst_node = self._save_assistant_with_thinking(
                session_id, new_msg_id, full_content, thinking_content
            )
            # 扩展当前分支 + 清除 incomplete（5.5：分支成为完整分支）
            self._branch_svc.extend_current_branch(session_id, [asst_node])
            self._tree.mark_incomplete(new_user_id, False)
            self._update_meta_after_reply(session_id, text, full_content)
            self._remember_reads(session_id)

    async def continue_message(
        self,
        session_id: str,
        partial_content: str,
        partial_thinking: str = "",
    ) -> AsyncGenerator[MessageChunk, None]:
        """
        继续生成未完成的助手消息（DeepSeek Beta 前缀续写）。

        从 tree.json 获取历史消息（与正常发送一致），将已有的部分助手内容作为
        prefix，请求模型补全其余内容。流式返回续写块；完成后持久化完整助手
        消息到 DB + 树。

        Args:
            session_id:       当前对话节点 ID
            partial_content:  未完成消息已有的部分文本（续写起点）
            partial_thinking: 未完成消息已有的部分思考内容（若有）

        Yields:
            MessageChunk — 与 send_message 相同；最后一块 is_done=True
        """
        self._stop_event.clear()

        # 被"继续生成"抢救：清除当前轮次用户消息的未完成标记（防止被自动清理）
        _uid = self.find_latest_user_message_id(session_id)
        if _uid:
            self._tree.mark_incomplete(_uid, False)

        # 验证会话
        node = self._tree.get_node(session_id)
        if node is None:
            raise ConversationNotFoundError(session_id)
        if not isinstance(node, ConversationNode):
            raise ConversationNotFoundError(
                f"Node {session_id} is not a conversation"
            )

        # 构建续写上下文（历史来自 tree.json，末尾 prefix assistant）
        context = self._context_svc.build_continue_context(
            partial_content=partial_content,
            partial_thinking=partial_thinking,
        )

        new_msg_id = str(uuid.uuid4())
        full_parts: list[str] = []
        thinking_parts: list[str] = []

        async for chunk in self._llm.stream_prefix_continue(
            context, partial_content, partial_thinking
        ):
            if self._stop_event.is_set():
                yield MessageChunk(
                    delta="", is_done=True, message_id=new_msg_id
                )
                break

            chunk.message_id = new_msg_id
            # 分离 thinking 块与正文块（避免思维链混入正文）
            if chunk.chunk_type == ChunkType.THINKING:
                thinking_parts.append(chunk.delta)
            else:
                full_parts.append(chunk.delta)
            yield chunk
            if chunk.is_done:
                break

        # 完整内容 = 已有部分 + 续写部分（API 只返回新增 token）
        continuation = "".join(full_parts)
        complete_content = (partial_content or "") + continuation
        complete_thinking = (partial_thinking or "") + "".join(thinking_parts)
        if complete_content.strip():
            # Phase 6: thinking 内容仍存 messages 行，树里只建 assistant 节点
            thinking_msg_id: str | None = None
            if complete_thinking.strip():
                thinking_msg = Message(
                    id=str(uuid.uuid4()),
                    conversation_id=session_id,
                    role=Role.THINKING,
                    content=complete_thinking,
                    is_thinking=True,
                    created_at=utcnow(),
                    token_count=len(complete_thinking) // 4,
                )
                self._msg_repo.save_message(thinking_msg)
                thinking_msg_id = thinking_msg.id

            assistant_msg = Message(
                id=new_msg_id,
                conversation_id=session_id,
                role=Role.ASSISTANT,
                content=complete_content,
                is_thinking=bool(complete_thinking.strip()),
                created_at=utcnow(),
                token_count=len(complete_content) // 4,
            )
            self._msg_repo.save_message(assistant_msg)

            preview = complete_content[:60]
            asst_node = MessageNode(
                id=new_msg_id,
                parent_id=session_id,
                message_id=new_msg_id,
                role=Role.ASSISTANT.value,
                preview=preview,
                title=f"Asst: {preview[:30]}" if preview else "Assistant message",
                enabled=True,
                thinking_message_id=thinking_msg_id,
            )
            # 分叉感知：同步扩展当前分支记录
            self._branch_svc.extend_current_branch(session_id, [asst_node])

            self._update_meta_after_reply(session_id, "", complete_content)

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
        """返回最后一条 assistant 消息的 ID，找不到返回 None。（tree.json 为准）"""
        messages = self.get_messages_for_node(session_id)
        for msg in reversed(messages):
            if msg.role == Role.ASSISTANT:
                return msg.id
        return None

    def _user_text_before(self, session_id: str, target_id: str) -> str:
        """返回目标消息之前最后一条 user 消息的完整文本（重新生成输入）。"""
        text = ""
        for msg in self.get_messages_for_node(session_id):
            if msg.id == target_id:
                break
            if msg.role == Role.USER:
                text = msg.content
        return text

    def _save_assistant_with_thinking(
        self,
        session_id: str,
        assistant_msg_id: str,
        full_content: str,
        thinking_content: str,
    ) -> MessageNode:
        """
        持久化 assistant 消息行 + 绑定 thinking 行（Phase 6），
        返回 assistant MessageNode（thinking 不再作为树节点）。
        """
        thinking_msg_id: str | None = None
        if thinking_content:
            thinking_msg = Message(
                id=str(uuid.uuid4()),
                conversation_id=session_id,
                role=Role.THINKING,
                content=thinking_content,
                is_thinking=True,
                created_at=utcnow(),
                token_count=len(thinking_content) // 4,
            )
            self._msg_repo.save_message(thinking_msg)
            thinking_msg_id = thinking_msg.id

        self._msg_repo.save_message(Message(
            id=assistant_msg_id,
            conversation_id=session_id,
            role=Role.ASSISTANT,
            content=full_content,
            is_thinking=bool(thinking_content),
            created_at=utcnow(),
            token_count=len(full_content) // 4,
        ))

        preview = full_content[:60]
        return MessageNode(
            id=assistant_msg_id,
            parent_id=session_id,
            message_id=assistant_msg_id,
            role=Role.ASSISTANT.value,
            preview=preview,
            title=f"Asst: {preview[:30]}" if preview else "Assistant message",
            enabled=True,
            thinking_message_id=thinking_msg_id,
        )

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

        # 更新消息计数（以 tree.json 的 MessageNode 为准，DB 中的孤立行不计入）
        message_nodes = [
            n for n in self._tree.get_descendants(session_id)
            if isinstance(n, MessageNode)
        ]
        updates["message_count"] = len(message_nodes)

        self._tree.update_node(session_id, **updates)


# ──────────────────────────────────────────────
# 领域异常
# ──────────────────────────────────────────────

class ConversationNotFoundError(Exception):
    """对话不存在时抛出，由 Controller 层捕获并处理。"""
    def __init__(self, session_id: str) -> None:
        super().__init__(f"Conversation not found: {session_id}")
        self.session_id = session_id
