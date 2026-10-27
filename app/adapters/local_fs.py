# Layer: Adapter
# File: app/adapters/local_fs.py
# Responsibility: 本地文件的只读访问——扫描目录、按行读取、编码探测、格式提取。
#                 是"用户文件"的唯一打开点，只读约定全部集中在这里。
# Input:  目录列表 / 文件路径 / 行范围
# Output: FileEntry 列表、ReadResult
# 禁止: 业务决策（授权范围、预算、轮次）、UI 导入、任何写操作

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

# ──────────────────────────────────────────────
# 只读约定
#
# 1. 本模块是打开用户文件的唯一入口，一律 "rb"，不存在写模式；
# 2. 不在用户目录创建任何临时文件（与 json_io 的 mkstemp 策略相反）；
# 3. 不调用外部程序打开文件；
# 4. 云端占位文件拒绝读取（打开即触发下载，属于副作用）。
# ──────────────────────────────────────────────

ALLOWED_EXTS: frozenset[str] = frozenset({".txt", ".md", ".json", ".jsonl", ".docx"})

_EXT_ALIAS: dict[str, str] = {
    ".markdown": ".md",
    ".text": ".txt",
}

# 目录名黑名单（小写比较）。命中即整棵子树跳过。
_DENY_DIRS: frozenset[str] = frozenset({
    "$recycle.bin", "system volume information", "node_modules", ".git",
    "__pycache__", ".ssh", ".gnupg", "appdata", "windows", "winsxs",
    "program files", "program files (x86)", "programdata",
})

# Windows 文件属性位
_ATTR_REPARSE_POINT = 0x400
_ATTR_OFFLINE = 0x1000
_ATTR_RECALL_ON_DATA_ACCESS = 0x400000

MAX_FILE_BYTES = 64 * 1024 * 1024      # 单文件读取上限
MAX_JSON_BYTES = 8 * 1024 * 1024       # 超过则不整体解析
MAX_PRETTY_BYTES = 400 * 1024          # 美化后超过则只给结构摘要


# ──────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────

@dataclass(frozen=True)
class FileEntry:
    path: str
    name: str
    ext: str
    size: int
    mtime: float


@dataclass
class ScanStats:
    files: int = 0
    dirs: int = 0
    skipped: int = 0        # 黑名单目录 / 无权限 / 非目标类型
    errors: int = 0
    truncated: bool = False  # 达到 max_files 提前停止


@dataclass
class ReadResult:
    text: str = ""
    start_line: int = 1
    end_line: int = 0
    total_lines: int = 0
    encoding: str = ""
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)
    error: str = ""


# ──────────────────────────────────────────────
# 扫描
# ──────────────────────────────────────────────

def scan(
    roots: list[str],
    *,
    max_files: int = 200_000,
    max_depth: int = 12,
    should_stop=None,
    on_batch=None,
    batch_size: int = 200,
) -> tuple[list[FileEntry], ScanStats]:
    """
    遍历授权目录，收集受支持的文件。

    用 os.scandir 而非 os.walk：Windows 上 scandir 的 DirEntry 自带
    FindFirstFile 缓存，is_dir/is_file 不额外发系统调用，快数倍。

    不跟随 junction/符号链接——用户目录里的 "Application Data" 这类
    联接会形成环，跟随会无限递归。
    """
    entries: list[FileEntry] = []
    stats = ScanStats()
    stack: list[tuple[str, int]] = [(r, 0) for r in roots if r]

    while stack:
        if should_stop is not None and should_stop():
            break

        current, depth = stack.pop()

        try:
            it = os.scandir(current)
        except OSError:
            stats.errors += 1
            continue

        try:
            for de in it:
                if should_stop is not None and should_stop():
                    break
                try:
                    if _is_dir(de):
                        if depth >= max_depth:
                            stats.skipped += 1
                            continue
                        if _is_link_like(de) or de.name.lower() in _DENY_DIRS:
                            stats.skipped += 1
                            continue
                        stack.append((de.path, depth + 1))
                        stats.dirs += 1
                    else:
                        ext = _norm_ext(de.name)
                        if ext not in ALLOWED_EXTS:
                            stats.skipped += 1
                            continue
                        if len(entries) >= max_files:
                            stats.truncated = True
                            break
                        st = de.stat(follow_symlinks=False)
                        entries.append(FileEntry(
                            path=de.path,
                            name=de.name,
                            ext=ext,
                            size=int(st.st_size),
                            mtime=float(st.st_mtime),
                        ))
                        stats.files += 1
                        if on_batch is not None and len(entries) % batch_size == 0:
                            on_batch(len(entries))
                except OSError:
                    stats.errors += 1
        except OSError:
            stats.errors += 1
        finally:
            try:
                it.close()
            except Exception:
                pass

        if stats.truncated:
            break

    return entries, stats


