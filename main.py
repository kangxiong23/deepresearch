# File: main.py
# Responsibility: 应用入口——装配完整依赖树，启动 PySide6 桌面应用。
#                 这是整个项目依赖关系最集中、最透明的地方：
#                 调试时只需在此文件看清楚"谁注入了谁"，调用链立刻清晰。
# Input:  无
# Output: 运行中的 PySide6 桌面应用

import sys
import faulthandler

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont

# 启用 faulthandler — segfault 时输出 Python 堆栈到 stderr
faulthandler.enable()

# ── 基础设施 ──────────────────────────────────
from app.storage.database import initialize_database
from app.storage.message_repo import MessageRepo
from app.storage.tree_store import TreeStore
from app.storage.branch_store import BranchStore
from app.storage.context_store import ContextStore

# ── 适配器 ────────────────────────────────────
from app.adapters.deepseek_client import DeepSeekClient
from app.adapters.file_parsers import default_parsers
from app.adapters.search_adapters import DuckDuckGoSearchAdapter, ArxivSearchAdapter

# ── Core 服务 ─────────────────────────────────
from app.core.conversation_service import ConversationService
from app.core.context_service import ContextService
from app.core.search_service import SearchService
from app.core.file_service import FileService
from app.core.file_tool_service import FileToolService
from app.core.knowledge_service import KnowledgeService
from app.core.branch_service import BranchService

# ── Storage（知识图谱）────────────────────────
from app.storage.kg_store import KGStore

# ── Controller ────────────────────────────────
from app.controllers.app_controller import AppController
from app.controllers.settings_controller import SettingsController

# ── UI (PySide6) ──────────────────────────────
from app.ui.app import ChatApp
from app.ui.theme import get_theme_manager

# ── 日志 ─────────────────────────────────────
from app.utils.async_utils import get_logger
from app.utils.run_marker import RunMarker, describe
from app.utils.file_access_settings import FileAccessSettings

logger = get_logger("MAIN", "main")


def assemble_app() -> ChatApp:
    """
    依赖树装配顺序：
        Storage → Adapters → Core Services → Controllers → UI

    每一层只依赖下面的层，依赖关系在此文件一目了然。
    调试时：
        - 想知道 ConversationService 用的哪个 LLM？看这里的 llm_client 变量。
        - 想知道搜索用的哪个后端？看这里的 search_adapters 列表。
        - 流式出错？从 logger 的 [CORE] / [ADPTR] 层标签过滤日志定位。
    """
    logger.info("=== DeepResearch starting ===")

    # ── 0. 读回持久化的用户设置 ───────────────
    # 本地文件访问的开关与授权目录存在 data/file_access.json，
    # 必须在 Core 服务构建前写回 config（FileToolService 在调用时读 config）。
    access = FileAccessSettings().apply_to_config()
    logger.info(
        "file access: enabled=%s roots=%d",
        access["enabled"], len(access["roots"]),
    )

    # ── 1. 初始化数据库 ───────────────────────
    initialize_database()
    logger.info("database initialized")

    # ── 2. Storage 层 ─────────────────────────
    message_repo  = MessageRepo()
    tree_store    = TreeStore()
    branch_store  = BranchStore()
    context_store = ContextStore()
    kg_store      = KGStore()

    # ── 3. Adapter 层 ─────────────────────────
    llm_client      = DeepSeekClient()
    file_parsers    = default_parsers()
    search_adapters = [
        DuckDuckGoSearchAdapter(),   # 通用网页搜索（无需 API Key）
        ArxivSearchAdapter(),         # 学术论文搜索（无需 API Key）
    ]

    # ── 4. Core 服务层 ────────────────────────
    context_service = ContextService(
        message_repo=message_repo,
        context_store=context_store,
        tree_store=tree_store,
    )
    search_service = SearchService(
        adapters=search_adapters,
    )
    file_service = FileService(
        parsers=file_parsers,
    )
    # 本地文件只读访问（工具调用）。默认关闭：config.file_access_enabled=False
    # 与空授权目录共同保证"未启用时请求体里不出现 tools"。
    file_tool_service = FileToolService()
    branch_service = BranchService(
        tree_store=tree_store,
        branch_store=branch_store,
        message_repo=message_repo,
    )
    conversation_service = ConversationService(
        message_repo=message_repo,
        tree_store=tree_store,
        llm_client=llm_client,
        context_service=context_service,
        search_service=search_service,
        file_service=file_service,
        branch_service=branch_service,
        file_tools=file_tool_service,
    )

    # ── 4b. 知识图谱服务 ──────────────────────
    knowledge_service = KnowledgeService(
        kg_store=kg_store,
        llm_client=llm_client,
    )

    # 把 KnowledgeService 注入 ContextService（让上下文自动带图谱知识）
    context_service.set_knowledge_service(knowledge_service)

    # ── 5. Controller 层 ──────────────────────
    app_controller      = AppController(conversation_service=conversation_service)
    app_controller.set_knowledge_service(knowledge_service)
    settings_controller = SettingsController()

    # ── 6. UI (PySide6) ───────────────────────
    chat_app = ChatApp(
        app_controller=app_controller,
        settings_controller=settings_controller,
    )

    logger.info("=== Dependency assembly complete ===")
    return chat_app


def main() -> int:
    """应用程序主入口。"""
    # PySide6 要求在创建任何 QWidget 之前先创建 QApplication
    app = QApplication(sys.argv)
    # linux版本中文适配
    font = QFont("WenQuanYi Micro Hei", 17)
    app.setFont(font)
    app.setApplicationName("DeepResearch")
    app.setOrganizationName("DeepResearch")

    # 会话标记：识别"上次是否异常退出"，并记录每次运行的存活时长。
    # 只写日志，不弹窗——误报会训练用户忽略提示。
    marker = RunMarker()
    for stale in marker.start():
        print(describe(stale))
    app.aboutToQuit.connect(marker.finish)
    heartbeat = QTimer(app)
    heartbeat.setInterval(30_000)
    heartbeat.timeout.connect(marker.beat)
    heartbeat.start()

    # [临时] 崩溃/卡死观测（app/utils/_tmp_diag.py）：全线程栈 + 事件时间线 +
    # [临时] 心跳看门狗。只观测不改逻辑，删除含 "[临时]" 的行即可移除。
    from app.utils import _tmp_diag
    _tmp_diag.start()
    _tmp_diag.install_qt(app)

    # 使用 Fusion 风格获得跨平台一致的渲染效果，
    # 深色背景下的原生控件（树展开箭头等）需要 Fusion 风格才能正确使用调色板颜色
    app.setStyle("Fusion")

    # 应用持久化主题（设置 Colors + 全局样式表 + 调色板）——
    # 必须在装配 UI 之前执行，确保控件构造时即读取到正确颜色
    get_theme_manager().apply(get_theme_manager().current_id)

    # 装配依赖树并构建 UI
    chat_app = assemble_app()
    chat_app.show()

    logger.info("=== UI mounted, app ready ===")

    # 进入 Qt 事件循环
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
