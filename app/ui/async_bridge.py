# Layer: UI (PySide6)
# File: app/ui/async_bridge.py
# Responsibility: 桥接 asyncio AsyncGenerator → Qt Signals
#                 将 Controller 层的异步流式生成器转为 Qt 线程安全的信号

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, Callable, Any

from PySide6.QtCore import QThread, QObject, Signal, Slot


class AsyncStreamWorker(QObject):
    """
    在后台 QThread 中运行 asyncio 事件循环，迭代 AsyncGenerator，
    通过 Qt 信号将每个 chunk 安全地发送到主线程。

    用法：
        thread = QThread()
        worker = AsyncStreamWorker()
        worker.moveToThread(thread)

        worker.chunk_ready.connect(on_chunk)        # 主线程处理
        worker.stream_finished.connect(on_finish)    # 主线程处理
        worker.stream_error.connect(on_error)        # 主线程处理
        worker.stream_aborted.connect(on_abort)      # 主线程处理

        thread.started.connect(lambda: worker.run_stream(
            controller.on_send_message, session_id, text, files
        ))
        thread.start()

        # 中止：
        worker.request_abort()
    """

    # chunk_ready(delta: str, is_done: bool, chunk_type: str, message_id: str)
    chunk_ready = Signal(str, bool, str, str)
    # stream_finished()
    stream_finished = Signal()
    # stream_error(error_msg: str)
    stream_error = Signal(str)
    # stream_aborted() — 用户中止
    stream_aborted = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._abort_flag: bool = False
        self._loop: asyncio.AbstractEventLoop | None = None

    @Slot(object, object)
    def run_stream(
        self,
        gen_factory: Callable[..., AsyncGenerator],
        *args: Any,
    ) -> None:
        """
        在 QThread 的事件循环中运行异步流。

        Args:
            gen_factory: 返回 AsyncGenerator 的可调用对象
                         （例如 controller.on_send_message）
            *args:       传递给 gen_factory 的位置参数
        """
        self._abort_flag = False
        try:
            # 在新线程中创建独立的 asyncio 事件循环
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(
                self._iterate_stream(gen_factory, *args)
            )
        except Exception as exc:
            self.stream_error.emit(str(exc))
        finally:
            if self._loop is not None:
                self._loop.close()
                self._loop = None

    async def _iterate_stream(
        self,
        gen_factory: Callable[..., AsyncGenerator],
        *args: Any,
    ) -> None:
        """
        迭代 AsyncGenerator 并将每个 chunk 通过信号发出。
        """
        try:
            async for chunk_vm in gen_factory(*args):
                if self._abort_flag:
                    self.stream_aborted.emit()
                    return

                self.chunk_ready.emit(
                    chunk_vm.delta,
                    chunk_vm.is_done,
                    chunk_vm.chunk_type,
                    chunk_vm.message_id,
                )

                if chunk_vm.is_done:
                    break

            self.stream_finished.emit()

        except Exception as exc:
            self.stream_error.emit(str(exc))

    def request_abort(self) -> None:
        """请求中止当前流。线程安全。"""
        self._abort_flag = True


class StopGenerationWorker(QObject):
    """
    在后台线程中调用 controller.on_stop_generation()。
    Core 层的 stop_generation 设置 asyncio.Event，需要
    在 event loop 所在线程中执行。

    用法：
        thread = QThread()
        worker = StopGenerationWorker()
        worker.moveToThread(thread)
        worker.done.connect(on_done)
        thread.started.connect(lambda: worker.do_stop(controller.on_stop_generation))
        thread.start()
    """

    done = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    @Slot(object)
    def do_stop(self, stop_func: Callable[[], None]) -> None:
        """在后台线程中执行停止回调。"""
        try:
            stop_func()
        finally:
            self.done.emit()


class AsyncTaskRunner(QObject):
    """
    在后台线程中运行单个 async 函数（非生成器）。
    用于 on_remember_conversation 等返回 awaitable 的操作。

    用法：
        thread = QThread()
        runner = AsyncTaskRunner()
        runner.moveToThread(thread)
        runner.result_ready.connect(on_result)
        runner.task_error.connect(on_error)
        thread.started.connect(lambda: runner.run(
            controller.on_remember_conversation, session_id
        ))
        thread.start()
    """

    result_ready = Signal(object)   # result: Any
    task_error = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._loop: asyncio.AbstractEventLoop | None = None

    @Slot(object, object)
    def run(self, coro_func: Callable, *args: Any) -> None:
        """运行单个协程并发出结果信号。"""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            result = self._loop.run_until_complete(coro_func(*args))
            self.result_ready.emit(result)
        except Exception as exc:
            self.task_error.emit(str(exc))
        finally:
            if self._loop is not None:
                self._loop.close()
                self._loop = None


def run_async_in_thread(
    coro_func: Callable,
    *args: Any,
    on_result: Callable | None = None,
    on_error: Callable | None = None,
    parent: QObject | None = None,
) -> tuple[QThread, AsyncTaskRunner]:
    """
    便捷函数：在后台线程运行 async 函数。

    Args:
        coro_func: 返回 awaitable 的可调用对象
        *args:     位置参数
        on_result: 结果回调
        on_error:  错误回调
        parent:    父 QObject

    Returns:
        (QThread, AsyncTaskRunner) — 调用方需保持引用以防止 GC
    """
    thread = QThread(parent)
    runner = AsyncTaskRunner(parent)
    runner.moveToThread(thread)

    if on_result:
        runner.result_ready.connect(on_result)
    if on_error:
        runner.task_error.connect(on_error)

    thread.started.connect(lambda: runner.run(coro_func, *args))
    thread.finished.connect(runner.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread, runner
