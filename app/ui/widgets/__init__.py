# Layer: UI (PySide6) → widgets
# File: app/ui/widgets/__init__.py

from app.ui.widgets.chat_message import ChatMessage, ThinkingBlock, _role_badge
from app.ui.widgets.file_drop import ChatDropArea, collect_files, is_supported_path
from app.ui.widgets.image_preview import (
    ImageSource,
    ImageTile,
    ImageViewer,
    show_image,
)
from app.ui.widgets.input_area import InputArea, ToggleChip, ModelSelector, AttachmentBar
from app.ui.widgets.tree_panel import TreePanel
from app.ui.widgets.settings_panel import SettingsPanel
from app.ui.widgets.sidebar import Sidebar
from app.ui.widgets.context_panel import ContextPanel
from app.ui.widgets.kg_panel import KGPanel
from app.ui.widgets.dialogs import (
    show_rename_dialog,
    show_new_folder_dialog,
    show_confirm_dialog,
    show_recycle_bin_dialog,
)

__all__ = [
    "ChatMessage",
    "ThinkingBlock",
    "_role_badge",
    "ChatDropArea",
    "collect_files",
    "is_supported_path",
    "ImageSource",
    "ImageTile",
    "ImageViewer",
    "show_image",
    "InputArea",
    "ToggleChip",
    "ModelSelector",
    "AttachmentBar",
    "TreePanel",
    "SettingsPanel",
    "Sidebar",
    "ContextPanel",
    "KGPanel",
    "show_rename_dialog",
    "show_new_folder_dialog",
    "show_confirm_dialog",
    "show_recycle_bin_dialog",
]
