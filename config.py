# File: config.py（项目根目录）
# Responsibility: 全局集中配置模块——所有可变业务配置的唯一来源。
#                 UI 层可直接修改此模块的属性值（如用户切换模型）。
#                 后端所有模块（Core、Adapters）直接 import config 读取，无需经过传递。
#                 静态部署配置（API Key、数据库路径等）从 .env 文件读取，在此处加载。
#                 ConfigScope 提供临时隔离覆盖（测试/临时切换用），退出自动恢复。
# Input:  .env 文件（静态部署配置）
# Output: 模块级变量，可被任意层直接读写

from __future__ import annotations

import contextvars
import os
import sys
import types
from pathlib import Path

from dotenv import load_dotenv

# 加载 .env（仅加载一次，已有环境变量不覆盖）
load_dotenv(Path(__file__).parent / ".env", override=False)


# ──────────────────────────────────────────────
# 静态部署配置（从 .env 读取，运行时不可变，不参与 scope 隔离）
# ──────────────────────────────────────────────

# DeepSeek API
DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# 日志级别
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# 思考强度可选值（供 UI 下拉展示 / 后端校验）
REASONING_EFFORTS: tuple[str, ...] = ("low", "high", "max")


# ──────────────────────────────────────────────
# 可变配置默认值（可被 ConfigScope 覆盖）
# ──────────────────────────────────────────────

_defaults: dict = {
    # 数据库路径
    "DB_PATH": os.getenv(
        "DB_PATH",
        str(Path(__file__).parent / "data" / "deepresearch.db"),
    ),
    # 上下文存储路径（JSON 文件目录）
    "CONTEXT_STORE_PATH": os.getenv(
        "CONTEXT_STORE_PATH",
        str(Path(__file__).parent / "data" / "context"),
    ),
    # 树形结构存储路径（tree.json + recycle_bin.json）
    "TREE_STORE_PATH": os.getenv(
        "TREE_STORE_PATH",
        str(Path(__file__).parent / "data" / "tree"),
    ),
    # 知识图谱存储路径
    "KG_STORE_PATH": os.getenv(
        "KG_STORE_PATH",
        str(Path(__file__).parent / "data" / "knowledge_graph"),
    ),
    # 当前使用的模型 ID
    # UI 写入：app_config.model_type = "deepseek-v4-pro"
    # 后端读取：model = app_config.model_type
    "model_type": os.getenv("DEFAULT_MODEL", "deepseek-v4-pro"),
    # 思考模式开关（flash / pro 均支持）
    "thinking_enabled": False,
    # 思考强度（仅 thinking_enabled=True 时生效；API 要求 thinking 未启用时不得传）
    "reasoning_effort": "high",
    # 联网搜索开关
    "search_enabled": False,
    # 单次回复最大 token 数
    "max_tokens": int(os.getenv("MAX_TOKENS", "8192")),
    # 采样温度（0.0 ~ 2.0）
    "temperature": float(os.getenv("TEMPERATURE", "1.0")),
    # 发送给 LLM 的历史消息最大 token 预算
    "max_history_tokens": int(os.getenv("MAX_HISTORY_TOKENS", "32000")),
    # 每次搜索最多返回的结果条数
    "search_max_results": int(os.getenv("SEARCH_MAX_RESULTS", "5")),
    # 多对话模式：显示所有启用对话的消息（合并时间线），而非仅当前对话
    "multi_conv_mode": True,
    # 知识注入开关：发消息时是否自动查询图谱并注入相关知识
    "kg_injection_enabled": True,
    # 知识注入最大条目数
    "kg_max_inject": int(os.getenv("KG_MAX_INJECT", "10")),
    # 知识提取时分析的最近消息条数
    "kg_extract_recent_n": int(os.getenv("KG_EXTRACT_RECENT_N", "6")),
}


# ──────────────────────────────────────────────
# Scope 隔离机制（ConfigScope）
# ──────────────────────────────────────────────

