# Layer: Utils（跨层通用）
# File: app/utils/_tmp_diag.py
# Responsibility: [临时] 崩溃/卡死观测模块 —— 只观测，不改业务逻辑。
#
#   [临时] 定位目标："管理上下文块 → context_panel.load_blocks 处
#   [临时] access violation / 整个进程卡死" 这一概率性故障的真实根源。
#
#   [临时] 采集四件事：
#     1) 致命异常 / 卡死时的【全线程 Python 栈】——写独立文件，不依赖 GIL
#        （主线程卡在 Qt C++ 或 GC 持锁时也能 dump）；
#     2) 【事件时间线】——面板重建行、effect 建/毁、菜单嵌套循环进出、
#        deleteLater 投递，每行带线程名与 gc 计数；崩溃前最后几行即现场；
#     3) 【控件健康度】——apply_style 注册表中「Python 包装器还活着、
#        但 C++ 对象已销毁」的条目数，用来判定是否包装器泄漏；
#     4) 【GC 参与度】——第 2 代回收耗时/回收数，判断崩溃是否与 GC 撞上。
#
#   [临时] 彻底移除的方式：
#     - 删除本文件；
#     - 删除仓库中所有含 "[临时]" 的行/段落（grep -rn "\[临时\]" 可一次列全）。
#
# Input:  main.py 启动时调用 start() / install_qt(app)
# Output: logs/crash_probe.log（时间线）、logs/crash_probe_fault.log（栈转储）

from __future__ import annotations

import gc
import os
import sys
import threading
import time
from pathlib import Path

# [临时] 全局开关：start() 之后为 True；未启动时 mark() 立即返回。
ENABLED = False

_LOG_FILE = None
_LOG_PATH = None
_FAULT_PATH = None
_LOCK = threading.RLock()
_START_TS = time.time()

# [临时] UI 线程心跳：主线程 QTimer 定时刷新；超时未刷新判定卡死。
_last_beat = 0.0
UI_TICK_MS = 200
WATCHDOG_SECS = 3.0
_stall_dumped = False

_watchdog_thread = None
_qt_timer = None

# [临时] 观测计数器（崩溃现场可直接看出哪条路径被高频走过）
_stats = {
    "load_blocks_calls": 0,
    "clear_layout_calls": 0,
    "rows_new": 0,
    "rows_destroyed": 0,
    "effect_set": 0,
    "effect_clear": 0,
    # [临时] 清空后 C++ 对象仍有效 = 异常（正常应为 0）
    "effect_clear_still_valid": 0,
    "mutation_depth": 0,
    "mutation_max_depth": 0,
    "gc2_runs": 0,
    "gc2_collected": 0,
    "gc2_slow": 0,
    "dlg_stress_rounds": 0,
}

_gc2_start_ts = 0.0


# ──────────────────────────────────────────────
# [临时] 输出基础设施
# ──────────────────────────────────────────────


def now_ms() -> float:
    """[临时] 相对启动的毫秒数（供压测计耗时）。"""
    return (time.time() - _START_TS) * 1000.0


def _ts() -> str:
    """[临时] 相对启动的毫秒数，便于看时序。"""
    return f"{(time.time() - _START_TS) * 1000:10.1f}ms"


def _open_log():
    """[临时] 打开时间线日志（行缓冲：崩溃前最后一行不会丢）。"""
    global _LOG_FILE, _LOG_PATH, _FAULT_PATH
    if _LOG_FILE is not None:
        return
    root = Path(__file__).resolve().parents[2]
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _LOG_PATH = log_dir / "crash_probe.log"
    _FAULT_PATH = log_dir / "crash_probe_fault.log"
    _LOG_FILE = open(_LOG_PATH, "a", encoding="utf-8", buffering=1)
    # [临时] header 只用 ASCII，且左右两条分隔线等长（避免编码/引号踩坑）
    _LOG_FILE.write(
        "\n"
        + "=" * 78
        + "\n"
        + f"[tmp-diag] crash_probe start  pid={os.getpid()}  "
        + f"python={sys.version.split()[0]}"
        + "\n"
        + "=" * 78
        + "\n"
    )


def mark(kind: str, **kw) -> None:
    """
    [临时] 写一行事件时间线。

    行格式：  1234.5ms [MainThread] gc=(n0,n1,n2) KIND key=value ...
    """
    if not ENABLED or _LOG_FILE is None:
        return
    try:
        g0, g1, g2 = gc.get_count()
        extra = " ".join(f"{k}={v}" for k, v in kw.items())
        with _LOCK:
            _LOG_FILE.write(
                f"{_ts()} [{threading.current_thread().name:<16}] "
                f"gc=({g0},{g1},{g2}) {kind} {extra}\n"
            )
    except Exception:
        pass  # [临时] 观测代码绝不能影响主流程


