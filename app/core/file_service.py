# Layer: Core
# File: app/core/file_service.py
# Responsibility: 文件内容提取编排服务。
#                 决定"用哪个解析器处理哪种文件"，将提取结果交给 ContextService 注入。
#                 具体文件解析委托给 FileParserProtocol 实现。
# Input:  文件路径列表
# Output: list[(filename, content)] 提取结果，供 ConversationService 传给 ContextService
# 禁止: 直接读取文件字节、UI 导入

from __future__ import annotations

from app.core.protocols import FileParserProtocol


class FileService:
    """
    文件内容提取编排服务。

    编排职责：
    1. 遍历传入的文件列表
    2. 找到第一个 can_parse() 为 True 的解析器
    3. 调用该解析器的 extract_text() 获取文本内容
    4. 收集所有提取结果返回给调用方

    不将内容直接写入 ContextStore，由 ConversationService 决定是否注入。
    """

    def __init__(self, parsers: list[FileParserProtocol]) -> None:
        """
        Args:
            parsers: 文件解析器列表，按注册顺序匹配
        """
        self._parsers = parsers

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
            list of (filename, content)。
            无法解析的文件被跳过（不抛出异常，保证其他文件继续处理）。
        """
        print(f"[FileService] extract_files 收到路径: {file_paths}")
        results: list[tuple[str, str]] = []
        for path in file_paths:
            filename = _extract_filename(path)
            content = await self._parse_single(path, filename)
            if content:
                results.append((filename, content))
        return results

    # ──────────────────────────────────────────
    # 内部编排
    # ──────────────────────────────────────────

    async def _parse_single(self, path: str, filename: str) -> str:
        """
        用第一个能处理该文件的解析器提取文本。
        若无解析器支持，或解析过程出错，返回空串。
        """
        print(f"[FileService] 尝试解析: path={repr(path)} filename={repr(filename)}")
        parser = self._find_parser(filename)
        if parser is None:
            print(f"[FileService] 没有找到支持 {filename} 的解析器")
            return ""
        try:
            content = await parser.extract_text(path)
            print(f"[FileService] 解析结果长度: {len(content)} 字符")
            return content
        except Exception as e:
            print(f"[FileService] 解析异常: {e}")
            return ""

    def _find_parser(self, filename: str) -> FileParserProtocol | None:
        """返回第一个声明支持该文件的解析器，找不到返回 None。"""
        for parser in self._parsers:
            if parser.can_parse(filename):
                return parser
        return None


# ──────────────────────────────────────────────
# 模块级工具
# ──────────────────────────────────────────────

def _extract_filename(path: str) -> str:
    """从路径中提取文件名（跨平台）。"""
    import os
    return os.path.basename(path)