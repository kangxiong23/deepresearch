# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the app
python main.py

# Run unit tests
python -m unittest tests.test_tree_store -v
python -m unittest tests.test_conversation_service -v
python -m unittest discover tests -v

# Filter logs by layer (all layers use structured tags)
python main.py 2>&1 | grep "\[CORE \]"      # Core orchestration
python main.py 2>&1 | grep "\[ADPTR\]"      # Adapter layer (includes HTTP details)
python main.py 2>&1 | grep "\[STORAGE\]"    # Storage layer
python main.py 2>&1 | grep "\[TREE \]"      # Tree operations
python main.py 2>&1 | grep "\[CTRL \]"      # Controller layer

# Enable debug-level logging
LOG_LEVEL=DEBUG python main.py
```

## Data safety during testing（测试数据安全）

**任何会修改持久化数据的测试（SQLite DB、`tree.json`、`recycle_bin.json`、分叉存储、`blocks.json`、`templates.json` 等），必须先备份原数据，测完再还原。**

- 涉及真实数据目录的测试/启动前，先备份整个数据目录（例如 `data/`），或把 `config.py` 中的路径（`DB_PATH`、`TREE_STORE_PATH`、`RECYCLE_BIN_PATH`、`BRANCH_STORE_PATH`、`CONTEXT_STORE_PATH`）指向一次性临时目录。
- 测完后若真实文件被改动，必须还原备份。
- 严禁在**没有备份**的情况下对真实数据目录运行 `assemble_app()` / `initialize_database()` / 应用启动 —— 迁移逻辑可能重写 `tree.json` 等文件。
- 只需全新状态的测试一律用临时目录隔离（与 `tests/` 中的做法一致）。

## One-time maintenance scripts (`scripts/`)

One-time / maintenance utilities live in `scripts/`. They are **not** part of the app and must be run manually from the project root. Both follow a **dry-run by default, `--execute` to apply** pattern and read paths via `config.py`:

```bash
# Prune orphan message rows from SQLite (based on tree.json / recycle_bin.json /
# branch storage — rows referenced by fork_nodes/branch_nodes are kept for branch switching)
python scripts/sync_db_with_tree.py            # dry-run: show orphan count + rows
python scripts/sync_db_with_tree.py --execute  # actually delete orphans

