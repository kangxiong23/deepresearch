# File: main.py
# Responsibility: 应用入口——装配完整依赖树，启动 Flet 应用。
#                 这是整个项目依赖关系最集中、最透明的地方：
#                 调试时只需在此文件看清楚"谁注入了谁"，调用链立刻清晰。
# Input:  无
# Output: 运行中的 Flet 桌面应用

import flet as ft

# ── 基础设施 ──────────────────────────────────
from app.storage.database import initialize_database
from app.storage.conversation_repo import ConversationRepo
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

# ── Storage（知识图谱）────────────────────────
from app.storage.kg_store import KGStore

# ── Controller ────────────────────────────────
from app.controllers.app_controller import AppController
from app.controllers.settings_controller import SettingsController

# ── UI ────────────────────────────────────────
from app.ui.app import ChatApp

# ── 日志 ─────────────────────────────────────
from app.utils.async_utils import get_logger

logger = get_logger("MAIN", "main")


def main(page: ft.Page) -> None:
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
    conversation_repo = ConversationRepo()
    context_store     = ContextStore()
    kg_store          = KGStore()

    # ── 3. Adapter 层 ─────────────────────────
    llm_client      = DeepSeekClient()
    file_parsers    = default_parsers()
    search_adapters = [
        DuckDuckGoSearchAdapter(),   # 通用网页搜索（无需 API Key）
        ArxivSearchAdapter(),         # 学术论文搜索（无需 API Key）
    ]

    # ── 4. Core 服务层 ────────────────────────
    context_service = ContextService(
        conversation_repo=conversation_repo,
        context_store=context_store,
    )
    search_service = SearchService(
        adapters=search_adapters,
    )
    file_service = FileService(
        parsers=file_parsers,
    )
    conversation_service = ConversationService(
        llm_client=llm_client,
        conversation_repo=conversation_repo,
        context_service=context_service,
        search_service=search_service,
        file_service=file_service,
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

    # ── 6. UI ─────────────────────────────────
    chat_app = ChatApp(
        app_controller=app_controller,
        settings_controller=settings_controller,
    )
    chat_app.build(page)

    logger.info("=== UI mounted, app ready ===")


if __name__ == "__main__":
    ft.run(main, view=ft.AppView.FLET_APP)