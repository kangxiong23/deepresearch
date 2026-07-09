# Layer: Utils（跨层通用）
# File: app/utils/async_utils.py
# Responsibility: 异步辅助工具 + 结构化日志。
#                 日志是解决"分层调试困难"的主要手段：
#                 每一层的关键操作都打印带层次标签的日志，
#                 调试时 grep 层名即可快速定位。
# Input:  无特定输入
# Output: logger 实例、async 辅助函数

from __future__ import annotations

import asyncio
import functools
import logging
import sys
import time
from typing import AsyncGenerator, Callable, TypeVar

import config as app_config

T = TypeVar("T")


# ──────────────────────────────────────────────
# 结构化日志配置
# ──────────────────────────────────────────────

def _build_formatter() -> logging.Formatter:
    """
    日志格式：
        [时间] [级别] [层标签] 消息
    示例：
        2024-05-13 14:32:01 INFO  [CORE ] send_message called session=abc123
        2024-05-13 14:32:02 DEBUG [ADPTR] DeepSeek SSE chunk delta=Hello
        2024-05-13 14:32:03 ERROR [STORE] SQLite error: ...
    """
    return logging.Formatter(
        fmt="%(asctime)s %(levelname)-5s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _configure_root_logger() -> None:
    """配置根 Logger，仅执行一次（幂等）。"""
    root = logging.getLogger()
    if root.handlers:
        return  # 已配置，跳过

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_build_formatter())
    root.addHandler(handler)

    level = getattr(logging, app_config.LOG_LEVEL.upper(), logging.INFO)
    root.setLevel(level)


_configure_root_logger()


def get_logger(layer: str, name: str) -> logging.Logger:
    """
    获取带层标签的 Logger。

    Args:
        layer: 层标识，如 "CORE"、"CTRL"、"ADPTR"、"STORE"、"UI"
        name:  模块名，如 "conversation_service"

    用法：
        logger = get_logger("CORE", "conversation_service")
        logger.info("send_message called session=%s", session_id)
    """
    tag = f"[{layer:<5}] {name}"
    return logging.getLogger(tag)


# ──────────────────────────────────────────────
# 调试装饰器：自动打印调用入口 / 返回 / 异常
# ──────────────────────────────────────────────

def log_call(logger: logging.Logger, level: int = logging.DEBUG):
    """
    装饰器：在函数入口和返回时打印日志，异常时打印 ERROR。
    适用于同步函数和异步函数（自动检测）。

    用法：
        @log_call(logger)
        async def send_message(self, session_id, text, files):
            ...
    """
    def decorator(func: Callable) -> Callable:
        fn_name = func.__qualname__

        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                logger.log(level, "→ %s args=%s kwargs=%s", fn_name, _safe_repr(args[1:]), kwargs)
                t0 = time.perf_counter()
                try:
                    result = await func(*args, **kwargs)
                    elapsed = (time.perf_counter() - t0) * 1000
                    logger.log(level, "← %s (%.1fms)", fn_name, elapsed)
                    return result
                except Exception as exc:
                    logger.error("✗ %s raised %s: %s", fn_name, type(exc).__name__, exc)
                    raise
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                logger.log(level, "→ %s args=%s kwargs=%s", fn_name, _safe_repr(args[1:]), kwargs)
                t0 = time.perf_counter()
                try:
                    result = func(*args, **kwargs)
                    elapsed = (time.perf_counter() - t0) * 1000
                    logger.log(level, "← %s (%.1fms)", fn_name, elapsed)
                    return result
                except Exception as exc:
                    logger.error("✗ %s raised %s: %s", fn_name, type(exc).__name__, exc)
                    raise
            return sync_wrapper

    return decorator


def log_stream(logger: logging.Logger, log_first_n: int = 3):
    """
    装饰器：包装 async generator，打印前 N 个 chunk（避免日志爆炸）。

    用法：
        @log_stream(logger, log_first_n=3)
        async def stream_chat(self, context):
            async for chunk in ...:
                yield chunk
    """
    def decorator(func: Callable) -> Callable:
        fn_name = func.__qualname__

        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> AsyncGenerator:
            logger.debug("⇒ %s stream started", fn_name)
            count = 0
            try:
                async for item in func(*args, **kwargs):
                    count += 1
                    if count <= log_first_n:
                        logger.debug("  chunk[%d] %s", count, _safe_repr(item))
                    yield item
                logger.debug("⇐ %s stream done (total %d chunks)", fn_name, count)
            except Exception as exc:
                logger.error("✗ %s stream error: %s: %s", fn_name, type(exc).__name__, exc)
                raise

        return wrapper
    return decorator


# ──────────────────────────────────────────────
# 异步重试
# ──────────────────────────────────────────────

async def retry_async(
    coro_func: Callable,
    *args,
    retries: int = 3,
    delay: float = 1.0,
    exceptions: tuple = (Exception,),
    logger: logging.Logger | None = None,
    **kwargs,
):
    """
    带退避重试的异步调用。

    Args:
        coro_func:  异步函数
        retries:    最大重试次数
        delay:      首次等待秒数，每次 ×2 指数退避
        exceptions: 捕获并重试的异常类型元组

    用法：
        result = await retry_async(client.fetch, url, retries=3, delay=0.5)
    """
    last_exc: Exception | None = None
    wait = delay
    for attempt in range(1, retries + 1):
        try:
            return await coro_func(*args, **kwargs)
        except exceptions as e:
            last_exc = e
            if logger:
                logger.warning("attempt %d/%d failed: %s", attempt, retries, e)
            if attempt < retries:
                await asyncio.sleep(wait)
                wait *= 2
    raise last_exc


# ──────────────────────────────────────────────
# 安全打印（避免大对象撑爆日志）
# ──────────────────────────────────────────────

def _safe_repr(obj, max_len: int = 120) -> str:
    try:
        s = repr(obj)
        return s if len(s) <= max_len else s[:max_len] + "…"
    except Exception:
        return "<unrepresentable>"
