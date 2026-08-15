# Layer: tests
# File: tests/test_config_scope.py
# Responsibility: 验证 ConfigScope 的读/写/覆盖/恢复，以及跨线程隔离。

from __future__ import annotations

import threading
import unittest

import config as app_config
from config import ConfigScope


class TestConfigScope(unittest.TestCase):
    def test_default_and_write(self) -> None:
        default = app_config.max_tokens
        self.assertIsInstance(default, int)
        app_config.max_tokens = 1234
        self.assertEqual(app_config.max_tokens, 1234)
        # 恢复默认，避免污染其他用例
        app_config.max_tokens = default

    def test_scope_override_and_restore(self) -> None:
        default = app_config.model_type
        with ConfigScope(model_type="deepseek-v4-flash"):
            self.assertEqual(app_config.model_type, "deepseek-v4-flash")
        self.assertEqual(app_config.model_type, default)

    def test_nested_scope(self) -> None:
        with ConfigScope(model_type="A"):
            self.assertEqual(app_config.model_type, "A")
            with ConfigScope(model_type="B"):
                self.assertEqual(app_config.model_type, "B")
            self.assertEqual(app_config.model_type, "A")

    def test_unknown_attribute_raises(self) -> None:
        with self.assertRaises(AttributeError):
            _ = app_config.does_not_exist

    def test_no_cross_thread_leak(self) -> None:
        """跨线程隔离：A 线程的 scope 覆盖不应泄漏到 B 线程。"""
        a_entered = threading.Event()
        b_done = threading.Event()
        result: list = []

        def worker_a() -> None:
            with ConfigScope(model_type="deepseek-v4-flash"):
                a_entered.set()
                b_done.wait(5)

        def worker_b() -> None:
            a_entered.wait(5)
            with ConfigScope(search_enabled=True):
                result.append(getattr(app_config, "model_type", None))
            b_done.set()

        ta = threading.Thread(target=worker_a)
        tb = threading.Thread(target=worker_b)
        ta.start()
        tb.start()
        ta.join()
        tb.join()

        self.assertTrue(result)
        self.assertNotEqual(result[0], "deepseek-v4-flash")


if __name__ == "__main__":
    unittest.main()