def _is_dir(de) -> bool:
    try:
        return de.is_dir(follow_symlinks=False)
    except OSError:
        return False


def _is_link_like(de) -> bool:
    """junction / 符号链接 / 其他重解析点。"""
    is_junction = getattr(de, "is_junction", None)
    if is_junction is not None:
        try:
            if is_junction():
                return True
        except OSError:
            pass
    try:
        attrs = de.stat(follow_symlinks=False).st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attrs & _ATTR_REPARSE_POINT)


def _norm_ext(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return _EXT_ALIAS.get(ext, ext)


# ──────────────────────────────────────────────
# 路径校验
# ──────────────────────────────────────────────

def is_within(path: str, roots: list[str]) -> bool:
    """
    路径是否落在授权目录内（大小写不敏感，按真实路径比较）。

    用 realpath 解析后再比，可挡住软链接/.. 造成的逃逸。
    """
    try:
        real = os.path.normcase(os.path.realpath(path))
    except OSError:
        return False
    for root in roots:
        if not root:
            continue
        try:
            base = os.path.normcase(os.path.realpath(root))
        except OSError:
            continue
        if real == base or real.startswith(base + os.sep):
            return True
    return False


def is_supported(path: str) -> bool:
    return _norm_ext(path) in ALLOWED_EXTS


def inspect(path: str) -> tuple[dict, str]:
    """
    读取前的体检。返回 (元信息, 错误说明)。

    错误非空时调用方必须放弃读取。
    """
    try:
        st = os.stat(path)
    except OSError as e:
        return {}, f"无法访问文件：{e.strerror or e}"

    if not os.path.isfile(path):
        return {}, "路径不是文件"

    attrs = getattr(st, "st_file_attributes", 0)
    if attrs & (_ATTR_RECALL_ON_DATA_ACCESS | _ATTR_OFFLINE):
        return {}, "该文件是云端占位，尚未下载到本地（读取会触发下载，已拒绝）"

    ext = _norm_ext(path)
    if ext not in ALLOWED_EXTS:
        return {}, f"不支持的文件类型：{ext or '无扩展名'}"

    return {
        "path": path,
        "name": os.path.basename(path),
        "ext": ext,
        "size": int(st.st_size),
        "mtime": float(st.st_mtime),
    }, ""


# ──────────────────────────────────────────────
# 编码探测
# ──────────────────────────────────────────────

def detect_encoding(head: bytes) -> str:
    """
    探测文本编码。

    顺序很重要：中文 Windows 上 GB18030 几乎能"成功"解码任意字节序列，
    所以必须 UTF-8 先试；UTF-16 无 BOM 时先按 NUL 分布判断，否则会被
    GB18030 解成一堆乱码。
    """
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
        return "utf-16"

    if _looks_utf8(head):
        return "utf-8"

    if b"\x00" in head:
        even = head[0:len(head):2].count(0)
        odd = head[1:len(head):2].count(0)
        return "utf-16-le" if odd > even else "utf-16-be"

    try:
        head.decode("gb18030")
        return "gb18030"
    except UnicodeDecodeError:
        return "latin-1"


def _looks_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError as e:
        # 缓冲区尾部可能截断了一个多字节字符，不算失败
        return e.start >= max(0, len(data) - 4)


# ──────────────────────────────────────────────
# 读取
# ──────────────────────────────────────────────

def read(path: str, start_line: int = 1, max_lines: int = 200) -> ReadResult:
    """按类型分派读取，返回行范围窗口。"""
    meta, err = inspect(path)
    if err:
        return ReadResult(error=err)

    if meta["size"] > MAX_FILE_BYTES:
        return ReadResult(error=(
            f"文件过大（{meta['size'] // 1024 // 1024} MB），超过单次读取上限"))

    ext = meta["ext"]
    if ext == ".docx":
        return _read_docx(path, start_line, max_lines, meta)
    if ext == ".json":
        return _read_json(path, start_line, max_lines, meta)
    return _read_lines(path, start_line, max_lines, meta)


def _read_lines(path: str, start_line: int, max_lines: int, meta: dict) -> ReadResult:
    """逐行读取 txt / md / jsonl。不整体载入内存。"""
    with open(path, "rb") as f:
        head = f.read(65536)
    encoding = detect_encoding(head)

    start_line = max(1, int(start_line))
    max_lines = max(1, int(max_lines))

    if encoding.startswith("utf-16"):
        # UTF-16 的换行不对齐字节边界，必须整体解码后切行
        with open(path, "rb") as f:
            raw = f.read(MAX_FILE_BYTES)
        text = raw.decode(encoding, errors="replace")
        return _slice_lines(text.splitlines(), start_line, max_lines, encoding, meta,
                            truncated=len(raw) >= MAX_FILE_BYTES)

    picked: list[str] = []
    total = 0
    read_bytes = 0
    with open(path, "rb") as f:
        for raw_line in f:
            read_bytes += len(raw_line)
            total += 1
            if total >= start_line and len(picked) < max_lines:
                picked.append(raw_line.decode(encoding, errors="replace").rstrip("\r\n"))
            elif len(picked) >= max_lines and read_bytes > MAX_FILE_BYTES:
                break

    truncated = total > (start_line + len(picked) - 1)
    return ReadResult(
        text="\n".join(picked),
        start_line=start_line,
        end_line=start_line + len(picked) - 1 if picked else start_line - 1,
        total_lines=total,
        encoding=encoding,
        truncated=truncated,
        warnings=_encoding_warning(encoding),
    )


def _slice_lines(lines: list[str], start_line: int, max_lines: int,
                 encoding: str, meta: dict, truncated: bool = False) -> ReadResult:
    total = len(lines)
    picked = lines[start_line - 1: start_line - 1 + max_lines]
    return ReadResult(
        text="\n".join(picked),
        start_line=start_line,
        end_line=start_line + len(picked) - 1 if picked else start_line - 1,
        total_lines=total,
        encoding=encoding,
        truncated=truncated or (start_line - 1 + len(picked)) < total,
        warnings=_encoding_warning(encoding),
    )


def _encoding_warning(encoding: str) -> list[str]:
    if encoding in ("gb18030", "latin-1"):
        return [f"文件不是 UTF-8，已按 {encoding} 解码，个别字符可能有偏差"]
    return []


def _read_json(path: str, start_line: int, max_lines: int, meta: dict) -> ReadResult:
    """整体解析后美化；过大则只给顶层结构摘要。"""
    if meta["size"] > MAX_JSON_BYTES:
        return ReadResult(error=(
            f"JSON 超过 {MAX_JSON_BYTES // 1024 // 1024} MB，不整体解析；"
            "如需查看原文请用 read_file 按行读取（扩展名改为可读文本不适用）"))

    with open(path, "rb") as f:
        raw = f.read(MAX_JSON_BYTES)

    encoding = detect_encoding(raw[:65536])
    try:
        text = raw.decode(encoding, errors="replace")
    except Exception:
        return ReadResult(error="JSON 文件解码失败")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return ReadResult(error=f"JSON 解析失败：第 {e.lineno} 行第 {e.colno} 列 {e.msg}")
    except RecursionError:
        return ReadResult(error="JSON 嵌套过深，无法解析")

    warnings = _encoding_warning(encoding)
    pretty = json.dumps(data, ensure_ascii=False, indent=2)

    if len(pretty.encode("utf-8")) > MAX_PRETTY_BYTES:
        pretty = _json_outline(data)
        warnings.append("内容较大，仅显示顶层结构；需要细节请指定更窄的读取范围")

    result = _slice_lines(pretty.splitlines(), max(1, int(start_line)),
                          max(1, int(max_lines)), encoding, meta)
    result.warnings = warnings + result.warnings
    return result


def _json_outline(data, indent: int = 0) -> str:
    """生成结构摘要：类型、键名、数组长度。"""
    pad = "  " * indent
    if isinstance(data, dict):
        if not data:
            return pad + "{}"
        lines = [pad + "{"]
        for k, v in list(data.items())[:50]:
            if isinstance(v, dict):
                lines.append(pad + f"  {k!r}: object({len(v)} keys)")
            elif isinstance(v, list):
                lines.append(pad + f"  {k!r}: array({len(v)} items)")
            else:
                lines.append(pad + f"  {k!r}: {type(v).__name__}")
        if len(data) > 50:
            lines.append(pad + f"  ... 其余 {len(data) - 50} 个键省略")
        lines.append(pad + "}")
        return "\n".join(lines)
    if isinstance(data, list):
        return pad + f"[array, {len(data)} items]"
    return pad + type(data).__name__


def _read_docx(path: str, start_line: int, max_lines: int, meta: dict) -> ReadResult:
    """用 python-docx 提取段落与表格（只读打开 zip，不解压到磁盘）。"""
    try:
        import docx
    except ImportError:
        return ReadResult(error="缺少 python-docx，无法读取 .docx")

    try:
        doc = docx.Document(path)
    except Exception as e:
        return ReadResult(error=(
            f"无法解析 .docx（旧版 .doc 请另存为 .docx）：{e}"))

    lines: list[str] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            lines.append(text)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            joined = " | ".join(c for c in cells if c)
            if joined:
                lines.append(joined)

    warnings = [
        "docx 预览仅含正文与表格：页眉页脚、脚注、批注、修订痕迹、"
        "文本框与图片不包含在内",
    ]
    result = _slice_lines(lines, max(1, int(start_line)), max(1, int(max_lines)),
                          "", meta)
    result.warnings = warnings + result.warnings
    return result