def note(msg: str) -> None:
    """[临时] 带说明的事件行。"""
    mark("NOTE", msg=msg)


# ──────────────────────────────────────────────
# [临时] 栈转储
# ──────────────────────────────────────────────


def dump_all(reason: str) -> None:
    """
    [临时] 转储所有线程的 Python 栈 + 计数器快照。

    用 faulthandler.dump_traceback 而非 traceback：它在其它线程卡死、
    GIL 被占用时依然能工作（崩溃栈本身就是 faulthandler 输出的）。
    """
    import faulthandler

    try:
        with _LOCK:
            if _LOG_FILE is not None:
                _LOG_FILE.write(
                    f"\n{'-' * 78}\n{_ts()} DUMP reason={reason}\n"
                    + _format_stats()
                    + f"{'-' * 78}\n"
                )
            if _FAULT_PATH is not None:
                # [临时] 本文件由 faulthandler 直接写字节，中文会乱码 → 只用 ASCII
                with open(_FAULT_PATH, "a", encoding="utf-8") as fh:
                    fh.write(
                        f"\n{'=' * 78}\n{_ts()} DUMP reason={reason} "
                        f"threads={threading.active_count()}\n{'=' * 78}\n"
                    )
                    faulthandler.dump_traceback(file=fh, all_threads=True)
                    fh.write(_format_stats_ascii())
    except Exception:
        pass


def _format_stats() -> str:
    """[临时] 计数器快照文本。"""
    body = "\n".join(f"    {k} = {v}" for k, v in _stats.items())
    return f"  [临时] stats:\n{body}\n"


def _format_stats_ascii() -> str:
    """[临时] 计数器快照（ASCII 版，供 faulthandler 写出的文件使用）。"""
    body = "\n".join(f"    {k} = {v}" for k, v in _stats.items())
    return f"  [tmp-diag] stats:\n{body}\n"


def audit_widgets(label: str) -> None:
    """
    [临时] 控件健康度审计。

    核心指标 dead_wrappers：apply_style 注册表里 Python 包装器仍存在、
    但 C++ 对象已被销毁的条目数。持续增长即为「包装器泄漏」的直接证据
    （对应「deleteLater 后包装器残留」这一假设）。
    """
    try:
        from PySide6.QtWidgets import QApplication
        import shiboken6
        from app.ui.theme import _STYLE_REGISTRY

        app = QApplication.instance()
        total = len(app.allWidgets()) if app is not None else -1
        dead = 0
        for ref, _fn in list(_STYLE_REGISTRY.values()):
            w = ref()
            if w is None:
                continue
            try:
                if not shiboken6.isValid(w):
                    dead += 1
            except Exception:
                dead += 1
        mark(
            "AUDIT",
            label=label,
            widgets=total,
            registry=len(_STYLE_REGISTRY),
            dead_wrappers=dead,
        )
    except Exception:
        pass


def cpp_ptr(obj) -> str:
    """[临时] 取 C++ 指针（用于对照销毁前后的同一对象）。"""
    try:
        import shiboken6

        return "0x%x" % shiboken6.getCppPointer(obj)[0]
    except Exception:
        return "?"


def is_alive(obj) -> bool:
    """[临时] C++ 对象是否仍然有效。"""
    try:
        import shiboken6

        return bool(shiboken6.isValid(obj))
    except Exception:
        return False


# ──────────────────────────────────────────────
# [临时] GC 观测
# ──────────────────────────────────────────────


def _gc_callback(phase: str, info: dict) -> None:
    """[临时] 记录第 2 代回收的耗时与回收数。"""
    global _gc2_start_ts
    if not ENABLED:
        return
    try:
        gen = info.get("generation", -1)
        if gen != 2:
            return
        if phase == "start":
            _gc2_start_ts = time.perf_counter()
            return
        dur_ms = (time.perf_counter() - _gc2_start_ts) * 1000.0
        _stats["gc2_runs"] += 1
        _stats["gc2_collected"] += int(info.get("collected", 0))
        if dur_ms >= 20.0:
            _stats["gc2_slow"] += 1
        # [临时] 只记录慢回收，避免正常回收把时间线刷屏
        if dur_ms >= 20.0:
            mark(
                "GC2",
                ms=f"{dur_ms:.1f}",
                collected=info.get("collected"),
                uncollectable=info.get("uncollectable"),
            )
    except Exception:
        pass


# ──────────────────────────────────────────────
# [临时] 嵌套事件循环 / 树变更覆盖层
# ──────────────────────────────────────────────


