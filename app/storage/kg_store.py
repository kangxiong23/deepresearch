# Layer: Storage
# File: app/storage/kg_store.py
# Responsibility: KGLite 知识图谱的存取封装。
#                 负责节点（实体）和边（关系）的增删查，以及持久化。
#                 只处理存取细节，不含任何业务逻辑。
# Input:  KGEntity / KGRelation 数据对象
# Output: 查询结果列表
# 禁止: 业务判断、导入 UI 库、调用 DeepSeek API

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import config as app_config


# ──────────────────────────────────────────────
# 领域数据对象（图谱专用，在此定义）
# ──────────────────────────────────────────────

@dataclass
class KGEntity:
    """知识图谱节点（实体）。"""
    id: str
    name: str                        # 实体名称，如"小明"
    entity_type: str                 # 实体类型，如"人物"/"技术"/"机构"
    description: str = ""            # 补充描述
    source_conversation_id: str = "" # 来源对话 ID
    created_at: str = ""             # ISO 8601 时间字符串
    properties: dict = field(default_factory=dict)


@dataclass
class KGRelation:
    """知识图谱边（关系）。"""
    id: str
    source_entity_name: str          # 起点实体名称
    target_entity_name: str          # 终点实体名称
    relation_type: str               # 关系类型，如"学习"/"就读于"
    description: str = ""
    source_conversation_id: str = ""
    created_at: str = ""
    properties: dict = field(default_factory=dict)


@dataclass
class KGQueryResult:
    """图谱查询结果，用于注入上下文。"""
    entity_name: str
    relation_type: str
    related_entity_name: str
    description: str = ""
    source_conversation_id: str = ""


# ──────────────────────────────────────────────
# KGStore
# ──────────────────────────────────────────────

