# Layer: Core
# File: app/core/branch_service.py
# Responsibility: 分叉分支业务编排（纯逻辑，无 UI）。
#                 tree.json = 工作副本，分支存储（fork_nodes/branch_nodes）= 归档库。
#                 职责：
#                 - 脏对话同步（3.4，仅切换/删除/新建分支三个时机触发）
#                 - 分支的新增 / 扩展 / 删除（含级联、退化、自动切换、孤儿行清理）
#                 - 分支的切换（含嵌套分叉按最大分支展开）
#                 - 分叉点删除（数据转交前驱，3.3.4）
#                 - 拖拽数据迁移助手（3.6）
# Input:  tree_store / branch_store / message_repo
# Output: 树与分支存储的同步状态
# 禁止: 直接 HTTP 调用、导入 UI 库

from __future__ import annotations

from app.storage.models import (
    ConversationNode,
    MessageNode,
)
from app.storage.tree_store import TreeStore
from app.storage.branch_store import BranchStore
from app.storage.message_repo import MessageRepo


class BranchService:
    """
    分叉分支编排服务。

    核心不变式（由本服务的所有操作维护）：
    - 分叉点与被修改节点相邻（被修改节点 = 分叉点的后继，无独立标记）
    - 对话中第一条消息被修改时，分叉点 = 对话节点本身
    - tree.json 只保存当前链路；历史分支全部以列表形式归档在 branch_nodes
    - 分叉点标记 is_fork_point == (branch_count >= 2)
    """

    def __init__(
        self,
        tree_store: TreeStore,
        branch_store: BranchStore,
        message_repo: MessageRepo,
    ) -> None:
        self._tree = tree_store
        self._branch = branch_store
        self._repo = message_repo

    # ──────────────────────────────────────────
    # 同步（spec 3.4）
    # ──────────────────────────────────────────

    def sync_dirty_conversations(self) -> None:
        """
        将全部脏对话（tree.json 已改、尚未归档）同步到分支存储。

        同步时机（3.4.1）：分支切换、删除分支、新建分支。其他操作只改
        tree.json，留待下次分支操作时统一归档。
        """
        dirty = self._tree.dirty_conversations
        if not dirty:
            return
        for conv_id in dirty:
            self._sync_conversation(conv_id)
        self._tree.clear_dirty()

    def _sync_conversation(self, conversation_id: str) -> None:
        """同步单个对话：节点元数据 + 每个分叉点的当前分支列表与 tree.json 一致。"""
        chain = self._tree.get_conversation_chain(conversation_id)
        conv = self._tree.get_node(conversation_id)
        if conv is not None and conv.is_fork_point:
            # 对话节点本身作为分叉点：当前分支 = [对话节点] + 整条链
            self._branch.set_branch(
                conversation_id, conversation_id, conv.fork_current_index,
                [conversation_id] + [n.id for n in chain],
            )
        for i, node in enumerate(chain):
            self._upsert_node_meta(node, conversation_id)
            if node.is_fork_point:
                branch_ids = [n.id for n in self._slice_chain(chain, i)]
                self._branch.set_branch(
                    conversation_id, node.id, node.fork_current_index, branch_ids,
                )

    def _slice_chain(self, chain: list[MessageNode], start_idx: int) -> list[MessageNode]:
        """
        从 start_idx 截取当前分支：以 start 节点为头部（分叉点），
        记录到下一个分叉点（含）或链尾停止（2.3 路径分解法规则）。
        """
        result = [chain[start_idx]]
        for n in chain[start_idx + 1:]:
            result.append(n)
            if n.is_fork_point:
                break
        return result

    def _upsert_node_meta(self, node: MessageNode, conversation_id: str) -> None:
        self._branch.upsert_node(
            conversation_id=conversation_id,
            node_id=node.id,
            role=node.role,
            title=node.title,
            preview=node.preview,
            parent_id=node.parent_id,
            sort_order=node.sort_order,
            enabled=node.enabled,
            incomplete=node.incomplete,
            thinking_message_id=getattr(node, "thinking_message_id", None),
            is_fork_point=node.is_fork_point,
            fork_branch_count=node.fork_branch_count,
            fork_current_index=node.fork_current_index,
            updated_at=node.updated_at,
        )

    # ──────────────────────────────────────────
    # 分支新增（spec 3.2 / 5.3）
    # ──────────────────────────────────────────

    def create_branch(
        self,
        conversation_id: str,
        fork_point_id: str,
        new_chain: list[MessageNode],
    ) -> None:
        """
        新建分支。

        调用时机：
        - 修改重发送：发送修改后的 user 消息时立即执行（5.3，无预分支）
        - 重新生成：流式输出完成后执行（3.1.4，预分支落地）

        Args:
            conversation_id: 对话节点 ID
            fork_point_id:   分叉点 ID（被修改节点的前驱；第一条消息时为对话节点 ID）
            new_chain:       完整新链路（含分叉点之前的部分）
        """
        self.sync_dirty_conversations()

        # 1. 首次分叉：归档当前链为分支 0（3.2.2 步骤 4）
        self._ensure_branches(conversation_id, fork_point_id)

        # 2. 新分支编号 = 现有最大编号 + 1（3.2.3）
        indexes = self._branch.get_branch_indexes(fork_point_id)
        new_index = max(indexes, default=-1) + 1

        # 3. 新分支列表 = 新链从分叉点截取（头部 = 分叉点）
        new_branch = self._branch_ids_from_chain(
            conversation_id, fork_point_id, new_chain
        )
        self._branch.add_branch(conversation_id, fork_point_id, new_index, new_branch)

        # 4. 替换 tree.json 链（此时分叉字段尚未更新，先换链）
        self._tree.replace_conversation_chain(conversation_id, new_chain)

        # 5. 更新分叉点 fork 字段（先换链再更新，避免副本覆盖）
        new_count = len(self._branch.get_branch_indexes(fork_point_id))
        self._tree.update_node(
            fork_point_id,
            is_fork_point=True,
            fork_branch_count=new_count,
            fork_current_index=new_index,
        )
        # 新链中其他嵌套分叉点：刷新计数（与 DB 一致）
        for n in self._tree.get_conversation_chain(conversation_id):
            if n.is_fork_point and n.id != fork_point_id:
                c = len(self._branch.get_branch_indexes(n.id))
                self._tree.update_node(n.id, fork_branch_count=c)

        # 6. 完整同步新链（元数据 + 所有分叉点的当前分支记录刷新——
        #    新链改变了父级分叉点的截取，旧记录可能过时）+ 清脏
        self._sync_conversation(conversation_id)
        self._tree.clear_dirty(conversation_id)

    def _ensure_branches(self, conversation_id: str, fork_point_id: str) -> None:
        """首次分叉时归档当前链为分支 0（分叉点已有记录则跳过）。

        注意：归档必须同步写入节点元数据——旧链节点即将离开 tree.json，
        切换回该分支时需要从 fork_nodes 重建，不能依赖脏标记（脏标记不持久化，
        重启后为空，且旧链节点可能从未被同步过）。
        """
        if self._branch.get_branch_indexes(fork_point_id):
            return
        chain = self._tree.get_conversation_chain(conversation_id)
        if fork_point_id == conversation_id:
            branch_ids = [conversation_id] + [n.id for n in chain]
        else:
            pos = next(
                (i for i, n in enumerate(chain) if n.id == fork_point_id), None
            )
            if pos is None:
                raise ValueError(f"Fork point {fork_point_id} not in chain")
            branch_ids = [n.id for n in self._slice_chain(chain, pos)]
        self._branch.add_branch(conversation_id, fork_point_id, 0, branch_ids)
        # 归档被归档分支节点的元数据（供切换回该分支时重建）
        archived = set(branch_ids)
        for node in chain:
            if node.id in archived:
                self._upsert_node_meta(node, conversation_id)

    def _branch_ids_from_chain(
        self, conversation_id: str, fork_point_id: str, chain: list[MessageNode]
    ) -> list[str]:
        """从完整链路中截取以分叉点为头部的分支列表。"""
        if fork_point_id == conversation_id:
            return [conversation_id] + [n.id for n in chain]
        pos = next(
            (i for i, n in enumerate(chain) if n.id == fork_point_id), None
        )
        if pos is None:
            raise ValueError(f"Fork point {fork_point_id} not in chain")
        return [n.id for n in self._slice_chain(chain, pos)]

    def extend_current_branch(
        self, conversation_id: str, appended_nodes: list[MessageNode]
    ) -> None:
        """
        修改重发送流式完成后：将新生成的节点追加到当前分支与 tree.json 链。

        分支在发送修改后的消息时已创建（5.3），此处只补上流式完成的节点
        （assistant 消息），并清除 user 节点的 incomplete 标记（由调用方完成）。
        """
        chain = self._tree.get_conversation_chain(conversation_id)

        # 定位当前分支头部 = 链中最后一个分叉点；对话根分叉时 = 对话节点
        last_fp = None
        for n in chain:
            if n.is_fork_point:
                last_fp = n
        conv = self._tree.get_node(conversation_id)
        if last_fp is None and conv is not None and conv.is_fork_point:
            last_fp = conv

        if last_fp is not None:
            branch_list = self._branch.get_branch_list(
                last_fp.id, last_fp.fork_current_index
            )
            branch_list.extend(n.id for n in appended_nodes)
            self._branch.set_branch(
                conversation_id, last_fp.id, last_fp.fork_current_index, branch_list,
            )

        self._tree.replace_conversation_chain(conversation_id, chain + appended_nodes)
        self._sync_chain_metadata(conversation_id)
        self._tree.clear_dirty(conversation_id)

    # ──────────────────────────────────────────
    # 分支切换（spec 3.5）
    # ──────────────────────────────────────────

    def switch_branch(
        self, conversation_id: str, fork_point_id: str, target_index: int
    ) -> list[MessageNode]:
        """
        切换分支（3.4.4 流程）：
        1. 同步当前 tree.json 状态到数据库
        2. 读取目标分支完整节点列表
        3. 嵌套分叉点按"最大编号分支"逐层展开（1.4/3.5.3）
        4. 重建链路并整体替换 tree.json 链
        5. 更新所有分叉点的 current index，同步元数据回数据库（2.4）

        Returns:
            重建后的完整链路（MessageNode 列表）
        """
        self.sync_dirty_conversations()
        new_chain, touched = self._build_chain(
            conversation_id, fork_point_id, target_index
        )

        self._tree.replace_conversation_chain(conversation_id, new_chain)
        for nid, idx in touched.items():
            self._tree.update_node(nid, fork_current_index=idx)
        self._sync_chain_metadata(conversation_id)
        self._tree.clear_dirty(conversation_id)
        return new_chain

    def _build_chain(
        self, conversation_id: str, fork_point_id: str, target_index: int
    ) -> tuple[list[MessageNode], dict[str, int]]:
        """
        重建完整链路：分叉点之前的前缀 + 目标分支展开。

        Returns:
            (new_chain, touched) — touched: {分叉点 ID: 切换后的 current index}
        """
        suffix, touched = self._expand(fork_point_id, target_index)

        if fork_point_id == conversation_id:
            prefix_ids: list[str] = []
        else:
            chain = self._tree.get_conversation_chain(conversation_id)
            pos = next(
                (i for i, n in enumerate(chain) if n.id == fork_point_id), None
            )
            if pos is None:
                raise ValueError(f"Fork point {fork_point_id} not in chain")
            prefix_ids = [n.id for n in chain[:pos]]

        return self._reconstruct_nodes(
            conversation_id, prefix_ids + suffix
        ), touched

    def _expand(
        self, fork_point_id: str, branch_index: int
    ) -> tuple[list[str], dict[str, int]]:
        """
        目标分支展开：读取分支列表；若尾部是嵌套分叉点，按最大编号分支
        逐层展开直至叶子（1.4/3.5.3）。返回 (节点 id 列表, touched 索引映射)。
        """
        lst = self._branch.get_branch_list(fork_point_id, branch_index)
        result = list(lst)
        touched: dict[str, int] = {fork_point_id: branch_index}
        tail = lst[-1]
        if tail != fork_point_id:
            sub_indexes = self._branch.get_branch_indexes(tail)
            if sub_indexes:
                sub, sub_touched = self._expand(tail, max(sub_indexes))
                result.extend(sub[1:])  # 尾部已在 result 中，跳过重复头部
                touched.update(sub_touched)
        return result, touched

    def _reconstruct_nodes(
        self, conversation_id: str, node_ids: list[str]
    ) -> list[MessageNode]:
        """
        按 id 列表重建 MessageNode：
        - 优先用 tree.json 中现有节点（用户可能已修改过元数据）
        - 否则从 fork_nodes 归档重建（含 role/preview/enabled/incomplete 等）
        """
        chain = self._tree.get_conversation_chain(conversation_id)
        current_map = {n.id: n for n in chain}
        result: list[MessageNode] = []
        for nid in node_ids:
            node = current_map.get(nid)
            if node is not None:
                result.append(node)
                continue
            meta = self._branch.get_node(nid)
            if meta is None:
                raise ValueError(
                    f"Node {nid} missing from fork archive (branch data corrupted)"
                )
            result.append(MessageNode(
                id=nid,
                parent_id=conversation_id,
                message_id=nid,
                role=meta["role"],
                title=meta["title"],
                preview=meta["preview"],
                enabled=meta["enabled"],
                incomplete=meta["incomplete"],
                thinking_message_id=meta["thinking_message_id"],
                is_fork_point=meta["is_fork_point"],
                fork_branch_count=meta["fork_branch_count"],
                fork_current_index=meta["fork_current_index"],
            ))
        return result

    # ──────────────────────────────────────────
    # 分支删除（spec 3.3）
    # ──────────────────────────────────────────

    def delete_branch(
        self, conversation_id: str, fork_point_id: str, branch_index: int
    ) -> None:
        """
        删除一个分支（被修改节点硬删除时调用）。

        流程（3.3.2）：
        1. 收集被删分支节点；分支内嵌套分叉点名下所有分支级联删除
        2. 删除分支记录
        3. 更新分叉点计数；仅剩 1 条分支时退化（方案 A：删除唯一记录、计数归零）
        4. 自动切换：前一个分支；删的是第一个则切到最后一个（3.3.3，用户拍板）
        5. 物理删除孤儿消息行与元数据行（未被当前链/其他分支/回收站引用者）
        """
        self.sync_dirty_conversations()

        # ── 1. 收集被删节点（级联） ──────────
        branch_list = self._branch.get_branch_list(fork_point_id, branch_index)
        removed_ids: set[str] = {
            nid for nid in branch_list if nid != fork_point_id
        }
        cascaded: list[str] = []
        seen: set[str] = set()

        def collect_below(fp_id: str) -> None:
            """递归收集 fp 名下全部分支的节点（嵌套分叉点继续向下）。"""
            if fp_id in seen:
                return
            seen.add(fp_id)
            for idx in self._branch.get_branch_indexes(fp_id):
                for nid in self._branch.get_branch_list(fp_id, idx):
                    if nid == fp_id:
                        continue
                    removed_ids.add(nid)
                    if self._branch.get_branch_indexes(nid):
                        collect_below(nid)

        for nid in list(removed_ids):
            if self._branch.get_branch_indexes(nid):
                cascaded.append(nid)
                collect_below(nid)

        # ── 2. 删除分支记录 ──────────────────
        self._branch.delete_branch(fork_point_id, branch_index)
        for fp_id in cascaded:
            self._branch.delete_all_for_fork_point(fp_id)

        # ── 3. 分支状态与链重建 ──────────────
        indexes = self._branch.get_branch_indexes(fork_point_id)
        count = len(indexes)

        if count >= 2:
            # 自动切换：前一个分支；删的是第一个 → 最后一个（3.3.3）
            target = branch_index - 1 if branch_index > 0 else max(indexes)
            new_chain, touched = self._build_chain(
                conversation_id, fork_point_id, target
            )
            self._tree.replace_conversation_chain(conversation_id, new_chain)
            self._tree.update_node(fork_point_id, fork_branch_count=count)
            for nid, idx in touched.items():
                self._tree.update_node(nid, fork_current_index=idx)
        else:
            # 退化：先切到唯一剩余分支（显示其完整内容，用户拍板 B），
            # 再删除分支记录（方案 A：计数归零、移除标记）
            target = indexes[0]
            new_chain, touched = self._build_chain(
                conversation_id, fork_point_id, target
            )
            self._tree.replace_conversation_chain(conversation_id, new_chain)
            self._branch.delete_branch(fork_point_id, indexes[0])
            for nid, idx in touched.items():
                if nid != fork_point_id:
                    self._tree.update_node(nid, fork_current_index=idx)
            self._tree.update_node(
                fork_point_id,
                is_fork_point=False,
                fork_branch_count=0,
                fork_current_index=0,
            )

        # ── 4. 物理删除孤儿行 ────────────────
        self._delete_orphan_rows(conversation_id, removed_ids)

        # ── 5. 同步元数据 + 清脏 ─────────────
        self._sync_chain_metadata(conversation_id)
        self._tree.clear_dirty(conversation_id)

    def _delete_orphan_rows(
        self, conversation_id: str, removed_ids: set[str]
    ) -> None:
        """物理删除分支节点行（未被当前链 / 其他分支 / 回收站引用者）。"""
        if not removed_ids:
            return
        referenced = self._branch.get_all_referenced_node_ids(conversation_id)
        chain_ids = {
            n.id for n in self._tree.get_conversation_chain(conversation_id)
        }
        trash_ids = self._trash_node_ids()
        doomed = [
            nid for nid in removed_ids
            if nid not in referenced
            and nid not in chain_ids
            and nid not in trash_ids
        ]
        if doomed:
            self._branch.delete_nodes(doomed)
            self._repo.delete_messages_by_ids(doomed)

    def _trash_node_ids(self) -> set[str]:
        """回收站中全部节点 ID（孤儿行清理时保留引用）。"""
        ids: set[str] = set()
        for entry in self._tree.list_trash():
            data = entry.node_data
            if isinstance(data, dict) and data.get("id"):
                ids.add(data["id"])
        return ids

    # ──────────────────────────────────────────
    # 分叉点删除（spec 3.3.4）
    # ──────────────────────────────────────────

    def delete_fork_point(self, conversation_id: str, fork_point_id: str) -> None:
        """
        删除分叉点：分叉身份与全部分支数据转交前驱节点（无前驱则交给对话节点），
        被修改节点身份不变。分叉点节点本身的删除由调用方（删除流程）按普通节点处理。

        规则（3.3.4）：
        - 新分叉点 = 分叉点在当前链中的前驱；无前驱 → 对话节点
        - 新分叉点原为普通节点：承接 count/index 成为分叉点
        - 新分叉点已是分叉点：数据并入（编号接续）、计数累加、index 保持
        """
        if fork_point_id == conversation_id:
            return  # 对话节点不可能被"删除分叉点"流程处理（对话删除是另一路径）
        chain = self._tree.get_conversation_chain(conversation_id)
        pos = next(
            (i for i, n in enumerate(chain) if n.id == fork_point_id), None
        )
        if pos is None:
            return  # 防御：不在当前链中
        new_fork_id = chain[pos - 1].id if pos > 0 else conversation_id
        self.migrate_branch_data(conversation_id, fork_point_id, new_fork_id)

    # ──────────────────────────────────────────
    # 拖拽数据迁移（spec 3.6）
    # ──────────────────────────────────────────

    def migrate_branch_data(
        self, conversation_id: str, old_fork_id: str, new_fork_id: str
    ) -> None:
        """
        分支数据迁移（re-anchor）：old_fork 名下全部分支列表的头部替换为
        new_fork 并从列表中移除 old_fork；编号接续 new_fork 现有最大编号。

        用于：
        - 拖拽节点到分叉点与被修改节点之间（新节点成为分叉点，原分叉点退化）
        - 分叉点被移走（新分叉点 = 被修改节点的前驱，3.6.2）
        - 分叉点被删除（3.3.4，数据转交前驱）
        """
        old_branches = self._branch.get_all_branches(old_fork_id)
        if not old_branches:
            return
        old_node = self._tree.get_node(old_fork_id)
        old_current = old_node.fork_current_index if old_node is not None else 0

        existing = self._branch.get_branch_indexes(new_fork_id)
        offset = max(existing, default=-1) + 1
        new_current: int | None = None
        for i, (idx, node_ids) in enumerate(sorted(old_branches.items())):
            new_list = [nid for nid in node_ids if nid != old_fork_id]
            if not new_list or new_list[0] != new_fork_id:
                new_list.insert(0, new_fork_id)
            self._branch.add_branch(
                conversation_id, new_fork_id, offset + i, new_list
            )
            if idx == old_current:
                new_current = offset + i
        self._branch.delete_all_for_fork_point(old_fork_id)

        # 字段迁移：old_fork 清除；new_fork 承接
        new_count = len(self._branch.get_branch_indexes(new_fork_id))
        new_fp_node = self._tree.get_node(new_fork_id)
        if new_fp_node is not None:
            if new_fp_node.is_fork_point:
                new_index = new_fp_node.fork_current_index  # 已分叉：index 保持
            else:
                new_index = new_current if new_current is not None else 0
            self._tree.update_node(
                new_fork_id,
                is_fork_point=True,
                fork_branch_count=new_count,
                fork_current_index=new_index,
            )
        self._tree.update_node(
            old_fork_id,
            is_fork_point=False,
            fork_branch_count=0,
            fork_current_index=0,
        )

    def transfer_branch_group(
        self,
        source_conv_id: str,
        fork_point_id: str,
        target_conv_id: str,
        target_fork_point_id: str,
    ) -> None:
        """
        跨对话整体迁移（3.6.1"其他对话的被修改节点"）：源对话分叉点名下的
        全部分支数据迁移到目标对话的分叉点名下（编号接续），源侧记录删除。
        节点树的移动由调用方（拖拽流程）完成，本方法只迁移分支数据。
        """
        branches = self._branch.get_all_branches(fork_point_id)
        if not branches:
            return
        src_node = self._tree.get_node(fork_point_id)
        src_current = src_node.fork_current_index if src_node is not None else 0

        existing = self._branch.get_branch_indexes(target_fork_point_id)
        offset = max(existing, default=-1) + 1
        new_current: int | None = None
        for i, (idx, node_ids) in enumerate(sorted(branches.items())):
            new_list = [nid for nid in node_ids if nid != fork_point_id]
            if not new_list or new_list[0] != target_fork_point_id:
                new_list.insert(0, target_fork_point_id)
            self._branch.add_branch(
                target_conv_id, target_fork_point_id, offset + i, new_list
            )
            if idx == src_current:
                new_current = offset + i
        self._branch.delete_all_for_fork_point(fork_point_id)

        tgt_node = self._tree.get_node(target_fork_point_id)
        if tgt_node is not None:
            new_count = len(self._branch.get_branch_indexes(target_fork_point_id))
            if tgt_node.is_fork_point:
                new_index = tgt_node.fork_current_index
            else:
                new_index = new_current if new_current is not None else 0
            self._tree.update_node(
                target_fork_point_id,
                is_fork_point=True,
                fork_branch_count=new_count,
                fork_current_index=new_index,
            )
        if src_node is not None:
            self._tree.update_node(
                fork_point_id,
                is_fork_point=False,
                fork_branch_count=0,
                fork_current_index=0,
            )

        # 两对话都标脏，由拖拽流程完成后统一同步
        self._tree.mark_conversation_dirty(source_conv_id)
        self._tree.mark_conversation_dirty(target_conv_id)

    # ──────────────────────────────────────────
    # 对话删除级联（spec 3.3.1）
    # ──────────────────────────────────────────

    def delete_conversation_branches(self, conversation_id: str) -> None:
        """对话（或含对话的文件夹）删除时：删除该对话的全部分支数据。"""
        self._branch.delete_all_for_conversation(conversation_id)
        self._tree.clear_dirty(conversation_id)

    # ──────────────────────────────────────────
    # 内部辅助
    # ──────────────────────────────────────────

    def _sync_chain_metadata(self, conversation_id: str) -> None:
        """把当前 tree.json 链的节点元数据同步回分支存储（2.4）。"""
        for node in self._tree.get_conversation_chain(conversation_id):
            self._upsert_node_meta(node, conversation_id)
