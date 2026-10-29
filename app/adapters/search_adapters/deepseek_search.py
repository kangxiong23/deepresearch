# Layer: Adapter
# File: app/adapters/search_adapters/deepseek_search.py
# Responsibility: 接 DeepSeek 官方的两个联网接口——
#                 POST {base}/v1/tools/search：按 query 返回结构化网页结果
#                 POST {base}/v1/tools/fetch ：按 URL 返回 Markdown 正文
# Input:  query: str / url: str
# Output: SearchResults / FetchResult
# 禁止: 业务逻辑、编排、导入 UI 库

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import httpx

import config as app_config
from app.adapters.search_adapters.base import BaseSearchAdapter
from app.storage.models import FetchResult, SearchResult, SearchResults
from app.utils.async_utils import get_logger

logger = get_logger("ADPTR", "deepseek_web")

_SEARCH_PATH = "/v1/tools/search"
_FETCH_PATH = "/v1/tools/fetch"

# 接口约束（官方 Search / Fetch 文档）
_MAX_QUERY_CHARS = 128
_COUNT_MIN, _COUNT_MAX = 1, 50

# 文档写明可重试的只有 408（读超时）、429（QPS）和 5xx。
# 其它 4xx 重发只会再失败一次，还可能多计一次费用——所以直接返回原因。
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
_RETRY_DELAYS: tuple[float, ...] = (0.6, 1.8)

_STATUS_HINTS = {
    400: "请求不合法",
    401: "API Key 无效或缺失",
    402: "账号余额不足",
    403: "被安全策略拒绝",
    404: "未能取到可用的网页内容",
    408: "读取请求超时",
    413: "请求体超过大小限制",
    429: "触发限速",
}


# ──────────────────────────────────────────────
# HTTP 细节（Search / Fetch 共用：同一个 base、同一套鉴权与错误语义）
# ──────────────────────────────────────────────

class _DeepSeekWebApi:
    """
    两个接口共用的 JSON POST 客户端。

    刻意不在请求失败时抛异常：搜索是"锦上添花"的一步，它失败不该整轮对话失败；
    但也不能静默返回空列表——那会让用户以为"模型懒得搜"，所以失败原因一路带回
    SearchResults.error / FetchResult.error，由 Core 决定怎么呈现给模型和用户。
    """

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_delays: tuple[float, ...] = _RETRY_DELAYS,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        # api_key / base_url 为空时按调用时刻读 config（与 DeepSeekClient 一致，
        # 用户改了 .env 不用重建对象）；显式传入只用于测试注入。
        self._transport = transport
        self._retry_delays = tuple(retry_delays)
        self._api_key = api_key
        self._base_url = base_url

    async def post(self, path: str, payload: dict) -> tuple[dict | None, str]:
        """
        发一次 JSON POST。

        Returns:
            (响应 JSON, "")  成功
            (None, 原因)     失败（已按需重试过）
        """
        api_key = (
            self._api_key if self._api_key is not None
            else str(getattr(app_config, "DEEPSEEK_API_KEY", "") or "")
        ).strip()
        if not api_key:
            return None, "未配置 DEEPSEEK_API_KEY"

        base = (
            self._base_url if self._base_url is not None
            else str(app_config.DEEPSEEK_BASE_URL)
        )
        url = base.rstrip("/") + path
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # ⚠️ 每次调用新建 AsyncClient，用完即关，**不能**跨调用缓存。
        # 应用里每条消息都在新线程 + 新事件循环里跑（AsyncStreamWorker），
        # 连接池里的 keep-alive 连接绑定在已经关闭的循环上：下一次复用会抛
        # "RuntimeError: Event loop is closed"（实测复现）。
        # 流式聊天没这个问题（SSE 结束时服务端断开连接，连接不会留在池里），
        # 而搜索/抓取都是普通请求，连接一定会被复用。
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=8.0, read=30.0, write=10.0, pool=5.0),
            transport=self._transport,
        ) as client:
            last_error = ""
            for attempt in range(len(self._retry_delays) + 1):
                try:
                    resp = await client.post(url, headers=headers, json=payload)
                except httpx.RequestError as exc:
                    last_error = f"网络请求失败：{type(exc).__name__}: {exc}"
                else:
                    if resp.status_code == 200:
                        try:
                            data = resp.json()
                        except ValueError:
                            return None, "响应不是合法 JSON"
                        if not isinstance(data, dict):
                            return None, "响应格式异常（不是 JSON 对象）"
                        return data, ""
                    last_error = _describe_error(resp)
                    if resp.status_code not in _RETRYABLE_STATUS:
                        return None, last_error

                if attempt < len(self._retry_delays):
                    await asyncio.sleep(self._retry_delays[attempt])

        return None, last_error