def mutation_enter(name: str) -> None:
    """[临时] 进入一次树/面板变更（用来自证是否处于嵌套事件循环中）。"""
    if not ENABLED:
        return
    _stats["mutation_depth"] += 1
    if _stats["mutation_depth"] > _stats["mutation_max_depth"]:
        _stats["mutation_max_depth"] = _stats["mutation_depth"]
    mark("MUT_ENTER", name=name, depth=_stats["mutation_depth"])


def mutation_exit(name: str) -> None:
    """[临时] 退出一次树/面板变更。"""
    if not ENABLED:
        return
    depth = _stats["mutation_depth"]
    _stats["mutation_depth"] = max(0, depth - 1)
    mark("MUT_EXIT", name=name, depth=_stats["mutation_depth"])


# ──────────────────────────────────────────────
# [临时] Qt 侧安装：心跳 / 看门狗 / Qt 消息
# ──────────────────────────────────────────────


def _install_qt_message_handler() -> None:
    """
    [临时] 捕获 Qt 自身的 qWarning/qFatal 输出。

    QGraphicsEffect 相关告警（例如效果被重复安装、对象已销毁）会出现在这里，
    是判断根因的重要旁证。
    """
    try:
        from PySide6.QtCore import qInstallMessageHandler

        def handler(mode, ctx, msg):
            mark("QTMSG", mode=str(mode).split(".")[-1], msg=str(msg)[:300])

        qInstallMessageHandler(handler)
    except Exception:
        pass


def _beat() -> None:
    """[临时] 主线程心跳（QTimer 驱动）。"""
    global _last_beat, _stall_dumped
    _last_beat = time.time()
    if _stall_dumped:
        _stall_dumped = False
        note("主线程恢复响应（此前判定为卡死）")


def _watchdog_loop() -> None:
    """[临时] 看门狗线程：主线程心跳超时 → 立即 dump 全线程栈。"""
    global _stall_dumped
    while True:
        time.sleep(0.5)
        try:
            if not ENABLED:
                continue
            if _last_beat and (time.time() - _last_beat) > WATCHDOG_SECS:
                if not _stall_dumped:
                    _stall_dumped = True
                    dump_all(
                        f"main thread unresponsive >{WATCHDOG_SECS}s "
                        f"(last beat {time.time() - _last_beat:.1f}s ago)"
                    )
        except Exception:
            pass


def install_qt(app) -> None:
    """[临时] 安装 Qt 侧观测：消息处理器 + 心跳 + 看门狗线程。"""
    global _watchdog_thread, _qt_timer
    _install_qt_message_handler()

    try:
        from PySide6.QtCore import QTimer

        _qt_timer = QTimer()
        _qt_timer.setInterval(UI_TICK_MS)
        _qt_timer.timeout.connect(_beat)
        _qt_timer.start()
        _last_beat = time.time()
    except Exception:
        pass

    if _watchdog_thread is None:
        _watchdog_thread = threading.Thread(
            target=_watchdog_loop, name="crash-watchdog", daemon=True
        )
        _watchdog_thread.start()


def _install_excepthooks() -> None:
    """[临时] 未捕获异常（主线程 / 子线程）也落盘并 dump 全线程栈。"""

    def _sys_hook(exc_type, exc, tb):
        try:
            mark("EXC", where="sys.excepthook", err=repr(exc)[:300])
            dump_all(f"uncaught exception: {exc_type.__name__}")
        finally:
            sys.__excepthook__(exc_type, exc, tb)

    def _thread_hook(args):
        try:
            mark(
                "EXC",
                where=f"thread {args.thread.name if args.thread else '?'}",
                err=repr(args.exc_value)[:300],
            )
            dump_all(f"uncaught thread exception: {args.exc_type.__name__}")
        except Exception:
            pass

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook


def start() -> None:
    """[临时] 启用全部观测（幂等）。必须在 QApplication 创建之后调用。"""
    global ENABLED
    if ENABLED:
        return
    _open_log()  # [临时] 只会真正写一次 header（_LOG_FILE 已存在时提前返回）
    ENABLED = True

    # [临时] faulthandler 写到独立文件：与时间线日志分开，避免缓冲交错
    try:
        import faulthandler

        _ff = open(_FAULT_PATH, "a", encoding="utf-8", buffering=1)
        faulthandler.enable(file=_ff, all_threads=True)
    except Exception:
        pass

    gc.callbacks.append(_gc_callback)
    _install_excepthooks()
    mark(
        "START",
        python=sys.version.split()[0],
        pid=os.getpid(),
        log=str(_LOG_PATH),
    )
