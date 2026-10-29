# Layer: tests
# File: tests/test_deepseek_search.py
# Responsibility: 验证 DeepSeek Search / Fetch 适配器——请求体、结果映射、
#                 编码错位修复、错误码与重试策略。用 httpx.MockTransport，不触网。

from __future__ import annotations

import json
import unittest

import httpx

from app.adapters.search_adapters.deepseek_search import (
    DeepSeekFetchAdapter,
    DeepSeekSearchAdapter,
    _DeepSeekWebApi,
)

BASE = "https://api.deepseek.com"


def make_api(handler, retry_delays=(), api_key="test-key") -> _DeepSeekWebApi:
    # 显式注入 key / base：config 的静态项不参与 ConfigScope 隔离（它们是模块全局，
    # 属性查找压根不走 __getattr__），而直接改全局又会影响别的用例。
    return _DeepSeekWebApi(
        transport=httpx.MockTransport(handler),
        retry_delays=retry_delays,
        api_key=api_key,
        base_url=BASE,
    )


class Recorder:
    """记录请求，按顺序回放预置响应。"""

    def __init__(self, responses) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = list(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            return httpx.Response(200, json={"results": []})
        return self._responses.pop(0)

    @property
    def bodies(self) -> list[dict]:
        return [json.loads(r.content.decode("utf-8")) for r in self.requests]


def ok_body(*results) -> dict:
    return {"results": list(results)}


ITEM = {
    "title": "DeepSeek V4 发布",
    "url": "https://example.com/a",
    "snippet": "短摘要",
    "summary": "长摘要" * 10,
    "content": "",
    "site_name": "示例站点",
    "date": "2026-06-01T00:00:00+00:00",
    "rerank_score": 2.4,
    "authority": 3,
    "site_icon": None,
}


class TestSearchPayload(unittest.IsolatedAsyncioTestCase):
    async def test_posts_to_search_endpoint(self) -> None:
        rec = Recorder([httpx.Response(200, json=ok_body(ITEM))])
        adapter = DeepSeekSearchAdapter(make_api(rec))
        await adapter.search("DeepSeek V4", max_results=3)

        self.assertEqual(len(rec.requests), 1)
        request = rec.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), BASE + "/v1/tools/search")
        self.assertEqual(request.headers["Authorization"], "Bearer test-key")
        self.assertEqual(request.headers["Content-Type"], "application/json")

        body = rec.bodies[0]
        self.assertEqual(body["query"], "DeepSeek V4")
        self.assertEqual(body["count"], 3)
        self.assertFalse(body["need_content"], "默认不请求正文，正文由 fetch_url 单独抓")
        self.assertEqual(body["ori_question"], "DeepSeek V4")

    async def test_count_clamped_to_api_range(self) -> None:
        rec = Recorder([httpx.Response(200, json=ok_body())])
        adapter = DeepSeekSearchAdapter(make_api(rec))
        await adapter.search("q", max_results=999)
        await adapter.search("q", max_results=0)
        await adapter.search("q", max_results="坏值")

        self.assertEqual([b["count"] for b in rec.bodies], [50, 1, 5])

    async def test_query_collapsed_and_truncated(self) -> None:
        rec = Recorder([httpx.Response(200, json=ok_body())])
        adapter = DeepSeekSearchAdapter(make_api(rec))
        await adapter.search("第一行\n\n第二行 " + "长" * 300)

        query = rec.bodies[0]["query"]
        self.assertNotIn("\n", query)
        self.assertLessEqual(len(query), 128)

    async def test_blank_query_makes_no_request(self) -> None:
        rec = Recorder([])
        adapter = DeepSeekSearchAdapter(make_api(rec))
        result = await adapter.search("   \n  ")

        self.assertEqual(rec.requests, [])
        self.assertEqual(result.results, [])
        self.assertEqual(result.error, "")


class TestSearchMapping(unittest.IsolatedAsyncioTestCase):
    async def test_maps_rich_fields(self) -> None:
        rec = Recorder([httpx.Response(200, json=ok_body(ITEM))])
        adapter = DeepSeekSearchAdapter(make_api(rec))
        result = await adapter.search("q")

        self.assertEqual(len(result.results), 1)
        hit = result.results[0]
        self.assertEqual(hit.title, "DeepSeek V4 发布")
        self.assertEqual(hit.url, "https://example.com/a")
        self.assertEqual(hit.site, "示例站点")
        self.assertEqual(hit.published, "2026-06-01T00:00:00+00:00")
        self.assertEqual(hit.source, "deepseek")
        self.assertTrue(hit.summary.startswith("长摘要"))
        self.assertEqual(hit.content, "", "need_content=False 时不带正文")

    async def test_repairs_hex_encoded_text(self) -> None:
        # 实测返回：UTF-8 字节被当十六进制串返回（"央广网" / "Xinhua"）
        rec = Recorder([httpx.Response(200, json=ok_body(
            dict(ITEM, site_name="e5a4aee5b9bfe7bd91"),
            dict(ITEM, url="https://example.com/b", site_name="58696e687561"),
        ))])
        adapter = DeepSeekSearchAdapter(make_api(rec))
        result = await adapter.search("q")

        self.assertEqual(result.results[0].site, "央广网")
        self.assertEqual(result.results[1].site, "Xinhua")

    async def test_repairs_latin1_mojibake(self) -> None:
        rec = Recorder([httpx.Response(200, json=ok_body(
            dict(ITEM, title="DeepSeek V4已上线"),
        ))])
        adapter = DeepSeekSearchAdapter(make_api(rec))
        result = await adapter.search("q")

        self.assertEqual(result.results[0].title, "DeepSeek V4已上线")

    async def test_plain_text_is_left_alone(self) -> None:
        rec = Recorder([httpx.Response(200, json=ok_body(
            dict(ITEM, title="café", site_name="deadbeef"),
        ))])
        adapter = DeepSeekSearchAdapter(make_api(rec))
        result = await adapter.search("q")

        self.assertEqual(result.results[0].title, "café")
        self.assertEqual(result.results[0].site, "deadbeef", "不像十六进制文本就别动")

    async def test_skips_results_without_url(self) -> None:
        rec = Recorder([httpx.Response(200, json=ok_body(
            {"title": "没有链接"}, "不是字典", dict(ITEM),
        ))])
        adapter = DeepSeekSearchAdapter(make_api(rec))
        result = await adapter.search("q")

        self.assertEqual(len(result.results), 1)
        self.assertEqual(result.results[0].url, "https://example.com/a")

    async def test_title_falls_back_to_url(self) -> None:
        rec = Recorder([httpx.Response(200, json=ok_body(
            {"url": "https://example.com/x"},
        ))])
        adapter = DeepSeekSearchAdapter(make_api(rec))
        result = await adapter.search("q")

        self.assertEqual(result.results[0].title, "https://example.com/x")

    async def test_empty_results_is_not_an_error(self) -> None:
        rec = Recorder([httpx.Response(200, json={"results": []})])
        adapter = DeepSeekSearchAdapter(make_api(rec))
        result = await adapter.search("q")

        self.assertEqual(result.results, [])
        self.assertEqual(result.error, "")

    async def test_missing_results_key_is_an_error(self) -> None:
        rec = Recorder([httpx.Response(200, json={})])
        adapter = DeepSeekSearchAdapter(make_api(rec))
        result = await adapter.search("q")

        self.assertIn("results", result.error)


