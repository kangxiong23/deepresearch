# Layer: Controller
# File: app/controllers/command_builder.py
# Responsibility: 将 UI 传来的原始参数打包为标准控制表（扁平字典），
#                 供 AppController 直接透传给 Core 层方法。
# Input:  UI 原始值（str, bool, list 等简单类型）
# Output: dict — 标准控制表，如 {"text": "...", "files": [...], "session_id": "..."}
# 禁止: 任何业务判断、配置解析、领域知识（不判断 model 类型含义，不计算 token 等）

from __future__ import annotations


class CommandBuilder:
    """
    纯静态工具类，将 UI 原始参数组装为控制表。

    所有方法均为 @staticmethod，无实例状态。
    控制表是扁平字典，由 Controller 直接解包传给 Core 服务。
    """

    @staticmethod
    def build_send_command(
        session_id: str,
        text: str,
        files: list[str],
    ) -> dict:
        """
        构造发送消息控制表。

        Returns:
            {
                "session_id": str,
                "text": str,
                "files": list[str],
            }
        """
        return {
            "session_id": session_id,
            "text": text,
            "files": files,
        }

    @staticmethod
    def build_switch_command(session_id: str) -> dict:
        """
        构造切换对话控制表。

        Returns:
            {"session_id": str}
        """
        return {"session_id": session_id}

    @staticmethod
    def build_delete_command(session_id: str) -> dict:
        """
        构造删除对话控制表。

        Returns:
            {"session_id": str}
        """
        return {"session_id": session_id}

    @staticmethod
    def build_regenerate_command(session_id: str, message_id: str) -> dict:
        """
        构造重新生成控制表。

        Returns:
            {"session_id": str, "message_id": str}
        """
        return {
            "session_id": session_id,
            "message_id": message_id,
        }
