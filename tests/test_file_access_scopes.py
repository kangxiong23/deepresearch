# Layer: tests
# File: tests/test_file_access_scopes.py
# Responsibility: 验证本地文件夹授权的三层作用域——全局 / 文件夹节点 / 对话节点，
#                 以及"节点被禁用时其授权不生效"这条与上下文注入一致的规则。
#                 直接构造 TreeStore（临时目录），不碰数据库与真实 data/。

from __future__ import annotations

import json
import os
import shutil
import unittest
from pathlib import Path

import config as app_config
from config import ConfigScope
from app.core.context_service import ContextService
from app.core.conversation_service import ConversationService
from app.core.file_tool_service import FileToolService
from app.storage.models import ConversationNode, FolderNode
from app.storage.tree_store import TreeStore
from app.utils.file_access_settings import normalize_root, root_key

_BASE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "_test_scopes",
)


def _mkdir(*parts: str) -> str:
    path = os.path.join(_BASE, *parts)
    os.makedirs(path, exist_ok=True)
    return path


class ScopeTestBase(unittest.TestCase):
    """一棵小树：root → A → (C1, B → C2)，root → C3。"""

    def setUp(self) -> None:
        shutil.rmtree(_BASE, ignore_errors=True)
        os.makedirs(_BASE, exist_ok=True)
        self.addCleanup(shutil.rmtree, _BASE, True)

        self.tree = TreeStore(base_path=Path(_BASE) / "tree")
        self._seq = 0
        self.folder_a = self._add(FolderNode, "root", "项目A")
        self.folder_b = self._add(FolderNode, self.folder_a, "子目录B")
        self.conv_1 = self._add(ConversationNode, self.folder_a, "对话1")
        self.conv_2 = self._add(ConversationNode, self.folder_b, "对话2")
        self.conv_3 = self._add(ConversationNode, "root", "对话3")

        self.root_a = _mkdir("roots", "a")
        self.root_b = _mkdir("roots", "b")
        self.root_g = _mkdir("roots", "global")

        self.svc = ConversationService.__new__(ConversationService)
        self.svc._tree = self.tree
        self.svc._context_svc = ContextService(None, None, tree_store=self.tree)
        self.svc._file_tools = FileToolService()
        self.svc._read_log = None
        self.svc._memory_reads = {}
        self.svc._blobs = None

    def _add(self, cls, parent_id, title):
        self._seq += 1
        node = cls(id=f"{title}-{self._seq}", parent_id=parent_id, title=title)
        self.tree.create_node(node)
        return node.id

    def grant(self, node_id: str, *paths: str) -> None:
        self.tree.update_node(node_id, local_roots=list(paths))

    def disable(self, node_id: str) -> None:
        """走界面同一条路径：级联关闭 + 重算祖先，否则根节点的 True 会压过来。"""
        self.tree.set_node_enabled_cascade_down(node_id, False)
        self.tree.recompute_ancestors_enabled(node_id)

    def scope(self, enabled=True, roots=None):
        return ConfigScope(
            file_access_enabled=enabled,
            file_access_roots=list(roots or []),
        )


class TestEffectiveRoots(ScopeTestBase):
    def test_global_roots_reach_every_conversation(self) -> None:
        with self.scope(roots=[self.root_g]):
            self.assertEqual(self.svc.effective_file_roots(self.conv_1), [self.root_g])
            self.assertEqual(self.svc.effective_file_roots(self.conv_3), [self.root_g])

    def test_global_switch_off_hides_global_roots(self) -> None:
        with self.scope(enabled=False, roots=[self.root_g]):
            self.assertEqual(self.svc.effective_file_roots(self.conv_1), [])

    def test_folder_grant_covers_its_subtree_only(self) -> None:
        self.grant(self.folder_a, self.root_a)
        with self.scope():
            self.assertEqual(self.svc.effective_file_roots(self.conv_1), [self.root_a])
            # 孙节点（A → B → 对话2）同样继承
            self.assertEqual(self.svc.effective_file_roots(self.conv_2), [self.root_a])
            # 隔壁对话不受影响
            self.assertEqual(self.svc.effective_file_roots(self.conv_3), [])

    def test_conversation_grant_is_self_only(self) -> None:
        self.grant(self.conv_1, self.root_a)
        with self.scope():
            self.assertEqual(self.svc.effective_file_roots(self.conv_1), [self.root_a])
            self.assertEqual(self.svc.effective_file_roots(self.conv_2), [])

    def test_layers_stack_and_dedupe(self) -> None:
        self.grant(self.folder_a, self.root_a)
        self.grant(self.conv_1, self.root_a, self.root_b)
        with self.scope(roots=[self.root_g]):
            roots = self.svc.effective_file_roots(self.conv_1)
        # 全局在前，然后是自己的（近的优先），祖先重复的目录不重复出现
        self.assertEqual(roots, [self.root_g, self.root_a, self.root_b])

    def test_disabled_folder_drops_its_grant(self) -> None:
        self.grant(self.folder_a, self.root_a)
        self.disable(self.folder_a)
        with self.scope():
            self.assertEqual(self.svc.effective_file_roots(self.conv_1), [])

    def test_missing_conversation_gets_global_only(self) -> None:
        with self.scope(roots=[self.root_g]):
            self.assertEqual(self.svc.effective_file_roots("not-a-node"), [self.root_g])

    def test_split_own_and_inherited(self) -> None:
        self.grant(self.folder_a, self.root_a)
        self.grant(self.conv_1, self.root_b)
        view = self.svc.get_node_local_roots(self.conv_1)
        self.assertEqual(view["own"], [self.root_b])
        self.assertEqual(view["inherited"], [self.root_a])


