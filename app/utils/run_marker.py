# Layer: Utils
# File: app/utils/run_marker.py
# Responsibility: 记录一次运行的边界（logs/runtime/session-<pid>.json），
#                 用于识别"上次是否异常退出"以及"上次会话存活了多久"。
#                 启动写入 -> 心跳刷新 -> 正常退出标记并删除；下次启动扫描残留。
# Input:  无
# Output: 上次异常退出会话的摘要（dict 列表）
# 禁止: 导入 UI 库、业务判断

from __future__ import annotations

import ctypes
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from app.storage.json_io import write_json_atomic


def _pid_alive(pid: int) -> bool:
    """目标进程是否仍然存在（Windows 用 OpenProcess，其余平台用 kill 0）。"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        # SYNCHRONIZE：能拿到句柄就说明进程还在
        handle = kernel32.OpenProcess(0x00100000, False, int(pid))
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _parse_ts(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


class RunMarker:
    """
    单次运行的标记文件。

    用途有两个：
    1. 判断上一次是否异常退出（崩溃、被强杀、断电等）；
    2. 记录每次会话的存活时长 —— 区分"启动即崩"和"用久了才崩"。

    只做诊断，不弹窗、不阻断启动：出错一律降级为无记录。
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        if base_dir is None:
            base_dir = (
                Path(__file__).resolve().parent.parent.parent / "logs" / "runtime"
            )
        self._base = Path(base_dir)
        self._path = self._base / f"session-{os.getpid()}.json"
        self._started_at = datetime.now()
        self._finished = False

    def start(self) -> list[dict]:
        """写本次会话标记，返回上次异常退出的会话摘要（并清理其残留文件）。"""
        self._base.mkdir(parents=True, exist_ok=True)
        stale = self._collect_stale()
        self._write(clean=False)
        return stale

    def beat(self) -> None:
        """心跳：刷新 last_heartbeat，用于估算存活时长。"""
        if not self._finished:
            self._write(clean=False)

    def finish(self) -> None:
        """正常退出：标记 clean_exit 并删除标记文件。"""
        if self._finished:
            return
        self._finished = True
        self._write(clean=True)
        self._unlink(self._path)

    # ── 内部 ────────────────────────────────

    def _write(self, clean: bool) -> None:
        payload = {
            "pid": os.getpid(),
            "started_at": self._started_at.isoformat(timespec="seconds"),
            "last_heartbeat": datetime.now().isoformat(timespec="seconds"),
            "clean_exit": clean,
        }
        try:
            write_json_atomic(self._path, payload)
        except OSError as exc:
            print(f"[RUN] 会话标记写入失败: {exc}")

    def _collect_stale(self) -> list[dict]:
        stale: list[dict] = []
        for path in sorted(self._base.glob("session-*.json")):
            if path == self._path:
                continue
            data = self._read(path)
            if data is None:
                self._unlink(path)          # 内容损坏 → 无信息价值，清掉
                continue
            if data.get("clean_exit"):
                self._unlink(path)          # 正常退出后没删掉 → 静默清理
                continue
            pid = int(data.get("pid") or 0)
            if pid == os.getpid() or _pid_alive(pid):
                continue                    # 另一个实例还在跑，别动它
            stale.append(data)
            self._unlink(path)
        return stale

    @staticmethod
    def _read(path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass


def describe(stale: dict) -> str:
    """把一条异常退出记录格式化为一行日志。"""
    pid = stale.get("pid", "?")
    started = _parse_ts(stale.get("started_at"))
    beat = _parse_ts(stale.get("last_heartbeat"))
    if started is None:
        return f"[RUN] 上次会话异常结束（pid={pid}，时间未知）"
    if beat is not None and beat > started:
        secs = int((beat - started).total_seconds())
        alive = f"{secs // 60}m{secs % 60:02d}s"
    else:
        alive = "<30s"
    return (
        f"[RUN] 上次会话异常结束（pid={pid}，"
        f"开始于 {started.strftime('%Y-%m-%d %H:%M:%S')}，存活约 {alive}）"
    )
