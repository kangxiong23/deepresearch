# Layer: Core
# File: app/core/file_tool_service.py
# Responsibility: 把"模型请求的本地文件访问"落到具体工具调用上——校验授权范围、
#                 维护会话级索引、执行预算与去重、把结果格式化为可直接回传的 JSON。
#                 真正的文件 I/O 全在 Adapter（local_fs），此处不打开任何文件。
# Input:  工具名 + 参数 JSON 串（来自 LLM 的 tool_calls）
# Output: JSON 串（作为 role="tool" 的 content 回传给模型）
# 禁止: UI 导入、HTTP 调用、实现文件读写

from __future__ import annotations

import json
import os
import time
from datetime import datetime

import config as app_config
from app.adapters import local_fs
from app.adapters.local_fs import FileEntry


# ──────────────────────────────────────────────
# 预算
#
# 工具结果是纯输入 token，不设上限会被一次大文件读取撑爆上下文。
# ──────────────────────────────────────────────

MAX_RESULT_BYTES = 12_000        # 单次工具结果
MAX_TURN_BYTES = 48_000          # 单轮累计
MAX_FILES_PER_TURN = 10          # 单轮不同文件数
MAX_SEARCH_RESULTS = 20
MAX_INDEX_ENTRIES = 200_000
INDEX_TTL_SECONDS = 60.0

# 内容检索的额外限制（要真的读文件，代价高）
_CONTENT_MAX_FILE_BYTES = 1_000_000
_CONTENT_MAX_SCAN_BYTES = 16_000_000


SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_files",
        "description": (
            "在用户授权的本地文件夹中查找文件。只支持 txt / md / json / jsonl / docx。"
            "默认按文件名匹配；把 in_content 设为 true 会进一步搜索文件内容（较慢）。"
            "返回匹配文件的绝对路径，找不到就返回空列表。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "文件名关键词或内容关键词；留空表示按类型列出",
                },
                "in_content": {
                    "type": "boolean",
                    "description": "是否搜索文件内容，默认 false 只匹配文件名",
                },
                "types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["txt", "md", "json", "jsonl", "docx"],
                    },
                    "description": "限定文件类型，省略表示全部",
                },
            },
            "required": ["query"],
        },
    },
}

READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "读取一个已授权文件的指定行范围。返回带行号的文本；"
            "若 total_lines 大于 end_line，说明还有后续内容，可再次调用读取。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件的绝对路径"},
                "start_line": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "起始行号，默认 1",
                },
                "max_lines": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "description": "最多读取多少行，默认 200",
                },
            },
            "required": ["path"],
        },
    },
}

_ALL_SCHEMAS = [SEARCH_SCHEMA, READ_SCHEMA]