# ──────────────────────────────────────────────
# Search：联网检索
# ──────────────────────────────────────────────

class DeepSeekSearchAdapter(BaseSearchAdapter):
    """
    DeepSeek 官方搜索接口。

    相比抓 Bing / DuckDuckGo 的页面，这里拿到的是结构化字段，其中 summary 是
    专门"给模型读"的长摘要（600~1800 字），比 100~200 字的 snippet 信息量大得多，
    直接决定模型答得实不实。
    """

    def __init__(self, api: _DeepSeekWebApi | None = None) -> None:
        self._api = api or _DeepSeekWebApi()

    @property
    def name(self) -> str:
        return "deepseek"

    async def search(self, query: str, max_results: int = 5) -> SearchResults:
        text = _normalize_query(query)
        if not text:
            return SearchResults(query=query)

        count = _clamp_count(max_results)
        payload = {
            "query": text,
            "count": count,
            # 刻意不要正文：单条正文一万字上下，5 条就能把上下文撑到三万 token。
            # 需要全文时让模型调 fetch_url 抓那一页（见 WebToolService）。
            "need_content": False,
            # 原始问题有助于理解搜索意图；这里与 query 同源（Core 只传一个串）。
            "ori_question": text,
        }

        data, error = await self._api.post(_SEARCH_PATH, payload)
        if data is None:
            logger.warning("deepseek search failed: %s", error)
            return SearchResults(query=query, error=error)

        items = data.get("results")
        if not isinstance(items, list):
            logger.warning("deepseek search: 响应缺少 results 字段")
            return SearchResults(query=query, error="响应缺少 results 字段")

        results: list[SearchResult] = []
        for item in items[:count]:
            result = _to_result(item)
            if result is not None:
                results.append(result)

        logger.debug("deepseek search query=%r returned %d", text, len(results))
        return SearchResults(query=query, results=results, total=len(results))


# ──────────────────────────────────────────────
# Fetch：打开单个网页
# ──────────────────────────────────────────────

class DeepSeekFetchAdapter:
    """
    DeepSeek 官方抓取接口（WebFetchProtocol 实现）。

    模型工具 fetch_url 的落地实现：用户贴了个链接、或模型想读某个结果页的全文时调用。
    """

    def __init__(self, api: _DeepSeekWebApi | None = None) -> None:
        self._api = api or _DeepSeekWebApi()

    async def fetch(self, url: str) -> FetchResult:
        target = str(url or "").strip()
        if not target:
            return FetchResult(url=target, error="缺少 url")
        if not _is_web_url(target):
            return FetchResult(url=target, error="url 必须是 http/https 地址")

        data, error = await self._api.post(_FETCH_PATH, {"url": target})
        if data is None:
            logger.warning("deepseek fetch failed: %s", error)
            return FetchResult(url=target, error=error)

        content = _normalize_markdown(data.get("content"))
        if not content:
            return FetchResult(url=target, error="页面没有可提取的正文")

        return FetchResult(
            url=target,
            title=_repair(_collapse(data.get("title"))),
            content=content,
        )


