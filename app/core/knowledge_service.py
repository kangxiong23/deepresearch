# Layer: Core
# File: app/core/knowledge_service.py
# Responsibility: 知识图谱业务编排。
#                 决定"做什么"：
#                 - 调用 DeepSeek 从对话中提取实体和关系
#                 - 把提取结果写入 KGStore
#                 - 根据当前消息查询图谱，返回相关知识供注入
# Input:  对话消息列表、当前用户消息
# Output: 提取结果摘要（str）、相关知识列表（list[KGQueryResult]）
# 禁止: 直接 HTTP 调用、导入 UI 库、直接操作 JSON 文件

from __future__ import annotations

import json
import re
import uuid

import config as app_config
from config import ConfigScope
from app.storage.kg_store import KGStore, KGEntity, KGRelation, KGQueryResult
from app.storage.models import utcnow


# ── 提取用的 Prompt ────────────────────────────────────────────────

_EXTRACT_SYSTEM_PROMPT = """你是一个知识图谱提取助手。
从给定的对话中提取所有有意义的实体和关系，以 JSON 格式返回。

重要规则：
- 对话中"用户"说的"我"，在图谱中统一用"用户"作为实体名
- 例如用户说"我叫小明" → 提取关系：用户 --[名字是]--> 小明
- 例如用户说"我在学Python" → 提取关系：用户 --[正在学习]--> Python
- 例如用户说"我就读于XX大学" → 提取关系：用户 --[就读于]--> XX大学

输出格式（严格遵守，只输出 JSON，不要有任何额外文字）：
{
  "entities": [
    {"name": "实体名称", "type": "实体类型", "description": "简短描述"}
  ],
  "relations": [
    {"source": "起点实体名", "relation": "关系类型", "target": "终点实体名", "description": "简短描述"}
  ]
}

实体类型参考：人物、技术、机构、地点、概念、产品、事件

关系类型参考：名字是、正在学习、就读于、工作于、位于、属于、包含、开发、研究、认识、喜欢、使用

注意：
- 实体名称要具体，如"Python"而不是"编程语言"
- 用户的个人信息（姓名、学校、职业等）必须提取
- 如果对话中没有有价值的实体关系，返回 {"entities": [], "relations": []}
"""


