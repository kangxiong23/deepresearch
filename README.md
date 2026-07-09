# DeepResearch

基于 DeepSeek API 的桌面端 AI 对话应用，支持流式输出、思考模式、联网搜索与上下文管理。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API Key（复制模板后填入）
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY

# 3. 启动
python main.py
```

## 项目结构

```
deepresearch/
├── main.py          # 入口：依赖注入树装配
├── config.py        # 全局配置（静态 + 可变业务配置）
└── app/
    ├── ui/          # UI 层：纯视图，Flet 组件
    ├── controllers/ # Controller 层：薄桥接，VM 映射
    ├── core/        # Core 层：业务编排
    ├── adapters/    # Adapter 层：外部 API、文件解析
    ├── storage/     # Storage 层：SQLite + JSON 持久化
    └── utils/       # 工具：日志、Markdown、文件工具
```

## 调试指南

所有层均使用结构化日志，按层标签过滤：

```bash
# 只看 Core 层日志
python main.py 2>&1 | grep "\[CORE \]"

# 只看 Adapter 层（含 HTTP 请求细节）
python main.py 2>&1 | grep "\[ADPTR\]"

# 开启 DEBUG 级别（在 .env 中设置）
LOG_LEVEL=DEBUG python main.py
```

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | ✅ | — | DeepSeek API 密钥 |
| `DEEPSEEK_BASE_URL` | ❌ | `https://api.deepseek.com` | API 基础 URL |
| `DEFAULT_MODEL` | ❌ | `deepseek-v4-flash` | 默认模型 |
| `MAX_TOKENS` | ❌ | `8192` | 单次回复最大 token |
| `LOG_LEVEL` | ❌ | `INFO` | 日志级别（DEBUG/INFO/WARNING）|
| `DB_PATH` | ❌ | `data/deepresearch.db` | SQLite 数据库路径 |
