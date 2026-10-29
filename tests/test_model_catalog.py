# Layer: tests
# File: tests/test_model_catalog.py
# Responsibility: 验证"换模型只改 .env / config.py"这条约束——
#                 模型清单是唯一来源，界面/面板/能力判断都从它派生，
#                 清单外的模型名既不会被静默替换，也不会被静默写坏。
#                 纯离屏 Qt，不触网。

from __future__ import annotations

import asyncio
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import config as app_config  # noqa: E402
from config import ConfigScope  # noqa: E402
from app.core import image_attachments  # noqa: E402
from app.core.knowledge_service import KnowledgeService  # noqa: E402
from app.storage.models import ChunkType, MessageChunk  # noqa: E402
from app.ui.widgets.config_dialog import _ConfigDialog  # noqa: E402
from app.ui.widgets.input_area import ModelSelector  # noqa: E402
from app.utils.app_settings import AppSettings, EDITABLE, READONLY, coerce  # noqa: E402

_app = QApplication.instance() or QApplication([])


class TestCatalog(unittest.TestCase):
    def test_ids_labels_and_vision_derived_from_one_table(self) -> None:
        ids = app_config.model_ids()
        labels = app_config.model_labels()

        self.assertEqual(len(ids), len(set(ids)), "模型 ID 不能重复")
        self.assertEqual([key for key, _ in labels], list(ids))
        self.assertEqual(
            app_config.vision_model_ids(),
            tuple(key for key, _label, vision in app_config.MODEL_OPTIONS if vision),
        )

    def test_alias_maps_old_name(self) -> None:
        self.assertEqual(app_config.resolve_model("deepseek-v4-flash"), "deepseek-flash")
        self.assertEqual(app_config.resolve_model("deepseek-flash"), "deepseek-flash")
        self.assertEqual(app_config.resolve_model("未知模型"), "未知模型")
        self.assertEqual(app_config.resolve_model(""), "")

    def test_model_is_not_persisted(self) -> None:
        """模型必须由 .env / MODEL_OPTIONS 决定，不能被 app_settings.json 盖掉。"""
        self.assertNotIn("model_type", EDITABLE)
        self.assertIn("model_type", READONLY)

    def test_current_model_is_a_catalog_entry(self) -> None:
        """没有 app_settings 覆盖时，config.model_type 应来自 DEFAULT_MODEL 或清单。"""
        with ConfigScope(model_type=app_config.model_ids()[0]):
            self.assertIn(app_config.model_type, app_config.model_ids())


class TestVisionCapability(unittest.TestCase):
    def test_catalog_flags_decide(self) -> None:
        for key, _label, vision in app_config.MODEL_OPTIONS:
            self.assertEqual(
                image_attachments.supports_images(key), vision, key,
            )

    def test_legacy_name_resolves(self) -> None:
        self.assertTrue(image_attachments.supports_images("deepseek-v4-flash"))

    def test_unknown_model_is_not_vision(self) -> None:
        """清单外按"不能看图"处理：宁可多问一句，也不静默丢图。"""
        self.assertFalse(image_attachments.supports_images("deepseek-v9-ultra"))
        self.assertFalse(image_attachments.supports_images(""))
        self.assertFalse(image_attachments.supports_images(None))


class TestModelSelector(unittest.TestCase):
    def test_items_follow_catalog(self) -> None:
        selector = ModelSelector()
        self.addCleanup(selector.deleteLater)

        data = [selector.itemData(i) for i in range(selector.count())]
        self.assertEqual(data, list(app_config.model_ids()))

    def test_out_of_catalog_model_is_shown_not_swapped(self) -> None:
        original = app_config.MODEL_OPTIONS
        try:
            app_config.MODEL_OPTIONS = (("deepseek-v4-pro", "Pro", False),)
            selector = ModelSelector()
            self.addCleanup(selector.deleteLater)

            selector.select_model("deepseek-flash")
            self.assertEqual(selector.currentData(), "deepseek-flash",
                             "清单外也不能替换成别的模型")
            self.assertIn("清单外", selector.currentText())
        finally:
            app_config.MODEL_OPTIONS = original

    def test_prefill_from_config_does_not_write_back(self) -> None:
        with ConfigScope(model_type=app_config.model_ids()[1]):
            before = app_config.model_type
            selector = ModelSelector()
            self.addCleanup(selector.deleteLater)
            self.assertEqual(app_config.model_type, before)