# ──────────────────────────────────────────────
# 解析与清洗（模块私有）
# ──────────────────────────────────────────────

def _to_result(item) -> SearchResult | None:
    """把一条接口结果转成领域模型；没有链接的条目直接丢（引用不了）。"""
    if not isinstance(item, dict):
        return None

    url = str(item.get("url") or "").strip()
    if not url:
        return None

    return SearchResult(
        title=_repair(_collapse(item.get("title"))) or url,
        url=url,
        snippet=_repair(_collapse(item.get("snippet"))),
        summary=_repair(_collapse(item.get("summary"))),
        content="",                      # need_content=False 时接口恒为空串
        published=str(item.get("date") or "").strip(),
        site=_repair(_collapse(item.get("site_name"))),
        source="deepseek",
    )


def _normalize_query(query: str) -> str:
    """接口要求 1~128 个 Unicode 字符且不能全为空白。"""
    return _collapse(query)[:_MAX_QUERY_CHARS]


def _clamp_count(max_results) -> int:
    try:
        count = int(max_results)
    except (TypeError, ValueError):
        count = 5
    return max(_COUNT_MIN, min(count, _COUNT_MAX))


def _collapse(text) -> str:
    """合并空白：正文外的字段（标题、摘要）不需要保留换行。"""
    return " ".join(str(text or "").split())


def _repair(text: str) -> str:
    """
    修服务端偶发的编码错位。

    实测 site_name 会返回 "e5a4aee5b9bfe7bd91"、"58696e687561" 这种——
    UTF-8 字节被当十六进制串返回了（真实站点是"央广网"、"Xinhua"）。另一种
    形态是同样字节被按 latin-1 解成乱码字符。两种都只"能完整还原"时才改写，
    正常中文与普通英文一律原样返回。
    """
    if not text:
        return text

    from_hex = _decode_hex_utf8(text)
    if from_hex is not None:
        return from_hex

    try:
        raw = text.encode("latin-1")
    except UnicodeEncodeError:
        return text
    try:
        fixed = raw.decode("utf-8")
    except UnicodeDecodeError:
        return text
    return fixed or text


def _decode_hex_utf8(text: str) -> str | None:
    """
    把"十六进制字符串形式的 UTF-8"还原成文本；不像就别动。

    要求：偶数长度、全十六进制字符、能解成合法 UTF-8、且没有控制字符。
    "deadbeef" 这类会停在 UTF-8 解码失败，"9812" 会解出控制字符，都能排掉。
    """
    if len(text) < 4 or len(text) % 2:
        return None
    if not all(ch in "0123456789abcdefABCDEF" for ch in text):
        return None
    try:
        decoded = bytes.fromhex(text).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if not decoded or any(ord(ch) < 32 for ch in decoded):
        return None
    return decoded


def _normalize_markdown(text) -> str:
    """统一换行、去掉行尾空白、连续空行压成一个。"""
    raw = str(text or "")
    if not raw.strip():
        return ""
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    blank = 0
    for line in raw.split("\n"):
        line = line.rstrip()
        if line:
            blank = 0
            out.append(line)
        else:
            blank += 1
            if blank == 1:
                out.append("")
    return "\n".join(out).strip()


def _is_web_url(url: str) -> bool:
    """只放行 http/https 且带 host、不带用户名密码的地址（接口的硬性要求）。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    return not (parts.username or parts.password)


def _describe_error(resp: httpx.Response) -> str:
    """把错误响应压成一行给人和模型看的中文原因。"""
    message = ""
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            message = str(err.get("message") or "").strip()
        elif isinstance(err, str):
            message = err.strip()
    if not message:
        message = (resp.text or "").strip()[:200]
    message = message[:200]

    head = f"HTTP {resp.status_code}"
    hint = _STATUS_HINTS.get(resp.status_code, "")
    if hint:
        head += f" {hint}"
    return f"{head}：{message}" if message else head
