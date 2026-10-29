# Layer: Core
# File: app/core/web_tool_service.py
# Responsibility: 模型可调用的联网工具（目前只有 fetch_url：打开一个网页读正文）。
#                 校验参数与预算、把结果格式化成可直接回传的 JSON。
#                 真正的 HTTP 请求在 Adapter（DeepSeekFetchAdapter）。
# Input:  工具名 + 参数 JSON 串（来自 LLM 的 tool_calls）
# Output: JSON 串（作为 role="tool" 的 content 回传给模型）
# 禁止: UI 导入、直接发起 HTTP 请求、实现网页解析

from __future__ import annotations

import json
from urllib.parse import urlsplit

import config as app_config
from app.core.protocols import WebFetchProtocol

# 预算：同 FileToolService 的理由——工具结果是纯输入 token，
# 一次不限量的抓取就能把上下文撑爆（接口本身不截断，一页可以几万字）。
MAX_FETCH_CHARS = 12_000         # 单页上限
MAX_TURN_CHARS = 36_000          # 单轮累计
MAX_FETCHES_PER_TURN = 4         # 单轮不同网址数

TOOL_NAMES: tuple[str, ...] = ("fetch_url",)

FETCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "fetch_url",
        "description": (
            "打开一个网页并返回它的标题与 Markdown 正文。"
            "适用于：用户给出链接、或需要阅读某个搜索结果页的完整内容。"
            "只支持 http/https 地址；同一轮里同一个网址只会抓一次。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "完整网址，例如 https://example.com/article",
                },
            },
            "required": ["url"],
        },
    },
}


class WebToolService:
    """
    联网工具的执行与策略层。

    与 FileToolService 一样：任何失败都返回 {"error": "..."}，永不抛异常——
    一次抓取失败不该终结整轮对话，模型看到原因可以自己换条路。
    """

    def __init__(self, fetcher: WebFetchProtocol | None = None) -> None:
        self._fetcher = fetcher
        self._fetched: list[str] = []
        self._chars = 0

    # ── 可用性 ───────────────────────────────

    def enabled(self) -> bool:
        """关掉开关或没装配抓取器时，连工具定义都不下发。"""
        return bool(getattr(app_config, "web_fetch_enabled", False)) and self._fetcher is not None

    def schemas(self) -> list[dict]:
        return [FETCH_SCHEMA] if self.enabled() else []

    def supports(self, name: str) -> bool:
        return name in TOOL_NAMES and self.enabled()

    # ── 单轮生命周期 ─────────────────────────

    def begin_turn(self) -> None:
        self._fetched = []
        self._chars = 0

    # ── 执行入口 ─────────────────────────────

    async def execute(self, name: str, arguments: str, should_stop=None) -> str:
        try:
            args = json.loads(arguments) if arguments else {}
            if not isinstance(args, dict):
                raise ValueError("参数必须是 JSON 对象")
        except (json.JSONDecodeError, ValueError) as exc:
            return _err(f"参数不是合法的 JSON 对象：{exc}")

        try:
            if name != "fetch_url":
                return _err(f"未知工具：{name}")
            return await self._fetch(args, should_stop)
        except Exception as exc:      # 兜底：任何异常都不能打断对话
            return _err(f"工具执行失败：{type(exc).__name__}: {exc}")

    # ── fetch_url ────────────────────────────

    async def _fetch(self, args: dict, should_stop=None) -> str:
        if self._fetcher is None:
            return _err("联网抓取未装配")
        if not self.enabled():
            return _err("网页抓取已在设置里关闭")

        url = str(args.get("url") or "").strip()
        if not url:
            return _err("缺少 url 参数")
        if not _is_web_url(url):
            return _err("url 必须是带 host 的 http/https 地址")

        if url in self._fetched:
            return _err("本轮已经抓取过这个网址，请直接使用已有内容")
        if len(self._fetched) >= MAX_FETCHES_PER_TURN:
            return _err(f"本轮已抓取 {MAX_FETCHES_PER_TURN} 个网页，达到上限")
        if self._chars >= MAX_TURN_CHARS:
            return _err("本轮网页内容已达上限，请基于已有信息作答")
        if should_stop is not None and should_stop():
            return _err("已中止")

        result = await self._fetcher.fetch(url)
        if result.error:
            return _err(result.error)

        text = str(result.content or "")
        truncated = len(text) > MAX_FETCH_CHARS
        if truncated:
            text = text[:MAX_FETCH_CHARS].rstrip() + "\n\n[正文过长，已截断]"

        self._fetched.append(url)
        self._chars += len(text)

        payload = {
            "url": url,
            "title": result.title or "",
            "truncated": truncated,
            "content": text,
        }
        return json.dumps(payload, ensure_ascii=False)


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

def _err(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _is_web_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    return not (parts.username or parts.password)