class KGStore:
    """
    KGLite 知识图谱存取层。

    存储方式：JSON 文件（entities.json + relations.json）
    路径：config.KG_STORE_PATH

    为什么不直接用 KGLite？
    KGLite 目前的嵌入式模式在某些 Python 3.14 环境下有兼容性问题。
    本实现用 JSON 文件实现等价的存取语义，接口与 KGLite 对齐，
    后续可无缝切换为真正的 KGLite 调用（只需替换本文件的 I/O 部分）。
    """

    def __init__(self) -> None:
        self._base = Path(app_config.KG_STORE_PATH)
        self._base.mkdir(parents=True, exist_ok=True)
        self._entities_path  = self._base / "entities.json"
        self._relations_path = self._base / "relations.json"

    # ── 实体（节点）────────────────────────────

    def save_entity(self, entity: KGEntity) -> None:
        """保存或更新实体（以 name 为唯一键 UPSERT）。"""
        entities = self._load_entities_raw()
        replaced = False
        for i, e in enumerate(entities):
            if e.get("name") == entity.name:
                entities[i] = _entity_to_dict(entity)
                replaced = True
                break
        if not replaced:
            entities.append(_entity_to_dict(entity))
        self._write_json(self._entities_path, entities)

    def get_entities(self) -> list[KGEntity]:
        """获取所有实体。"""
        return [_dict_to_entity(d) for d in self._load_entities_raw()]

    def find_entity(self, name: str) -> KGEntity | None:
        """按名称查找实体，不区分大小写。"""
        for d in self._load_entities_raw():
            if d.get("name", "").lower() == name.lower():
                return _dict_to_entity(d)
        return None

    def delete_entity(self, name: str) -> None:
        """删除实体（同时删除相关边）。"""
        entities = [e for e in self._load_entities_raw()
                    if e.get("name") != name]
        self._write_json(self._entities_path, entities)
        # 同时删除相关边
        relations = [r for r in self._load_relations_raw()
                     if r.get("source_entity_name") != name
                     and r.get("target_entity_name") != name]
        self._write_json(self._relations_path, relations)

    # ── 关系（边）──────────────────────────────

    def save_relation(self, relation: KGRelation) -> None:
        """
        保存关系（以 source+target+type 为唯一键 UPSERT）。
        相同的关系不重复存储。
        """
        relations = self._load_relations_raw()
        replaced = False
        for i, r in enumerate(relations):
            if (r.get("source_entity_name") == relation.source_entity_name
                    and r.get("target_entity_name") == relation.target_entity_name
                    and r.get("relation_type") == relation.relation_type):
                relations[i] = _relation_to_dict(relation)
                replaced = True
                break
        if not replaced:
            relations.append(_relation_to_dict(relation))
        self._write_json(self._relations_path, relations)

    def get_relations(self) -> list[KGRelation]:
        """获取所有关系。"""
        return [_dict_to_relation(d) for d in self._load_relations_raw()]

    def find_relations_by_entity(self, entity_name: str) -> list[KGRelation]:
        """查找与某实体相关的所有关系（作为起点或终点）。"""
        name_lower = entity_name.lower()
        return [
            _dict_to_relation(r)
            for r in self._load_relations_raw()
            if r.get("source_entity_name", "").lower() == name_lower
            or r.get("target_entity_name", "").lower() == name_lower
        ]

    def delete_relation(self, relation_id: str) -> None:
        """按 ID 删除关系。"""
        relations = [r for r in self._load_relations_raw()
                     if r.get("id") != relation_id]
        self._write_json(self._relations_path, relations)

    # ── 图谱查询（用于上下文注入）──────────────

    def query_related_knowledge(
        self,
        keywords: list[str],
        max_results: int = 10,
    ) -> list[KGQueryResult]:
        """
        根据关键词列表查询相关知识。
        关键词匹配实体名称（模糊匹配），返回相关的关系三元组。

        Args:
            keywords:    关键词列表，来自用户当前消息的分词
            max_results: 最多返回条目数

        Returns:
            list[KGQueryResult] — 按相关度排序
        """
        if not keywords:
            return []

        keywords_lower = [k.lower() for k in keywords]
        all_relations = self._load_relations_raw()
        scored: list[tuple[int, dict]] = []

        for r in all_relations:
            src = r.get("source_entity_name", "").lower()
            tgt = r.get("target_entity_name", "").lower()
            score = 0
            for kw in keywords_lower:
                if kw in src or src in kw:
                    score += 2
                if kw in tgt or tgt in kw:
                    score += 1
            if score > 0:
                scored.append((score, r))

        # 按得分降序排列
        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for _, r in scored[:max_results]:
            results.append(KGQueryResult(
                entity_name=r.get("source_entity_name", ""),
                relation_type=r.get("relation_type", ""),
                related_entity_name=r.get("target_entity_name", ""),
                description=r.get("description", ""),
                source_conversation_id=r.get("source_conversation_id", ""),
            ))
        return results

    def get_all_entity_names(self) -> list[str]:
        """返回所有实体名称列表，供关键词匹配使用。"""
        return [d.get("name", "") for d in self._load_entities_raw()]

    def get_stats(self) -> dict:
        """返回图谱统计信息。"""
        return {
            "entity_count": len(self._load_entities_raw()),
            "relation_count": len(self._load_relations_raw()),
        }

    # ── 内部 I/O ──────────────────────────────

    def _load_entities_raw(self) -> list[dict]:
        return self._read_json(self._entities_path)

    def _load_relations_raw(self) -> list[dict]:
        return self._read_json(self._relations_path)

    @staticmethod
    def _read_json(path: Path) -> list[dict]:
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    @staticmethod
    def _write_json(path: Path, data: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────
# 序列化 / 反序列化
# ──────────────────────────────────────────────

def _entity_to_dict(e: KGEntity) -> dict:
    return {
        "id": e.id,
        "name": e.name,
        "entity_type": e.entity_type,
        "description": e.description,
        "source_conversation_id": e.source_conversation_id,
        "created_at": e.created_at or datetime.utcnow().isoformat(),
        "properties": e.properties,
    }


def _dict_to_entity(d: dict) -> KGEntity:
    return KGEntity(
        id=d.get("id", str(uuid.uuid4())),
        name=d.get("name", ""),
        entity_type=d.get("entity_type", "未知"),
        description=d.get("description", ""),
        source_conversation_id=d.get("source_conversation_id", ""),
        created_at=d.get("created_at", ""),
        properties=d.get("properties", {}),
    )


def _relation_to_dict(r: KGRelation) -> dict:
    return {
        "id": r.id,
        "source_entity_name": r.source_entity_name,
        "target_entity_name": r.target_entity_name,
        "relation_type": r.relation_type,
        "description": r.description,
        "source_conversation_id": r.source_conversation_id,
        "created_at": r.created_at or datetime.utcnow().isoformat(),
        "properties": r.properties,
    }


def _dict_to_relation(d: dict) -> KGRelation:
    return KGRelation(
        id=d.get("id", str(uuid.uuid4())),
        source_entity_name=d.get("source_entity_name", ""),
        target_entity_name=d.get("target_entity_name", ""),
        relation_type=d.get("relation_type", ""),
        description=d.get("description", ""),
        source_conversation_id=d.get("source_conversation_id", ""),
        created_at=d.get("created_at", ""),
        properties=d.get("properties", {}),
    )