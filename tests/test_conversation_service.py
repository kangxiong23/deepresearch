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
    MessageNode,
    Role,
)
from app.storage.tree_store import TreeStore
from app.storage.context_store import ContextStore
from app.storage.message_repo import MessageRepo
from config import ConfigScope
from app.core.context_service import ContextService
from app.core.conversation_service import ConversationService


class TestConversationServiceTreeOps(unittest.TestCase):
    """测试 ConversationService 的树操作（不涉及 LLM/搜索/文件）。"""

    @classmethod
    def setUpClass(cls) -> None:
        """初始化数据库和存储。"""
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls._base = Path(cls._tmpdir.name)

        # 用 ConfigScope 隔离临时路径（退出自动恢复 config + 关闭 DB 连接）
        cls._scope = ConfigScope(
            DB_PATH=str(cls._base / "test.db"),
            TREE_STORE_PATH=str(cls._base / "tree"),
            CONTEXT_STORE_PATH=str(cls._base / "context"),
        )
        cls._scope.__enter__()

        from app.storage.database import initialize_database
        initialize_database()

        cls.tree_store = TreeStore()
        cls.message_repo = MessageRepo()
        cls.context_store = ContextStore()

        # 创建不依赖 LLM/Search/File 的 ConversationService
        # 直接测试 tree_store 方法（conversation_service 是薄封装）

    @classmethod
    def tearDownClass(cls) -> None:
        cls._scope.__exit__(None, None, None)  # 关闭连接 + 恢复 config
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

    def test_incomplete_mark_and_cleanup(self) -> None:
        """标记未完成 → 软删除到回收站（只删标记节点，其余保留）。"""
        conv = ConversationNode(id="conv-clean", parent_id="root", title="清理测试")
        self.tree_store.create_node(conv)
        n1 = MessageNode(
            id="clean-m1", parent_id="conv-clean", message_id="clean-m1",
            role="user", preview="问题", title="User: 问题",
        )
        n2 = MessageNode(
            id="clean-m2", parent_id="conv-clean", message_id="clean-m2",
            role="assistant", preview="回答", title="Asst: 回答",
        )
        self.tree_store.create_node(n1)
        self.tree_store.create_node(n2)

        # 标记 m1 未完成
        self.tree_store.mark_incomplete("clean-m1", True)
        self.assertTrue(self.tree_store.get_node("clean-m1").incomplete)

        # 清理 → 只删 m1，m2 保留，回收站 +1
        count = self.tree_store.cleanup_incomplete_nodes()
        self.assertEqual(count, 1)
        self.assertIsNone(self.tree_store.get_node("clean-m1"))
        self.assertIsNotNone(self.tree_store.get_node("clean-m2"))
        self.assertEqual(len(self.tree_store.list_trash()), 1)