class TestToolGating(ScopeTestBase):
    def test_no_grants_means_no_tools(self) -> None:
        with self.scope():
            self.assertEqual(self.svc._tool_schemas(self.conv_1), [])

    def test_node_grant_alone_enables_tools(self) -> None:
        self.grant(self.conv_1, self.root_a)
        with self.scope():                     # 全局关着
            names = [s["function"]["name"] for s in self.svc._tool_schemas(self.conv_1)]
        self.assertEqual(names, ["search_files", "read_file"])

    def test_scope_is_per_conversation(self) -> None:
        self.grant(self.conv_1, self.root_a)
        with self.scope():
            self.svc._tool_schemas(self.conv_1)
            self.assertEqual(self.svc._file_tools.roots(), [self.root_a])
            self.svc._tool_schemas(self.conv_3)
            self.assertEqual(self.svc._file_tools.roots(), [])
            self.assertFalse(self.svc._file_tools.enabled())

    def test_scope_change_drops_index(self) -> None:
        tools = self.svc._file_tools
        with self.scope():
            tools.set_scope([self.root_a])
            tools._entries = ["stale"]          # 模拟已扫过
            tools._indexed_roots = (self.root_a,)
            tools.set_scope([self.root_b])
            self.assertEqual(tools._entries, [])

    def test_read_outside_scope_is_refused(self) -> None:
        inside = os.path.join(self.root_a, "inside.md")
        outside = os.path.join(self.root_b, "outside.md")
        for path in (inside, outside):
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("# 内容\n")

        tools = self.svc._file_tools
        tools.set_scope([self.root_a])
        ok = json.loads(tools.execute(
            "read_file", json.dumps({"path": inside}, ensure_ascii=False)
        ))
        self.assertEqual(ok.get("total_lines"), 1)

        denied = json.loads(tools.execute(
            "read_file", json.dumps({"path": outside}, ensure_ascii=False)
        ))
        self.assertIn("不在用户授权", denied.get("error", ""))


class TestNodeGrantCrud(ScopeTestBase):
    def test_add_validates_directory(self) -> None:
        err = self.svc.add_node_local_root(self.conv_1, os.path.join(_BASE, "nope"))
        self.assertIn("目录不存在", err)
        self.assertEqual(self.tree.get_node(self.conv_1).local_roots, [])

    def test_add_normalizes_and_dedupes(self) -> None:
        noisy = self.root_a.replace("\\", "/") + "/"
        self.assertEqual(self.svc.add_node_local_root(self.conv_1, noisy), "")
        self.assertEqual(
            self.tree.get_node(self.conv_1).local_roots, [normalize_root(self.root_a)]
        )
        # 同一目录换个写法再加：静默成功，不重复
        self.assertEqual(self.svc.add_node_local_root(self.conv_1, self.root_a.upper()), "")
        self.assertEqual(len(self.tree.get_node(self.conv_1).local_roots), 1)

    def test_add_rejects_message_nodes(self) -> None:
        err = self.svc.add_node_local_root("no-such-node", self.root_a)
        self.assertTrue(err)

    def test_remove_only_touches_own_grant(self) -> None:
        self.grant(self.folder_a, self.root_a)
        self.grant(self.conv_1, self.root_a, self.root_b)
        self.assertEqual(self.svc.remove_node_local_root(self.conv_1, self.root_a), "")
        self.assertEqual(self.tree.get_node(self.conv_1).local_roots, [self.root_b])
        # 祖先的授权不受影响
        self.assertEqual(self.tree.get_node(self.folder_a).local_roots, [self.root_a])

    def test_remove_missing_path_is_noop(self) -> None:
        self.grant(self.conv_1, self.root_a)
        self.assertEqual(self.svc.remove_node_local_root(self.conv_1, self.root_b), "")
        self.assertEqual(self.tree.get_node(self.conv_1).local_roots, [self.root_a])


class TestPersistence(ScopeTestBase):
    def test_local_roots_survive_reload(self) -> None:
        self.grant(self.conv_1, self.root_a)
        self.grant(self.folder_a, self.root_b)

        again = TreeStore(base_path=Path(_BASE) / "tree")
        self.assertEqual(again.get_node(self.conv_1).local_roots, [self.root_a])
        self.assertEqual(again.get_node(self.folder_a).local_roots, [self.root_b])

    def test_overview_lists_only_granted_nodes(self) -> None:
        self.grant(self.folder_a, self.root_a)
        self.grant(self.conv_3, self.root_b)
        with self.scope(roots=[self.root_g]):
            view = self.svc.get_file_access_overview()

        self.assertTrue(view["enabled"])
        self.assertEqual(view["global_roots"], [self.root_g])
        by_id = {n["id"]: n for n in view["nodes"]}
        self.assertEqual(set(by_id), {self.folder_a, self.conv_3})
        self.assertEqual(by_id[self.folder_a]["node_type"], "folder")
        self.assertEqual(by_id[self.folder_a]["roots"], [self.root_a])
        self.assertTrue(by_id[self.folder_a]["path"].endswith("项目A"))
        self.assertEqual(by_id[self.conv_3]["node_type"], "conversation")
        self.assertTrue(by_id[self.conv_3]["enabled"])

    def test_overview_reports_disabled_node(self) -> None:
        self.grant(self.folder_a, self.root_a)
        self.disable(self.folder_a)
        with self.scope():
            view = self.svc.get_file_access_overview()
        self.assertFalse(view["nodes"][0]["enabled"])
        self.assertEqual(view["global_roots"], [])


if __name__ == "__main__":
    unittest.main()
