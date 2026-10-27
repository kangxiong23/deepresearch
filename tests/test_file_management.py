# Layer: tests
# File: tests/test_file_management.py
# Responsibility: 验证三种文件意图的落盘语义——
#                 消息附件（随消息行走）、节点挂载（快照）、工具读取记录（追加日志），
#                 以及附件进入上下文时的展开/预算/截断规则。

from __future__ import annotations

import os
import shutil
import unittest

from config import ConfigScope
from app.core import message_attachments as ma
from app.core.context_service import ContextService
from app.core.conversation_service import ConversationService
from app.storage.blob_store import (
    BlobTooLarge,
    FileBlobStore,
    node_mounts_bytes,
)
from app.storage.database import get_connection, initialize_database, reset_connection
from app.storage.message_repo import MessageRepo
from app.storage.models import (
    ConversationNode,
    FolderNode,
    LLMContext,
    Message,
    Role,
    utcnow,
)
from app.storage.read_log_store import ReadLogStore, MAX_ENTRIES_PER_CONVERSATION
from app.storage.tree_store import TreeStore

# 沙箱下 tempfile.mkdtemp 不可写，统一用工作区内的临时目录（logs/ 已 gitignore）
_BASE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "_test_file_management",
)


class TempDataCase(unittest.TestCase):
    """把 DB / tree / files 都指向 logs/ 下的一次性目录。"""

    def setUp(self) -> None:
        shutil.rmtree(_BASE, ignore_errors=True)
        os.makedirs(_BASE, exist_ok=True)
        self.addCleanup(shutil.rmtree, _BASE, True)
        self.root = _BASE
        self._scope = ConfigScope(
            DB_PATH=os.path.join(_BASE, "db.sqlite"),
            TREE_STORE_PATH=os.path.join(_BASE, "tree"),
            CONTEXT_STORE_PATH=os.path.join(_BASE, "context"),
            KG_STORE_PATH=os.path.join(_BASE, "kg"),
            FILES_STORE_PATH=os.path.join(_BASE, "files"),
        )
        self._scope.__enter__()
        self.addCleanup(self._scope.__exit__, None, None, None)
        initialize_database()
        reset_connection()

    def tearDown(self) -> None:
        reset_connection()

    def write(self, name: str, text: str) -> str:
        folder = os.path.join(self.root, "src")
        os.makedirs(folder, exist_ok=True)
        full = os.path.join(folder, name)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)
        return full


# ──────────────────────────────────────────────
# 纯函数：上限 / 展开 / 记账
# ──────────────────────────────────────────────

class TestAttachmentRules(unittest.TestCase):
    def test_small_files_pass_through(self) -> None:
        items, warnings = ma.normalize([("a.md", "内容"), ("b.txt", "更多")])
        self.assertEqual([i["name"] for i in items], ["a.md", "b.txt"])
        self.assertEqual(warnings, [])

    def test_per_file_cap_truncates_with_marker(self) -> None:
        big = "字" * (ma.MAX_FILE_CHARS + 500)
        items, warnings = ma.normalize([("big.md", big)])
        self.assertEqual(len(items), 1)
        self.assertIn("已截断 500 字", items[0]["text"])
        self.assertLessEqual(len(items[0]["text"]), ma.MAX_FILE_CHARS + 80)
        self.assertTrue(warnings and "单文件上限" in warnings[0])

    def test_message_cap_drops_later_files(self) -> None:
        one = "x" * ma.MAX_FILE_CHARS
        pairs = [(f"f{i}.txt", one) for i in range(4)]
        items, warnings = ma.normalize(pairs)
        total = sum(len(i["text"]) for i in items)
        self.assertLessEqual(total, ma.MAX_MESSAGE_CHARS + 200)
        self.assertLess(len(items), 4)
        self.assertTrue(any(("未附加" in w) or ("总量上限" in w) for w in warnings))

    def test_empty_text_is_skipped(self) -> None:
        items, _ = ma.normalize([("a.txt", "   "), ("b.txt", "")])
        self.assertEqual(items, [])

    def test_expand_appends_after_body(self) -> None:
        text = ma.expand("问题", [{"name": "a.md", "text": "资料"}])
        self.assertTrue(text.startswith("问题"))
        self.assertIn("[附件：a.md]", text)
        self.assertIn("资料", text)

    def test_expand_without_attachments_is_identity(self) -> None:
        self.assertEqual(ma.expand("问题", []), "问题")
        self.assertEqual(ma.expand("问题", None), "问题")

    def test_estimate_counts_attachments(self) -> None:
        plain = ma.estimate_tokens("x" * 400, [])
        heavy = ma.estimate_tokens("x" * 400, [{"name": "a", "text": "y" * 4000}])
        self.assertEqual(plain, 100)
        self.assertGreater(heavy, plain * 10)