# Fix message ordering where assistant precedes thinking (thinking = LLM draft, must come first)
python scripts/fix_thinking_order.py            # dry-run: show affected conversations
python scripts/fix_thinking_order.py --execute  # rewrite tree.json
```

## Architecture

This is a desktop AI chat application (DeepResearch) built with **PySide6** (Qt for Python), powered by the **DeepSeek API**. It supports streaming chat, thinking/reasoning mode, web search, file upload/parsing, context block management, and a knowledge graph extracted from conversations.

The UI was originally built with **Flet** (Flutter-for-Python) and has been migrated to PySide6. Legacy Flet code lives in `app/ui_flet_legacy/` and is no longer used.

### Layer stack (strict top-down dependency)

```
UI (app/ui/)          → PySide6 views and widgets, Qt signals/slots
Controllers (app/controllers/) → Thin bridge: UI events → domain calls → ViewModels
Core (app/core/)      → Business orchestration, depends only on Protocols
Adapters (app/adapters/) → External systems: DeepSeek API, search, file parsers
Storage (app/storage/) → SQLite (messages) + JSON files (tree, context blocks, KG)
```

Dependencies flow one way: `Storage → Adapters → Core → Controllers → UI`. The full dependency tree is assembled by hand in `main.py:47-120` — this is the single source of truth for "who injects whom."

### Key architectural decisions

**Config is a global mutable module (`config.py`).** Every layer imports `config` directly. Static values (API key, DB path) are loaded from `.env` at import time. Mutable values (`model_type`, `thinking_enabled`, `reasoning_effort`, `search_enabled`) are written by the UI and read by Core/Adapters — no dependency injection for config. **These four mutable vars are the single sync point between frontend and backend**: the UI writes them (InputArea/ModelSelector → `SettingsController`) and Core/Adapters read them fresh on each call. In particular, `DeepSeekClient._build_payload` honors `thinking_enabled` for **both** `deepseek-v4-pro` and `deepseek-v4-flash` (treated equally): when enabled it sends `thinking: {type: enabled}` plus `reasoning_effort` (`config.reasoning_effort`, one of `low|high|max`); when disabled it sends `thinking: {type: disabled}` and **must not** include `reasoning_effort` (the API rejects it). `SearchService` gates on `search_enabled`.

**Protocol-based dependency inversion (`app/core/protocols.py`).** Core services depend on `typing.Protocol` classes (`LLMClientProtocol`, `SearchAdapterProtocol`, `FileParserProtocol`, `MessageRepoProtocol`, `ContextStoreProtocol`, `TreeStoreProtocol`), not concrete implementations. Adapters and Storage implement these protocols. This means you can swap the LLM backend or search provider by writing a new adapter — no Core changes needed.

**Send-message flow (`ConversationService.send_message`).** The core orchestration pipeline:
1. Extract text from attached files via `FileService`
2. Run web search via `SearchService` (gated by `config.search_enabled`)
3. Build the full LLM context via `ContextService` (system prompt + history + context blocks + search results + KG injection)
4. Persist the user message
5. Stream from `LLMClient.stream_chat()` — chunks are yielded directly to the caller, no buffering in Core
6. On `is_done`: persist the assistant message (and separate thinking message if reasoner mode), update conversation metadata, save MessageNodes to tree

**Streaming architecture (PySide6).** `AsyncStreamWorker` (`app/ui/async_bridge.py`) runs in a background `QThread` with its own asyncio event loop. It iterates the `AsyncGenerator` from `AppController.on_send_message()` and emits Qt signals (`chunk_ready`, `stream_finished`, `stream_error`, `stream_aborted`) that are connected to `ChatApp` slots on the main thread. This bridges the async Core layer to the synchronous Qt UI without blocking the event loop.

**Streaming abort.** `ConversationService._stop_event` (an `asyncio.Event`) is set by `stop_generation()`. The streaming loop checks it on each chunk and breaks. `AsyncStreamWorker.request_abort()` sets a flag checked between chunks; `StopGenerationWorker` calls the Core stop in a background thread.

**Knowledge graph.** After each assistant reply, the UI can trigger `KnowledgeService` to extract entities and relations from recent messages via LLM call. On subsequent messages, matching KG entries are injected into the system prompt (`config.kg_injection_enabled` controls this).

**Search backends.** Two adapters with no API keys needed: `DuckDuckGoSearchAdapter` (tries Bing first, falls back to DuckDuckGo Lite) and `ArxivSearchAdapter` (academic papers via Arxiv Atom Feed API).

**UI layout (PySide6).** Three-column layout in `MainWindow`:
- **Sidebar (260px)** — header + action buttons + `TreePanel` (QTreeView-based conversation tree) + bottom entries (trash, context, KG)
- **Chat Area (stretch)** — `MessageListView` (QScrollArea with ChatMessage widgets) + `InputArea` (text input, model selector, toggle chips)
- **Right Panel (0/380px)** — `QStackedWidget` switching between `ContextPanel` (index 0) and `KGPanel` (index 1)

**Theme.** `QApplication.setStyle("Fusion")` + dark `QPalette` for cross-platform consistent rendering. Global stylesheet built by `build_global_stylesheet()` in `app/ui/theme.py`. Color constants in `Colors` class match the original Flet design.

**Custom QProxyStyle (`_TreeStyle`).** Because Qt's stylesheet engine takes over `QTreeView::branch` and `QTreeView::indicator` rendering, the tree panel uses a custom `QProxyStyle` subclass that overrides `drawPrimitive` for:
- `PE_IndicatorBranch` — draws white ▶/▼ arrows (visible on dark background)
- `PE_IndicatorItemViewItemCheck` — draws custom checkboxes: green fill + white ✓ (checked), yellow fill + white — (partial), transparent + gray border (unchecked)

### Tree-based conversation management

Conversations, folders, and messages are organized in a hierarchical tree stored in `tree.json`, replacing the old flat `conversations` SQL table (migrated and backed up as `conversations_bak`).

**Tree structure.** A flat list of nodes with `parent_id` references:
- `FolderNode` — directory, can contain sub-folders, conversations, and message nodes. Has `context_block_ids` (linked ContextBlocks) and `attachment_paths` (file paths for context injection).
- `ConversationNode` — container for messages; its `id` is the FK into `messages` SQLite table. Has `summary`, `message_count`. Its children are `MessageNode` instances.
- `MessageNode` — represents a single message in the tree. Has `message_id` (FK to `messages`), `role` (user/assistant/thinking), `preview` (first 200 chars). Always a leaf (no children).
- `TreeRoot` — top-level container with `version` and `nodes: list[AnyTreeNode]`.
- `TrashEntry` — soft-deleted node record in `recycle_bin.json`. Contains `json_path` (human-readable path like "root/目录/对话") and full `node_data` for restore.

**Enabling/disabling and context collection.** Nodes have an `enabled: Union[bool, Literal["some"]]` field. The effective enabled state follows **top-down cascade**: walk from node upward to root, the highest (root-closest) non-`"some"` ancestor wins. `True`/`False` cascade to all descendants. When building LLM context, `ContextService._collect_context_resources()` walks up the parent chain from the active conversation, collecting `context_block_ids` and `attachment_paths` from enabled folders.

**Soft delete & recycle bin.** Deleting a node moves it to `recycle_bin.json`. Two modes:
- `recursive` — entire subtree removed, all nodes → trash
- `raise` — only the node is removed, children promoted to parent

**Tree UI (PySide6).** `TreePanel` widget (`app/ui/widgets/tree_panel.py`) renders the tree using `QTreeView` + `QStandardItemModel`. Features:
- DFS-ordered node list → nested `QStandardItem` tree via recursive `create_subtree()`
- Message nodes formatted as `👤 用户: 前30字…` with role-specific icons (👤/🤖/🧠/⚙️)
- Right-click `QMenu` for create/rename/delete/toggle/manage-context/attach-files — menu items vary by node type
- Drag-and-drop via custom MIME type (`application/x-treenode-id`), with `_TreeView.dropEvent` intercepting drops (message nodes not draggable, only folders accept drops)
- Checkboxes on all node types (including messages) with custom `_TreeStyle` rendering
- `setExpandsOnDoubleClick(True)` — single click triggers `switch_conversation`, double click expands/collapses
- Expand state tracked via `_expanded_ids: set[str]` and restored on `_rebuild()`
- Active node highlighted with `Colors.BG_OVERLAY` background

**Node cache.** `TreeStore` maintains a `_node_cache: dict[str, AnyTreeNode]` for O(1) node lookup. The cache is rebuilt on every `_save_tree()` call, ensuring consistency with the JSON file.

**Message-to-tree sync.** After each streaming response completes, `ChatApp._sync_message_widget_ids()` matches `ChatMessage` widgets to ViewModels by role + content prefix, then registers their IDs for scroll-to-message targeting.

**Scroll positioning (Phase 5).** `MessageListView` supports:
- Message registration (`register_message`) for O(1) widget lookup by ID
- Smooth scroll-to-message via `QPropertyAnimation` on scrollbar value (300ms, OutCubic easing)
- 2-second highlight with automatic fade-out
- Auto-follow mode: scrolls to bottom during streaming; user scrolling up pauses it, scrolling back down resumes
- Per-conversation scroll state save/restore (persisted to `data/scroll_positions.json`)
- `QTimer.singleShot(0, callback)` used to defer scroll operations until after Qt layout completes

**Migration path.** On first launch after upgrade, `database._migrate_if_needed()` converts old `conversations` table rows to `ConversationNode` entries under the "未分类" root folder, then renames the old table to `conversations_bak`.

### Fork / branch feature (分叉功能)

Design spec: `分支功能设计说明.md` (project root, Chinese). Conflict priority declared in the spec: Ch3 > Ch1 > Ch4 > Ch5 > Ch2. Implementation plan: `FORK_PLAN.md`.

**Core concepts.** A *modified node* (edited-and-resent user message, or regenerated assistant) has **no marker**; it is identified as "the successor of a fork point". The *fork point* = the modified node's predecessor (or the conversation node itself when the first message is modified). Invariant: fork point and modified node are always adjacent.

**Storage split.** `tree.json` holds ONLY the current chain (per conversation, the `MessageNode` children). All historical branches live in SQLite:
- `fork_nodes` — archived node metadata (title/preview/enabled/thinking_message_id/fork fields). `role` column exists; `_ensure_column` backfills older tables.
- `branch_nodes` — path-decomposition branch lists: rows `(conversation_id, fork_point_id, branch_index, node_id, position)`; a branch = rows with same `(fork_point_id, branch_index)` ordered by `position`, **head = the fork point itself**, list ends at the next fork point or chain end.

**Fork fields on tree nodes** (MessageNode + ConversationNode): `is_fork_point`, `fork_branch_count` (n), `fork_current_index` (m, 0-based; UI shows m+1/n). Invariant: `is_fork_point == (branch_count >= 2)`; degrade (count 1→0) deletes the last branch record (ruling A) and the view shows the remaining branch's full content (ruling B).

**Dirty-marking sync (spec 3.4).** `TreeStore._dirty_conversations` — every tree-mutating op marks its conversation(s) dirty. **Sync (tree.json → DB) happens ONLY at the 3 branch triggers**: branch switch, branch delete, branch create (`BranchService.sync_dirty_conversations()` first). `replace_conversation_chain()` replaces a conversation's chain without marking dirty (it IS a sync trigger).

**Key operations (`BranchService`, `app/core/branch_service.py`)**: `create_branch` (first fork archives the old chain explicitly — never rely on dirty state, it isn't persisted across restarts), `extend_current_branch` (edit-resend / send / continue append into the current branch record — keeps branch records in sync with chain growth), `switch_branch` (nested forks expand via **largest index**, `touched` map updates all fork-point indexes), `delete_branch` (cascade deletes sub-branches, degrade, auto-switch to previous branch / last if first deleted, physical orphan-row cleanup incl. bound thinking rows, trash-reference kept), `delete_fork_point` (3.3.4: fork identity + data transfer to the predecessor; merge into existing fork point: indexes continue, counts add, index kept), `migrate_branch_data` / `transfer_branch_group` (drag helpers).

**ConversationService integration**: `regenerate_message` = fork mode (pre-fork: **nothing persisted until natural completion** — `completed` flag; stop/error = discard & revert per ruling, no incomplete marks); `resend_edited_message` = branch created at send time (new node id, old row preserved in archived branch), extended + incomplete cleared on completion, stop = normal incomplete paused state (plus "放弃本次修改" abandon button); delete routing: modified node → **hard delete** (never recycle bin, confirm dialog), fork point → transfer + soft delete, conversation/folder → branch data cascade at soft-delete, restore clears fork markers; `cleanup_incomplete_nodes` is branch-aware (incomplete modified nodes are always chain-tail — branch deletion is effectively non-cascading). `get_fork_info_map` feeds the `<m/n>` controls.

**UI (P3)**: ChatMessage edit button (✏️, complete user messages), `_ForkControl` (`‹ m/n ›` under modified bubbles), abandon button, translucency, `set_actions_locked`; InputArea edit mode (✕ button, `set_text_locked` keeps the stop button enabled); ChatApp states `_edit_node_id` / `_prefork` (regenerate pre-fork: hidden tail widgets snapshot, streaming bubble, stop = revert) and `_can_mutate_tree(allow_send)` gating on all tree-mutating handlers; TreePanel `set_ops_locked` (drag/checkbox/context-menu interception), `set_edited_node`, `fork_display` title prefix.

**Drag & drop (P4, spec 3.6)**: `ConversationService.move_message_with_fork(dragged_id, parent, position, prev_id, next_id)` — modified node same-conversation → rejected; "between fork point & modified node" → inserted node becomes the new fork point (data migrates, old degrades); fork point moved away → data re-anchors to the post-fill predecessor; cross-conversation modified node → whole-group transfer (X + tail chain move, target predecessor becomes fork point, rulings); plain moves unchanged. TreePanel computes `prev_id/next_id` (recursive model lookup, `_drop_sibling_context`) and passes them via the extended `move_node` signal; cross-conversation transfer shows a confirm dialog; rejections show a QToolTip hint.

### Key files

| File | Role |
|------|------|
| `main.py` | Entry point, dependency tree assembly, dark palette + Fusion style setup |
| `config.py` | Global config (`.env` loader + mutable settings) |
| `app/core/conversation_service.py` | Main orchestrator — full send-message pipeline, tree CRUD |
| `app/core/context_service.py` | Builds the LLM context (system prompt + history + tree context) |
| `app/core/protocols.py` | Abstract interfaces (`MessageRepoProtocol`, `TreeStoreProtocol`, etc.) |
| `app/core/knowledge_service.py` | KG extraction from conversations via LLM |
| `app/core/branch_service.py` | 分叉编排:同步(3.4)、建/切/删分支、分叉点迁移、拖拽数据迁移 |
| `app/storage/branch_store.py` | fork_nodes/branch_nodes 表 CRUD + 引用收集(孤儿清理用) |
| `app/core/file_service.py` | File parsing orchestration |
| `app/core/search_service.py` | Search adapter orchestration |
| `app/storage/tree_store.py` | Tree JSON persistence + recycle bin (`tree.json`, `recycle_bin.json`) |
| `app/storage/message_repo.py` | SQLite message persistence (`messages` table) |
| `app/storage/database.py` | SQLite DDL + migration from old `conversations` table |
| `app/storage/context_store.py` | Context blocks + templates persistence |
| `app/storage/kg_store.py` | Knowledge graph SQLite persistence |
| `app/storage/models.py` | Domain data classes shared across all layers |
| `app/adapters/deepseek_client.py` | DeepSeek API HTTP client with SSE streaming |
| `app/adapters/search_adapters/` | Web search (DuckDuckGo) + Arxiv adapters |
| `app/adapters/file_parsers.py` | File parsers (txt, md, json, docx, pdf) |
| `app/controllers/app_controller.py` | Main controller — maps UI events → Core calls → ViewModels |
| `app/controllers/view_models.py` | ViewModel dataclasses (`TreeNodeVM`, `MessageVM`, `StreamChunkVM`, etc.) |
| `app/controllers/settings_controller.py` | Settings controller (model, thinking, search toggles) |
| `app/controllers/command_builder.py` | Parameter packing for Core service calls |
| `app/ui/app.py` | ChatApp — main application class, holds MainWindow, manages conversation lifecycle, streaming, signal wiring |
| `app/ui/main_window.py` | MainWindow — 3-column QHBoxLayout, MessageListView with scroll tracking, right panel QStackedWidget |
| `app/ui/async_bridge.py` | AsyncStreamWorker, StopGenerationWorker, AsyncTaskRunner — bridge asyncio → Qt signals |
| `app/ui/theme.py` | Colors, Fonts, Spacing, Radius constants + `build_global_stylesheet()` |
| `app/ui/widgets/tree_panel.py` | TreePanel — QTreeView + QStandardItemModel, _TreeStyle (custom checkboxes/arrows), right-click menus, drag-drop |
| `app/ui/widgets/sidebar.py` | Sidebar — header + action buttons + TreePanel + bottom entries |
| `app/ui/widgets/chat_message.py` | ChatMessage — role-styled message bubble, Markdown rendering, copy/regenerate/remember buttons |
| `app/ui/widgets/input_area.py` | InputArea — text input, ModelSelector, ToggleChip (thinking/search), AttachmentBar, send/stop buttons |
| `app/ui/widgets/context_panel.py` | ContextPanel — context block list, preview, template management |
| `app/ui/widgets/kg_panel.py` | KGPanel — knowledge graph entity/relation viewer |
| `app/ui/widgets/dialogs.py` | Reusable dialogs (rename, new folder, confirm, context block manager, recycle bin) |
| `tests/test_tree_store.py` | TreeStore unit tests (25 tests) |
| `tests/test_conversation_service.py` | ConversationService + ContextService unit tests (14 tests) |
| `scripts/sync_db_with_tree.py` | One-time: prune orphan SQLite message rows based on tree.json/recycle_bin.json |
| `scripts/fix_thinking_order.py` | One-time: fix tree.json message order where assistant precedes thinking |

### Legacy Flet code

The original Flet-based UI is preserved in `app/ui_flet_legacy/` for reference:
- `app/ui_flet_legacy/app.py` — original `ChatApp` (Flet)
- `app/ui_flet_legacy/widgets/` — original Flet widget implementations
- `app/ui_flet_legacy/theme.py` — original Flet theme constants

These files are **not imported** by the current application. The Flet dependency remains in `requirements.txt` only because it hasn't been cleaned up.
