# Layer: Adapter
# File: app/adapters/deepseek_client.py
# Responsibility: 实现 LLMClientProtocol——封装 DeepSeek API 的 HTTP 细节。
#                 从集中配置读取 API Key、Base URL、模型参数，构建 HTTP 请求，
#                 将 SSE 流解析为 MessageChunk 序列。
# Input:  LLMContext（Core 层组装好的上下文）
# Output: AsyncGenerator[MessageChunk]
# 禁止: 业务逻辑、编排、导入 UI 库

from __future__ import annotations

import json
from typing import AsyncGenerator

import httpx

import config as app_config
from app.storage.models import ChunkType, MessageChunk


class DeepSeekClient:
    """
    DeepSeek API 流式客户端。

    实现 LLMClientProtocol（结构性兼容，无需显式继承）。

    特性：
    - 完整 SSE 解析，支持 text / reasoning_content 两路流
    - 通过 httpx.AsyncClient 复用连接
    - abort() 取消当前请求（设置 _abort_flag，下次 chunk 前检测）
    - 所有配置（api_key、model、max_tokens、temperature）在调用时从 config 读取，
      支持用户在对话中途切换模型
    """

    _API_PATH = "/chat/completions"

    def __init__(self) -> None:
        self._abort_flag = False
        # 复用 AsyncClient（连接池）；在应用生命周期内保持打开
        self._client: httpx.AsyncClient | None = None

    # ──────────────────────────────────────────
    # LLMClientProtocol 接口
    # ──────────────────────────────────────────

    async def stream_chat(
        self,
        context,  # LLMContext
    ) -> AsyncGenerator[MessageChunk, None]:
        """
        发起流式对话请求，逐块 yield MessageChunk。

        Args:
            context: LLMContext — Core 层组装的完整请求上下文

        Yields:
            MessageChunk(delta, is_done, chunk_type, message_id)
            最后一个 chunk 的 is_done=True。
        """
        self._abort_flag = False
        url = app_config.DEEPSEEK_BASE_URL.rstrip("/") + self._API_PATH
        headers = {
            "Authorization": f"Bearer {app_config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        payload = self._build_payload(context)

        client = await self._get_client()

        try:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                async for chunk in self._iter_sse(resp):
                    yield chunk

        except httpx.HTTPStatusError as e:
            raise LLMAPIError(
                f"DeepSeek API error {e.response.status_code}: "
                f"{e.response.text[:200]}"
            ) from e
        except httpx.RequestError as e:
            raise LLMAPIError(f"DeepSeek request failed: {e}") from e

    async def stream_prefix_continue(
        self,
        context,
        partial_content: str,
        partial_thinking: str = "",
    ) -> AsyncGenerator[MessageChunk, None]:
        """
        前缀续写（Beta）：从已有的部分 assistant 内容继续补全。

        使用 Beta base_url（https://api.deepseek.com/beta）。
        context.messages 末尾应为 prefix=True 的 assistant 消息
        （由 ContextService.build_continue_context 构建）。

        Args:
            context:          LLMContext（含 system + 历史 + prefix assistant 消息）
            partial_content:  未完成消息已有的部分文本（信息性；已含于 context）
            partial_thinking: 未完成消息已有的部分思考内容（信息性；已含于 context）

        Yields:
            MessageChunk — 与 stream_chat 相同；最后一块 is_done=True
        """
        self._abort_flag = False
        url = app_config.DEEPSEEK_BASE_URL.rstrip("/") + "/beta" + self._API_PATH
        headers = {
            "Authorization": f"Bearer {app_config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        payload = self._build_payload(context)

        client = await self._get_client()

        try:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                async for chunk in self._iter_sse(resp):
                    yield chunk

        except httpx.HTTPStatusError as e:
            raise LLMAPIError(
                f"DeepSeek API error {e.response.status_code}: "
                f"{e.response.text[:200]}"
            ) from e
        except httpx.RequestError as e:
            raise LLMAPIError(f"DeepSeek request failed: {e}") from e

    async def _iter_sse(self, resp) -> AsyncGenerator[MessageChunk, None]:
        """
        把一次 HTTP 响应解析为 MessageChunk 序列。

        有状态（工具调用的 arguments 是跨行分片下发的），故每次请求新建解析器。
        """
        parser = _SSEParser()
        async for line in resp.aiter_lines():
            if self._abort_flag:
                yield MessageChunk(delta="", is_done=True, chunk_type=ChunkType.TEXT)
                return
            for chunk in parser.feed(line):
                yield chunk
                if chunk.is_done:
                    return

    def abort(self) -> None:
        """设置中止标志，stream_chat 在下次读取前检测并退出。"""
        self._abort_flag = True

    # ──────────────────────────────────────────
    # 请求构建（仅技术细节，不含业务逻辑）
    # ──────────────────────────────────────────

    @staticmethod
    def _build_payload(context) -> dict:
        """
        将 LLMContext 转换为 DeepSeek API JSON payload。

        system_prompt 作为首条 system 消息插入（如果非空）。
        思考模式 & 思考强度（flash / pro 平等对待）：
            - config.thinking_enabled 是前后端同步的唯一数据源。
            - thinking 启用时：下发 thinking.type=enabled，并携带
              reasoning_effort（low/high/max，来自 config.reasoning_effort）。
            - thinking 未启用时：下发 thinking.type=disabled，且【不得】传
              reasoning_effort —— API 会因此报错。
            - temperature：思考模式启用时一律不传（API 不支持，flash / pro
              均如此）；关闭时才携带。
        """
        # 从 config 读取当前模型（覆盖 context 中的值，确保用最新选择）
        model = app_config.model_type

        messages: list[dict] = []

        # 注入 system prompt
        if context.system_prompt:
            messages.append({"role": "system", "content": context.system_prompt})

        messages.extend(context.messages)

        payload: dict = {
            "model":    model,
            "messages": messages,
            "stream":   True,
        }

        # 工具定义：仅当 Core 提供了工具时才下发。
        # 无工具请求体保持原样，避免影响现有正常对话。
        if context.tools:
            payload["tools"] = context.tools

        # max_tokens
        max_tokens = context.max_tokens or app_config.max_tokens
        if max_tokens:
            payload["max_tokens"] = max_tokens

        # temperature
        # 思考模式启用时：API 不支持 temperature，一律不传（flash / pro 均如此）
        # 思考模式关闭时：flash / pro 均可携带 temperature
        # 思考模式 & 思考强度：flash / pro 平等对待
        if app_config.thinking_enabled:
            payload["thinking"] = {"type": "enabled"}
            effort = app_config.reasoning_effort or "high"
            payload["reasoning_effort"] = effort
            # 思考模式下不传 temperature
        else:
            payload["thinking"] = {"type": "disabled"}
            # 不传 reasoning_effort —— API 要求 thinking 未启用时不得携带
            temp = context.temperature
            if temp is None:
                temp = app_config.temperature
            payload["temperature"] = temp

        return payload

    # ──────────────────────────────────────────
    # 连接管理
    # ──────────────────────────────────────────

    async def _get_client(self) -> httpx.AsyncClient:
        """获取（或初始化）共享 AsyncClient，设置合理超时。"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=10.0,
                    read=120.0,   # 长回复需要较长读超时
                    write=10.0,
                    pool=5.0,
                )
            )
        return self._client

    async def aclose(self) -> None:
        """关闭 HTTP 连接，应在应用退出时调用。"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# ──────────────────────────────────────────────
# SSE 解析（模块私有）
# ──────────────────────────────────────────────

class _SSEParser:
    """
    SSE 行 → MessageChunk 的有状态解析器。

    相比原先的逐行无状态解析，有两处必须区别对待：

    1. tool_calls 的 arguments 是跨多行分片下发的（实测一次调用会拆成 10+ 段），
       需按 index 累加拼成完整 JSON，不能逐行解析；
    2. finish_reason == "tool_calls" 表示"本轮把控制权交给客户端"，**不是**回答结束。
       此时若产出 is_done=True，上层会立刻落库一条空回答并结束生成。

    DeepSeek SSE 格式：
        data: {"choices":[{"delta":{"content":"..."},"finish_reason":null}]}
        data: {"choices":[{"delta":{"reasoning_content":"..."},"finish_reason":null}]}
        data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{...}}]}}]}
        data: [DONE]
    """

    def __init__(self) -> None:
        self._slots: dict[int, dict] = {}
        self._finish_reason: str | None = None

    def feed(self, line: str) -> list[MessageChunk]:
        """解析单行，返回 0..n 个块（同一行可能同时带思考与工具分片）。"""
        if not line or not line.startswith("data:"):
            return []

        raw = line[len("data:"):].strip()

        if raw == "[DONE]":
            # 工具轮：结束信号由 TOOL_CALL 块承担，这里不能再补一个 done
            if self._finish_reason == "tool_calls":
                return []
            return [MessageChunk(delta="", is_done=True, chunk_type=ChunkType.TEXT)]

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []

        choices = data.get("choices") or []
        if not choices:
            return []

        choice = choices[0]
        delta = choice.get("delta") or {}
        finish = choice.get("finish_reason")
        if finish:
            self._finish_reason = finish

        # 工具轮的 finish_reason 只是交棒，本轮并未结束
        turn_ends = finish is not None and finish != "tool_calls"

        out: list[MessageChunk] = []

        reasoning = delta.get("reasoning_content") or ""
        if reasoning:
            out.append(
                MessageChunk(delta=reasoning, chunk_type=ChunkType.THINKING)
            )

        self._feed_tool_calls(delta.get("tool_calls"))

        content = delta.get("content") or ""
        if content:
            out.append(MessageChunk(delta=content, chunk_type=ChunkType.TEXT))

        # 工具调用：把累积好的完整调用作为一个块交出
        if finish == "tool_calls":
            calls = self._build_calls()
            if calls:
                out.append(
                    MessageChunk(
                        delta="",
                        chunk_type=ChunkType.TOOL_CALL,
                        tool_calls=calls,
                        finish_reason=finish,
                    )
                )
            else:
                # 声明了工具调用却没收到任何分片：按正常结束收尾，避免界面永远停在生成中
                out.append(
                    MessageChunk(delta="", is_done=True, chunk_type=ChunkType.TEXT)
                )
            return out

        # 普通收尾：把结束标记放在本行最后一个块上（thinking / text 都适用）
        if turn_ends:
            if out:
                out[-1].is_done = True
            else:
                out.append(
                    MessageChunk(delta="", is_done=True, chunk_type=ChunkType.TEXT)
                )

        return out

    # ── 工具调用分片累积 ──────────────────────

    def _feed_tool_calls(self, raw: list | None) -> None:
        for tc in raw or []:
            try:
                idx = int(tc.get("index") or 0)
            except (TypeError, ValueError):
                idx = 0

            slot = self._slots.setdefault(idx, {"id": "", "name": "", "args": ""})

            if tc.get("id"):
                slot["id"] = tc["id"]

            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["name"] += fn["name"]
            args = fn.get("arguments")
            if args:
                slot["args"] += args

    def _build_calls(self) -> list[dict]:
        """拼装为可直接回传的 OpenAI 兼容结构（保持 index 顺序）。"""
        calls: list[dict] = []
        for idx in sorted(self._slots):
            slot = self._slots[idx]
            name = slot.get("name") or ""
            if not name:
                continue
            calls.append(
                {
                    "id": slot.get("id") or "",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": slot.get("args") or "",
                    },
                }
            )
        return calls


def _parse_sse_line(line: str) -> MessageChunk | None:
    """
    单行便捷解析（无跨行状态）。

    仅用于一次性解析与单元测试；流式主路径请用 _SSEParser ——
    工具调用参数是分片下发的，逐行解析只能拿到碎片。
    """
    chunks = _SSEParser().feed(line)
    return chunks[0] if chunks else None


# ──────────────────────────────────────────────
# 领域异常
# ──────────────────────────────────────────────

class LLMAPIError(Exception):
    """DeepSeek API 调用失败时抛出，由 Core 层捕获并处理。"""