class FileToolService:
    """
    本地文件工具的执行与策略层。

    所有失败的返回都是 {"error": "..."}，永不抛异常 —— 一次工具失败不应该
    终结整轮对话，模型看到错误信息可以自行纠正。
    """

    def __init__(self, clock=time.time) -> None:
        self._clock = clock
        self._entries: list[FileEntry] = []
        self._indexed_at: float = 0.0
        self._indexed_roots: tuple[str, ...] = ()
        self._stats: local_fs.ScanStats = local_fs.ScanStats()

        # 单轮预算与去重（由 begin_turn 重置）
        self._turn_bytes = 0
        self._turn_files: set[str] = set()
        self._read_ranges: set[tuple[str, int, int]] = set()
        self._audit: list[dict] = []

    # ── 授权 ─────────────────────────────────

    def roots(self) -> list[str]:
        return [r for r in (app_config.file_access_roots or []) if r]

    def enabled(self) -> bool:
        return bool(getattr(app_config, "file_access_enabled", False)) and bool(self.roots())

    def schemas(self) -> list[dict]:
        return list(_ALL_SCHEMAS) if self.enabled() else []

    # ── 单轮生命周期 ─────────────────────────

    def begin_turn(self) -> None:
        self._turn_bytes = 0
        self._turn_files = set()
        self._read_ranges = set()
        self._audit = []

    def audit(self) -> list[dict]:
        """本轮发生过的工具调用（供 UI 展示"读了哪些文件"）。"""
        return list(self._audit)

    # ── 执行入口 ─────────────────────────────

    def execute(self, name: str, arguments: str) -> str:
        try:
            args = json.loads(arguments) if arguments else {}
            if not isinstance(args, dict):
                raise ValueError("参数必须是 JSON 对象")
        except (json.JSONDecodeError, ValueError) as e:
            return _err(f"参数不是合法的 JSON 对象：{e}")

        try:
            if name == "search_files":
                return self._search(args)
            if name == "read_file":
                return self._read(args)
            return _err(f"未知工具：{name}")
        except Exception as e:  # 兜底：任何异常都不能打断对话
            return _err(f"工具执行失败：{type(e).__name__}: {e}")

    # ── search_files ─────────────────────────

    def _search(self, args: dict) -> str:
        roots = self.roots()
        if not roots:
            return _err("用户尚未授权任何文件夹")

        query = str(args.get("query") or "").strip()
        in_content = bool(args.get("in_content") or False)
        types = _normalize_types(args.get("types"))

        entries = self._ensure_index()
        if not entries:
            return json.dumps(
                {"count": 0, "files": [], "note": "授权范围内没有找到受支持的文件"},
                ensure_ascii=False,
            )

        needle = query.casefold()
        hits: list[FileEntry] = []
        for e in entries:
            if types and e.ext not in types:
                continue
            if not needle or needle in e.name.casefold() or needle in e.path.casefold():
                hits.append(e)
                if len(hits) >= MAX_SEARCH_RESULTS * 4:
                    break

        note = ""
        if in_content and len(hits) < MAX_SEARCH_RESULTS:
            content_hits, scanned = self._search_content(entries, needle, types)
            seen = {h.path for h in hits}
            for e in content_hits:
                if e.path not in seen:
                    hits.append(e)
                    seen.add(e.path)
            note = f"已扫描 {scanned} 个文件的内容"

        truncated = len(hits) > MAX_SEARCH_RESULTS
        hits = hits[:MAX_SEARCH_RESULTS]

        for e in hits:
            self._audit.append({"tool": "search_files", "path": e.path, "query": query})

        payload = {
            "count": len(hits),
            "truncated": truncated,
            "files": [
                {
                    "path": e.path,
                    "name": e.name,
                    "type": e.ext.lstrip("."),
                    "size": e.size,
                    "mtime": _fmt_time(e.mtime),
                }
                for e in hits
            ],
        }
        if note:
            payload["note"] = note
        if truncated:
            payload["note"] = (payload.get("note", "") + " 结果过多，已截断。").strip()
        return json.dumps(payload, ensure_ascii=False)

    def _search_content(self, entries: list[FileEntry], needle: str,
                        types: set[str]) -> tuple[list[FileEntry], int]:
        """在文件内容里找关键词。有字节预算，命中即停。"""
        if not needle:
            return [], 0

        hits: list[FileEntry] = []
        scanned = 0
        budget = _CONTENT_MAX_SCAN_BYTES
        for e in entries:
            if types and e.ext not in types:
                continue
            if e.size > _CONTENT_MAX_FILE_BYTES:
                continue
            if budget <= 0 or len(hits) >= MAX_SEARCH_RESULTS:
                break
            budget -= min(e.size, _CONTENT_MAX_FILE_BYTES)
            scanned += 1
            result = local_fs.read(e.path, 1, 500)
            if result.error:
                continue
            if needle in result.text.casefold():
                hits.append(e)
        return hits, scanned

    # ── read_file ────────────────────────────

    def _read(self, args: dict) -> str:
        roots = self.roots()
        path = str(args.get("path") or "").strip()
        if not path:
            return _err("缺少 path 参数")
        if not roots:
            return _err("用户尚未授权任何文件夹")

        path = os.path.abspath(path)

        if not local_fs.is_within(path, roots):
            return _err("路径不在用户授权的文件夹范围内，已拒绝")
        if not local_fs.is_supported(path):
            return _err("不支持的文件类型（仅支持 txt / md / json / jsonl / docx）")

        try:
            start = max(1, int(args.get("start_line") or 1))
            max_lines = min(500, max(1, int(args.get("max_lines") or 200)))
        except (TypeError, ValueError):
            return _err("start_line / max_lines 必须是整数")

        key = (os.path.normcase(path), start, max_lines)
        if key in self._read_ranges:
            return _err("该文件的这个行范围本轮已经读取过，请直接使用已有内容")
        if path not in self._turn_files and len(self._turn_files) >= MAX_FILES_PER_TURN:
            return _err(f"本轮已读取 {MAX_FILES_PER_TURN} 个不同文件，达到上限")
        if self._turn_bytes >= MAX_TURN_BYTES:
            return _err("本轮工具结果已达上限，请基于已有信息作答")

        result = local_fs.read(path, start, max_lines)
        if result.error:
            return _err(result.error)

        text = result.text
        encoded = len(text.encode("utf-8"))
        if encoded > MAX_RESULT_BYTES:
            text = _truncate_utf8(text, MAX_RESULT_BYTES)
            result.truncated = True
            result.warnings.append("内容过长，已截断；请用 start_line 读取后续部分")

        self._read_ranges.add(key)
        self._turn_files.add(path)
        self._turn_bytes += len(text.encode("utf-8"))
        self._audit.append(
            {"tool": "read_file", "path": path,
             "lines": [result.start_line, result.end_line]}
        )

        payload = {
            "path": path,
            "start_line": result.start_line,
            "end_line": result.end_line,
            "total_lines": result.total_lines,
            "truncated": result.truncated,
            "encoding": result.encoding,
            "content": text,
        }
        if result.warnings:
            payload["warnings"] = result.warnings
        return json.dumps(payload, ensure_ascii=False)

    # ── 索引 ─────────────────────────────────

    def _ensure_index(self) -> list[FileEntry]:
        roots = tuple(self.roots())
        fresh = (
            self._entries
            and roots == self._indexed_roots
            and (self._clock() - self._indexed_at) < INDEX_TTL_SECONDS
        )
        if fresh:
            return self._entries

        entries, stats = local_fs.scan(
            list(roots), max_files=MAX_INDEX_ENTRIES, should_stop=None
        )
        self._entries = entries
        self._stats = stats
        self._indexed_at = self._clock()
        self._indexed_roots = roots
        return entries

    def index_stats(self) -> dict:
        return {
            "files": self._stats.files,
            "skipped": self._stats.skipped,
            "errors": self._stats.errors,
            "truncated": self._stats.truncated,
        }

    def invalidate(self) -> None:
        self._entries = []
        self._indexed_at = 0.0


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

def _err(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _normalize_types(raw) -> set[str]:
    """把 ["txt", "md"] 归一成 {".txt", ".md"}；非法值忽略。"""
    if not raw:
        return set()
    out: set[str] = set()
    for item in raw if isinstance(raw, list) else [raw]:
        ext = "." + str(item).strip().lstrip(".").lower()
        if ext in local_fs.ALLOWED_EXTS:
            out.add(ext)
    return out


def _fmt_time(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return ""


def _truncate_utf8(text: str, limit: int) -> str:
    data = text.encode("utf-8")
    return data[:limit].decode("utf-8", errors="ignore")
