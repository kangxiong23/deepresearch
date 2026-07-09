# File: config.py（项目根目录）
# Responsibility: 全局集中配置模块——所有可变业务配置的唯一来源。
#                 UI 层可直接修改此模块的属性值（如用户切换模型）。
#                 后端所有模块（Core、Adapters）直接 import config 读取，无需经过传递。
#                 静态部署配置（API Key、数据库路径等）从 .env 文件读取，在此处加载。
# Input:  .env 文件（静态部署配置）
# Output: 模块级变量，可被任意层直接读写

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 加载 .env（仅加载一次，已有环境变量不覆盖）
load_dotenv(Path(__file__).parent / ".env", override=False)


# ──────────────────────────────────────────────
# 静态部署配置（从 .env 读取，运行时不可变）
# ──────────────────────────────────────────────

# DeepSeek API
DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# 数据库路径
DB_PATH: str = os.getenv("DB_PATH", str(Path(__file__).parent / "data" / "deepresearch.db"))

# 上下文存储路径（JSON 文件目录）
CONTEXT_STORE_PATH: str = os.getenv(
    "CONTEXT_STORE_PATH",
    str(Path(__file__).parent / "data" / "context"),
)

# 树形结构存储路径（tree.json + recycle_bin.json）
TREE_STORE_PATH: str = os.getenv(
    "TREE_STORE_PATH",
    str(Path(__file__).parent / "data" / "tree"),
)

# 日志级别
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


# ──────────────────────────────────────────────
# 可变业务配置（UI 层可直接写入，后端直接读取）
# ──────────────────────────────────────────────

# 当前使用的模型 ID
# UI 写入：app_config.model_type = "deepseek-v4-pro"
# 后端读取：model = app_config.model_type
model_type: str = os.getenv("DEFAULT_MODEL", "deepseek-v4-pro")

# 思考模式开关（仅 deepseek-v4-pro 支持）
thinking_enabled: bool = False

# 联网搜索开关
search_enabled: bool = False

# ──────────────────────────────────────────────
# LLM 推理参数
# ──────────────────────────────────────────────

# 单次回复最大 token 数
max_tokens: int = int(os.getenv("MAX_TOKENS", "8192"))

# 采样温度（0.0 ~ 2.0）
temperature: float = float(os.getenv("TEMPERATURE", "1.0"))

# ──────────────────────────────────────────────
# 上下文窗口管理
# ──────────────────────────────────────────────

# 发送给 LLM 的历史消息最大 token 预算
# 超出时从最旧消息截断，始终保留最近消息
max_history_tokens: int = int(os.getenv("MAX_HISTORY_TOKENS", "32000"))

# ──────────────────────────────────────────────
# 搜索参数
# ──────────────────────────────────────────────

# 每次搜索最多返回的结果条数
search_max_results: int = int(os.getenv("SEARCH_MAX_RESULTS", "5"))

# ──────────────────────────────────────────────
# 知识图谱配置（KGLite）
# ──────────────────────────────────────────────

# 知识图谱存储路径
KG_STORE_PATH: str = os.getenv(
    "KG_STORE_PATH",
    str(Path(__file__).parent / "data" / "knowledge_graph"),
)

# 多对话模式：显示所有启用对话的消息（合并时间线），而非仅当前对话
# Phase 5 — 启用后，聊天区展示所有 effectively-enabled 消息
multi_conv_mode: bool = True

# 知识注入开关：发消息时是否自动查询图谱并注入相关知识
kg_injection_enabled: bool = True

# 知识注入最大条目数
kg_max_inject: int = int(os.getenv("KG_MAX_INJECT", "10"))

# 知识提取时分析的最近消息条数
kg_extract_recent_n: int = int(os.getenv("KG_EXTRACT_RECENT_N", "6"))