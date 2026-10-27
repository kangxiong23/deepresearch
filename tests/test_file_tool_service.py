# Layer: tests
# File: tests/test_file_tool_service.py
# Responsibility: 验证工具层的策略——授权校验、预算、去重、错误兜底。
#                 不验证文件解析细节（那是 test_local_fs 的职责）。

from __future__ import annotations

import json
import os
import shutil
import unittest

import config as app_config
from config import ConfigScope
from app.core.file_tool_service import FileToolService, MAX_FILES_PER_TURN


class FileToolServiceTestBase(unittest.TestCase):
    def setUp(self) -> None:
        base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "logs", "_test_file_tools",
        )
        self.root = os.path.join(base, self._testMethodName)
        shutil.rmtree(self.root, ignore_errors=True)
        os.makedirs(self.root, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.root, True)
        self.outside = os.path.join(base, "outside")
        os.makedirs(self.outside, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.outside, True)

    def write(self, rel: str, text: str, root: str | None = None) -> str:
        path = os.path.join(root or self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return path

    def scope(self, roots=None, enabled=True):
        return ConfigScope(
            file_access_enabled=enabled,
            file_access_roots=list(roots if roots is not None else [self.root]),
        )

    def run_tool(self, svc, name, **kwargs):
        return json.loads(svc.execute(name, json.dumps(kwargs, ensure_ascii=False)))


class TestGating(FileToolServiceTestBase):
    def test_disabled_without_roots(self) -> None:
        with self.scope(roots=[]):
            svc = FileToolService()
            self.assertFalse(svc.enabled())
            self.assertEqual(svc.schemas(), [])

    def test_disabled_by_switch(self) -> None:
        with self.scope(enabled=False):
            svc = FileToolService()
            self.assertFalse(svc.enabled())
            self.assertEqual(svc.schemas(), [])

    def test_enabled_exposes_two_schemas(self) -> None:
        with self.scope():
            svc = FileToolService()
            names = [s["function"]["name"] for s in svc.schemas()]
            self.assertEqual(names, ["search_files", "read_file"])


class TestSearch(FileToolServiceTestBase):
    def test_finds_by_name(self) -> None:
        self.write("2026预算.md", "# 预算")
        self.write("notes.txt", "x")
        with self.scope():
            svc = FileToolService()
            out = self.run_tool(svc, "search_files", query="预算")
            self.assertEqual(out["count"], 1)
            self.assertTrue(out["files"][0]["path"].endswith("2026预算.md"))

    def test_type_filter(self) -> None:
        self.write("a.json", "{}")
        self.write("b.md", "# x")
        with self.scope():
            svc = FileToolService()
            out = self.run_tool(svc, "search_files", query="", types=["json"])
            self.assertEqual([f["name"] for f in out["files"]], ["a.json"])

    def test_empty_result_is_not_error(self) -> None:
        self.write("a.txt", "hello")
        with self.scope():
            svc = FileToolService()
            out = self.run_tool(svc, "search_files", query="不存在的东西")
            self.assertEqual(out["count"], 0)
            self.assertNotIn("error", out)

    def test_content_search(self) -> None:
        self.write("a.txt", "这里提到了预算")
        self.write("b.txt", "无关内容")
        with self.scope():
            svc = FileToolService()
            out = self.run_tool(svc, "search_files", query="预算", in_content=True)
            names = {f["name"] for f in out["files"]}
            self.assertEqual(names, {"a.txt"})


class TestRead(FileToolServiceTestBase):
    def test_reads_lines(self) -> None:
        p = self.write("a.txt", "第一行\n第二行\n第三行\n")
        with self.scope():
            svc = FileToolService()
            out = self.run_tool(svc, "read_file", path=p, start_line=2, max_lines=1)
            self.assertEqual(out["content"], "第二行")
            self.assertEqual(out["start_line"], 2)
            self.assertEqual(out["total_lines"], 3)

    def test_rejects_outside_roots(self) -> None:
        p = self.write("secret.txt", "x", root=self.outside)
        with self.scope():
            svc = FileToolService()
            out = self.run_tool(svc, "read_file", path=p)
            self.assertIn("不在用户授权", out["error"])

    def test_rejects_unsupported_type(self) -> None:
        p = os.path.join(self.root, "a.pdf")
        with open(p, "wb") as f:
            f.write(b"%PDF")
        with self.scope():
            svc = FileToolService()
            out = self.run_tool(svc, "read_file", path=p)
            self.assertIn("不支持的文件类型", out["error"])

    def test_redundant_read_is_refused(self) -> None:
        p = self.write("a.txt", "x\n")
        with self.scope():
            svc = FileToolService()
            self.run_tool(svc, "read_file", path=p)
            again = self.run_tool(svc, "read_file", path=p)
            self.assertIn("已经读取过", again["error"])

    def test_file_limit_per_turn(self) -> None:
        paths = [self.write(f"f{i}.txt", "x\n") for i in range(MAX_FILES_PER_TURN + 1)]
        with self.scope():
            svc = FileToolService()
            for p in paths[:MAX_FILES_PER_TURN]:
                self.assertNotIn("error", self.run_tool(svc, "read_file", path=p))
            out = self.run_tool(svc, "read_file", path=paths[-1])
            self.assertIn("达到上限", out["error"])

    def test_begin_turn_resets_budget(self) -> None:
        p = self.write("a.txt", "x\n")
        with self.scope():
            svc = FileToolService()
            self.run_tool(svc, "read_file", path=p)
            svc.begin_turn()
            out = self.run_tool(svc, "read_file", path=p)
            self.assertNotIn("error", out)

    def test_audit_records_reads(self) -> None:
        p = self.write("a.txt", "x\n")
        with self.scope():
            svc = FileToolService()
            self.run_tool(svc, "read_file", path=p)
            audit = svc.audit()
            self.assertEqual(audit[-1]["tool"], "read_file")
            self.assertTrue(audit[-1]["path"].endswith("a.txt"))


class TestErrors(FileToolServiceTestBase):
    def test_bad_json_arguments(self) -> None:
        with self.scope():
            svc = FileToolService()
            out = json.loads(svc.execute("read_file", "{not json"))
            self.assertIn("不是合法的 JSON", out["error"])

    def test_unknown_tool(self) -> None:
        with self.scope():
            svc = FileToolService()
            out = json.loads(svc.execute("delete_file", "{}"))
            self.assertIn("未知工具", out["error"])

    def test_non_object_arguments(self) -> None:
        with self.scope():
            svc = FileToolService()
            out = json.loads(svc.execute("search_files", "[1,2]"))
            self.assertIn("JSON 对象", out["error"])

    def test_missing_path(self) -> None:
        with self.scope():
            svc = FileToolService()
            out = json.loads(svc.execute("read_file", "{}"))
            self.assertIn("缺少 path", out["error"])


class TestStopSignal(FileToolServiceTestBase):
    def test_stop_aborts_scan_and_skips_cache(self) -> None:
        self.write("a.txt", "x")
        with self.scope():
            svc = FileToolService()
            svc.execute("search_files", json.dumps({"query": "x"}),
                        should_stop=lambda: True)
            self.assertTrue(svc._stats.aborted, "扫描应被标记为中止")
            self.assertEqual(svc._indexed_at, 0.0, "中止的结果不得缓存")

    def test_normal_scan_caches(self) -> None:
        self.write("a.txt", "x")
        with self.scope():
            svc = FileToolService()
            svc.execute("search_files", json.dumps({"query": "x"}),
                        should_stop=lambda: False)
            self.assertFalse(svc._stats.aborted)
            self.assertGreater(svc._indexed_at, 0.0)


class TestIndexCache(FileToolServiceTestBase):
    def test_index_reused_within_ttl(self) -> None:
        self.write("a.txt", "x")
        now = [1000.0]
        with self.scope():
            svc = FileToolService(clock=lambda: now[0])
            svc._ensure_index()
            first = svc._stats.files
            self.write("b.txt", "y")
            svc._ensure_index()
            self.assertEqual(svc._stats.files, first, "TTL 内不应重扫")

            now[0] += 61
            svc._ensure_index()
            self.assertEqual(svc._stats.files, first + 1, "超过 TTL 应重扫")


if __name__ == "__main__":
    unittest.main()
