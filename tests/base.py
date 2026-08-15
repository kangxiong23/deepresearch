# Layer: tests
# File: tests/base.py
# Responsibility: 测试基类 —— 把 config 的持久化路径指向一次性临时目录，
#                 测试结束后自动还原并删除临时目录，避免污染真实 data/。

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from config import ConfigScope
from app.storage.database import initialize_database, reset_connection


class TempConfigTestCase(unittest.TestCase):
    """隔离数据目录的测试基类。

    每个用例使用独立的临时目录承载 DB / tree / context / kg，
    避免读写真实 data/；tearDown 关闭连接并清理临时目录。
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="deepresearch_test_")
        self._scope = ConfigScope(
            DB_PATH=os.path.join(self._tmpdir, "deepresearch.db"),
            TREE_STORE_PATH=os.path.join(self._tmpdir, "tree"),
            CONTEXT_STORE_PATH=os.path.join(self._tmpdir, "context"),
            KG_STORE_PATH=os.path.join(self._tmpdir, "kg"),
        )
        self._scope.__enter__()
        initialize_database()

    def tearDown(self) -> None:
        reset_connection()
        self._scope.__exit__(None, None, None)
        reset_connection()
        shutil.rmtree(self._tmpdir, ignore_errors=True)