class TestSearchErrors(unittest.IsolatedAsyncioTestCase):
    async def test_401_not_retried(self) -> None:
        body = {"error": {"message": "Authentication Fails", "type": "authentication_error"}}
        rec = Recorder([httpx.Response(401, json=body)])
        adapter = DeepSeekSearchAdapter(make_api(rec, retry_delays=(0, 0)))
        result = await adapter.search("q")

        self.assertEqual(len(rec.requests), 1, "4xx 不该重试")
        self.assertIn("401", result.error)
        self.assertIn("API Key", result.error)
        self.assertIn("Authentication Fails", result.error)

    async def test_429_retried_then_succeeds(self) -> None:
        rec = Recorder([
            httpx.Response(429, json={"error": {"message": "rate limited"}}),
            httpx.Response(200, json=ok_body(ITEM)),
        ])
        adapter = DeepSeekSearchAdapter(make_api(rec, retry_delays=(0, 0)))
        result = await adapter.search("q")

        self.assertEqual(len(rec.requests), 2)
        self.assertEqual(len(result.results), 1)
        self.assertEqual(result.error, "")

    async def test_5xx_exhausts_retries(self) -> None:
        rec = Recorder([
            httpx.Response(500, text="boom"),
            httpx.Response(503, text="unavailable"),
            httpx.Response(500, text="boom"),
        ])
        adapter = DeepSeekSearchAdapter(make_api(rec, retry_delays=(0, 0)))
        result = await adapter.search("q")

        self.assertEqual(len(rec.requests), 3)
        self.assertIn("500", result.error)
        self.assertEqual(result.results, [])

    async def test_network_error_is_reported_not_raised(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("连不上", request=request)

        adapter = DeepSeekSearchAdapter(make_api(handler))
        result = await adapter.search("q")

        self.assertEqual(result.results, [])
        self.assertIn("网络请求失败", result.error)

    async def test_missing_api_key_makes_no_request(self) -> None:
        rec = Recorder([])
        adapter = DeepSeekSearchAdapter(make_api(rec, api_key=""))
        result = await adapter.search("q")

        self.assertEqual(rec.requests, [])
        self.assertIn("DEEPSEEK_API_KEY", result.error)


class TestFetch(unittest.IsolatedAsyncioTestCase):
    async def test_returns_title_and_markdown(self) -> None:
        rec = Recorder([httpx.Response(200, json={
            "title": "示例文章", "content": "# 标题\n\n正文第一段。",
        })])
        adapter = DeepSeekFetchAdapter(make_api(rec))
        result = await adapter.fetch("https://example.com/a")

        self.assertEqual(str(rec.requests[0].url), BASE + "/v1/tools/fetch")
        self.assertEqual(rec.bodies[0], {"url": "https://example.com/a"})
        self.assertEqual(result.title, "示例文章")
        self.assertIn("正文第一段", result.content)
        self.assertEqual(result.error, "")

    async def test_rejects_non_http_url(self) -> None:
        rec = Recorder([])
        adapter = DeepSeekFetchAdapter(make_api(rec))
        for bad in ("not-a-url", "file:///C:/x.txt", "ftp://example.com/a",
                    "https://user:pw@example.com/a", "http:///nohost"):
            result = await adapter.fetch(bad)
            self.assertTrue(result.error, bad)

        self.assertEqual(rec.requests, [], "非法地址不该发出去")

    async def test_404_reported(self) -> None:
        rec = Recorder([httpx.Response(
            404, json={"error": {"message": "failed to fetch URL"}},
        )])
        adapter = DeepSeekFetchAdapter(make_api(rec))
        result = await adapter.fetch("https://example.invalid/x")

        self.assertIn("404", result.error)
        self.assertEqual(result.content, "")

    async def test_empty_content_is_an_error(self) -> None:
        rec = Recorder([httpx.Response(200, json={"title": "空", "content": "   "})])
        adapter = DeepSeekFetchAdapter(make_api(rec))
        result = await adapter.fetch("https://example.com/a")

        self.assertIn("没有可提取的正文", result.error)


if __name__ == "__main__":
    unittest.main()
