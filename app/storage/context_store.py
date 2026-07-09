# Layer: Storage
# File: app/storage/context_store.py
# Responsibility: 上下文块（ContextBlock）和模板（ContextTemplate）的 JSON 文件持久化。
#                 实现 Core 层定义的 ContextStoreProtocol。
#                 只负责读写 JSON 文件与对象序列化，不含业务逻辑。
# Input:  ContextBlock / ContextTemplate 领域对象
# Output: 领域对象列表或 None
# 禁止: 业务判断、编排逻辑、导入 UI 库

from __future__ import annotations

import json
from pathlib import Path

import config as app_config
from app.storage.models import ContextBlock, ContextSource, ContextTemplate


class ContextStore:
    """
    上下文块与模板的 JSON 文件存储。

    目录结构：
        {CONTEXT_STORE_PATH}/
            blocks.json      ← 所有 ContextBlock 列表
            templates.json   ← 所有 ContextTemplate 列表

    JSON 格式为列表，每次完整读写（数据量小，无需 append 优化）。
    """

    def __init__(self) -> None:
        self._base = Path(app_config.CONTEXT_STORE_PATH)
        self._base.mkdir(parents=True, exist_ok=True)
        self._blocks_path = self._base / "blocks.json"
        self._templates_path = self._base / "templates.json"

    # ──────────────────────────────────────────
    # ContextBlock CRUD
    # ──────────────────────────────────────────

    def save_block(self, block: ContextBlock) -> None:
        """保存或更新上下文块（以 id 为键 UPSERT）。"""
        blocks = self._load_blocks_raw()
        # 替换已有条目，否则追加
        replaced = False
        for i, b in enumerate(blocks):
            if b.get("id") == block.id:
                blocks[i] = _block_to_dict(block)
                replaced = True
                break
        if not replaced:
            blocks.append(_block_to_dict(block))
        self._write_json(self._blocks_path, blocks)

    def get_blocks(self) -> list[ContextBlock]:
        """获取所有上下文块，按 order 正序。"""
        raw = self._load_blocks_raw()
        blocks = [_dict_to_block(d) for d in raw]
        return sorted(blocks, key=lambda b: b.order)

    def delete_block(self, block_id: str) -> None:
        """按 ID 删除上下文块，不存在时静默忽略。"""
        blocks = self._load_blocks_raw()
        filtered = [b for b in blocks if b.get("id") != block_id]
        self._write_json(self._blocks_path, filtered)

    def update_block_enabled(self, block_id: str, enabled: bool) -> None:
        """切换上下文块的启用状态。"""
        blocks = self._load_blocks_raw()
        for b in blocks:
            if b.get("id") == block_id:
                b["enabled"] = enabled
                break
        self._write_json(self._blocks_path, blocks)

    # ──────────────────────────────────────────
    # ContextTemplate CRUD
    # ──────────────────────────────────────────

    def list_templates(self) -> list[ContextTemplate]:
        """获取所有模板。"""
        raw = self._load_templates_raw()
        return [_dict_to_template(d) for d in raw]

    def get_template(self, template_id: str) -> ContextTemplate | None:
        """按 ID 获取模板，不存在返回 None。"""
        for d in self._load_templates_raw():
            if d.get("id") == template_id:
                return _dict_to_template(d)
        return None

    def save_template(self, template: ContextTemplate) -> None:
        """保存或更新模板。"""
        templates = self._load_templates_raw()
        replaced = False
        for i, t in enumerate(templates):
            if t.get("id") == template.id:
                templates[i] = _template_to_dict(template)
                replaced = True
                break
        if not replaced:
            templates.append(_template_to_dict(template))
        self._write_json(self._templates_path, templates)

    # ──────────────────────────────────────────
    # 内部 I/O
    # ──────────────────────────────────────────

    def _load_blocks_raw(self) -> list[dict]:
        return self._read_json(self._blocks_path)

    def _load_templates_raw(self) -> list[dict]:
        return self._read_json(self._templates_path)

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
# 序列化 / 反序列化（模块私有）
# ──────────────────────────────────────────────

def _block_to_dict(block: ContextBlock) -> dict:
    return {
        "id":      block.id,
        "label":   block.label,
        "content": block.content,
        "source":  block.source.value if hasattr(block.source, "value") else str(block.source),
        "enabled": block.enabled,
        "order":   block.order,
    }


def _dict_to_block(d: dict) -> ContextBlock:
    return ContextBlock(
        id=d["id"],
        label=d.get("label", ""),
        content=d.get("content", ""),
        source=ContextSource(d.get("source", "manual")),
        enabled=d.get("enabled", True),
        order=d.get("order", 0),
    )


def _template_to_dict(template: ContextTemplate) -> dict:
    return {
        "id":          template.id,
        "name":        template.name,
        "description": template.description,
        "blocks":      [_block_to_dict(b) for b in template.blocks],
    }


def _dict_to_template(d: dict) -> ContextTemplate:
    return ContextTemplate(
        id=d["id"],
        name=d.get("name", ""),
        description=d.get("description", ""),
        blocks=[_dict_to_block(b) for b in d.get("blocks", [])],
    )
