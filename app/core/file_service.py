# Layer: Core
# File: app/core/file_service.py
# Responsibility: 文件内容提取编排服务。
#                 决定"用哪个解析器处理哪种文件"，将提取结果交给 ContextService 注入。
#                 具体文件解析委托给 FileParserProtocol 实现。
# Input:  文件路径列表
# Output: list[(filename, content)] 提取结果，供 ConversationService 传给 ContextService
#         解析失败的文件返回一句可见的说明，**不会被静默丢弃**
# 禁止: 直接读取文件字节、UI 导入

from __future__ import annotations

from app.core.protocols import FileParserProtocol

# 解析失败的说明统一以这个前缀开头，挂载缓存据此拒绝缓存失败结果
_FAILURE_PREFIX = "[无法解析"


class FileService:
    """
    文件内容提取编排服务。

    编排职责：
    1. 遍历传入的文件列表
    2. 找到第一个 can_parse() 为 True 的解析器
    3. 调用该解析器的 extract_text() 获取文本内容
    4. 收集所有提取结果返回给调用方

    两条刻意行为：

    - **一个文件都不丢**。没有解析器、解析抛异常、解析结果为空，都会返回一句
      人话说明。静默丢弃会让用户看到"传了文件但模型没反应"——这类故障在界面
      上没有任何痕迹，只能翻日志，属于最坏的可观测性。
    - **失败结果不进挂载缓存**。挂载快照的解析缓存以内容哈希为键，失败说明一旦
      写进 <sha256>.txt 就永远不会重试；下次换了实现也还是那句旧提示。

    不将内容直接写入 ContextStore，由 ConversationService 决定是否注入。
    """

    def __init__(
        self,
        parsers: list[FileParserProtocol],
        unsupported_hints: dict[str, str] | None = None,
    ) -> None:
        """
        Args:
            parsers:            文件解析器列表，按注册顺序匹配
            unsupported_hints:  "{扩展名: 处置建议}"，用于"没有解析器"时给出
                                可操作提示（如 .doc → 请另存为 .docx）。
                                格式知识属于适配器层，由 main.py 注入。
        """
        self._parsers = parsers
        self._unsupported_hints = dict(unsupported_hints or {})

    # ──────────────────────────────────────────
    # 主接口
    # ──────────────────────────────────────────

    async def extract_files(
        self, file_paths: list[str]
    ) -> list[tuple[str, str]]:
        """
        批量提取文件文本内容。

        Args:
            file_paths: 文件路径列表（绝对路径）

        Returns:
            list of (filename, content)，与输入**一一对应**。
            解析不出来的项，content 是一句以 "[无法解析…" 开头的说明，
            调用方原样注入即可（模型看得见、用户也看得见）。
        """
        print(f"[FileService] extract_files 收到路径: {file_paths}")
        results: list[tuple[str, str]] = []
        for path in file_paths:
            filename = _extract_filename(path)
            results.append((filename, await self._parse_single(path, filename)))
        return results

    # ──────────────────────────────────────────
    # 内部编排
    # ──────────────────────────────────────────

    async def _parse_single(self, path: str, filename: str) -> str:
        """用第一个能处理该文件的解析器提取文本；任何失败都返回可见说明。"""
        print(f"[FileService] 尝试解析: path={repr(path)} filename={repr(filename)}")
        parser = self._find_parser(filename)
        if parser is None:
            return self._unsupported_note(filename)

        try:
            content = await parser.extract_text(path)
        except Exception as e:
            print(f"[FileService] 解析异常: {e}")
            return f"[无法解析「{filename}」：解析过程出错（{type(e).__name__}）]"

        if not content or not content.strip():
            return f"[「{filename}」没有可提取的文字内容]"

        print(f"[FileService] 解析结果长度: {len(content)} 字符")
        return content

    def _find_parser(self, filename: str) -> FileParserProtocol | None:
        """返回第一个声明支持该文件的解析器，找不到返回 None。"""
        for parser in self._parsers:
            if parser.can_parse(filename):
                return parser
        return None

    def _unsupported_note(self, filename: str) -> str:
        """没有解析器时的说明：优先给可操作的处置建议。"""
        extension = _ext_of(filename)
        hint = self._unsupported_hints.get(extension, "")
        if hint:
            return f"[无法解析「{filename}」：{hint}]"
        kind = extension or "（无扩展名）"
        return f"[无法解析「{filename}」：不支持的文件类型 {kind}]"

    # ──────────────────────────────────────────
    # 同步提取（上下文构建等同步路径用）
    # ──────────────────────────────────────────

    def extract_text_sync(self, path: str, filename: str | None = None) -> str:
        """
        同步提取单个文件的文本。

        解析器都提供 extract_text_sync；老的自定义解析器若只有异步版本，
        在没有运行中的事件循环时也能被驱动完成（它们内部不含 await）。
        这样上下文构建（同步）与发送链路（异步）共用同一套解析器，
        docx/pdf 不会再被当成裸 UTF-8 读出乱码。

        失败时同样返回可见说明（调用方通常会把它包进 [附件：…] 段），
        不再返回空串。

        Args:
            path:     文件绝对路径
            filename: 用于选解析器的文件名（默认取路径 basename）。
                      挂载快照的路径没有扩展名，必须靠展示名路由。
        """
        name = filename or _extract_filename(path)
        parser = self._find_parser(name)
        if parser is None:
            return self._unsupported_note(name)

        sync_fn = getattr(parser, "extract_text_sync", None)
        if callable(sync_fn):
            try:
                return sync_fn(path) or ""
            except Exception as e:
                print(f"[FileService] 同步解析异常: {e}")
                return f"[无法解析「{name}」：解析过程出错（{type(e).__name__}）]"

        return _drive_coroutine(parser.extract_text(path))

    def extract_embedded_images(
        self,
        path: str,
        filename: str | None = None,
        limit: int = 8,
    ) -> list[bytes]:
        """
        取文档内嵌图片的原始字节（目前只有 docx 支持）。拿不到就返回空表。

        只负责搬运字节；能不能发、发多大由 Core 的 image_attachments 判断
        （适配器不该知道模型的图片限制）。
        """
        name = filename or _extract_filename(path)
        parser = self._find_parser(name)
        if parser is None:
            return []

        extractor = getattr(parser, "extract_images_sync", None)
        if not callable(extractor):
            return []

        try:
            images = extractor(path) or []
        except Exception as e:
            print(f"[FileService] 提取内嵌图片异常: {e}")
            return []
        return list(images[: max(0, limit)])

    def read_mount_text(self, blob_store, mount: dict) -> str:
        """
        读节点挂载文件的文本：优先用解析缓存，缺失时解析 blob 并回写缓存。

        blob 内容寻址且不可变，所以缓存永远不需要失效逻辑 —— 前提是缓存里
        存的是**解析成功**的结果。失败说明（"[无法解析…"）不写缓存，
        否则一次偶发失败会被固定下来，永远不再重试。
        """
        blob_id = str((mount or {}).get("blob_id") or "")
        name = str((mount or {}).get("name") or "")
        if not blob_id or blob_store is None:
            return ""

        cached = blob_store.read_cached_text(blob_id)
        if cached is not None:
            return cached

        path = blob_store.blob_path(blob_id)
        if not path.exists():
            print(f"[FileService] 挂载快照缺失: {blob_id[:12]} ({name})")
            return ""

        text = self.extract_text_sync(str(path), name)
        if text and not text.lstrip().startswith(_FAILURE_PREFIX):
            blob_store.write_cached_text(blob_id, text)
        return text


# ──────────────────────────────────────────────
# 模块级工具
# ──────────────────────────────────────────────

def _extract_filename(path: str) -> str:
    """从路径中提取文件名（跨平台）。"""
    import os
    return os.path.basename(path)


def _ext_of(filename: str) -> str:
    """小写扩展名，没有则返回空串。"""
    import os
    return os.path.splitext(filename or "")[1].lower()


def _drive_coroutine(coro) -> str:
    """
    在同步上下文里跑完一个不含 await 的协程（老式纯异步解析器的兜底）。

    刻意不用 asyncio.run：上下文构建可能发生在事件循环内部，那样会直接报错。
    """
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value or ""
    except Exception as e:
        print(f"[FileService] 解析协程异常: {e}")
        return ""
    finally:
        try:
            coro.close()
        except Exception:
            pass
    # 协程真的 yield 了：说明它是流式的，同步路径无法安全驱动
    print("[FileService] 解析器返回了未完成的协程，已放弃")
    return ""