# 每线程独立的 scope 栈（contextvars 保证线程/asyncio 隔离）。
# 栈内层 scope 的覆盖优先于外层；无 scope 时读取 _defaults。
_scope_stack: contextvars.ContextVar[list] = contextvars.ContextVar(
    "config_scope_stack", default=[]
)


class ConfigScope:
    """
    config 隔离作用域上下文管理器。

    用法:
        with ConfigScope(DB_PATH="/tmp/test.db", model_type="deepseek-v4-flash"):
            # 作用域内 app_config.DB_PATH → "/tmp/test.db"
            #        app_config.model_type → "deepseek-v4-flash"
            ...
        # 退出后自动恢复之前的配置

    嵌套:
        with ConfigScope(model_type="A"):
            # model_type == "A"
            with ConfigScope(model_type="B"):
                # model_type == "B"（内层优先）
            # model_type == "A"（外层恢复）

    线程安全：每线程独立的 scope 栈；后台 QThread 不受主线程 scope 影响。
    连接重置：overrides 含 DB_PATH 时，进入/退出自动重置当前线程的 SQLite 连接。
    """

    def __init__(self, **overrides) -> None:
        self._overrides = dict(overrides)

    def __enter__(self) -> "ConfigScope":
        stack = _scope_stack.get()
        stack.append(self)
        _scope_stack.set(stack)
        if "DB_PATH" in self._overrides:
            _force_reset_connection()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        stack = _scope_stack.get()
        if stack:
            stack.pop()
            _scope_stack.set(stack)
        if "DB_PATH" in self._overrides:
            _force_reset_connection()
        return False  # 不吞异常


def _force_reset_connection() -> None:
    """关闭当前线程的 SQLite 连接（延迟导入避免 config ↔ database 循环依赖）。"""
    from app.storage.database import reset_connection
    reset_connection()


# ──────────────────────────────────────────────
# 模块 __class__ 替换 — 拦截 app_config.X 读/写
# ──────────────────────────────────────────────

# 注意：PEP 562 只支持模块级 __getattr__/__dir__，不支持 __setattr__。
# 为拦截 `app_config.X = value` 写入，须将模块 __class__ 替换为自定义
# types.ModuleType 子类。概念验证已通过（读/写/覆盖/恢复均正确）。
class _ConfigModule(types.ModuleType):
    """拦截 config 属性读/写，支持 ConfigScope 隔离。"""

    def __getattr__(self, name: str):
        # 1. scope 栈内层优先（reversed 保证最近进入的 scope 优先）
        stack = _scope_stack.get()
        for scope in reversed(stack):
            if name in scope._overrides:
                return scope._overrides[name]
        # 2. _defaults
        if name in _defaults:
            return _defaults[name]
        # 3. 找不到
        raise AttributeError(
            f"module 'config' has no attribute {name!r}"
        )

    def __setattr__(self, name: str, value) -> None:
        # 内部属性正常设置
        if name.startswith("_"):
            super().__setattr__(name, value)
            return
        # 静态部署配置（在模块 globals 中但不在 _defaults 中）→ 直接设
        module_globals = globals()
        if name in module_globals and name not in _defaults:
            super().__setattr__(name, value)
            return
        # 有活跃 scope → 写入最内层 scope 的覆盖
        stack = _scope_stack.get()
        if stack:
            stack[-1]._overrides[name] = value
            return
        # 无 scope → 写入 _defaults
        if name in _defaults:
            _defaults[name] = value
            return
        # 未知名称 → 正常模块属性（兼容扩展）
        super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        if name in _defaults:
            raise AttributeError(
                f"cannot delete config key {name!r}"
            )
        super().__delattr__(name)


# 模块定义完成后替换 __class__，使后续属性访问都走上面的拦截逻辑
sys.modules[__name__].__class__ = _ConfigModule
