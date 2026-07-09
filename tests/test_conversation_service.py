# File: tests/test_conversation_service.py
# Phase 5 — ConversationService 单元测试
# 运行方式: python -m pytest tests/test_conversation_service.py -v
#         或 python tests/test_conversation_service.py

import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.storage.models import (
    ContextBlock,
    ContextSource,
    ConversationNode,
    FolderNode,
    Message,
    Role,
)
from app.storage.tree_store import TreeStore
from app.storage.context_store import ContextStore
from app.storage.message_repo import MessageRepo
from app.core.context_service import ContextService


class TestConversationServiceTreeOps(unittest.TestCase):
    """测试 ConversationService 的树操作（不涉及 LLM/搜索/文件）。"""

    @classmethod
    def setUpClass(cls) -> None:
        """初始化数据库和存储。"""
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls._base = Path(cls._tmpdir.name)

        # 用临时路径覆盖 config
        import config as app_config
        app_config.DB_PATH = str(cls._base / "test.db")
        app_config.TREE_STORE_PATH = str(cls._base / "tree")
        app_config.CONTEXT_STORE_PATH = str(cls._base / "context")

        from app.storage.database import initialize_database
        initialize_database()

        cls.tree_store = TreeStore()
        cls.message_repo = MessageRepo()
        cls.context_store = ContextStore()

        # 创建不依赖 LLM/Search/File 的 ConversationService
        # 直接测试 tree_store 方法（conversation_service 是薄封装）

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmpdir.cleanup()

    def setUp(self) -> None:
        """每个测试前确保树和回收站干净。"""
        from app.storage.models import TreeRoot
        self.tree_store._root = TreeRoot(version="1.0", nodes=[])
        self.tree_store._rebuild_cache()
        # 重建默认根目录（启用状态设为 "some" 以便子节点自行决定）
        root = FolderNode(
            id="root", parent_id=None, sort_order=0, enabled="some",
            title="未分类",
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        )
        self.tree_store._root.nodes.append(root)
        self.tree_store._rebuild_cache()
        self.tree_store._save_tree(self.tree_store._root)
        # 清空回收站
        self.tree_store._save_trash([])

    # ── 直接测试 tree_store 树操作 ─────────

    def test_create_folder(self) -> None:
        """创建目录应返回 FolderNode 并持久化。"""
        folder = FolderNode(
            id="test-folder", parent_id="root", title="新目录",
        )
        self.tree_store.create_node(folder)
        found = self.tree_store.get_node("test-folder")
        self.assertIsNotNone(found)
        self.assertEqual(found.title, "新目录")
        self.assertEqual(found.parent_id, "root")

    def test_rename_node(self) -> None:
        """重命名应更新标题。"""
        conv = ConversationNode(id="c1", parent_id="root", title="原名")
        self.tree_store.create_node(conv)
        self.tree_store.update_node("c1", title="改名后")
        self.assertEqual(self.tree_store.get_node("c1").title, "改名后")

    def test_toggle_enabled(self) -> None:
        """Toggle enabled 应在 True/False 间切换。"""
        conv = ConversationNode(id="c1", parent_id="root", enabled=True)
        self.tree_store.create_node(conv)

        self.tree_store.update_node("c1", enabled=False)
        self.assertFalse(self.tree_store.get_node("c1").enabled)

        self.tree_store.update_node("c1", enabled=True)
        self.assertTrue(self.tree_store.get_node("c1").enabled)

    def test_update_folder_context_blocks(self) -> None:
        """update_node 应更新 context_block_ids。"""
        folder = FolderNode(id="f1", parent_id="root", title="上下文目录")
        self.tree_store.create_node(folder)
        self.tree_store.update_node(
            "f1", context_block_ids=["block-1", "block-2"],
        )
        updated = self.tree_store.get_node("f1")
        self.assertEqual(updated.context_block_ids, ["block-1", "block-2"])

    def test_attach_and_detach_file(self) -> None:
        """attach/detach 应正确更新 attachment_paths。"""
        folder = FolderNode(id="f1", parent_id="root", title="附件目录")
        self.tree_store.create_node(folder)

        # 挂载
        self.tree_store.update_node(
            "f1", attachment_paths=["/path/to/file.txt"],
        )
        self.assertIn("/path/to/file.txt",
                      self.tree_store.get_node("f1").attachment_paths)

        # 移除
        self.tree_store.update_node(
            "f1", attachment_paths=[],
        )
        self.assertEqual(self.tree_store.get_node("f1").attachment_paths, [])

    def test_soft_delete_and_trash_roundtrip(self) -> None:
        """软删除 → 回收站列表 → 恢复 → 彻底删除 的完整流程。"""
        conv = ConversationNode(
            id="roundtrip", parent_id="root", title="往返测试",
        )
        self.tree_store.create_node(conv)

        # 软删除
        self.tree_store.soft_delete_node("roundtrip")
        self.assertIsNone(self.tree_store.get_node("roundtrip"))

        trash = self.tree_store.list_trash()
        self.assertEqual(len(trash), 1)
        entry_id = trash[0].id

        # 恢复
        self.tree_store.restore_from_trash(entry_id)
        self.assertIsNotNone(self.tree_store.get_node("roundtrip"))
        self.assertEqual(len(self.tree_store.list_trash()), 0)

        # 再次软删除
        self.tree_store.soft_delete_node("roundtrip")
        trash2 = self.tree_store.list_trash()
        self.assertEqual(len(trash2), 1)

        # 彻底删除
        self.tree_store.permanently_delete_from_trash(trash2[0].id)
        self.assertEqual(len(self.tree_store.list_trash()), 0)

    def test_get_trash_entries(self) -> None:
        """list_trash 应返回所有回收站条目。"""
        c1 = ConversationNode(id="c1", parent_id="root", title="一")
        c2 = ConversationNode(id="c2", parent_id="root", title="二")
        self.tree_store.create_node(c1)
        self.tree_store.create_node(c2)
        self.tree_store.soft_delete_node("c1")
        self.tree_store.soft_delete_node("c2")

        trash = self.tree_store.list_trash()
        self.assertEqual(len(trash), 2)