class TestConfigDialogUnknownChoice(unittest.TestCase):
    """
    任何"选择型"字段回填时遇到清单外的值，都必须显示成"（清单外）"并照原样带走。

    这条不变量是被模型改名踩出来的：以前找不到就把下拉留在第一项，
    用户只是打开面板点个保存，配置里的模型名就被换成了另一个。
    """

    def make(self, effort: str):
        values = {key: getattr(app_config, key, None) for key in EDITABLE}
        values["reasoning_effort"] = effort
        dlg = _ConfigDialog(None, values, dict(values), [], lambda v: v)
        self.addCleanup(dlg.deleteLater)
        return dlg

    def test_unknown_choice_keeps_its_value(self) -> None:
        dlg = self.make("ultra")

        self.assertEqual(dlg._widgets["reasoning_effort"].currentData(), "ultra")
        self.assertIn("清单外", dlg._widgets["reasoning_effort"].currentText())
        self.assertTrue(dlg._status.text().startswith("⚠"))
        self.assertEqual(dlg._collect()["reasoning_effort"], "ultra")

    def test_catalog_value_needs_no_warning(self) -> None:
        dlg = self.make("low")
        self.assertEqual(dlg._unknown_choices, [])


class TestCoerceModel(unittest.TestCase):
    def test_model_is_not_in_the_whitelist(self) -> None:
        """模型不进白名单：app_settings.json 里就算存了也应用不到 config。"""
        self.assertEqual(coerce("model_type", app_config.model_ids()[0]), (False, None))

    def test_stale_model_dropped_on_load(self) -> None:
        """
        旧名字留在 app_settings.json 里时读回来应被丢弃（回落到 .env），
        否则一次保存就把 .env 的模型永久盖住。

        注意：用 os.makedirs 造目录，不要用 tempfile —— 沙箱下 mkdtemp 建的目录
        带 0o700 DACL，写进去会被拦（见 AGENTS.md）。
        """
        import shutil
        from pathlib import Path

        tmp = os.path.join("logs", "_test_model_catalog")
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, exist_ok=True)
        self.addCleanup(shutil.rmtree, tmp, True)

        store = AppSettings(path=Path(tmp) / "app_settings.json")
        store.save({"model_type": "deepseek-v4-flash", "max_tokens": 4096})
        loaded = store.load()

        self.assertNotIn("model_type", loaded)
        self.assertEqual(loaded.get("max_tokens"), 4096)


class RecordLLM:
    """记录每次请求时 config.model_type 是什么。"""

    def __init__(self) -> None:
        self.models: list[str] = []

    async def stream_chat(self, context):
        self.models.append(app_config.model_type)
        yield MessageChunk(delta="{}", is_done=True, chunk_type=ChunkType.TEXT)


class TestKnowledgeExtractionModel(unittest.IsolatedAsyncioTestCase):
    async def test_uses_kg_model_not_current_model(self) -> None:
        llm = RecordLLM()
        service = KnowledgeService.__new__(KnowledgeService)
        service._llm = llm

        before = app_config.model_type
        with ConfigScope(model_type=app_config.model_ids()[-1], kg_model="deepseek-flash"):
            await service._call_llm_for_extraction([{"role": "user", "content": "hi"}])

        self.assertEqual(llm.models, ["deepseek-flash"])
        # ConfigScope 退出后当前模型必须回到进入前的值，
        # 否则"跑一次知识提取，之后所有对话都变成提取用的模型"
        self.assertEqual(app_config.model_type, before)


if __name__ == "__main__":
    unittest.main()
