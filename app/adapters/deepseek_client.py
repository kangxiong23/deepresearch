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
                async for line in resp.aiter_lines():
                    if self._abort_flag:
                        yield MessageChunk(delta="", is_done=True,
                                           chunk_type=ChunkType.TEXT)
                        return

                    chunk = _parse_sse_line(line)
                    if chunk is None:
                        continue
                    yield chunk
                    if chunk.is_done:
                        return

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
        url = "https://api.deepseek.com/beta" + self._API_PATH
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
                async for line in resp.aiter_lines():
                    if self._abort_flag:
                        yield MessageChunk(delta="", is_done=True,
                                           chunk_type=ChunkType.TEXT)
                        return

                    chunk = _parse_sse_line(line)
                    if chunk is None:
                        continue
                    yield chunk
                    if chunk.is_done:
                        return

        except httpx.HTTPStatusError as e:
            raise LLMAPIError(
                f"DeepSeek API error {e.response.status_code}: "
                f"{e.response.text[:200]}"
            ) from e
        except httpx.RequestError as e:
            raise LLMAPIError(f"DeepSeek request failed: {e}") from e

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
            - temperature：reasoner 场景不传（保留：非 pro 模型传 temperature）。
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

        # max_tokens
        max_tokens = context.max_tokens or app_config.max_tokens
        if max_tokens:
            payload["max_tokens"] = max_tokens

        # temperature（reasoner 模型不支持，跳过）
        if model != "deepseek-v4-pro":
            temp = context.temperature
            if temp is None:
                temp = app_config.temperature
            payload["temperature"] = temp

        # 思考模式 & 思考强度：flash / pro 平等对待
        if app_config.thinking_enabled:
            payload["thinking"] = {"type": "enabled"}
            effort = app_config.reasoning_effort or "high"
            payload["reasoning_effort"] = effort
        else:
            payload["thinking"] = {"type": "disabled"}
            # 不传 reasoning_effort —— API 要求 thinking 未启用时不得携带

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

def _parse_sse_line(line: str) -> MessageChunk | None:
    """
    解析单行 SSE 数据。

    返回值：
    - MessageChunk — 有内容的块
    - None         — 空行 / 注释行 / 非 data 行，跳过

    DeepSeek SSE 格式：
        data: {"choices":[{"delta":{"content":"..."},"finish_reason":null}]}
        data: {"choices":[{"delta":{"reasoning_content":"..."},"finish_reason":null}]}
        data: [DONE]
    """
    if not line or not line.startswith("data:"):
        return None

    raw = line[len("data:"):].strip()

    if raw == "[DONE]":
        return MessageChunk(delta="", is_done=True, chunk_type=ChunkType.TEXT)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    choices = data.get("choices")
    if not choices:
        return None

    choice = choices[0]
    delta = choice.get("delta", {})
    finish_reason = choice.get("finish_reason")

    # reasoning_content → THINKING 块
    reasoning = delta.get("reasoning_content") or ""
    if reasoning:
        return MessageChunk(
            delta=reasoning,
            is_done=False,
            chunk_type=ChunkType.THINKING,
        )

    # content → TEXT 块
    content = delta.get("content") or ""
    is_done = finish_reason is not None
    if content or is_done:
        return MessageChunk(
            delta=content,
            is_done=is_done,
            chunk_type=ChunkType.TEXT,
        )

    return None


# ──────────────────────────────────────────────
# 领域异常
# ──────────────────────────────────────────────

class LLMAPIError(Exception):
    """DeepSeek API 调用失败时抛出，由 Core 层捕获并处理。"""
