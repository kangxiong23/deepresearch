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
python main.py 2>&1 | grep "\[TREE \]"      # Tree operations (Phase 4+)

# Enable debug-level logging
LOG_LEVEL=DEBUG python main.py
```

## Architecture

This is a desktop AI chat application (DeepResearch) built with **Flet** (Flutter-for-Python), powered by the **DeepSeek API**. It supports streaming chat, thinking/reasoning mode, web search, file upload/parsing, context block management, and a knowledge graph extracted from conversations.

### Layer stack (strict top-down dependency)

```
UI (app/ui/)          → Flet views and widgets, event handlers
Controllers (app/controllers/) → Thin bridge: UI events → domain calls → ViewModels
Core (app/core/)      → Business orchestration, depends only on Protocols
Adapters (app/adapters/) → External systems: DeepSeek API, search, file parsers
Storage (app/storage/) → SQLite (messages) + JSON files (tree, context blocks, KG)
```

Dependencies flow one way: `Storage → Adapters → Core → Controllers → UI`. The full dependency tree is assembled by hand in `main.py:43-113` — this is the single source of truth for "who injects whom."

### Key architectural decisions

**Config is a global mutable module (`config.py`).** Every layer imports `config` directly. Static values (API key, DB path) are loaded from `.env` at import time. Mutable values (`model_type`, `thinking_enabled`, `search_enabled`) are written by the UI and read by Core/Adapters — no dependency injection for config.

**Protocol-based dependency inversion (`app/core/protocols.py`).** Core services depend on `typing.Protocol` classes (`LLMClientProtocol`, `SearchAdapterProtocol`, `FileParserProtocol`, `MessageRepoProtocol`, `ContextStoreProtocol`, `TreeStoreProtocol`), not concrete implementations. Adapters and Storage implement these protocols. This means you can swap the LLM backend or search provider by writing a new adapter — no Core changes needed.

**Send-message flow (`ConversationService.send_message`).** The core orchestration pipeline:
1. Extract text from attached files via `FileService`
2. Run web search via `SearchService` (gated by `config.search_enabled`)
3. Build the full LLM context via `ContextService` (system prompt + history + context blocks + search results + KG injection)
4. Persist the user message
5. Stream from `LLMClient.stream_chat()` — chunks are yielded directly to the caller, no buffering in Core
6. On `is_done`: persist the assistant message (and separate thinking message if reasoner mode), update conversation metadata

**Streaming abort.** `ConversationService._stop_event` (an `asyncio.Event`) is set by `stop_generation()`. The streaming loop checks it on each chunk and breaks. The LLM client's `abort()` is also called to close the underlying HTTP connection.

**Knowledge graph.** After each assistant reply, the UI can trigger `KnowledgeService` to extract entities and relations from recent messages via LLM call. On subsequent messages, matching KG entries are injected into the system prompt (`config.kg_injection_enabled` controls this).

**Search backends.** Two adapters with no API keys needed: `DuckDuckGoSearchAdapter` (tries Bing first, falls back to DuckDuckGo Lite) and `ArxivSearchAdapter` (academic papers via Arxiv Atom Feed API).

**UI layout.** Three-column: sidebar (tree panel with folders + conversations) | message list + input area | slide-out drawer panels (context manager, KG manager). The `ChatApp` class in `app/ui/app.py` wires all widget callbacks to controller methods.

### Tree-based conversation management (Phase 4+)

Conversations and folders are organized in a hierarchical tree stored in `tree.json`, replacing the old flat `conversations` SQL table (migrated and backed up as `conversations_bak`).

**Tree structure.** A flat list of nodes with `parent_id` references:
- `FolderNode` — directory, can contain sub-folders and conversations. Has `context_block_ids` (linked ContextBlocks) and `attachment_paths` (file paths for context injection).
- `ConversationNode` — leaf node, its `id` is the FK into `messages` SQLite table. Has `summary`, `message_count`.
- `TreeRoot` — top-level container with `version` and `nodes: list[AnyTreeNode]`.
- `TrashEntry` — soft-deleted node record in `recycle_bin.json`. Contains `json_path` (human-readable path like "root/目录/对话") and full `node_data` for restore.

**Enabling/disabling and context collection.** Nodes have an `enabled: Union[bool, Literal["some"]]` field. The effective enabled state follows **top-down cascade**: walk from node upward to root, the highest (root-closest) non-`"some"` ancestor wins. `True`/`False` cascade to all descendants. When building LLM context, `ContextService._collect_context_resources()` walks up the parent chain from the active conversation, collecting `context_block_ids` and `attachment_paths` from enabled folders.

**Soft delete & recycle bin.** Deleting a node moves it to `recycle_bin.json`. Two modes:
- `recursive` — entire subtree removed, all nodes → trash
- `raise` — only the node is removed, children promoted to parent

**Tree UI.** `TreePanel` widget (`app/ui/widgets/tree_panel.py`) renders the tree using nested `ft.ExpansionTile` controls. Supports:
- Right-click context menu (`ft.ContextMenu`) for create/rename/delete/toggle/manage-context/attach-files
- Drag-and-drop (`ft.Draggable` + `ft.DragTarget`) to move nodes between folders
- Enabled state indicators (✅/🚫/🔄) with tooltips

**Node cache.** `TreeStore` maintains a `_node_cache: dict[str, AnyTreeNode]` for O(1) node lookup. The cache is rebuilt on every `_save_tree()` call, ensuring consistency with the JSON file.

**Migration path.** On first launch after upgrade, `database._migrate_if_needed()` converts old `conversations` table rows to `ConversationNode` entries under the "未分类" root folder, then renames the old table to `conversations_bak`.

### Key files

| File | Role |
|------|------|
| `main.py` | Entry point, dependency tree assembly |
| `config.py` | Global config (`.env` loader + mutable settings) |
| `app/core/conversation_service.py` | Main orchestrator — full send-message pipeline, tree CRUD |
| `app/core/context_service.py` | Builds the LLM context (system prompt + history + tree context) |
| `app/core/protocols.py` | Abstract interfaces (`MessageRepoProtocol`, `TreeStoreProtocol`, etc.) |
| `app/storage/tree_store.py` | Tree JSON persistence + recycle bin (`tree.json`, `recycle_bin.json`) |
| `app/storage/message_repo.py` | SQLite message persistence (`messages` table) |
| `app/storage/database.py` | SQLite DDL + migration from old `conversations` table |
| `app/adapters/deepseek_client.py` | DeepSeek API HTTP client with SSE streaming |
| `app/ui/app.py` | Flet app class — layout, event wiring, streaming UI updates |
| `app/ui/widgets/tree_panel.py` | Tree panel widget — ExpansionTile rendering, context menus, drag-drop |
| `app/ui/widgets/sidebar.py` | Sidebar — houses TreePanel, buttons, bottom entries |
| `app/ui/widgets/dialogs.py` | Reusable dialogs (rename, new folder, confirm, context blocks, recycle bin) |
| `app/storage/models.py` | Domain data classes shared across all layers |
| `tests/test_tree_store.py` | TreeStore unit tests (26 tests) |
| `tests/test_conversation_service.py` | ConversationService + ContextService unit tests (13 tests) |