class TestContextServiceTreeIntegration(unittest.TestCase):
    """测试 ContextService 的树形上下文收集（Phase 3 功能）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls._base = Path(cls._tmpdir.name)

        # 用 ConfigScope 隔离临时路径（退出自动恢复 config + 关闭 DB 连接）
        cls._scope = ConfigScope(
            DB_PATH=str(cls._base / "test.db"),
            TREE_STORE_PATH=str(cls._base / "tree"),
            CONTEXT_STORE_PATH=str(cls._base / "context"),
        )
        cls._scope.__enter__()

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
        cls._scope.__exit__(None, None, None)  # 关闭连接 + 恢复 config
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

    def test_history_excludes_deleted_message(self) -> None:
        """
        删除 MessageNode 后，get_enabled_history_messages 不应包含该消息。

        即：历史以 tree.json 为准，即使 DB 中消息行仍然存在。
        """
        conv = ConversationNode(
            id="conv-hist", parent_id="root", title="历史测试",
        )
        self.tree_store.create_node(conv)

        # 保存两条消息到 DB
        m1 = Message(
            id="m1", conversation_id="conv-hist", role=Role.USER,
            content="问题1", created_at=datetime.utcnow(), token_count=2,
        )
        m2 = Message(
            id="m2", conversation_id="conv-hist", role=Role.ASSISTANT,
            content="回答1", created_at=datetime.utcnow(), token_count=2,
        )
        self.message_repo.save_message(m1)
        self.message_repo.save_message(m2)

        # 创建对应 MessageNode
        n1 = MessageNode(
            id="m1", parent_id="conv-hist", message_id="m1",
            role=Role.USER.value, preview="问题1", title="User: 问题1",
        )
        n2 = MessageNode(
            id="m2", parent_id="conv-hist", message_id="m2",
            role=Role.ASSISTANT.value, preview="回答1", title="Asst: 回答1",
        )
        self.tree_store.create_node(n1)
        self.tree_store.create_node(n2)

        # 初始：两条消息都在历史中（DFS 前序）
        history = self.context_service.get_enabled_history_messages()
        self.assertEqual([m.id for m in history], ["m1", "m2"])

        # 软删除 m1 的 MessageNode（模拟删除操作 —— 只动 tree.json）
        self.tree_store.soft_delete_node("m1")

        # DB 中 m1 仍然存在，但历史不应包含它
        self.assertEqual(len(self.message_repo.get_messages_by_ids(["m1"])), 1)
        history2 = self.context_service.get_enabled_history_messages()
        self.assertEqual([m.id for m in history2], ["m2"])

    def test_history_excludes_disabled_folder(self) -> None:
        """禁用父目录后，其下对话的消息不应出现在历史中（启用级联）。"""
        fa = FolderNode(
            id="fa-dis", parent_id="root", title="禁用目录", enabled=False,
        )
        conv = ConversationNode(
            id="cv-dis", parent_id="fa-dis", title="禁用对话",
        )
        self.tree_store.create_node(fa)
        self.tree_store.create_node(conv)

        m = Message(
            id="m-dis", conversation_id="cv-dis", role=Role.USER,
            content="禁用内容", created_at=datetime.utcnow(), token_count=2,
        )
        self.message_repo.save_message(m)
        node = MessageNode(
            id="m-dis", parent_id="cv-dis", message_id="m-dis",
            role=Role.USER.value, preview="禁用内容", title="User: 禁用内容",
        )
        self.tree_store.create_node(node)

        history = self.context_service.get_enabled_history_messages()
        self.assertNotIn("m-dis", [mm.id for mm in history])

    def test_history_skips_incomplete(self) -> None:
        """标记为未完成的 MessageNode 不应进入 LLM 历史上下文。"""
        conv = ConversationNode(
            id="conv-inc", parent_id="root", title="未完成测试",
        )
        self.tree_store.create_node(conv)

        m1 = Message(
            id="inc-m1", conversation_id="conv-inc", role=Role.USER,
            content="问题1", created_at=datetime.utcnow(), token_count=2,
        )
        self.message_repo.save_message(m1)
        n1 = MessageNode(
            id="inc-m1", parent_id="conv-inc", message_id="inc-m1",
            role=Role.USER.value, preview="问题1", title="User: 问题1",
        )
        self.tree_store.create_node(n1)

        # 初始：在历史中
        self.assertEqual(len(self.context_service.get_enabled_history_messages()), 1)

        # 标记未完成 → 历史中应消失
        self.tree_store.mark_incomplete("inc-m1", True)
        self.assertEqual(self.context_service.get_enabled_history_messages(), [])

    def test_display_includes_incomplete_but_context_skips(self) -> None:
        """前端展示(include_incomplete=True)包含 incomplete 节点，LLM 上下文排除。"""
        conv = ConversationNode(
            id="conv-ii", parent_id="root", title="问题2测试",
        )
        self.tree_store.create_node(conv)

        m = Message(
            id="ii-m1", conversation_id="conv-ii", role=Role.USER,
            content="问题", created_at=datetime.utcnow(), token_count=2,
        )
        self.message_repo.save_message(m)
        n = MessageNode(
            id="ii-m1", parent_id="conv-ii", message_id="ii-m1",
            role=Role.USER.value, preview="问题", title="User: 问题",
        )
        self.tree_store.create_node(n)
        self.tree_store.mark_incomplete("ii-m1", True)

        # LLM 上下文（默认 include_incomplete=False）：排除
        self.assertEqual(self.context_service.get_enabled_history_messages(), [])
        # 前端展示（include_incomplete=True）：包含
        display = self.context_service.get_enabled_history_messages(
            include_incomplete=True
        )
        self.assertEqual([mm.id for mm in display], ["ii-m1"])


class TestThinkingBindingInterleave(unittest.TestCase):
    """Phase 6: get_messages_for_node / 展示路径 的绑定 thinking 穿插。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls._base = Path(cls._tmpdir.name)

        # 用 ConfigScope 隔离临时路径（退出自动恢复 config + 关闭 DB 连接）
        cls._scope = ConfigScope(
            DB_PATH=str(cls._base / "test.db"),
            TREE_STORE_PATH=str(cls._base / "tree"),
            CONTEXT_STORE_PATH=str(cls._base / "context"),
        )
        cls._scope.__enter__()

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
        # llm/search/file 用 stub —— 本组测试只调用 get_messages_for_node
        # 与 get_effective_enabled_messages，不触发 LLM/搜索/文件。
        cls.conversation_service = ConversationService(
            message_repo=cls.message_repo,
            tree_store=cls.tree_store,
            llm_client=object(),
            context_service=cls.context_service,
            search_service=object(),
            file_service=object(),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._scope.__exit__(None, None, None)  # 关闭连接 + 恢复 config
        cls._tmpdir.cleanup()

    def setUp(self) -> None:
        from app.storage.models import TreeRoot
        self.tree_store._root = TreeRoot(version="3.0", nodes=[])
        self.tree_store._rebuild_cache()
        root = FolderNode(
            id="root", parent_id=None, sort_order=0, enabled="some",
            title="未分类",
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        )
        self.tree_store._root.nodes.append(root)
        self.tree_store._rebuild_cache()
        self.tree_store._save_tree(self.tree_store._root)
        self.tree_store._save_trash([])

    def _build_conv_with_thinking(self, conv_id: str, u_id: str, a_id: str, t_id: str) -> None:
        """建一个对话: user → assistant(thinking_message_id=t_id)。"""
        conv = ConversationNode(id=conv_id, parent_id="root", title="穿插")
        self.tree_store.create_node(conv)
        u = MessageNode(
            id=u_id, parent_id=conv_id, message_id=u_id,
            role="user", preview="问", title="User: 问",
        )
        a = MessageNode(
            id=a_id, parent_id=conv_id, message_id=a_id,
            role="assistant", preview="答", title="Asst: 答",
            thinking_message_id=t_id,
        )
        self.tree_store.create_node(u)
        self.tree_store.create_node(a)
        self.message_repo.save_message(Message(
            id=u_id, conversation_id=conv_id, role=Role.USER, content="问题",
        ))
        self.message_repo.save_message(Message(
            id=t_id, conversation_id=conv_id, role=Role.THINKING,
            content="思考", is_thinking=True,
        ))
        self.message_repo.save_message(Message(
            id=a_id, conversation_id=conv_id, role=Role.ASSISTANT, content="回答",
        ))

    def test_get_messages_for_node_interleaves_thinking(self) -> None:
        """get_messages_for_node 应将绑定 thinking 行插到 assistant 之前。"""
        self._build_conv_with_thinking("conv-i", "u1", "a1", "t1")
        msgs = self.conversation_service.get_messages_for_node("conv-i")
        self.assertEqual([m.id for m in msgs], ["u1", "t1", "a1"])

    def test_get_effective_enabled_messages_includes_thinking(self) -> None:
        """前端展示路径应包含绑定 thinking；LLM 上下文路径应排除。"""
        self._build_conv_with_thinking("conv-e", "u2", "a2", "t2")

        display = self.conversation_service.get_effective_enabled_messages()
        self.assertEqual([m.id for m in display], ["u2", "t2", "a2"])

        history = self.context_service.get_enabled_history_messages()
        self.assertEqual([m.id for m in history], ["u2", "a2"])


# ──────────────────────────────────────────────
# P2: 分叉集成测试（regenerate 分叉模式 / 修改重发送 / 分支感知删除与清理）
# ──────────────────────────────────────────────

class _FakeLLM:
    """模拟 DeepSeek 流式：按预定块输出后结束。"""

    def __init__(self, chunks: list[tuple[str, str]] | None = None) -> None:
        self._chunks = chunks or [("回答内容", "text")]

    async def stream_chat(self, context):
        from app.storage.models import ChunkType, MessageChunk
        for delta, ctype in self._chunks:
            yield MessageChunk(delta=delta, chunk_type=ChunkType(ctype))
        yield MessageChunk(delta="", is_done=True)

    async def stream_prefix_continue(self, context, *args):
        from app.storage.models import ChunkType, MessageChunk
        yield MessageChunk(delta="", is_done=True)

    def abort(self) -> None:
        pass


class _FakeSearch:
    async def search_and_format(self, text: str) -> str:
        return ""


class _FakeFile:
    async def extract_files(self, files: list[str]):
        return []


class TestForkIntegration(unittest.TestCase):
    """ConversationService 分叉集成测试（spec 3/4/5 章）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls._base = Path(cls._tmpdir.name)
        cls._scope = ConfigScope(
            DB_PATH=str(cls._base / "test.db"),
            TREE_STORE_PATH=str(cls._base / "tree"),
            CONTEXT_STORE_PATH=str(cls._base / "context"),
        )
        cls._scope.__enter__()

        from app.storage.database import initialize_database
        from app.core.branch_service import BranchService
        from app.storage.branch_store import BranchStore
        initialize_database()

        cls.tree_store = TreeStore()
        cls.message_repo = MessageRepo()
        cls.context_store = ContextStore()
        cls.context_service = ContextService(
            message_repo=cls.message_repo,
            context_store=cls.context_store,
            tree_store=cls.tree_store,
        )
        cls.branch_store = BranchStore()
        cls.branch_service = BranchService(
            tree_store=cls.tree_store,
            branch_store=cls.branch_store,
            message_repo=cls.message_repo,
        )
        cls.svc = ConversationService(
            message_repo=cls.message_repo,
            tree_store=cls.tree_store,
            llm_client=_FakeLLM(),
            context_service=cls.context_service,
            search_service=_FakeSearch(),
            file_service=_FakeFile(),
            branch_service=cls.branch_service,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._scope.__exit__(None, None, None)
        cls._tmpdir.cleanup()

    def setUp(self) -> None:
        """重置树、回收站与分支数据表。"""
        from app.storage.models import TreeRoot
        from app.storage.database import get_connection
        self.tree_store._root = TreeRoot(version="1.0", nodes=[])
        self.tree_store._rebuild_cache()
        root = FolderNode(
            id="root", parent_id=None, sort_order=0, enabled="some",
            title="未分类",
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        )
        self.tree_store._root.nodes.append(root)
        self.tree_store._rebuild_cache()
        self.tree_store._save_tree(self.tree_store._root)
        self.tree_store._save_trash([])
        conn = get_connection()
        with conn:
            conn.execute("DELETE FROM branch_nodes")
            conn.execute("DELETE FROM fork_nodes")
            conn.execute("DELETE FROM messages")

        # 标准链: conv [u1, a1, u2, a2]
        self.tree_store.create_node(ConversationNode(
            id="conv", parent_id="root", title="对话",
        ))
        for node_id, role, content in [
            ("u1", Role.USER, "问题一"), ("a1", Role.ASSISTANT, "回答一"),
            ("u2", Role.USER, "问题二"), ("a2", Role.ASSISTANT, "回答二"),
        ]:
            self.tree_store.create_node(MessageNode(
                id=node_id, parent_id="conv", message_id=node_id,
                role=role.value, title=f"{role.value}: {content}",
                preview=content,
            ))
            self.message_repo.save_message(Message(
                id=node_id, conversation_id="conv", role=role, content=content,
            ))
        self.tree_store.clear_dirty()

    def _chain_ids(self) -> list[str]:
        return [n.id for n in self.tree_store.get_conversation_chain("conv")]

    def _drain(self, agen) -> list:
        """跑完一个异步生成器并收集全部块。"""
        async def _collect():
            out = []
            async for c in agen:
                out.append(c)
            return out
        import asyncio
        return asyncio.run(_collect())

    # ── regenerate 分叉模式 ──────────────────

    def test_regenerate_creates_fork_branch(self) -> None:
        """重新生成完整 assistant → 分叉点 = 前驱;旧回复归档,新回复进链。"""
        self._drain(self.svc.regenerate_message("conv", "a2"))
        # 分叉点 = u2(a2 的前驱)
        u2 = self.tree_store.get_node("u2")
        self.assertTrue(u2.is_fork_point)
        self.assertEqual(u2.fork_branch_count, 2)
        self.assertEqual(u2.fork_current_index, 1)
        # 分支 0 = [u2, a2](旧),分支 1 = [u2, 新a](新)
        branches = self.branch_store.get_all_branches("u2")
        self.assertEqual(branches[0], ["u2", "a2"])
        self.assertEqual(len(branches[1]), 2)
        new_a_id = branches[1][1]
        self.assertNotEqual(new_a_id, "a2")
        # 新链 = [u1, a1, u2, 新a];旧 a2 不在链中但消息行保留
        self.assertEqual(self._chain_ids(), ["u1", "a1", "u2", new_a_id])
        rows = {m.id for m in self.message_repo.get_messages("conv")}
        self.assertIn("a2", rows)  # 归档分支保留
        self.assertIn(new_a_id, rows)
        # 新 assistant 已入库
        new_msg = next(m for m in self.message_repo.get_messages("conv")
                       if m.id == new_a_id)
        self.assertEqual(new_msg.role, Role.ASSISTANT)
        self.assertEqual(new_msg.content, "回答内容")

    def test_regenerate_middle_assistant(self) -> None:
        """重新生成中间 assistant → 其后的消息整体替换(1.3)。"""
        # 链加长: [u1, a1, u2, a2, u3, a3]
        self.tree_store.create_node(MessageNode(
            id="u3", parent_id="conv", message_id="u3", role="user",
            title="User: 问题三", preview="问题三",
        ))
        self.tree_store.create_node(MessageNode(
            id="a3", parent_id="conv", message_id="a3", role="assistant",
            title="Asst: 回答三", preview="回答三",
        ))
        self.message_repo.save_message(Message(
            id="u3", conversation_id="conv", role=Role.USER, content="问题三",
        ))
        self.message_repo.save_message(Message(
            id="a3", conversation_id="conv", role=Role.ASSISTANT, content="回答三",
        ))
        self.tree_store.clear_dirty()

        self._drain(self.svc.regenerate_message("conv", "a2"))
        # 分叉点 = u2;新链 = [u1, a1, u2, 新a](u3/a3 被替换)
        chain = self._chain_ids()
        self.assertEqual(chain[:3], ["u1", "a1", "u2"])
        self.assertNotIn("a3", chain)
        self.assertNotIn("u3", chain)
        # 旧分支 0 = [u2, a2, u3, a3](截取到链尾)
        self.assertEqual(
            self.branch_store.get_branch_list("u2", 0), ["u2", "a2", "u3", "a3"]
        )

    def test_regenerate_abort_persists_nothing(self) -> None:
        """停止生成 → 不持久化任何内容(4.3 丢弃回退)。"""
        self._drain(self.svc.regenerate_message("conv", "a2"))
        # 首次正常生成后记下状态(目标变为链尾的新 assistant)
        target = self._chain_ids()[-1]
        chain_before = self._chain_ids()
        rows_before = {m.id for m in self.message_repo.get_messages("conv")}
        svc = self.svc

        async def _abort_drain():
            out = []
            async for c in svc.regenerate_message("conv", target):
                out.append(c)
                if c.delta:  # 收到首个内容块后停止
                    svc.stop_generation()
            return out

        import asyncio
        asyncio.run(_abort_drain())
        # 树与消息行完全不变(预分支零持久化)
        self.assertEqual(self._chain_ids(), chain_before)
        rows_after = {m.id for m in self.message_repo.get_messages("conv")}
        self.assertEqual(rows_before, rows_after)
        # 无新增分支
        self.assertEqual(
            self.branch_store.get_branch_indexes("u2"), [0, 1]
        )

    # ── 修改重发送 ────────────────────────────

    def test_resend_edited_message_creates_branch_at_send(self) -> None:
        """修改重发送:发送时立即建分支;完成后扩展并清除 incomplete。"""
        self._drain(self.svc.resend_edited_message(
            "conv", "u2", "修改后的问题二", []
        ))
        chain = self._chain_ids()
        # 新链 = [u1, a1, 新u, 新a](u2 及其后整体替换)
        self.assertEqual(chain[:2], ["u1", "a1"])
        new_u, new_a = chain[2], chain[3]
        self.assertNotEqual(new_u, "u2")
        # 分叉点 = u2 的前驱 a1;分支 0 = [a1, u2, a2],分支 1 = [a1, 新u, 新a]
        self.assertTrue(self.tree_store.get_node("a1").is_fork_point)
        self.assertEqual(
            self.branch_store.get_branch_list("a1", 0), ["a1", "u2", "a2"]
        )
        self.assertEqual(
            self.branch_store.get_branch_list("a1", 1), ["a1", new_u, new_a]
        )
        # 新 user 内容 = 修改后;incomplete 已清除
        new_user = next(m for m in self.message_repo.get_messages("conv")
                        if m.id == new_u)
        self.assertEqual(new_user.content, "修改后的问题二")
        self.assertFalse(self.tree_store.get_node(new_u).incomplete)
        # 原 u2 内容保留(归档分支)
        old_u = next(m for m in self.message_repo.get_messages("conv")
                     if m.id == "u2")
        self.assertEqual(old_u.content, "问题二")

    def test_resend_first_message_root_fork(self) -> None:
        """第一条消息被修改 → 分叉点 = 对话节点。"""
        self._drain(self.svc.resend_edited_message(
            "conv", "u1", "修改后的问题一", []
        ))
        conv = self.tree_store.get_node("conv")
        self.assertTrue(conv.is_fork_point)
        self.assertEqual(conv.fork_branch_count, 2)
        self.assertEqual(
            self.branch_store.get_branch_list("conv", 0),
            ["conv", "u1", "a1", "u2", "a2"],
        )
        # 新链 = [新u, 新a];分支 1 = [conv, 新u, 新a]
        new_chain = self._chain_ids()
        self.assertEqual(
            self.branch_store.get_branch_list("conv", 1),
            ["conv"] + new_chain,
        )

    def test_resend_stop_leaves_incomplete_then_cleanup_deletes_branch(self) -> None:
        """修改重发送停止 → incomplete 暂停态;清理时分支删除回退(5.4/5.5)。"""
        svc = self.svc

        async def _stop_mid():
            out = []
            async for c in svc.resend_edited_message(
                "conv", "u2", "半路停止", []
            ):
                out.append(c)
                if c.delta:
                    svc.stop_generation()
            return out

        import asyncio
        asyncio.run(_stop_mid())
        # 分支已建(发送时),新 user 节点 incomplete;链 = [u1, a1, 新u]
        new_u = self._chain_ids()[2]
        self.assertTrue(self.tree_store.get_node(new_u).incomplete)
        self.assertEqual(
            self.branch_store.get_branch_list("a1", 1), ["a1", new_u]
        )
        # 系统清理 → 分支删除,链回退到分支 0
        self.svc.cleanup_incomplete_nodes()
        self.assertEqual(self._chain_ids(), ["u1", "a1", "u2", "a2"])
        self.assertEqual(self.branch_store.get_branch_indexes("a1"), [])
        self.assertFalse(self.tree_store.get_node("a1").is_fork_point)
        # 硬删除:新 user 不在回收站,行已物理清理
        trash_ids = {e.node_data.get("id") for e in self.tree_store.list_trash()}
        self.assertNotIn(new_u, trash_ids)
        rows = {m.id for m in self.message_repo.get_messages("conv")}
        self.assertNotIn(new_u, rows)

    # ── 分支感知删除 ──────────────────────────

    def test_delete_modified_node_hard_deletes_branch(self) -> None:
        """删除被修改节点 → 硬删除(不进回收站)+ 分支删除 + 链回退。"""
        self._drain(self.svc.resend_edited_message(
            "conv", "u2", "修改后的问题二", []
        ))
        new_u = self._chain_ids()[2]
        self.svc.delete_conversation(new_u, "recursive")
        # 链回退到剩余分支 0
        self.assertEqual(self._chain_ids(), ["u1", "a1", "u2", "a2"])
        # 不在回收站(硬删除)
        trash_ids = {e.node_data.get("id") for e in self.tree_store.list_trash()}
        self.assertNotIn(new_u, trash_ids)
        # 分支记录清理 + 退化(剩 1 条分支)
        self.assertEqual(self.branch_store.get_branch_indexes("a1"), [])
        self.assertFalse(self.tree_store.get_node("a1").is_fork_point)
        # 消息行物理清理
        rows = {m.id for m in self.message_repo.get_messages("conv")}
        self.assertNotIn(new_u, rows)

    def test_delete_fork_point_node_transfers_data(self) -> None:
        """删除分叉点节点 → 分叉数据转交前驱(3.3.4)。"""
        self._drain(self.svc.regenerate_message("conv", "a2"))
        # 分叉点 = u2(a2 的前驱),删除它 → 数据转交 a1
        self.svc.delete_conversation("u2", "recursive")
        self.assertEqual(self.branch_store.get_branch_indexes("a1"), [0, 1])
        self.assertEqual(
            self.branch_store.get_branch_list("a1", 0), ["a1", "a2"]
        )
        self.assertEqual(
            self.branch_store.get_branch_list("a1", 1), ["a1", self._chain_ids()[-1]]
        )
        # u2 软删除进回收站(普通节点删除流程)
        trash_ids = {e.node_data.get("id") for e in self.tree_store.list_trash()}
        self.assertIn("u2", trash_ids)
        # 新分叉点 a1 承接
        self.assertTrue(self.tree_store.get_node("a1").is_fork_point)

    def test_delete_conversation_removes_branch_data(self) -> None:
        """删除对话 → 分支数据一并删除(3.3.1 补充说明)。"""
        self._drain(self.svc.regenerate_message("conv", "a2"))
        self.svc.delete_conversation("conv", "recursive")
        self.assertEqual(self.branch_store.get_branch_indexes("u2"), [])
        self.assertIsNone(self.branch_store.get_node("a2"))
        # 消息行保留(对话可恢复)
        self.assertEqual(len(self.message_repo.get_messages("conv")), 5)

    def test_restore_clears_fork_fields(self) -> None:
        """恢复对话 → 分叉标记清除,成为普通对话(3.3.1 补充说明)。"""
        self._drain(self.svc.regenerate_message("conv", "a2"))
        self.svc.delete_conversation("conv", "recursive")
        # 找到 conv 的回收站条目并恢复
        entries = [e for e in self.tree_store.list_trash()
                   if e.node_data.get("id") == "conv"]
        self.assertEqual(len(entries), 1)
        self.svc.restore_conversation(entries[0].id)
        conv = self.tree_store.get_node("conv")
        self.assertFalse(conv.is_fork_point)
        self.assertEqual(conv.fork_branch_count, 0)

    # ── 分叉信息 ──────────────────────────────

    def test_get_fork_info_map(self) -> None:
        """get_fork_info_map 应返回被修改节点的 m/n/分叉点。"""
        self._drain(self.svc.regenerate_message("conv", "a2"))
        info = self.svc.get_fork_info_map()
        new_a = self._chain_ids()[-1]
        # 被修改节点 = 分叉点 u2 的后继(新 a)
        self.assertIn(new_a, info)
        conv_id, m, n, fp_id = info[new_a]
        self.assertEqual(conv_id, "conv")
        self.assertEqual(m, 2)   # index 1 → 展示 2
        self.assertEqual(n, 2)
        self.assertEqual(fp_id, "u2")

    def test_cleanup_plain_incomplete_soft_deletes(self) -> None:
        """非分支的 incomplete 节点仍走软删除。"""
        self.tree_store.mark_incomplete("a1")
        self.svc.cleanup_incomplete_nodes()
        self.assertIsNone(self.tree_store.get_node("a1"))
        trash_ids = {e.node_data.get("id") for e in self.tree_store.list_trash()}
        self.assertIn("a1", trash_ids)

    def test_send_in_forked_conversation_extends_branch(self) -> None:
        """分叉对话中发送新消息 → 当前分支记录同步扩展(重启后切分支不丢链)。"""
        self._drain(self.svc.regenerate_message("conv", "a2"))
        self._drain(self.svc.send_message("conv", "问题三", []))
        chain = self._chain_ids()
        # 链 = [u1, a1, u2, 新a, u3, a3]
        self.assertEqual(chain[:3], ["u1", "a1", "u2"])
        new_a, u3, a3 = chain[3], chain[4], chain[5]
        self.assertEqual(self.branch_store.get_branch_list("u2", 0), ["u2", "a2"])
        self.assertEqual(
            self.branch_store.get_branch_list("u2", 1),
            ["u2", new_a, u3, a3],
        )

    def test_continue_in_forked_conversation_extends_branch(self) -> None:
        """分叉对话中继续生成 → 分支记录同步扩展。"""
        self._drain(self.svc.regenerate_message("conv", "a2"))
        # 模拟未完成轮次:发送新消息后停止(user 节点已入链)
        import asyncio
        svc = self.svc

        async def _stop():
            async for c in svc.send_message("conv", "问题三", []):
                if c.delta:
                    svc.stop_generation()

        asyncio.run(_stop())
        chain = self._chain_ids()
        u3 = chain[4]
        # 继续生成
        self._drain(self.svc.continue_message("conv", "部分回答"))
        final = self._chain_ids()
        self.assertEqual(final[4], u3)
        self.assertEqual(
            self.branch_store.get_branch_list("u2", 1),
            ["u2", chain[3], u3, final[-1]],
        )


# ──────────────────────────────────────────────
# P4: 分支感知拖拽测试（3.6 组合表）
# ──────────────────────────────────────────────

class TestForkDragDrop(unittest.TestCase):
    """move_message_with_fork 的分支感知拖拽处理。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls._base = Path(cls._tmpdir.name)
        cls._scope = ConfigScope(
            DB_PATH=str(cls._base / "test.db"),
            TREE_STORE_PATH=str(cls._base / "tree"),
            CONTEXT_STORE_PATH=str(cls._base / "context"),
        )
        cls._scope.__enter__()

        from app.storage.database import initialize_database
        from app.core.branch_service import BranchService
        from app.storage.branch_store import BranchStore
        initialize_database()

        cls.tree_store = TreeStore()
        cls.message_repo = MessageRepo()
        cls.context_store = ContextStore()
        cls.context_service = ContextService(
            message_repo=cls.message_repo,
            context_store=cls.context_store,
            tree_store=cls.tree_store,
        )
        cls.branch_store = BranchStore()
        cls.branch_service = BranchService(
            tree_store=cls.tree_store,
            branch_store=cls.branch_store,
            message_repo=cls.message_repo,
        )
        cls.svc = ConversationService(
            message_repo=cls.message_repo,
            tree_store=cls.tree_store,
            llm_client=_FakeLLM(),
            context_service=cls.context_service,
            search_service=_FakeSearch(),
            file_service=_FakeFile(),
            branch_service=cls.branch_service,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._scope.__exit__(None, None, None)
        cls._tmpdir.cleanup()

    def setUp(self) -> None:
        from app.storage.models import TreeRoot
        from app.storage.database import get_connection
        self.tree_store._root = TreeRoot(version="1.0", nodes=[])
        self.tree_store._rebuild_cache()
        root = FolderNode(
            id="root", parent_id=None, sort_order=0, enabled="some",
            title="未分类",
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        )
        self.tree_store._root.nodes.append(root)
        self.tree_store._rebuild_cache()
        self.tree_store._save_tree(self.tree_store._root)
        self.tree_store._save_trash([])
        conn = get_connection()
        with conn:
            conn.execute("DELETE FROM branch_nodes")
            conn.execute("DELETE FROM fork_nodes")
            conn.execute("DELETE FROM messages")

        for conv_id in ("conv", "conv2"):
            self.tree_store.create_node(ConversationNode(
                id=conv_id, parent_id="root", title=conv_id,
            ))
            for node_id, role, content in [
                ("u1", Role.USER, "问题一"), ("a1", Role.ASSISTANT, "回答一"),
                ("u2", Role.USER, "问题二"), ("a2", Role.ASSISTANT, "回答二"),
            ]:
                nid = f"{conv_id}-{node_id}"
                self.tree_store.create_node(MessageNode(
                    id=nid, parent_id=conv_id, message_id=nid,
                    role=role.value, title=f"{role.value}: {content}",
                    preview=content,
                ))
                self.message_repo.save_message(Message(
                    id=nid, conversation_id=conv_id, role=role, content=content,
                ))
        self.tree_store.clear_dirty()

    def _chain(self, conv_id: str) -> list[str]:
        return [n.id for n in self.tree_store.get_conversation_chain(conv_id)]

    def _drain(self, agen) -> list:
        async def _collect():
            out = []
            async for c in agen:
                out.append(c)
            return out
        import asyncio
        return asyncio.run(_collect())

    def _make_fork(self, conv_id: str) -> None:
        """在 conv 的 a2 上重新生成 → 分叉点 = 该对话的 u2。"""
        self._drain(self.svc.regenerate_message(conv_id, f"{conv_id}-a2"))

    # ── 普通移动 ──────────────────────────────

    def test_plain_move(self) -> None:
        """普通消息节点移动：纯移动，无分叉影响。"""
        ok = self.svc.move_message_with_fork("conv-u1", "conv", 2)
        self.assertTrue(ok)
        self.assertEqual(self._chain("conv"), ["conv-a1", "conv-u2", "conv-u1", "conv-a2"])

    def test_modified_node_same_conversation_rejected(self) -> None:
        """被修改节点（同对话内）→ 拒绝（3.6.1，拍板 3）。"""
        self._make_fork("conv")  # 分叉点 conv-u2;被修改节点 = 新 a
        new_a = self._chain("conv")[-1]
        ok = self.svc.move_message_with_fork(new_a, "conv", 0)
        self.assertFalse(ok)
        # 树未变
        self.assertEqual(self._chain("conv")[-1], new_a)

    # ── "之间"插入 ────────────────────────────

    def test_drop_between_makes_new_fork_point(self) -> None:
        """拖普通节点到分叉点与被修改节点之间 → 新节点成为分叉点（原则 3）。"""
        self._make_fork("conv")
        new_a = self._chain("conv")[-1]
        # 把 conv-u1 拖到 conv-u2(分叉点) 与 new_a(被修改节点) 之间
        ok = self.svc.move_message_with_fork(
            "conv-u1", "conv", 2, prev_id="conv-u2", next_id=new_a
        )
        self.assertTrue(ok)
        # 新链 = [a1, u2, u1, new_a];u1 成为分叉点,u2 退化
        chain = self._chain("conv")
        self.assertEqual(chain, ["conv-a1", "conv-u2", "conv-u1", new_a])
        u1 = self.tree_store.get_node("conv-u1")
        self.assertTrue(u1.is_fork_point)
        self.assertEqual(u1.fork_branch_count, 2)
        self.assertFalse(self.tree_store.get_node("conv-u2").is_fork_point)
        # 分支数据迁移:分支 0 = [u1, u2, a2]?——原 u2 名下分支 re-anchor 到 u1
        branches = self.branch_store.get_all_branches("conv-u1")
        # 原分支 0 = [u2, a2] → [u1, a2];原分支 1 = [u2, new_a] → [u1, new_a]
        self.assertEqual(branches[0], ["conv-u1", "conv-a2"])
        self.assertEqual(branches[1], ["conv-u1", new_a])

    # ── 分叉点移走 ────────────────────────────

    def test_fork_point_moved_away_reanchors(self) -> None:
        """分叉点移走 → 数据原地保留,re-anchor 到补位后的新前驱（3.6.2）。"""
        self._make_fork("conv")
        new_a = self._chain("conv")[-1]
        # 把分叉点 conv-u2 移到链尾(普通位置)
        ok = self.svc.move_message_with_fork(
            "conv-u2", "conv", 4, prev_id=new_a, next_id=None
        )
        self.assertTrue(ok)
        chain = self._chain("conv")
        # 补位后 new_a 的前驱 = a1;数据迁移到 a1
        self.assertEqual(chain, ["conv-u1", "conv-a1", new_a, "conv-u2"])
        a1 = self.tree_store.get_node("conv-a1")
        self.assertTrue(a1.is_fork_point)
        self.assertEqual(a1.fork_branch_count, 2)
        self.assertFalse(self.tree_store.get_node("conv-u2").is_fork_point)
        # 分支数据:原 u2 分支 re-anchor 到 a1;
        # 当前分支(1)随后被同步覆写为链切片(含移走的 u2)
        branches = self.branch_store.get_all_branches("conv-a1")
        self.assertEqual(branches[0], ["conv-a1", "conv-a2"])
        self.assertEqual(branches[1], ["conv-a1", new_a, "conv-u2"])

    # ── 跨对话整体迁移 ────────────────────────

    def test_cross_conv_modified_transfers_to_prev(self) -> None:
        """跨对话被修改节点 → 整体迁移;目标前驱成为新分叉点（拍板 1/2）。"""
        self._make_fork("conv")
        new_a = self._chain("conv")[-1]
        # 拖到 conv2 的链尾(普通位置):前驱 = conv2-a2
        ok = self.svc.move_message_with_fork(
            new_a, "conv2", 4, prev_id="conv2-a2", next_id=None
        )
        self.assertTrue(ok)
        # 源对话链回退到 X 之前
        self.assertEqual(self._chain("conv"), ["conv-u1", "conv-a1", "conv-u2"])
        # 目标对话链:X 插入链尾;前驱 conv2-a2 成为分叉点
        tgt_chain = self._chain("conv2")
        self.assertEqual(tgt_chain[-1], new_a)
        a2 = self.tree_store.get_node("conv2-a2")
        self.assertTrue(a2.is_fork_point)
        self.assertEqual(a2.fork_branch_count, 2)
        # 分支数据迁移到 conv2-a2 名下(源分叉点 u2 移除,头部替换)
        branches = self.branch_store.get_all_branches("conv2-a2")
        self.assertEqual(branches[0], ["conv2-a2", "conv-a2"])
        self.assertEqual(branches[1], ["conv2-a2", new_a])
        # 源分叉点 conv-u2 数据已清
        self.assertEqual(self.branch_store.get_branch_indexes("conv-u2"), [])
        self.assertFalse(self.tree_store.get_node("conv-u2").is_fork_point)

    def test_cross_conv_modified_into_between(self) -> None:
        """跨对话被修改节点拖到"之间" → 数据并入目标分叉点。"""
        self._make_fork("conv")
        new_a = self._chain("conv")[-1]
        # conv2 上再造一个分叉:分叉点 conv2-u2,被修改节点 = conv2 新 a
        self._make_fork("conv2")
        tgt_new_a = self._chain("conv2")[-1]
        # 把 conv 的 new_a 拖到 conv2-u2 与 tgt_new_a 之间
        ok = self.svc.move_message_with_fork(
            new_a, "conv2", 3, prev_id="conv2-u2", next_id=tgt_new_a
        )
        self.assertTrue(ok)
        # 目标分叉点 conv2-u2 名下:原分支 + 迁移分支(编号接续)
        branches = self.branch_store.get_all_branches("conv2-u2")
        self.assertEqual(len(branches), 4)
        self.assertEqual(branches[3], ["conv2-u2", new_a])
        # 目标链含 new_a(在 u2 之后)
        tgt_chain = self._chain("conv2")
        self.assertEqual(tgt_chain[2], "conv2-u2")
        self.assertEqual(tgt_chain[3], new_a)

    def test_folder_move_plain(self) -> None:
        """文件夹移动:纯移动,分支数据不动。"""
        self.tree_store.create_node(FolderNode(
            id="f1", parent_id="root", title="目录",
        ))
        ok = self.svc.move_message_with_fork("conv", "f1", 0)
        self.assertTrue(ok)
        self.assertEqual(self.tree_store.get_node("conv").parent_id, "f1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
