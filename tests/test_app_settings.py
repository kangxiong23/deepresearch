# Layer: tests
# File: tests/test_app_settings.py
# Responsibility: 验证用户偏好（config.py 可变项）的校验、落盘与启动回填。
#                 纯临时目录，不碰真实 data/。

from __future__ import annotations

import json
import os
import shutil
import unittest

import config as app_config
from config import ConfigScope
from app.utils.app_settings import AppSettings, EDITABLE, coerce

_BASE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "_test_app_settings",
)


class AppSettingsTestBase(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(_BASE, ignore_errors=True)
        os.makedirs(_BASE, exist_ok=True)
        self.addCleanup(shutil.rmtree, _BASE, True)
        self.db_path = os.path.join(_BASE, "deepresearch.db")

    def scope(self):
        return ConfigScope(DB_PATH=self.db_path)


class TestCoerce(AppSettingsTestBase):
    def test_bool(self) -> None:
        self.assertEqual(coerce("thinking_enabled", True), (True, True))
        self.assertEqual(coerce("thinking_enabled", 1), (True, True))
        self.assertEqual(coerce("thinking_enabled", "false"), (True, False))
        self.assertEqual(coerce("thinking_enabled", "yes"), (False, None))

    def test_int_clamps_instead_of_rejecting(self) -> None:
        self.assertEqual(coerce("max_tokens", 4096), (True, 4096))
        self.assertEqual(coerce("max_tokens", 1), (True, 64))
        self.assertEqual(coerce("max_tokens", 10 ** 9), (True, 65536))
        self.assertEqual(coerce("max_tokens", 4096.7), (True, 4097))
        self.assertFalse(coerce("max_tokens", "many")[0])
        self.assertFalse(coerce("max_tokens", True)[0])

    def test_float(self) -> None:
        self.assertEqual(coerce("temperature", 0.73), (True, 0.73))
        self.assertEqual(coerce("temperature", -1), (True, 0.0))
        self.assertEqual(coerce("temperature", 9), (True, 2.0))

    def test_choice(self) -> None:
        self.assertEqual(
            coerce("reasoning_effort", "max"), (True, "max")
        )
        self.assertFalse(coerce("reasoning_effort", "ultra")[0])

    def test_unknown_key(self) -> None:
        self.assertFalse(coerce("DB_PATH", "/tmp/x")[0])
        self.assertFalse(coerce("whatever", 1)[0])


class TestStore(AppSettingsTestBase):
    def test_missing_file_means_no_override(self) -> None:
        with self.scope():
            store = AppSettings()
            self.assertEqual(store.load(), {})
            self.assertEqual(store.apply_to_config(), {})

    def test_roundtrip(self) -> None:
        with self.scope():
            store = AppSettings()
            self.assertEqual(store.path.name, "app_settings.json")
            store.save({"reasoning_effort": "max", "max_tokens": 4096})
            self.assertEqual(
                store.load(),
                {"reasoning_effort": "max", "max_tokens": 4096},
            )

    def test_corrupt_file_falls_back(self) -> None:
        with self.scope():
            store = AppSettings()
            store.path.parent.mkdir(parents=True, exist_ok=True)
            with open(store.path, "w", encoding="utf-8") as f:
                f.write("{ not json")
            self.assertEqual(store.load(), {})

    def test_invalid_entries_are_dropped_not_fatal(self) -> None:
        with self.scope():
            store = AppSettings()
            store.path.parent.mkdir(parents=True, exist_ok=True)
            with open(store.path, "w", encoding="utf-8") as f:
                json.dump({
                    "temperature": "hot",
                    "reasoning_effort": "ultra",
                    "max_tokens": 4096,
                    "DB_PATH": "/elsewhere",
                }, f)
            self.assertEqual(store.load(), {"max_tokens": 4096})

    def test_merge_and_save_keeps_untouched_keys(self) -> None:
        with self.scope():
            store = AppSettings()
            store.save({"max_tokens": 4096, "temperature": 0.5})
            merged = store.merge_and_save({"temperature": 0.9, "nope": 1})
            self.assertEqual(merged["max_tokens"], 4096)
            self.assertEqual(merged["temperature"], 0.9)
            self.assertNotIn("nope", merged)

    def test_apply_to_config(self) -> None:
        with self.scope():
            AppSettings().save({
                "reasoning_effort": "max",
                "temperature": 0.3,
                "max_tokens": 2048,
            })
            data = AppSettings().apply_to_config()
            self.assertEqual(len(data), 3)
            self.assertEqual(app_config.reasoning_effort, "max")
            self.assertEqual(app_config.temperature, 0.3)
            self.assertEqual(app_config.max_tokens, 2048)

    def test_apply_only_touches_saved_keys(self) -> None:
        with ConfigScope(DB_PATH=self.db_path, temperature=1.0):
            AppSettings().save({"max_tokens": 2048})
            AppSettings().apply_to_config()
            self.assertEqual(app_config.max_tokens, 2048)
            self.assertEqual(app_config.temperature, 1.0, "没保存的项不该被改")

    def test_current_reports_editable_keys(self) -> None:
        with self.scope():
            current = AppSettings().current()
            self.assertEqual(set(current), set(EDITABLE))
            self.assertNotIn("DB_PATH", current)
            # 模型是部署配置，不参与持久化——否则启动时会把 .env 的 DEFAULT_MODEL 盖掉
            self.assertNotIn("model_type", current)

    def test_factory_defaults_ignore_saved_preferences(self) -> None:
        with self.scope():
            defaults = AppSettings.factory_defaults()
            AppSettings().save({"max_tokens": 1234})
            AppSettings().apply_to_config()
            self.assertEqual(AppSettings.factory_defaults(), defaults)
            self.assertEqual(set(defaults), set(EDITABLE))


if __name__ == "__main__":
    unittest.main()