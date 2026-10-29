# File: tests/test_branch_store.py
# 分叉存储层（fork_nodes / branch_nodes 表）单元测试
# 运行方式: python -m pytest tests/test_branch_store.py -v
#         或 python tests/test_branch_store.py

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import ConfigScope
from app.storage.branch_store import BranchStore
from app.storage.database import initialize_database


class TestBranchStore(unittest.TestCase):
    """BranchStore 的 fork_nodes / branch_nodes 表 CRUD 测试。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls._base = Path(cls._tmpdir.name)
        cls._scope = ConfigScope(
            DB_PATH=str(cls._base / "test.db"),
        )
        cls._scope.__enter__()
        initialize_database()
        cls.store = BranchStore()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._scope.__exit__(None, None, None)
        cls._tmpdir.cleanup()

    def setUp(self) -> None:
        """每个测试前清空两张表（临时 DB 跨测试复用）。"""
        from app.storage.database import get_connection
        conn = get_connection()
        with conn:
            conn.execute("DELETE FROM branch_nodes")
            conn.execute("DELETE FROM fork_nodes")

    # ── fork_nodes ────────────────────────────

    def test_upsert_and_get_node(self) -> None:
        """upsert_node 后应能完整读回（含 enabled 三态编码）。"""
        now = datetime.utcnow()
        self.store.upsert_node(
            conversation_id="conv1", node_id="n1", title="标题",
            preview="预览", summary="摘要", parent_id="conv1", sort_order=2,
            enabled="some", incomplete=True, thinking_message_id="t1",
            is_fork_point=True, fork_branch_count=3, fork_current_index=1,
            updated_at=now,
        )
        got = self.store.get_node("n1")
        self.assertEqual(got["conversation_id"], "conv1")
        self.assertEqual(got["title"], "标题")
        self.assertEqual(got["enabled"], "some")
        self.assertTrue(got["incomplete"])
        self.assertEqual(got["thinking_message_id"], "t1")
        self.assertTrue(got["is_fork_point"])
        self.assertEqual(got["fork_branch_count"], 3)
        self.assertEqual(got["fork_current_index"], 1)

        # UPSERT 覆盖
        self.store.upsert_node(
            conversation_id="conv1", node_id="n1", title="新标题",
            fork_branch_count=4,
        )
        got2 = self.store.get_node("n1")
        self.assertEqual(got2["title"], "新标题")
        self.assertEqual(got2["fork_branch_count"], 4)

    def test_get_nodes_by_conversation(self) -> None:
        """get_nodes 应只返回指定对话的节点。"""
        self.store.upsert_node(conversation_id="convA", node_id="a1")
        self.store.upsert_node(conversation_id="convB", node_id="b1")
        ids = {n["node_id"] for n in self.store.get_nodes("convA")}
        self.assertEqual(ids, {"a1"})

    def test_delete_nodes(self) -> None:
        """delete_nodes 应删除指定节点元数据。"""
        self.store.upsert_node(conversation_id="conv1", node_id="n1")
        self.store.delete_nodes(["n1"])
        self.assertIsNone(self.store.get_node("n1"))

    # ── branch_nodes ──────────────────────────

    def test_add_and_get_branch_list(self) -> None:
        """add_branch 后应能按 position 顺序读回分支列表。"""
        self.store.add_branch("conv1", "fp1", 0, ["fp1", "m1", "m2"])
        self.store.add_branch("conv1", "fp1", 1, ["fp1", "m3", "m4"])
        self.assertEqual(
            self.store.get_branch_list("fp1", 0), ["fp1", "m1", "m2"]
        )
        self.assertEqual(
            self.store.get_branch_list("fp1", 1), ["fp1", "m3", "m4"]
        )

    def test_get_all_branches(self) -> None:
        """get_all_branches 应返回 {branch_index: [node_ids]}。"""
        self.store.add_branch("conv1", "fp1", 0, ["fp1", "a", "b"])
        self.store.add_branch("conv1", "fp1", 1, ["fp1", "c"])
        branches = self.store.get_all_branches("fp1")
        self.assertEqual(sorted(branches.keys()), [0, 1])
        self.assertEqual(branches[0], ["fp1", "a", "b"])
        self.assertEqual(branches[1], ["fp1", "c"])

    def test_get_branch_indexes(self) -> None:
        """get_branch_indexes 应返回升序的分支编号列表。"""
        self.store.add_branch("conv1", "fp1", 0, ["fp1", "a"])
        self.store.add_branch("conv1", "fp1", 2, ["fp1", "c"])
        self.assertEqual(self.store.get_branch_indexes("fp1"), [0, 2])

    def test_set_branch_overwrites(self) -> None:
        """set_branch 应整体覆写分支列表（同步 tree.json 用）。"""
        self.store.add_branch("conv1", "fp1", 0, ["fp1", "a", "b"])
        self.store.set_branch("conv1", "fp1", 0, ["fp1", "a", "b2"])
        self.assertEqual(
            self.store.get_branch_list("fp1", 0), ["fp1", "a", "b2"]
        )
        self.assertEqual(len(self.store.get_branch_indexes("fp1")), 1)

    def test_delete_branch(self) -> None:
        """delete_branch 应只删除指定分支。"""
        self.store.add_branch("conv1", "fp1", 0, ["fp1", "a"])
        self.store.add_branch("conv1", "fp1", 1, ["fp1", "b"])
        self.store.delete_branch("fp1", 0)
        self.assertEqual(self.store.get_branch_indexes("fp1"), [1])
        self.assertEqual(self.store.get_branch_list("fp1", 1), ["fp1", "b"])

    def test_delete_all_for_fork_point(self) -> None:
        """delete_all_for_fork_point 应删除分叉点下全部分支。"""
        self.store.add_branch("conv1", "fp1", 0, ["fp1", "a"])
        self.store.add_branch("conv1", "fp1", 1, ["fp1", "b"])
        self.store.delete_all_for_fork_point("fp1")
        self.assertEqual(self.store.get_branch_indexes("fp1"), [])

    def test_delete_all_for_conversation(self) -> None:
        """delete_all_for_conversation 应删除对话的全部分支数据与元数据。"""
        self.store.upsert_node(conversation_id="conv1", node_id="n1")
        self.store.add_branch("conv1", "fp1", 0, ["fp1", "n1"])
        self.store.delete_all_for_conversation("conv1")
        self.assertIsNone(self.store.get_node("n1"))
        self.assertEqual(self.store.get_branch_indexes("fp1"), [])

    def test_referenced_node_ids(self) -> None:
        """get_all_referenced_node_ids 应收集分支列表引用的全部节点。"""
        self.store.add_branch("conv1", "fp1", 0, ["fp1", "a", "b"])
        self.store.add_branch("conv1", "fp2", 0, ["fp2", "c"])
        self.store.add_branch("conv2", "fp9", 0, ["fp9", "z"])
        refs = self.store.get_all_referenced_node_ids()
        self.assertEqual(
            refs, {"fp1", "a", "b", "fp2", "c", "fp9", "z"}
        )
        conv1_refs = self.store.get_all_referenced_node_ids("conv1")
        self.assertEqual(conv1_refs, {"fp1", "a", "b", "fp2", "c"})
        self.assertTrue(self.store.node_id_in_any_branch("a"))
        self.assertFalse(self.store.node_id_in_any_branch("zzz"))

    def test_unique_node_in_branch(self) -> None:
        """同一分支内不允许重复节点（UNIQUE 约束）。"""
        import sqlite3
        self.store.add_branch("conv1", "fp1", 0, ["fp1", "a"])
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.add_branch("conv1", "fp1", 0, ["fp1", "a", "a"])

    def test_get_all_archived_node_ids(self) -> None:
        """fork_nodes 归档的节点 ID 全集（孤儿清理防御）。"""
        self.store.upsert_node(conversation_id="conv1", node_id="n1")
        self.store.upsert_node(conversation_id="conv1", node_id="n2")
        self.assertEqual(self.store.get_all_archived_node_ids(), {"n1", "n2"})

    def test_get_all_thinking_message_ids(self) -> None:
        """归档 assistant 的 thinking 绑定行 ID 全集。"""
        self.store.upsert_node(conversation_id="conv1", node_id="a1",
                               thinking_message_id="t1")
        self.store.upsert_node(conversation_id="conv1", node_id="a2")
        self.assertEqual(self.store.get_all_thinking_message_ids(), {"t1"})

    def test_sync_script_branch_keep_ids(self) -> None:
        """sync_db_with_tree 的分支引用收集：branch_nodes + fork_nodes +
        thinking 绑定全覆盖。"""
        self.store.add_branch("conv1", "fp1", 0, ["fp1", "old_a"])
        self.store.upsert_node(conversation_id="conv1", node_id="archived_n",
                               thinking_message_id="archived_t")
        import importlib
        script = importlib.import_module("scripts.sync_db_with_tree")
        node_ids, thinking_ids = script.collect_branch_keep_ids()
        self.assertIn("old_a", node_ids)
        self.assertIn("fp1", node_ids)
        self.assertIn("archived_n", node_ids)
        self.assertIn("archived_t", thinking_ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