class KnowledgeService:
    """
    知识图谱编排服务。

    职责：
    - extract_and_save：调用 LLM 提取实体关系，写入图谱
    - query_for_context：根据关键词查询图谱，返回可注入的知识

    注入依赖：
        kg_store:   KGStore 实例
        llm_client: LLMClientProtocol（复用 deepseek_client）
    """

    def __init__(self, kg_store: KGStore, llm_client) -> None:
        self._store = kg_store
        self._llm = llm_client

    # ── 提取并保存（手动触发）──────────────────

    async def extract_and_save(
        self,
        messages: list[dict],
        conversation_id: str,
    ) -> str:
        """
        从最近几条消息中提取实体和关系，写入知识图谱。

        Args:
            messages:        OpenAI 格式消息列表 [{"role": ..., "content": ...}]
            conversation_id: 来源对话 ID，用于溯源

        Returns:
            提取结果摘要字符串，如"提取到 3 个实体，5 条关系"
        """
        if not messages:
            return "没有消息可以提取"

        # 取最近 N 条（由 config 控制）
        recent = messages[-app_config.kg_extract_recent_n:]

        # 构建提取请求的 messages
        conversation_text = self._format_messages_for_extraction(recent)
        extract_messages = [
            {"role": "user", "content": f"请从以下对话中提取实体和关系：\n\n{conversation_text}"}
        ]

        # 调用 LLM 提取
        raw_json = await self._call_llm_for_extraction(extract_messages)
        if not raw_json:
            return "提取失败：LLM 未返回有效结果"

        # 解析 JSON
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            # 尝试从文本中提取 JSON
            match = re.search(r'\{.*\}', raw_json, re.DOTALL)
            if not match:
                return f"提取失败：无法解析 JSON\n原始输出：{raw_json[:200]}"
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                return "提取失败：JSON 格式错误"

        entities = data.get("entities", [])
        relations = data.get("relations", [])

        if not entities and not relations:
            return "本段对话未发现有价值的实体关系"

        # 写入图谱
        now = utcnow().isoformat()
        saved_entities = 0
        saved_relations = 0

        for e in entities:
            name = e.get("name", "").strip()
            if not name:
                continue
            entity = KGEntity(
                id=str(uuid.uuid4()),
                name=name,
                entity_type=e.get("type", "未知"),
                description=e.get("description", ""),
                source_conversation_id=conversation_id,
                created_at=now,
            )
            self._store.save_entity(entity)
            saved_entities += 1
            print(f"  [KG] 实体: {name} ({entity.entity_type})")

        for r in relations:
            src = r.get("source", "").strip()
            tgt = r.get("target", "").strip()
            rel = r.get("relation", "").strip()
            if not src or not tgt or not rel:
                continue
            relation = KGRelation(
                id=str(uuid.uuid4()),
                source_entity_name=src,
                target_entity_name=tgt,
                relation_type=rel,
                description=r.get("description", ""),
                source_conversation_id=conversation_id,
                created_at=now,
            )
            self._store.save_relation(relation)
            saved_relations += 1
            print(f"  [KG] 关系: {src} --[{rel}]--> {tgt}")

        stats = self._store.get_stats()
        return (
            f"✓ 提取完成：新增 {saved_entities} 个实体，{saved_relations} 条关系\n"
            f"  图谱现有：{stats['entity_count']} 个实体，{stats['relation_count']} 条关系"
        )

    # ── 查询图谱（自动触发，每次发消息时）────────

    def query_for_context(self, user_message: str) -> list[KGQueryResult]:
        """
        根据用户消息内容查询图谱，返回相关知识。

        策略：
        1. 从用户消息中提取关键词（简单分词）
        2. 同时把图谱中已知的所有实体名称加入匹配
        3. 调用 KGStore.query_related_knowledge 返回相关三元组

        Args:
            user_message: 用户当前输入的消息文本

        Returns:
            list[KGQueryResult] — 相关知识列表
        """
        if not app_config.kg_injection_enabled:
            return []
        if not user_message.strip():
            return []

        # 关键词：用户消息分词 + 图谱中已知的实体名称
        keywords = _extract_keywords(user_message)
        entity_names = self._store.get_all_entity_names()

        # 把图谱实体名也加入关键词（提升召回率）
        all_keywords = list(set(keywords + entity_names))

        return self._store.query_related_knowledge(
            keywords=all_keywords,
            max_results=app_config.kg_max_inject,
        )

    def format_knowledge_for_injection(
        self, results: list[KGQueryResult]
    ) -> str:
        """
        将查询结果格式化为注入 system prompt 的文本。

        Args:
            results: query_for_context 返回的结果

        Returns:
            格式化文本，如果没有结果返回空串
        """
        if not results:
            return ""

        lines = ["[关于当前用户的已知背景信息（来自知识图谱，请在回答中主动使用这些信息）]"]
        for r in results:
            line = f"- {r.entity_name} [{r.relation_type}] {r.related_entity_name}"
            if r.description:
                line += f"（{r.description}）"
            lines.append(line)

        return "\n".join(lines)

    def get_stats(self) -> dict:
        """返回图谱统计信息。"""
        return self._store.get_stats()

    # ── 内部辅助 ──────────────────────────────

    async def _call_llm_for_extraction(
        self, messages: list[dict]
    ) -> str:
        """
        调用 LLM 执行提取，收集完整响应。
        使用 deepseek-v4-flash（不用 reasoner，节省 token）。
        """
        from app.storage.models import LLMContext, ChunkType

        context = LLMContext(
            messages=messages,
            system_prompt=_EXTRACT_SYSTEM_PROMPT,
            max_tokens=1024,
            temperature=0.2,  # 低温度，输出更稳定
        )

        full_text = ""
        # 强制使用 chat 模型提取，不受当前 model_type 影响（ConfigScope 自动恢复）
        with ConfigScope(model_type="deepseek-v4-flash"):
            async for chunk in self._llm.stream_chat(context):
                if chunk.chunk_type == ChunkType.TEXT:
                    full_text += chunk.delta
                if chunk.is_done:
                    break

        return full_text.strip()

    @staticmethod
    def _format_messages_for_extraction(messages: list[dict]) -> str:
        """将消息列表格式化为适合提取的纯文本。"""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            # 跳过 system 消息
            if role == "system":
                continue
            prefix = "用户" if role == "user" else "AI"
            # 截断过长内容（文件内容等）
            if len(content) > 500:
                content = content[:500] + "..."
            lines.append(f"{prefix}: {content}")
        return "\n".join(lines)


# ── 模块级辅助 ────────────────────────────────

def _extract_keywords(text: str) -> list[str]:
    """
    简单关键词提取：
    - 去除标点符号
    - 按空格和常见中文分隔符切分
    - 过滤长度 < 2 的词和停用词
    """
    # 替换标点为空格
    cleaned = re.sub('[，。！？、；：\u201c\u201d\u2018\u2019（）【】\t\n\r ]+', ' ', text)
    # 分词（简单按空格）
    tokens = [t.strip() for t in cleaned.split() if t.strip()]
    # 过滤停用词和短词
    stopwords = {
        '的', '了', '在', '是', '我', '有', '和', '就', '不', '人',
        '都', '一', '一个', '上', '也', '很', '到', '说', '要', '去',
        '你', '吗', '吧', '呢', '啊', '这', '那', '什么', '怎么', '为什么',
        '可以', '如何', '请问', '帮我', '告诉', '介绍', '分析', '解释',
    }
    return [t for t in tokens if len(t) >= 2 and t not in stopwords]