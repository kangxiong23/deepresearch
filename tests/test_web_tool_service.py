# Layer: tests
# File: tests/test_web_tool_service.py
# Responsibility: 验证 fetch_url 工具——开关、参数校验、单轮预算与去重、
#                 失败一律返回 {"error": ...} 而不抛异常。

from __future__ import annotations

import json
import unittest

from config import ConfigScope
from app.core.web_tool_service import (
    MAX_FETCH_CHARS,
    MAX_FETCHES_PER_TURN,
    WebToolService,
)
from app.storage.models import FetchResult


class FakeFetcher:
    def __init__(self, result: FetchResult | None = None) -> None:
        self.calls: list[str] = []
        self._result = result or FetchResult(
            url="", title="示例", content="# 标题\n\n正文",
        )

    async def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        return self._result


class TestAvailability(unittest.TestCase):
    def test_no_schema_when_switch_off(self) -> None:
        service = WebToolService(FakeFetcher())
        with ConfigScope(web_fetch_enabled=False):
            self.assertEqual(service.schemas(), [])
            self.assertFalse(service.supports("fetch_url"))

    def test_schema_when_enabled(self) -> None:
        service = WebToolService(FakeFetcher())
        with ConfigScope(web_fetch_enabled=True):
            schemas = service.schemas()
            self.assertTrue(service.supports("fetch_url"))
            self.assertFalse(service.supports("read_file"))

        self.assertEqual(len(schemas), 1)
        function = schemas[0]["function"]
        self.assertEqual(function["name"], "fetch_url")
        self.assertEqual(function["parameters"]["required"], ["url"])

    def test_no_schema_without_fetcher(self) -> None:
        service = WebToolService(None)
        with ConfigScope(web_fetch_enabled=True):
            self.assertEqual(service.schemas(), [])


class TestExecute(unittest.IsolatedAsyncioTestCase):
    async def test_returns_content_json(self) -> None:
        fetcher = FakeFetcher(FetchResult(url="", title="示例文章", content="正文第一段"))
        service = WebToolService(fetcher)
        with ConfigScope(web_fetch_enabled=True):
            payload = json.loads(await service.execute(
                "fetch_url", json.dumps({"url": "https://example.com/a"}),
            ))

        self.assertEqual(fetcher.calls, ["https://example.com/a"])
        self.assertEqual(payload["title"], "示例文章")
        self.assertEqual(payload["content"], "正文第一段")
        self.assertFalse(payload["truncated"])

    async def test_rejects_non_http_url(self) -> None:
        fetcher = FakeFetcher()
        service = WebToolService(fetcher)
        with ConfigScope(web_fetch_enabled=True):
            for bad in ("", "abc", "file:///C:/x.txt", "ftp://e.com/a",
                        "https://u:p@e.com/a"):
                payload = json.loads(await service.execute(
                    "fetch_url", json.dumps({"url": bad}),
                ))
                self.assertIn("error", payload, bad)

        self.assertEqual(fetcher.calls, [], "非法地址不该真的发出去")

    async def test_same_url_twice_in_turn_is_rejected(self) -> None:
        fetcher = FakeFetcher()
        service = WebToolService(fetcher)
        with ConfigScope(web_fetch_enabled=True):
            await service.execute("fetch_url", json.dumps({"url": "https://e.com/a"}))
            again = json.loads(await service.execute(
                "fetch_url", json.dumps({"url": "https://e.com/a"}),
            ))

        self.assertIn("已经抓取过", again["error"])
        self.assertEqual(len(fetcher.calls), 1)

    async def test_turn_limit(self) -> None:
        fetcher = FakeFetcher()
        service = WebToolService(fetcher)
        with ConfigScope(web_fetch_enabled=True):
            for i in range(MAX_FETCHES_PER_TURN):
                payload = json.loads(await service.execute(
                    "fetch_url", json.dumps({"url": f"https://e.com/{i}"}),
                ))
                self.assertNotIn("error", payload)
            overflow = json.loads(await service.execute(
                "fetch_url", json.dumps({"url": "https://e.com/overflow"}),
            ))

        self.assertIn("上限", overflow["error"])
        self.assertEqual(len(fetcher.calls), MAX_FETCHES_PER_TURN)

    async def test_begin_turn_resets_budget(self) -> None:
        fetcher = FakeFetcher()
        service = WebToolService(fetcher)
        with ConfigScope(web_fetch_enabled=True):
            await service.execute("fetch_url", json.dumps({"url": "https://e.com/a"}))
            service.begin_turn()
            payload = json.loads(await service.execute(
                "fetch_url", json.dumps({"url": "https://e.com/a"}),
            ))

        self.assertNotIn("error", payload)

    async def test_long_content_truncated(self) -> None:
        fetcher = FakeFetcher(FetchResult(url="", title="长", content="字" * 30_000))
        service = WebToolService(fetcher)
        with ConfigScope(web_fetch_enabled=True):
            payload = json.loads(await service.execute(
                "fetch_url", json.dumps({"url": "https://e.com/a"}),
            ))

        self.assertTrue(payload["truncated"])
        self.assertLessEqual(len(payload["content"]), MAX_FETCH_CHARS + 40)

    async def test_fetch_error_passed_through(self) -> None:
        fetcher = FakeFetcher(FetchResult(url="", error="HTTP 404：failed to fetch URL"))
        service = WebToolService(fetcher)
        with ConfigScope(web_fetch_enabled=True):
            payload = json.loads(await service.execute(
                "fetch_url", json.dumps({"url": "https://e.com/a"}),
            ))

        self.assertIn("404", payload["error"])

    async def test_bad_arguments_do_not_raise(self) -> None:
        service = WebToolService(FakeFetcher())
        with ConfigScope(web_fetch_enabled=True):
            self.assertIn("error", json.loads(await service.execute("fetch_url", "{不是 JSON")))
            self.assertIn("error", json.loads(await service.execute("fetch_url", "[1,2]")))
            self.assertIn("未知工具", json.loads(await service.execute("nope", "{}"))["error"])

    async def test_switch_off_blocks_execution(self) -> None:
        fetcher = FakeFetcher()
        service = WebToolService(fetcher)
        with ConfigScope(web_fetch_enabled=False):
            payload = json.loads(await service.execute(
                "fetch_url", json.dumps({"url": "https://e.com/a"}),
            ))
        self.assertIn("关闭", payload["error"])
        self.assertEqual(fetcher.calls, [])


if __name__ == "__main__":
    unittest.main()
