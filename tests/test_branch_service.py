# File: tests/test_branch_service.py
# 分叉分支服务（BranchService）单元测试
# 运行方式: python -m pytest tests/test_branch_service.py -v
#         或 python tests/test_branch_service.py

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import ConfigScope
from app.core.branch_service import BranchService
from app.storage.branch_store import BranchStore
from app.storage.database import get_connection, initialize_database
from app.storage.message_repo import MessageRepo
from app.storage.models import ConversationNode, Message, MessageNode, Role
from app.storage.tree_store import TreeStore


def _msg(conv_id: str, msg_id: str, role: Role, content: str) -> Message:
    return Message(
        id=msg_id,
        conversation_id=conv_id,
        role=role,
        content=content,
    )


class BranchServiceTestCase(unittest.TestCase):
    """公共 setUp：隔离 DB + 临时树目录，建 conv + 链 M1..M4。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls._base = Path(cls._tmpdir.name)
        cls._scope = ConfigScope(
            DB_PATH=str(cls._base / "test.db"),
        )
        cls._scope.__enter__()
        initialize_database()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._scope.__exit__(None, None, None)
        cls._tmpdir.cleanup()

    def setUp(self) -> None:
        conn = get_connection()
        with conn:
            conn.execute("DELETE FROM branch_nodes")
            conn.execute("DELETE FROM fork_nodes")
            conn.execute("DELETE FROM messages")
        self._tree_dir = Path(tempfile.mkdtemp(dir=str(self._base)))
        self.tree = TreeStore(base_path=self._tree_dir)
        self.branch_store = BranchStore()
        self.repo = MessageRepo()
        self.svc = BranchService(self.tree, self.branch_store, self.repo)

        self.tree.create_node(ConversationNode(
            id="conv", parent_id="root", title="对话",
        ))
        for i in range(1, 5):
            node_id = f"m{i}"
            role = Role.USER if i % 2 == 1 else Role.ASSISTANT
            self.tree.create_node(MessageNode(
                id=node_id, parent_id="conv", message_id=node_id,
                role=role.value, title=f"{role.value}: 内容{i}", preview=f"内容{i}",
            ))
            self.repo.save_message(_msg("conv", node_id, role, f"内容{i}"))
        self.tree.clear_dirty()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tree_dir, ignore_errors=True)

    def _chain_ids(self) -> list[str]:
        return [n.id for n in self.tree.get_conversation_chain("conv")]

    def _new_nodes(self, *ids: str) -> list[MessageNode]:
        """从 id 列表构造新链（未入库的节点，供首个 create 使用）。"""
        result = []
        for i, nid in enumerate(ids):
            if i % 2 == 0:
                role, content = Role.USER.value, f"新内容{i}"
            else:
                role, content = Role.ASSISTANT.value, f"新回答{i}"
            result.append(MessageNode(
                id=nid, parent_id="conv", message_id=nid,
                role=role, title=f"{role}: {content}", preview=content,
            ))
        return result

    def _chain_from(self, *ids: str) -> list[MessageNode]:
        """
        构造新链：已存在于树中的节点复用树副本（保留分叉字段），
        新 id 生成新节点。与生产调用方式一致——共享前缀节点必须携带
        分叉字段，否则 replace 后会丢失分叉点标记。
        """
        result = []
        for i, nid in enumerate(ids):
            existing = self.tree.get_node(nid)
            if isinstance(existing, MessageNode):
                result.append(existing)
                continue
            if i % 2 == 0:
                role, content = Role.USER.value, f"新内容{i}"
            else:
                role, content = Role.ASSISTANT.value, f"新回答{i}"
            result.append(MessageNode(
                id=nid, parent_id="conv", message_id=nid,
                role=role, title=f"{role}: {content}", preview=content,
            ))
        return result


# ──────────────────────────────────────────────
# 同步
# ──────────────────────────────────────────────

class TestSync(BranchServiceTestCase):

    def test_sync_dirty_archives_node_metadata(self) -> None:
        """脏对话同步后，节点元数据应进入 fork_nodes。"""
        self.tree.update_node("m2", title="改标题")
        self.assertIn("conv", self.tree.dirty_conversations)
        self.svc.sync_dirty_conversations()
        meta = self.branch_store.get_node("m2")
        self.assertEqual(meta["title"], "改标题")
        self.assertEqual(meta["role"], "assistant")
        self.assertEqual(self.tree.dirty_conversations, set())

    def test_sync_writes_current_branch_for_fork_point(self) -> None:
        """分叉点的当前分支记录应与 tree.json 链截取一致。"""
        # 造一个分叉点 m2（count=2, index=1, 分支记录 0/1）
        self.svc.create_branch(
            "conv", "m2", self._new_nodes("m1", "m2", "m3b", "m4b")
        )
        # 之后用户改动链（重命名 m4b）→ 标脏
        self.tree.clear_dirty()
        self.tree.update_node("m4b", title="改名")
        # 脏同步:当前分支 1 = [m2, m3b, m4b] 应被覆写为最新状态
        self.svc.sync_dirty_conversations()
        branch1 = self.branch_store.get_branch_list("m2", 1)
        self.assertEqual(branch1, ["m2", "m3b", "m4b"])
        meta = self.branch_store.get_node("m4b")
        self.assertEqual(meta["title"], "改名")


# ──────────────────────────────────────────────
# 分支新增
# ──────────────────────────────────────────────

class TestCreateBranch(BranchServiceTestCase):

    def test_create_branch_first_fork(self) -> None:
        """首次分叉:归档旧链为分支 0,新建分支 1,更新字段,替换链。"""
        self.svc.create_branch(
            "conv", "m2", self._new_nodes("m1", "m2", "m3b", "m4b")
        )
        # 分支记录
        self.assertEqual(
            self.branch_store.get_branch_list("m2", 0), ["m2", "m3", "m4"]
        )
        self.assertEqual(
            self.branch_store.get_branch_list("m2", 1), ["m2", "m3b", "m4b"]
        )
        # 分叉点字段
        m2 = self.tree.get_node("m2")
        self.assertTrue(m2.is_fork_point)
        self.assertEqual(m2.fork_branch_count, 2)
        self.assertEqual(m2.fork_current_index, 1)
        # 树链替换,旧节点移除
        self.assertEqual(self._chain_ids(), ["m1", "m2", "m3b", "m4b"])
        self.assertIsNone(self.tree.get_node("m3"))
        self.assertIsNone(self.tree.get_node("m4"))
        # 新节点元数据已归档
        self.assertEqual(self.branch_store.get_node("m3b")["role"], "user")
        # 脏标记清除
        self.assertEqual(self.tree.dirty_conversations, set())

    def test_create_branch_index_is_max_plus_one(self) -> None:
        """再次分叉:新分支编号 = 最大编号 + 1。"""
        self.svc.create_branch(
            "conv", "m2", self._new_nodes("m1", "m2", "m3b", "m4b")
        )
        self.svc.create_branch(
            "conv", "m2", self._chain_from("m1", "m2", "m3c", "m4c")
        )
        self.assertEqual(self.branch_store.get_branch_indexes("m2"), [0, 1, 2])
        m2 = self.tree.get_node("m2")
        self.assertEqual(m2.fork_branch_count, 3)
        self.assertEqual(m2.fork_current_index, 2)
        self.assertEqual(self._chain_ids(), ["m1", "m2", "m3c", "m4c"])

    def test_create_branch_conversation_root_fork(self) -> None:
        """第一条消息被修改:分叉点 = 对话节点,分支列表以对话节点为头。"""
        self.svc.create_branch(
            "conv", "conv", self._new_nodes("m1b", "m2b")
        )
        self.assertEqual(
            self.branch_store.get_branch_list("conv", 0),
            ["conv", "m1", "m2", "m3", "m4"],
        )
        self.assertEqual(
            self.branch_store.get_branch_list("conv", 1),
            ["conv", "m1b", "m2b"],
        )
        conv = self.tree.get_node("conv")
        self.assertTrue(conv.is_fork_point)
        self.assertEqual(conv.fork_branch_count, 2)
        self.assertEqual(conv.fork_current_index, 1)
        self.assertEqual(self._chain_ids(), ["m1b", "m2b"])

    def test_create_branch_refreshes_parent_fork_branch(self) -> None:
        """嵌套分叉时,父分叉点的当前分支记录应随新链刷新。"""
        # 修改 m3 → 分叉点 m2(分支 1 = [m2, m3b, m4b])
        self.svc.create_branch(
            "conv", "m2", self._new_nodes("m1", "m2", "m3b", "m4b")
        )
        # 修改 m4b → 分叉点 m3b(新链 [m1, m2, m3b, m4b', m5b'])
        self.svc.create_branch(
            "conv", "m3b",
            self._chain_from("m1", "m2", "m3b", "m4b2", "m5b"),
        )
        # 父分叉点 m2 的当前分支(1)应刷新为 [m2, m3b](遇分叉点停止)
        self.assertEqual(
            self.branch_store.get_branch_list("m2", 1), ["m2", "m3b"]
        )
        # m3b 的分支
        self.assertEqual(
            self.branch_store.get_branch_list("m3b", 0), ["m3b", "m4b"]
        )
        self.assertEqual(
            self.branch_store.get_branch_list("m3b", 1), ["m3b", "m4b2", "m5b"]
        )
        m3b = self.tree.get_node("m3b")
        self.assertTrue(m3b.is_fork_point)
        self.assertEqual(m3b.fork_branch_count, 2)

    def test_extend_current_branch(self) -> None:
        """修改重发送流完成:新 assistant 追加到当前分支与链。"""
        # 发送修改后的 user:m3b(incomplete),分支已建
        m3b = self._new_nodes("m1", "m2", "m3b")[2]
        m3b.incomplete = True
        self.svc.create_branch(
            "conv", "m2", self._new_nodes("m1", "m2", "m3b")
        )
        # 流式完成:追加 assistant m4b
        m4b = self._new_nodes("m4b")[0]
        m4b.role = "assistant"
        self.svc.extend_current_branch("conv", [m4b])
        self.assertEqual(self._chain_ids(), ["m1", "m2", "m3b", "m4b"])
        self.assertEqual(
            self.branch_store.get_branch_list("m2", 1),
            ["m2", "m3b", "m4b"],
        )
        self.assertEqual(self.tree.dirty_conversations, set())

    def test_extend_current_branch_conversation_root(self) -> None:
        """对话根分叉时的 extend:分支记录以对话节点为头追加。"""
        self.svc.create_branch(
            "conv", "conv", self._new_nodes("m1b", "m2b")
        )
        m3b = self._new_nodes("m3b")[0]
        m3b.role = "assistant"
        self.svc.extend_current_branch("conv", [m3b])
        self.assertEqual(self._chain_ids(), ["m1b", "m2b", "m3b"])
        self.assertEqual(
            self.branch_store.get_branch_list("conv", 1),
            ["conv", "m1b", "m2b", "m3b"],
        )


# ──────────────────────────────────────────────
# 分支切换
# ──────────────────────────────────────────────

class TestSwitchBranch(BranchServiceTestCase):

    def _two_branches(self) -> None:
        """构造 m2 分叉点,分支 0 = [m2,m3,m4],分支 1 = [m2,m3b,m4b]。"""
        self.svc.create_branch(
            "conv", "m2", self._new_nodes("m1", "m2", "m3b", "m4b")
        )

    def test_switch_back_to_original(self) -> None:
        """切回分支 0:链恢复原状,分叉点 index 更新。"""
        self._two_branches()
        chain = self.svc.switch_branch("conv", "m2", 0)
        self.assertEqual([n.id for n in chain], ["m1", "m2", "m3", "m4"])
        self.assertEqual(self._chain_ids(), ["m1", "m2", "m3", "m4"])
        m2 = self.tree.get_node("m2")
        self.assertEqual(m2.fork_current_index, 0)
        # 分支 0 的节点元数据已归档(切回时从归档重建)
        meta = self.branch_store.get_node("m3")
        self.assertEqual(meta["role"], "user")
        self.assertEqual(self.tree.dirty_conversations, set())

    def test_switch_to_newest(self) -> None:
        """切到分支 1:链替换为新分支内容。"""
        self._two_branches()
        chain = self.svc.switch_branch("conv", "m2", 1)
        self.assertEqual([n.id for n in chain], ["m1", "m2", "m3b", "m4b"])

    def test_switch_nested_expands_to_largest(self) -> None:
        """外层切换时,嵌套分叉点按最大编号分支展开(1.4/3.5.3)。"""
        # 修改 m3 → 分叉点 m2:0=[m2,m3,m4], 1=[m2,m3b,m4b];链=[m1,m2,m3b,m4b]
        self.svc.create_branch(
            "conv", "m2", self._new_nodes("m1", "m2", "m3b", "m4b")
        )
        # 修改 m4b → 分叉点 m3b:0=[m3b,m4b], 1=[m3b,m4b2,m5b]
        self.svc.create_branch(
            "conv", "m3b", self._chain_from("m1", "m2", "m3b", "m4b2", "m5b")
        )
        # 切到 m2 分支 0([m2,m3,m4],m3 不是分叉点)
        self.svc.switch_branch("conv", "m2", 0)
        self.assertEqual(self._chain_ids(), ["m1", "m2", "m3", "m4"])
        # 在分支 0 中修改 m4 → 分叉点 m3:0=[m3,m4], 1=[m3,m4x,m5x]
        self.svc.create_branch(
            "conv", "m3", self._chain_from("m1", "m2", "m3", "m4x", "m5x")
        )
        # 切回 m2 分支 1:尾部 m3b 是分叉点 → 按最大编号展开(1)
        chain = self.svc.switch_branch("conv", "m2", 1)
        self.assertEqual(
            [n.id for n in chain], ["m1", "m2", "m3b", "m4b2", "m5b"]
        )
        # 嵌套分叉点 m3b 的 current index 更新为最大(1)
        m3b = self.tree.get_node("m3b")
        self.assertEqual(m3b.fork_current_index, 1)

    def test_switch_reconstructs_from_archive(self) -> None:
        """切分支后,不在树中的节点应从归档重建;元数据经脏同步保持最新。"""
        self._two_branches()
        # 切到分支 0:旧链节点 m3/m4 从归档重建(首次分叉时已归档)
        chain = self.svc.switch_branch("conv", "m2", 0)
        m3 = next(n for n in chain if n.id == "m3")
        self.assertEqual(m3.title, "user: 内容3")
        # 在分支 0 中改名 m3 → 切走(触发脏同步)→ 切回,标题保持
        self.tree.update_node("m3", title="改名后的标题")
        self.svc.switch_branch("conv", "m2", 1)
        chain2 = self.svc.switch_branch("conv", "m2", 0)
        m3b = next(n for n in chain2 if n.id == "m3")
        self.assertEqual(m3b.title, "改名后的标题")


# ──────────────────────────────────────────────
# 分支删除
# ──────────────────────────────────────────────

class TestDeleteBranch(BranchServiceTestCase):

    def _two_branches(self) -> None:
        self.svc.create_branch(
            "conv", "m2", self._new_nodes("m1", "m2", "m3b", "m4b")
        )

    def test_delete_branch_degrades_showing_remaining_branch(self) -> None:
        """删分支 1(剩 1 条)→ 退化:切到剩余分支显示完整内容(拍板 B),
        分支记录删除、标记移除(方案 A)。"""
        self._two_branches()
        self.svc.delete_branch("conv", "m2", 1)
        # 视图 = 剩余分支 0 的完整内容
        self.assertEqual(self._chain_ids(), ["m1", "m2", "m3", "m4"])
        # 退化:标记移除、计数归零、唯一分支记录删除
        m2 = self.tree.get_node("m2")
        self.assertFalse(m2.is_fork_point)
        self.assertEqual(m2.fork_branch_count, 0)
        self.assertEqual(m2.fork_current_index, 0)
        self.assertEqual(self.branch_store.get_branch_indexes("m2"), [])
        # 剩余分支节点保留在树与消息行中
        rows = {m.id for m in self.repo.get_messages("conv")}
        self.assertIn("m3", rows)
        self.assertIn("m4", rows)

    def test_delete_first_branch_switches_to_last(self) -> None:
        """3 分支删分支 0 → 切到最后一个剩余分支。"""
        self._two_branches()
        self.svc.create_branch(
            "conv", "m2", self._chain_from("m1", "m2", "m3c", "m4c")
        )
        self.svc.delete_branch("conv", "m2", 0)
        self.assertEqual(self._chain_ids(), ["m1", "m2", "m3c", "m4c"])
        m2 = self.tree.get_node("m2")
        self.assertEqual(m2.fork_current_index, 2)
        self.assertEqual(m2.fork_branch_count, 2)
        self.assertTrue(m2.is_fork_point)

    def test_delete_branch_orphan_rows_purged(self) -> None:
        """被删分支的消息行与元数据行物理删除;剩余分支的引用保留。"""
        self._two_branches()
        # 分支 1 的节点入库
        self.repo.save_message(_msg("conv", "m3b", Role.USER, "新内容"))
        self.repo.save_message(_msg("conv", "m4b", Role.ASSISTANT, "新回答"))
        self.svc.delete_branch("conv", "m2", 1)
        # 分支 1 的节点行删除
        rows = {m.id for m in self.repo.get_messages("conv")}
        self.assertNotIn("m3b", rows)
        self.assertNotIn("m4b", rows)
        # 剩余分支(0)的节点行保留
        self.assertIn("m3", rows)
        self.assertIn("m4", rows)
        self.assertIsNone(self.branch_store.get_node("m3b"))
        self.assertIsNotNone(self.branch_store.get_node("m3"))

    def test_delete_branch_cascade_nested(self) -> None:
        """被删分支含嵌套分叉点 → 其下所有分支级联删除。"""
        # m3 分叉点:0=[m3,m4], 1=[m3,m4b,m5b]
        self.svc.create_branch(
            "conv", "m3", self._new_nodes("m1", "m2", "m3", "m4b", "m5b")
        )
        # 在 m4b 上分叉:0=[m4b,m5b], 1=[m4b,m5b2,m6b]
        self.svc.create_branch(
            "conv", "m4b", self._chain_from("m1", "m2", "m3", "m4b", "m5b2", "m6b")
        )
        # 当前链 = [m1,m2,m3,m4b,m5b2,m6b];删 m3 分支 1
        self.svc.delete_branch("conv", "m3", 1)
        # 级联:m4b 名下所有分支删除
        self.assertEqual(self.branch_store.get_branch_indexes("m4b"), [])
        # 退化(剩分支 0):视图 = [m1,m2,m3,m4];m3 标记移除、记录删除
        self.assertEqual(self._chain_ids(), ["m1", "m2", "m3", "m4"])
        self.assertEqual(self.branch_store.get_branch_indexes("m3"), [])
        self.assertFalse(self.tree.get_node("m3").is_fork_point)

    def test_delete_branch_keeps_trash_referenced_rows(self) -> None:
        """回收站引用的节点行保留(防御)。"""
        self._two_branches()
        self.repo.save_message(_msg("conv", "m3b", Role.USER, "内容"))
        # 模拟 m3b 在回收站(被单独软删过但行未清)
        from app.storage.models import TrashEntry
        from app.storage.tree_store import _node_to_dict
        trash = [
            TrashEntry(
                id="trash1", json_path="root/对话/User: 新内容",
                node_data=_node_to_dict(MessageNode(
                    id="m3b", parent_id="conv", message_id="m3b",
                    role="user", title="User: 新内容", preview="内容",
                )),
            )
        ]
        with open(self._tree_dir / "recycle_bin.json", "w", encoding="utf-8") as f:
            import json
            json.dump(
                [{"id": e.id, "json_path": e.json_path,
                  "node_data": e.node_data,
                  "deleted_at": e.deleted_at.isoformat()} for e in trash],
                f, ensure_ascii=False,
            )
        self.svc.delete_branch("conv", "m2", 1)
        rows = {m.id for m in self.repo.get_messages("conv")}
        self.assertIn("m3b", rows)  # 回收站引用 → 行保留

    def test_delete_branch_syncs_dirty_first(self) -> None:
        """删除前先同步脏对话(3.4.1 同步时机 2)。"""
        self._two_branches()
        self.tree.clear_dirty()
        # 造一个脏对话(改动当前链中节点的标题)
        self.tree.update_node("m1", title="改过标题")
        self.svc.delete_branch("conv", "m2", 1)
        # m1 在剩余分支与树中 → 归档元数据应为同步后的最新值
        meta = self.branch_store.get_node("m1")
        self.assertEqual(meta["title"], "改过标题")
        self.assertEqual(self.tree.dirty_conversations, set())


# ──────────────────────────────────────────────
# 分叉点删除(3.3.4)
# ──────────────────────────────────────────────

class TestDeleteForkPoint(BranchServiceTestCase):

    def test_delete_fork_point_transfers_to_predecessor(self) -> None:
        """删除分叉点:数据转交前驱,被修改节点身份不变。"""
        self.svc.create_branch(
            "conv", "m2", self._new_nodes("m1", "m2", "m3b", "m4b")
        )
        self.svc.delete_fork_point("conv", "m2")
        # 新分叉点 = m1(m2 的前驱)
        self.assertEqual(self.branch_store.get_branch_indexes("m1"), [0, 1])
        self.assertEqual(
            self.branch_store.get_branch_list("m1", 0), ["m1", "m3", "m4"]
        )
        self.assertEqual(
            self.branch_store.get_branch_list("m1", 1), ["m1", "m3b", "m4b"]
        )
        m1 = self.tree.get_node("m1")
        self.assertTrue(m1.is_fork_point)
        self.assertEqual(m1.fork_branch_count, 2)
        self.assertEqual(m1.fork_current_index, 1)
        # 旧分叉点清除
        m2 = self.tree.get_node("m2")
        self.assertFalse(m2.is_fork_point)
        self.assertEqual(m2.fork_branch_count, 0)

    def test_delete_fork_point_root_goes_to_conversation(self) -> None:
        """分叉点是第一条消息(无前驱)→ 对话节点承接。"""
        # 修改 m2 → 分叉点 m1(第一条消息,无前驱)
        self.svc.create_branch(
            "conv", "m1", self._new_nodes("m1", "m2b", "m3b", "m4b")
        )
        self.assertEqual(
            self.branch_store.get_branch_list("m1", 0), ["m1", "m2", "m3", "m4"]
        )
        self.svc.delete_fork_point("conv", "m1")
        # m1 无前驱 → 对话节点承接
        self.assertEqual(self.branch_store.get_branch_indexes("conv"), [0, 1])
        self.assertEqual(
            self.branch_store.get_branch_list("conv", 0),
            ["conv", "m2", "m3", "m4"],
        )
        conv = self.tree.get_node("conv")
        self.assertTrue(conv.is_fork_point)
        self.assertEqual(conv.fork_branch_count, 2)
        self.assertEqual(conv.fork_current_index, 1)
        m1 = self.tree.get_node("m1")
        self.assertFalse(m1.is_fork_point)
        self.assertEqual(m1.fork_branch_count, 0)

    def test_delete_fork_point_merges_into_existing_fork(self) -> None:
        """前驱已是分叉点 → 数据并入(编号接续,计数累加,index 保持)。"""
        # 修改 m2 → 分叉点 m1(留在链中);新链 [m1, m2b, m3b, m4b]
        self.svc.create_branch(
            "conv", "m1", self._new_nodes("m1", "m2b", "m3b", "m4b")
        )
        self.assertEqual(
            self.branch_store.get_branch_list("m1", 1),
            ["m1", "m2b", "m3b", "m4b"],
        )
        # 修改 m3b → 分叉点 m2b;m1 的当前分支 1 刷新为 [m1, m2b](遇分叉点停止)
        self.svc.create_branch(
            "conv", "m2b", self._chain_from("m1", "m2b", "m3b2", "m4b2")
        )
        self.assertEqual(
            self.branch_store.get_branch_list("m1", 1), ["m1", "m2b"]
        )
        self.assertEqual(
            self.branch_store.get_branch_list("m2b", 0), ["m2b", "m3b", "m4b"]
        )
        self.assertEqual(
            self.branch_store.get_branch_list("m2b", 1), ["m2b", "m3b2", "m4b2"]
        )
        # 删除分叉点 m2b → 新分叉点 = 前驱 m1(已是分叉点)→ 数据并入
        self.svc.delete_fork_point("conv", "m2b")
        self.assertEqual(self.branch_store.get_branch_indexes("m2b"), [])
        self.assertEqual(self.branch_store.get_branch_indexes("m1"), [0, 1, 2, 3])
        # m2b 分支 0 → m1 分支 2;分支 1 → m1 分支 3(编号接续)
        self.assertEqual(
            self.branch_store.get_branch_list("m1", 2), ["m1", "m3b", "m4b"]
        )
        self.assertEqual(
            self.branch_store.get_branch_list("m1", 3), ["m1", "m3b2", "m4b2"]
        )
        m1 = self.tree.get_node("m1")
        self.assertTrue(m1.is_fork_point)
        self.assertEqual(m1.fork_branch_count, 4)
        self.assertEqual(m1.fork_current_index, 1)  # index 保持(用户正在看分支 1)
        self.assertFalse(self.tree.get_node("m2b").is_fork_point)


# ──────────────────────────────────────────────
# 数据迁移与对话级联
# ──────────────────────────────────────────────

class TestMigration(BranchServiceTestCase):

    def test_migrate_branch_data(self) -> None:
        """分支数据 re-anchor 到新分叉点(编号接续)。"""
        self.svc.create_branch(
            "conv", "m2", self._new_nodes("m1", "m2", "m3b", "m4b")
        )
        # 新分叉点 m1(已是分叉点):并入
        self.svc.migrate_branch_data("conv", "m2", "m1")
        self.assertEqual(self.branch_store.get_branch_indexes("m2"), [])
        self.assertEqual(self.branch_store.get_branch_indexes("m1"), [0, 1])
        self.assertEqual(
            self.branch_store.get_branch_list("m1", 1), ["m1", "m3b", "m4b"]
        )
        self.assertTrue(self.tree.get_node("m1").is_fork_point)
        self.assertFalse(self.tree.get_node("m2").is_fork_point)

    def test_transfer_branch_group_across_conversations(self) -> None:
        """跨对话整体迁移:分支数据移到目标对话分叉点名下。"""
        # 源对话 conv:分叉点 m2,分支 0/1
        self.svc.create_branch(
            "conv", "m2", self._new_nodes("m1", "m2", "m3b", "m4b")
        )
        # 目标对话 conv2
        self.tree.create_node(ConversationNode(
            id="conv2", parent_id="root", title="对话2",
        ))
        self.tree.create_node(MessageNode(
            id="x1", parent_id="conv2", message_id="x1", role="user",
            title="User: 目标", preview="目标",
        ))
        self.svc.transfer_branch_group("conv", "m2", "conv2", "x1")
        self.assertEqual(self.branch_store.get_branch_indexes("m2"), [])
        self.assertEqual(self.branch_store.get_branch_indexes("x1"), [0, 1])
        self.assertEqual(
            self.branch_store.get_branch_list("x1", 1), ["x1", "m3b", "m4b"]
        )
        self.assertTrue(self.tree.get_node("x1").is_fork_point)
        self.assertFalse(self.tree.get_node("m2").is_fork_point)
        # 两对话标脏
        self.assertIn("conv", self.tree.dirty_conversations)
        self.assertIn("conv2", self.tree.dirty_conversations)

    def test_delete_conversation_branches(self) -> None:
        """对话删除级联:全部分支数据清除。"""
        self.svc.create_branch(
            "conv", "m2", self._new_nodes("m1", "m2", "m3b", "m4b")
        )
        self.svc.delete_conversation_branches("conv")
        self.assertEqual(self.branch_store.get_branch_indexes("m2"), [])
        self.assertIsNone(self.branch_store.get_node("m3b"))
        # 消息行不删(对话可能从回收站恢复,由对话删除流程决定)
        self.assertEqual(len(self.repo.get_messages("conv")), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