# ──────────────────────────────────────────────
# 消息附件落库
# ──────────────────────────────────────────────

class TestMessageRepoAttachments(TempDataCase):
    def test_roundtrip(self) -> None:
        repo = MessageRepo()
        repo.save_message(Message(
            id="m-1", conversation_id="c-1", role=Role.USER, content="问题",
            created_at=utcnow(), token_count=5,
            attachments=[{"name": "报告.docx", "text": "正文内容"}],
        ))
        loaded = repo.get_messages("c-1")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].content, "问题", "content 只放用户自己写的字")
        self.assertEqual(loaded[0].attachments,
                         [{"name": "报告.docx", "text": "正文内容"}])

    def test_message_without_attachments_reads_back_empty(self) -> None:
        repo = MessageRepo()
        repo.save_message(Message(
            id="m-2", conversation_id="c-1", role=Role.ASSISTANT, content="回答",
            created_at=utcnow(),
        ))
        self.assertEqual(repo.get_messages("c-1")[0].attachments, [])

    def test_dirty_json_degrades_to_empty(self) -> None:
        repo = MessageRepo()
        repo.save_message(Message(
            id="m-3", conversation_id="c-1", role=Role.USER, content="x",
            created_at=utcnow(),
        ))
        conn = get_connection()
        with conn:
            conn.execute(
                "UPDATE messages SET attachments = ? WHERE id = 'm-3'",
                ("{不是数组}",),
            )
        self.assertEqual(repo.get_messages("c-1")[0].attachments, [])

    def test_legacy_table_without_column_still_loads(self) -> None:
        """老库（无 attachments 列）不能被新增字段拖垮。"""
        conn = get_connection()
        with conn:
            conn.execute("DROP TABLE messages")
            conn.execute(
                """
                CREATE TABLE messages (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',
                    is_thinking INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    token_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                "INSERT INTO messages VALUES "
                "('old-1','c-1','user','历史消息',0,'2026-01-01T00:00:00.000000',3)"
            )
        loaded = MessageRepo().get_messages("c-1")
        self.assertEqual(loaded[0].content, "历史消息")
        self.assertEqual(loaded[0].attachments, [])


# ──────────────────────────────────────────────
# 上下文展开
# ──────────────────────────────────────────────

class _StubStore:
    def get_blocks(self):
        return []

    def list_templates(self):
        return []


class TestContextExpansion(TempDataCase):
    def make(self, history: list[Message]) -> ContextService:
        svc = ContextService(
            message_repo=MessageRepo(),
            context_store=_StubStore(),
            tree_store=None,
        )
        svc.get_enabled_history_messages = lambda **kw: list(history)
        return svc

    def test_history_message_attachments_are_expanded(self) -> None:
        history = [Message(
            id="m-1", conversation_id="c-1", role=Role.USER, content="旧问题",
            created_at=utcnow(),
            attachments=[{"name": "预算.md", "text": "预算 245 万元"}],
        )]
        ctx = self.make(history).build_llm_context("c-1", "新问题")
        self.assertEqual(ctx.messages[0]["content"].count("预算 245 万元"), 1)
        self.assertTrue(ctx.messages[0]["content"].startswith("旧问题"))
        self.assertEqual(ctx.messages[-1]["content"], "新问题")

    def test_new_user_attachments_are_expanded_once(self) -> None:
        svc = self.make([])
        ctx = svc.build_llm_context(
            "c-1", "新问题",
            new_user_attachments=[{"name": "a.md", "text": "资料"}],
        )
        last = ctx.messages[-1]["content"]
        self.assertTrue(last.startswith("新问题"))
        self.assertEqual(last.count("资料"), 1)

    def test_content_stays_clean(self) -> None:
        """content 列保持干净：编辑/复制/搜索都只看到用户写的字。"""
        history = [Message(
            id="m-1", conversation_id="c-1", role=Role.USER, content="旧问题",
            created_at=utcnow(), attachments=[{"name": "a.md", "text": "资料"}],
        )]
        self.assertEqual(history[0].content, "旧问题")

    def test_truncation_counts_attachment_tokens(self) -> None:
        heavy = Message(
            id="m-1", conversation_id="c-1", role=Role.USER, content="短",
            created_at=utcnow(),
            attachments=[{"name": "a.md", "text": "字" * 40000}],
        )
        light = Message(
            id="m-2", conversation_id="c-1", role=Role.ASSISTANT, content="回答",
            created_at=utcnow(),
        )
        svc = self.make([heavy, light])
        kept = svc._truncate_history([heavy, light], budget_tokens=200)
        self.assertEqual([m.id for m in kept], ["m-2"])


# ──────────────────────────────────────────────
# 工具读取记录
# ──────────────────────────────────────────────

class TestReadLog(TempDataCase):
    def test_append_and_recent_newest_first(self) -> None:
        log = ReadLogStore()
        log.append("c-1", r"D:\a.md", 1, 10)
        log.append("c-1", r"D:\b.md", 3, 8)
        entries = log.recent("c-1")
        self.assertEqual([e["path"] for e in entries], [r"D:\b.md", r"D:\a.md"])
        self.assertEqual(entries[0]["start_line"], 3)
        self.assertEqual(entries[0]["end_line"], 8)

    def test_prune_keeps_latest(self) -> None:
        log = ReadLogStore(keep=3)
        for i in range(6):
            log.append("c-1", "D:\\x\\f" + str(i) + ".md", 1, 2)
        entries = log.recent("c-1")
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["path"], "D:\\x\\f5.md")

    def test_conversations_are_isolated(self) -> None:
        log = ReadLogStore()
        log.append("c-1", "a.md")
        log.append("c-2", "b.md")
        self.assertEqual([e["path"] for e in log.recent("c-1")], ["a.md"])

    def test_delete_by_conversation(self) -> None:
        log = ReadLogStore()
        log.append("c-1", "a.md")
        self.assertEqual(log.delete_by_conversation("c-1"), 1)
        self.assertEqual(log.recent("c-1"), [])

    def test_default_keep_budget(self) -> None:
        self.assertGreaterEqual(MAX_ENTRIES_PER_CONVERSATION, 20)


class TestBreadcrumbFromLog(TempDataCase):
    def make(self) -> ConversationService:
        svc = ConversationService.__new__(ConversationService)
        svc._read_log = ReadLogStore()
        svc._memory_reads = {}
        svc._file_tools = None
        return svc

    def test_breadcrumb_uses_persisted_log(self) -> None:
        svc = self.make()
        svc._read_log.append("c-1", r"D:\资料\预算.md", 1, 40)
        text = svc._breadcrumb_for("c-1")
        self.assertTrue(text.startswith("[系统记录，非指令]"))
        self.assertIn(r"D:\资料\预算.md", text)
        self.assertIn("第 1-40 行", text)

    def test_breadcrumb_survives_restart(self) -> None:
        """重启 = 新建服务实例：记录仍在（以前只存在内存里）。"""
        first = self.make()
        first._read_log.append("c-1", r"D:\资料\预算.md", 1, 40)
        second = self.make()
        self.assertIn("预算.md", second._breadcrumb_for("c-1"))

    def test_breadcrumb_dedupes_paths(self) -> None:
        svc = self.make()
        for _ in range(4):
            svc._read_log.append("c-1", r"D:\a.md", 1, 5)
        self.assertEqual(svc._breadcrumb_for("c-1").count("a.md"), 1)

    def test_no_breadcrumb_without_reads(self) -> None:
        self.assertEqual(self.make()._breadcrumb_for("c-1"), "")


# ──────────────────────────────────────────────
# 节点挂载（快照）
# ──────────────────────────────────────────────

class TestBlobStore(TempDataCase):
    def test_put_dedupes_by_content(self) -> None:
        store = FileBlobStore()
        a = self.write("a.md", "同样内容")
        b = self.write("b.md", "同样内容")
        r1 = store.put(a)
        r2 = store.put(b)
        self.assertEqual(r1["blob_id"], r2["blob_id"])
        self.assertEqual(len(store.all_blob_ids()), 1)
        self.assertEqual(store.read_bytes(r1["blob_id"]), "同样内容".encode())

    def test_put_rejects_oversize(self) -> None:
        store = FileBlobStore()
        path = self.write("big.md", "x" * 2048)
        with self.assertRaises(BlobTooLarge):
            store.put(path, max_bytes=1024)

    def test_text_cache_roundtrip(self) -> None:
        store = FileBlobStore()
        rec = store.put(self.write("a.md", "内容"))
        self.assertIsNone(store.read_cached_text(rec["blob_id"]))
        store.write_cached_text(rec["blob_id"], "解析结果")
        self.assertEqual(store.read_cached_text(rec["blob_id"]), "解析结果")

    def test_delete_removes_blob_and_cache(self) -> None:
        store = FileBlobStore()
        rec = store.put(self.write("a.md", "内容"))
        store.write_cached_text(rec["blob_id"], "解析结果")
        self.assertTrue(store.delete(rec["blob_id"]))
        self.assertFalse(store.exists(rec["blob_id"]))
        self.assertIsNone(store.read_cached_text(rec["blob_id"]))

    def test_bad_blob_id_cannot_escape_root(self) -> None:
        store = FileBlobStore()
        with self.assertRaises(ValueError):
            store.blob_path("../../etc/passwd")

    def test_node_mounts_bytes_counts_unique(self) -> None:
        self.assertEqual(node_mounts_bytes([
            {"blob_id": "a", "size": 10},
            {"blob_id": "a", "size": 10},
            {"blob_id": "b", "size": 5},
        ]), 15)


class _StubTree:
    def __init__(self, node) -> None:
        self._node = node
        self.updated: list = []

    def get_node(self, node_id):
        return self._node if node_id == self._node.id else None

    def update_node(self, node_id, **updates) -> None:
        self.updated.append((node_id, updates))
        for key, value in updates.items():
            setattr(self._node, key, value)


class TestAttachFileMounts(TempDataCase):
    def make(self, node):
        svc = ConversationService.__new__(ConversationService)
        svc._tree = _StubTree(node)
        svc._blobs = FileBlobStore()
        return svc

    def test_attach_creates_snapshot_not_path(self) -> None:
        node = ConversationNode(id="c-1", parent_id=None, title="对话")
        svc = self.make(node)
        path = self.write("报告.md", "# 标题")

        self.assertEqual(svc.attach_file("c-1", path), "")
        self.assertEqual(node.attachment_paths, [], "新式挂载不再记裸路径")
        self.assertEqual(len(node.mounts), 1)
        self.assertEqual(node.mounts[0]["name"], "报告.md")
        self.assertTrue(svc._blobs.exists(node.mounts[0]["blob_id"]))

    def test_snapshot_survives_source_deletion(self) -> None:
        node = ConversationNode(id="c-1", parent_id=None, title="对话")
        svc = self.make(node)
        path = self.write("报告.md", "原始内容")
        svc.attach_file("c-1", path)
        os.remove(path)
        blob_id = node.mounts[0]["blob_id"]
        self.assertEqual(svc._blobs.read_bytes(blob_id), "原始内容".encode())

    def test_attach_same_content_twice_is_idempotent(self) -> None:
        node = ConversationNode(id="c-1", parent_id=None, title="对话")
        svc = self.make(node)
        path = self.write("报告.md", "内容")
        svc.attach_file("c-1", path)
        svc.attach_file("c-1", path)
        self.assertEqual(len(node.mounts), 1)

    def test_attach_over_node_budget_returns_reason(self) -> None:
        node = ConversationNode(id="c-1", parent_id=None, title="对话")
        svc = self.make(node)
        node.mounts.append({
            "blob_id": "f" * 64, "name": "已占满", "size": 200 * 1024 * 1024,
        })
        msg = svc.attach_file("c-1", self.write("b.md", "y"))
        self.assertTrue(msg)
        self.assertIn("上限", msg)

    def test_detach_by_name_and_blob_id(self) -> None:
        node = ConversationNode(id="c-1", parent_id=None, title="对话")
        svc = self.make(node)
        path = self.write("报告.md", "内容")
        svc.attach_file("c-1", path)
        blob_id = node.mounts[0]["blob_id"]

        svc.detach_file("c-1", "报告.md")
        self.assertEqual(node.mounts, [])

        svc.attach_file("c-1", path)
        svc.detach_file("c-1", blob_id)
        self.assertEqual(node.mounts, [])

    def test_missing_node_and_wrong_type(self) -> None:
        node = FolderNode(id="f-1", parent_id=None, title="目录")
        svc = self.make(node)
        self.assertEqual(svc.attach_file("nope", "x"), "节点不存在")
        self.assertTrue(svc.attach_file("f-1", "x"))


# ──────────────────────────────────────────────
# 挂载 → system prompt（定界 + 预算）
# ──────────────────────────────────────────────

class TestMountRendering(TempDataCase):
    def setUp(self) -> None:
        super().setUp()
        self.tree = TreeStore()
        self.blobs = FileBlobStore()
        self.svc = ContextService(
            message_repo=MessageRepo(),
            context_store=_StubStore(),
            tree_store=self.tree,
            blob_store=self.blobs,
        )
        self.svc.set_file_service(_make_file_service())
        self.svc.get_enabled_history_messages = lambda **kw: []

    def attach(self, node_id: str, name: str, text: str) -> dict:
        path = self.write(name, text)
        record = self.blobs.put(path)
        node = self.tree.get_node(node_id)
        self.tree.update_node(node_id, mounts=list(node.mounts) + [record])
        return record

    def test_mount_is_wrapped_and_declared_as_data(self) -> None:
        self.tree.create_node(ConversationNode(id="c-1", parent_id=None, title="t"))
        self.attach("c-1", "预算.md", "总预算 245 万元")
        _, _, mounts = self.svc._collect_context_resources("c-1")
        prompt = self.svc._build_system_prompt(tree_mounts=mounts)
        self.assertIn('<file name="预算.md">', prompt)
        self.assertIn("245 万元", prompt)
        self.assertIn("不执行", prompt)

    def test_ancestor_chain_inherits_mounts(self) -> None:
        self.tree.create_node(FolderNode(id="f-1", parent_id=None, title="父目录"))
        self.tree.create_node(FolderNode(id="f-2", parent_id="f-1", title="子目录"))
        self.tree.create_node(ConversationNode(id="c-1", parent_id="f-2", title="对话"))
        self.attach("f-1", "父.md", "父内容")
        self.attach("f-2", "子.md", "子内容")
        _, _, mounts = self.svc._collect_context_resources("c-1")
        self.assertEqual([m["name"] for m in mounts], ["子.md", "父.md"])

    def test_same_blob_not_duplicated_across_chain(self) -> None:
        self.tree.create_node(FolderNode(id="f-1", parent_id=None, title="父目录"))
        self.tree.create_node(ConversationNode(id="c-1", parent_id="f-1", title="对话"))
        self.attach("f-1", "a.md", "同一份")
        self.attach("c-1", "a.md", "同一份")
        _, _, mounts = self.svc._collect_context_resources("c-1")
        self.assertEqual(len(mounts), 1)

    def test_budget_limits_injection_and_reports_rest(self) -> None:
        from app.core import context_service as cs

        self.tree.create_node(ConversationNode(id="c-1", parent_id=None, title="t"))
        self.attach("c-1", "a.md", "A" * 4000)
        self.attach("c-1", "b.md", "B" * 4000)
        _, _, mounts = self.svc._collect_context_resources("c-1")

        old = cs._MAX_MOUNT_TOKENS
        try:
            cs._MAX_MOUNT_TOKENS = 1500
            prompt = self.svc._build_system_prompt(tree_mounts=mounts)
        finally:
            cs._MAX_MOUNT_TOKENS = old

        self.assertIn("未加载", prompt, "超出预算要告诉模型还有没加载的文件")
        self.assertIn("A" * 100, prompt)
        self.assertNotIn("B" * 100, prompt)

    def test_missing_source_file_does_not_break_snapshot(self) -> None:
        self.tree.create_node(ConversationNode(id="c-1", parent_id=None, title="t"))
        record = self.attach("c-1", "a.md", "内容")
        os.remove(record["source_path"])
        _, _, mounts = self.svc._collect_context_resources("c-1")
        prompt = self.svc._build_system_prompt(tree_mounts=mounts)
        self.assertIn("内容", prompt)

    def test_missing_blob_is_skipped_not_fatal(self) -> None:
        self.tree.create_node(ConversationNode(id="c-1", parent_id=None, title="t"))
        record = self.attach("c-1", "a.md", "内容")
        self.blobs.delete(record["blob_id"])
        _, _, mounts = self.svc._collect_context_resources("c-1")
        prompt = self.svc._build_system_prompt(tree_mounts=mounts)
        # 读不到内容就不注入（退回默认提示），但不能抛异常、不能留半句标签
        self.assertNotIn("<file", prompt)


class TestLegacyAttachmentPaths(TempDataCase):
    def test_missing_file_is_visible_not_silent(self) -> None:
        svc = ContextService(
            message_repo=MessageRepo(),
            context_store=_StubStore(),
            tree_store=None,
        )
        text = svc._read_attachment_content(
            os.path.join(self.root, "不存在的文件.md")
        )
        self.assertIn("已丢失", text)


# ──────────────────────────────────────────────
# 发送链路：附件只存列、不进 content
# ──────────────────────────────────────────────

class _SendTree:
    def __init__(self, node) -> None:
        self._node = node

    def get_node(self, node_id):
        return self._node

    def mark_incomplete(self, *a, **k) -> None:
        pass


class _SendRepo:
    def __init__(self) -> None:
        self.saved: list = []

    def save_message(self, message) -> None:
        self.saved.append(message)

    def get_messages_by_ids(self, ids):
        return [m for m in self.saved if m.id in ids]


class _SendBranch:
    def extend_current_branch(self, *a, **k) -> None:
        pass

    def create_branch(self, *a, **k) -> None:
        pass


class _SendSearch:
    async def search_and_format(self, text: str) -> str:
        return ""


class _SendFile:
    def __init__(self, pairs) -> None:
        self._pairs = pairs

    async def extract_files(self, paths):
        return list(self._pairs)


class _SendContext:
    def __init__(self) -> None:
        self.kw: dict = {}

    def build_llm_context(self, **kwargs):
        self.kw = kwargs
        return LLMContext(messages=[], tools=[])


class _SendLLM:
    async def stream_chat(self, context):
        from app.storage.models import ChunkType, MessageChunk
        yield MessageChunk(delta="回答", is_done=True, chunk_type=ChunkType.TEXT)

    def abort(self) -> None:
        pass


def _make_send_service(pairs):
    import asyncio

    from app.storage.models import ConversationNode

    svc = ConversationService.__new__(ConversationService)
    svc._llm = _SendLLM()
    svc._stop_event = asyncio.Event()
    svc._file_tools = None
    svc._read_log = None
    svc._memory_reads = {}
    svc._blobs = None
    svc._tree = _SendTree(ConversationNode(id="c-1", parent_id=None, title="t"))
    svc._msg_repo = _SendRepo()
    svc._branch_svc = _SendBranch()
    svc._search_svc = _SendSearch()
    svc._file_svc = _SendFile(pairs)
    svc._context_svc = _SendContext()
    svc._save_assistant_with_thinking = lambda *a, **k: None
    svc._update_meta_after_reply = lambda *a, **k: None
    return svc


class TestSendStoresAttachments(unittest.IsolatedAsyncioTestCase):
    async def test_attachments_are_persisted_beside_content(self) -> None:
        svc = _make_send_service([("预算.md", "总预算 245 万元")])
        async for _ in svc.send_message("c-1", "看看预算", ["预算.md"]):
            pass

        user_msg = [m for m in svc._msg_repo.saved if m.role.value == "user"][0]
        self.assertEqual(user_msg.content, "看看预算", "content 只存用户写的字")
        self.assertEqual(user_msg.attachments,
                         [{"name": "预算.md", "text": "总预算 245 万元"}])

    async def test_token_count_includes_attachments(self) -> None:
        svc = _make_send_service([("a.md", "字" * 4000)])
        async for _ in svc.send_message("c-1", "问", ["a.md"]):
            pass
        user_msg = [m for m in svc._msg_repo.saved if m.role.value == "user"][0]
        self.assertGreater(user_msg.token_count, len("问") // 4 + 900)

    async def test_current_turn_gets_attachments_once(self) -> None:
        svc = _make_send_service([("a.md", "资料")])
        async for _ in svc.send_message("c-1", "问", ["a.md"]):
            pass
        kw = svc._context_svc.kw
        self.assertEqual(kw["new_user_message"], "问",
                         "附件不得拼进用户文本（否则历史里会出现两份）")
        self.assertEqual(kw["new_user_attachments"],
                         [{"name": "a.md", "text": "资料"}])

    async def test_no_files_means_no_attachments(self) -> None:
        svc = _make_send_service([])
        async for _ in svc.send_message("c-1", "问", []):
            pass
        user_msg = [m for m in svc._msg_repo.saved if m.role.value == "user"][0]
        self.assertEqual(user_msg.attachments, [])

    async def test_resend_reuses_original_attachments(self) -> None:
        """编辑重发送没选新文件时，沿用原消息的附件（改错字不丢资料）。"""
        svc = _make_send_service([])
        svc._msg_repo.save_message(Message(
            id="u-1", conversation_id="c-1", role=Role.USER, content="旧问题",
            created_at=utcnow(),
            attachments=[{"name": "资料.md", "text": "原资料"}],
        ))
        self.assertEqual(svc._attachments_of("u-1"),
                         [{"name": "资料.md", "text": "原资料"}])
        self.assertEqual(svc._attachments_of("不存在"), [])


def _make_file_service():
    from app.adapters.file_parsers import default_parsers
    from app.core.file_service import FileService
    return FileService(parsers=default_parsers())


if __name__ == "__main__":
    unittest.main()
