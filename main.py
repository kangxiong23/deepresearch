# File: main.py
# Responsibility: 应用入口——装配完整依赖树，启动 PySide6 桌面应用。
#                 这是整个项目依赖关系最集中、最透明的地方：
#                 调试时只需在此文件看清楚"谁注入了谁"，调用链立刻清晰。
# Input:  无
# Output: 运行中的 PySide6 桌面应用

import sys
import faulthandler

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPalette, QColor, QFont

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
from app.core.knowledge_service import KnowledgeService
from app.core.branch_service import BranchService

# ── Storage（知识图谱）────────────────────────
from app.storage.kg_store import KGStore

# ── Controller ────────────────────────────────
from app.controllers.app_controller import AppController
from app.controllers.settings_controller import SettingsController

# ── UI (PySide6) ──────────────────────────────
from app.ui.app import ChatApp

# ── 日志 ─────────────────────────────────────
from app.utils.async_utils import get_logger

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


def _apply_dark_palette(app: QApplication) -> None:
    """为 QApplication 设置深色调色板，确保原生控件（展开箭头等）在深色背景下可见。"""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#0D0F14"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#E8EAF0"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#13161D"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#1A1E28"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#E8EAF0"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#1A1E28"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#E8EAF0"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#4A9EFF"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#0D0F14"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#1A1E28"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#E8EAF0"))
    # 禁用态颜色组 — 确保禁用文字在深色背景上也可见
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#3D4255"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#3D4255"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#3D4255"))
    app.setPalette(palette)


def main() -> int:
    """应用程序主入口。"""
    # PySide6 要求在创建任何 QWidget 之前先创建 QApplication
    app = QApplication(sys.argv)
    # linux版本中文适配
    font = QFont("WenQuanYi Micro Hei", 14)
    app.setApplicationName("DeepResearch")
    app.setOrganizationName("DeepResearch")

    # 使用 Fusion 风格获得跨平台一致的渲染效果，
    # 深色背景下的原生控件（树展开箭头等）需要 Fusion 风格才能正确使用调色板颜色
    app.setStyle("Fusion")

    # 应用深色调色板，确保原生控件（树展开箭头等）在深色背景下可见
    _apply_dark_palette(app)

    # 装配依赖树并构建 UI
    chat_app = assemble_app()
    chat_app.show()

    logger.info("=== UI mounted, app ready ===")

    # 进入 Qt 事件循环
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
