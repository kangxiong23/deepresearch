# Layer: Controller
# File: app/controllers/settings_controller.py
# Responsibility: 接收 UI 的配置变更事件，写入集中配置模块。
#                 这是唯一一个直接写 config 的 Controller，因为配置变更
#                 不需要经过 Core 编排，后端模块在下次调用时自动读取新值。
# Input:  UI 原始值（model_type: str, enabled: bool）
# Output: 无返回值（写入 config 即完成）
# 禁止: 调用任何 Core 服务、adapter、存储；不做配置值的业务解释。

from __future__ import annotations
import config as app_config  # 集中配置，直接读写


class SettingsController:
    """
    配置变更控制器。

    通过依赖注入传入，无需持有任何 Core 服务。
    UI 切换模型/开关时调用对应方法，方法只做一件事：写 config。

    用法（在 main.py 或 app 入口处）：
        settings_ctrl = SettingsController()
        chat_app = ChatApp(app_controller, settings_ctrl)
    """

    # ── 模型切换 ──────────────────────────────

    def on_change_model(self, model_type: str) -> None:
        """
        UI 切换模型时调用。
        将用户选择的 model_type 字符串写入集中配置。
        后端（deepseek_client 等）在下次调用时自动读取。

        Args:
            model_type: 如 "deepseek-v4-flash" 或 "deepseek-v4-pro"
        """
        app_config.model_type = model_type

    # ── 思考模式开关 ──────────────────────────

    def on_toggle_thinking(self, enabled: bool) -> None:
        """
        UI 切换思考模式开关时调用。

        Args:
            enabled: True 开启，False 关闭
        """
        app_config.thinking_enabled = enabled

    # ── 思考强度 ──────────────────────────────

    def on_change_reasoning_effort(self, effort: str) -> None:
        """
        UI 切换思考强度时调用。

        Args:
            effort: 取值 "low" / "high" / "max"
        """
        app_config.reasoning_effort = effort

    # ── 搜索开关 ──────────────────────────────

    def on_toggle_search(self, enabled: bool) -> None:
        """
        UI 切换联网搜索开关时调用。

        Args:
            enabled: True 开启，False 关闭
        """
        app_config.search_enabled = enabled
