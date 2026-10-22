# Layer: Storage
# File: app/storage/json_io.py
# Responsibility: JSON 文件的原子写入与容错读取。
#                 - 写入：同目录临时文件 + os.replace，崩溃时不会留下半截文件。
#                 - 读取：解析失败时把损坏文件改名隔离，避免"静默当成空数据"，
#                         从而在后续保存时覆盖掉原始内容。
# Input:  Path / 可 JSON 序列化的对象
# Output: 读取结果（列表）
# 禁止: 业务判断、导入 UI 库

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def atomic_replace(src: Path, dst: Path, retries: int = 5, delay: float = 0.05) -> None:
    """
    原子替换文件（os.replace），Windows 上对瞬态锁重试。

    tree_store._atomic_replace 是本函数的包装，实现只此一份。
    """
    for attempt in range(retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(delay)


def write_json_atomic(path: Path, data: Any) -> None:
    """
    原子写入 JSON：先写同目录临时文件，再 os.replace 覆盖目标。

    崩溃若发生在 json.dump 过程中，目标文件保持旧内容不变，
    不会出现被截断的半截文件（裸 open("w") 会先清空目标文件）。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # 用 mkstemp 生成唯一临时名，避免并发写入互相覆盖
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            # 先落盘再替换：断电/蓝屏时不会留下一个 0 字节的"新"文件
            f.flush()
            os.fsync(f.fileno())
        atomic_replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def quarantines_of(path: Path) -> list[Path]:
    """返回某文件已产生的隔离副本（调试/自检用）。"""
    path = Path(path)
    return sorted(path.parent.glob(path.name + ".corrupt-*"))


def _quarantine(path: Path) -> Path | None:
    """
    把损坏文件改名保留（追加 .corrupt-<时间戳>），返回新路径。

    这样即使文件真的坏了，内容也还在磁盘上，
    不会因为下一次保存而被静默覆盖。
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.corrupt-{stamp}")
    n = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.corrupt-{stamp}-{n}")
        n += 1
    try:
        path.rename(candidate)
        return candidate
    except OSError:
        return None


def read_json_list(path: Path) -> list[dict]:
    """
    读取顶层为列表的 JSON 文件。

    - 文件不存在           → []
    - 解析失败（截断/编码错）→ 隔离损坏文件后返回 []
    - 其它读取错误（占用等）→ 打印告警、保留原文件，返回 []
    - 顶层不是列表         → 打印告警，返回 []
    """
    path = Path(path)
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        kept = _quarantine(path)
        where = str(kept) if kept else "（隔离失败，原文件保持不动）"
        print(f"[STORAGE] JSON 解析失败，已隔离损坏文件: {path} -> {where}")
        return []
    except OSError as exc:
        print(f"[STORAGE] 读取 JSON 失败（保留原文件）: {path} ({exc})")
        return []

    if not isinstance(data, list):
        print(f"[STORAGE] JSON 顶层不是列表，按空处理: {path}")
        return []
    return data