class TestContextServiceTreeIntegration(unittest.TestCase):
    """测试 ContextService 的树形上下文收集（Phase 3 功能）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls._base = Path(cls._tmpdir.name)

        import config as app_config
        app_config.TREE_STORE_PATH = str(cls._base / "tree")
        app_config.CONTEXT_STORE_PATH = str(cls._base / "context")
        app_config.DB_PATH = str(cls._base / "test.db")

        from app.storage.database import initialize_database
        initialize_database()

        cls.tree_store = TreeStore()
        cls.message_repo = MessageRepo()
        cls.context_store = ContextStore()
        cls.context_service = ContextService(
            message_repo=cls.message_repo,
            context_store=cls.context_store,
            tree_store=cls.tree_store,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmpdir.cleanup()

    def setUp(self) -> None:
        """每个测试前重置树和回收站。"""
        from app.storage.models import TreeRoot
        self.tree_store._root = TreeRoot(version="1.0", nodes=[])
        self.tree_store._rebuild_cache()
        # 根目录用 "some" 让子节点自行决定启用状态
        root = FolderNode(
            id="root", parent_id=None, sort_order=0, enabled="some",
            title="未分类",
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        )
        self.tree_store._root.nodes.append(root)
        self.tree_store._rebuild_cache()
        self.tree_store._save_tree(self.tree_store._root)
        self.tree_store._save_trash([])

    def test_collect_context_from_parent_folders(self) -> None:
        """应收集父目录链中的 context_block_ids。"""
        # 创建上下文块
        block = ContextBlock(
            id="block-1", label="测试块", content="测试内容",
            source=ContextSource.MANUAL, enabled=True, order=0,
        )
        self.context_store.save_block(block)

        # 创建目录结构: root → folder-a → folder-b → conversation
        fa = FolderNode(
            id="folder-a", parent_id="root", title="A",
            context_block_ids=["block-1"],
        )
        fb = FolderNode(
            id="folder-b", parent_id="folder-a", title="B",
            context_block_ids=[],
        )
        conv = ConversationNode(
            id="conv-1", parent_id="folder-b", title="对话",
        )
        self.tree_store.create_node(fa)
        self.tree_store.create_node(fb)
        self.tree_store.create_node(conv)

        block_ids, _attachments = self.context_service._collect_context_resources("conv-1")
        self.assertIn("block-1", block_ids)

    def test_collect_context_respects_enabled(self) -> None:
        """禁用的目录不应贡献上下文。"""
        block = ContextBlock(
            id="block-x", label="X", content="内容X",
            source=ContextSource.MANUAL, enabled=True, order=0,
        )
        self.context_store.save_block(block)

        fa = FolderNode(
            id="fa", parent_id="root", title="A",
            context_block_ids=["block-x"], enabled=False,
        )
        conv = ConversationNode(
            id="conv-1", parent_id="fa", title="对话",
        )
        self.tree_store.create_node(fa)
        self.tree_store.create_node(conv)

        block_ids, _attachments = self.context_service._collect_context_resources("conv-1")
        self.assertNotIn("block-x", block_ids)

    def test_collect_no_blocks_for_root_conversation(self) -> None:
        """根级对话不应收集到任何上下文块。"""
        conv = ConversationNode(
            id="root-conv", parent_id="root", title="根对话",
        )
        self.tree_store.create_node(conv)

        block_ids, attachments = self.context_service._collect_context_resources("root-conv")
        self.assertEqual(block_ids, [])
        self.assertEqual(attachments, [])

    def test_is_node_enabled_top_down_cascade(self) -> None:
        """自顶向下级联：最高层 False 应覆盖下层 True。"""
        fa = FolderNode(id="fa", parent_id="root", title="A", enabled=False)
        fb = FolderNode(id="fb", parent_id="fa", title="B", enabled=True)
        conv = ConversationNode(id="cv", parent_id="fb", title="C")
        self.tree_store.create_node(fa)
        self.tree_store.create_node(fb)
        self.tree_store.create_node(conv)

        # fa(False) 覆盖 fb(True) → conv 实际是 disabled
        self.assertFalse(self.context_service._is_node_enabled("cv"))

    def test_is_node_enabled_all_some(self) -> None:
        """全部 some 时使用节点自身值。"""
        conv = ConversationNode(id="cv", parent_id="root", title="C", enabled=False)
        self.tree_store.create_node(conv)
        # root 是 True，conv 自身是 False
        self.assertFalse(self.context_service._is_node_enabled("cv"))

    def test_build_system_prompt_includes_tree_blocks(self) -> None:
        """_build_system_prompt 应注入树目录块。"""
        block = ContextBlock(
            id="tb-1", label="树块", content="[TreeContext]",
            source=ContextSource.MANUAL, enabled=True, order=0,
        )
        self.context_store.save_block(block)

        prompt = self.context_service._build_system_prompt(
            tree_block_ids=["tb-1"],
        )
        self.assertIn("[TreeContext]", prompt)

    def test_build_system_prompt_deduplicates(self) -> None:
        """树块不应在全局块段中重复出现。"""
        block = ContextBlock(
            id="both", label="共享块", content="[Shared]",
            source=ContextSource.MANUAL, enabled=True, order=0,
        )
        self.context_store.save_block(block)

        prompt = self.context_service._build_system_prompt(
            tree_block_ids=["both"],
        )
        # "[Shared]" 应只出现一次（在树块段）
        self.assertEqual(prompt.count("[Shared]"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
