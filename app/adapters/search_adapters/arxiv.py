# Layer: Adapter
# File: app/adapters/search_adapters/arxiv.py
# Responsibility: 实现 Arxiv API 搜索适配器，返回学术论文摘要。
#                 使用 Arxiv 官方 Atom Feed API（无需 API Key）。
# Input:  query: str, max_results: int
# Output: SearchResults（含论文标题、摘要、链接）
# 禁止: 业务逻辑、编排、导入 UI 库

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import httpx

from app.adapters.search_adapters.base import BaseSearchAdapter
from app.storage.models import SearchResult, SearchResults
from app.utils.async_utils import get_logger

logger = get_logger("ADPTR", "arxiv")

# Arxiv Atom API namespace
_NS = {"atom": "http://www.w3.org/2005/Atom"}
_ARXIV_API = "https://export.arxiv.org/api/query"


class ArxivSearchAdapter(BaseSearchAdapter):
    """
    Arxiv 学术搜索适配器。

    调用 Arxiv 官方 Atom Feed REST API，解析论文条目。
    无需 API Key，但建议控制请求频率（Arxiv 限速约 3 req/s）。

    搜索字段：标题 + 摘要（ti+abs），适合学术关键词查询。
    """

    async def search(self, query: str, max_results: int = 5) -> SearchResults:
        """
        搜索 Arxiv 论文。

        Args:
            query:       搜索关键词（支持 Arxiv 语法，如 "ti:transformer"）
            max_results: 最多返回条目数（Arxiv API 上限 100）

        Returns:
            SearchResults — 含论文标题、摘要前 300 字、PDF 链接
        """
        if not query.strip():
            return SearchResults(query=query, results=[], total=0)

        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": min(max_results, 20),  # 避免超大响应
            "sortBy": "relevance",
            "sortOrder": "descending",
        }

        try:
            xml_text = await self._fetch_feed(params)
            results = _parse_arxiv_feed(xml_text, max_results)
            logger.debug("arxiv query=%r returned %d results", query, len(results))
            return SearchResults(query=query, results=results, total=len(results))

        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.warning("arxiv search failed: %s", e)
            return SearchResults(query=query, results=[], total=0)

    async def _fetch_feed(self, params: dict) -> str:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=8.0, read=20.0, write=5.0, pool=5.0),
            follow_redirects=True,
        ) as client:
            resp = await client.get(_ARXIV_API, params=params)
            resp.raise_for_status()
            return resp.text


# ──────────────────────────────────────────────
# Atom Feed 解析（模块私有）
# ──────────────────────────────────────────────

def _parse_arxiv_feed(xml_text: str, max_results: int) -> list[SearchResult]:
    """解析 Arxiv Atom XML，提取论文条目。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    results: list[SearchResult] = []
    entries = root.findall("atom:entry", _NS)

    for entry in entries[:max_results]:
        title_el = entry.find("atom:title", _NS)
        summary_el = entry.find("atom:summary", _NS)
        id_el = entry.find("atom:id", _NS)

        title = _clean_whitespace(title_el.text) if title_el is not None else "无标题"
        summary = _clean_whitespace(summary_el.text) if summary_el is not None else ""
        arxiv_url = id_el.text.strip() if id_el is not None else ""

        # 将 abs URL 转换为 PDF URL
        pdf_url = arxiv_url.replace("abs", "pdf") if "abs" in arxiv_url else arxiv_url

        # 提取 arXiv ID 作为 source 标识
        arxiv_id = re.search(r"\d{4}\.\d+", arxiv_url)
        source_tag = f"arxiv:{arxiv_id.group()}" if arxiv_id else "arxiv"

        results.append(SearchResult(
            title=title,
            url=pdf_url,
            snippet=summary[:300] + ("…" if len(summary) > 300 else ""),
            source=source_tag,
        ))

    return results


def _clean_whitespace(text: str | None) -> str:
    """合并多余空白（Atom 摘要常含换行缩进）。"""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()
