# Layer: tests
# File: tests/test_search_service.py
# Responsibility: 验证搜索编排——开关、多适配器补齐与提前停止、注入文本格式、
#                 失败时告诉模型"没拿到资料"而不是静默返回空串。

from __future__ import annotations

import unittest

from config import ConfigScope
from app.core.search_service import SearchService
from app.storage.models import SearchResult, SearchResults


def hit(title: str, url: str = "https://example.com/a", **kwargs) -> SearchResult:
    kwargs.setdefault("snippet", "")
    return SearchResult(title=title, url=url, **kwargs)


class FakeAdapter:
    """按预置结果回放的搜索后端，记录每次调用。"""

    def __init__(self, results=None, error: str = "", name: str = "fake") -> None:
        self._results = list(results or [])
        self._error = error
        self.name = name
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, max_results: int = 5) -> SearchResults:
        self.calls.append((query, max_results))
        return SearchResults(
            query=query,
            results=list(self._results),
            total=len(self._results),
            error=self._error,
        )


class TestGate(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_returns_empty_and_skips_adapters(self) -> None:
        adapter = FakeAdapter([hit("A")])
        service = SearchService([adapter])
        with ConfigScope(search_enabled=False):
            self.assertEqual(await service.search_and_format("问题"), "")
        self.assertEqual(adapter.calls, [])

    async def test_blank_query_returns_empty(self) -> None:
        adapter = FakeAdapter([hit("A")])
        service = SearchService([adapter])
        with ConfigScope(search_enabled=True):
            self.assertEqual(await service.search_and_format("   "), "")
        self.assertEqual(adapter.calls, [])


class TestFormatting(unittest.IsolatedAsyncioTestCase):
    async def test_uses_summary_date_and_site(self) -> None:
        adapter = FakeAdapter([SearchResult(
            title="DeepSeek V4 发布",
            url="https://example.com/a",
            snippet="短摘要不用",
            summary="这是给模型读的长摘要",
            site="央广网",
            published="2026-04-26T16:00:00+00:00",
        )])
        service = SearchService([adapter])
        with ConfigScope(search_enabled=True, search_max_results=5):
            text = await service.search_and_format("DeepSeek V4")

        self.assertIn("1. DeepSeek V4 发布", text)
        self.assertIn("来源: 央广网 https://example.com/a", text)
        self.assertIn("摘要: 这是给模型读的长摘要", text)
        self.assertNotIn("短摘要不用", text, "有 summary 就不该退回 snippet")
        # 时区不同可能跨一天，只校验格式与字段存在
        self.assertRegex(text, r"发布时间: \d{4}-\d{2}-\d{2}")

    async def test_falls_back_to_snippet(self) -> None:
        adapter = FakeAdapter([hit("A", snippet="只有短摘要")])
        service = SearchService([adapter])
        with ConfigScope(search_enabled=True, search_max_results=5):
            text = await service.search_and_format("q")
        self.assertIn("摘要: 只有短摘要", text)

    async def test_summary_is_clipped(self) -> None:
        adapter = FakeAdapter([hit("A", summary="长" * 5000)])
        service = SearchService([adapter])
        with ConfigScope(search_enabled=True, search_max_results=5):
            text = await service.search_and_format("q")
        self.assertLess(len(text), 1500)
        self.assertIn("…", text)

    async def test_total_budget_truncates_tail(self) -> None:
        # 每条摘要都会被裁到 900 字，条数要够多才会撞上总预算（14k）
        results = [hit(f"结果{i}", url=f"https://e.com/{i}", summary="摘要" * 1000)
                   for i in range(20)]
        adapter = FakeAdapter(results)
        service = SearchService([adapter])
        with ConfigScope(search_enabled=True, search_max_results=20):
            text = await service.search_and_format("q")
        self.assertIn("其余", text)
        self.assertLessEqual(len(text), 14_500, "注入文本必须有总预算")


class TestAdapterCoordination(unittest.IsolatedAsyncioTestCase):
    async def test_stops_when_enough_results(self) -> None:
        first = FakeAdapter([hit(f"A{i}", url=f"https://a.com/{i}") for i in range(5)], name="first")
        second = FakeAdapter([hit("B")], name="second")
        service = SearchService([first, second])
        with ConfigScope(search_enabled=True, search_max_results=5):
            text = await service.search_and_format("q")

        self.assertEqual(len(first.calls), 1)
        self.assertEqual(second.calls, [], "凑够条数就不该再打第二个后端")
        self.assertIn("5. A4", text)

    async def test_supplements_from_second_adapter(self) -> None:
        first = FakeAdapter([hit("A")], name="first")
        second = FakeAdapter([hit(f"B{i}", url=f"https://b.com/{i}") for i in range(5)],
                             name="second")
        service = SearchService([first, second])
        with ConfigScope(search_enabled=True, search_max_results=3):
            text = await service.search_and_format("q")

        self.assertEqual(len(second.calls), 1)
        self.assertIn("3.", text)
        self.assertNotIn("4.", text, "总量不得超过 search_max_results")

    async def test_adapter_exception_does_not_break_turn(self) -> None:
        class Boom:
            name = "boom"

            async def search(self, query, max_results=5):
                raise RuntimeError("炸了")

        good = FakeAdapter([hit("A")])
        service = SearchService([Boom(), good])
        with ConfigScope(search_enabled=True, search_max_results=5):
            text = await service.search_and_format("q")
        self.assertIn("1. A", text)


class TestFailureIsVisible(unittest.IsolatedAsyncioTestCase):
    async def test_error_tells_model_to_avoid_fake_citations(self) -> None:
        adapter = FakeAdapter(error="HTTP 401 API Key 无效")
        service = SearchService([adapter])
        with ConfigScope(search_enabled=True):
            text = await service.search_and_format("q")

        self.assertIn("联网搜索未成功", text)
        self.assertIn("401", text)
        self.assertIn("不要编造引用", text)

    async def test_partial_failure_is_flagged(self) -> None:
        broken = FakeAdapter(error="HTTP 401 API Key 无效", name="broken")
        good = FakeAdapter([hit("A")], name="good")
        service = SearchService([broken, good])
        with ConfigScope(search_enabled=True, search_max_results=5):
            text = await service.search_and_format("q")

        self.assertIn("1. A", text, "有结果就照用")
        self.assertIn("部分搜索后端不可用", text)
        self.assertIn("401", text)

    async def test_empty_without_error_injects_nothing(self) -> None:
        service = SearchService([FakeAdapter([])])
        with ConfigScope(search_enabled=True):
            self.assertEqual(await service.search_and_format("q"), "")

    async def test_no_adapters_injects_nothing(self) -> None:
        service = SearchService([])
        with ConfigScope(search_enabled=True):
            self.assertEqual(await service.search_and_format("q"), "")


if __name__ == "__main__":
    unittest.main()
