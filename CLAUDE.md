# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the app
python main.py

# Filter logs by layer (all layers use structured tags)
python main.py 2>&1 | grep "\[CORE \]"      # Core orchestration
python main.py 2>&1 | grep "\[ADPTR\]"      # Adapter layer (includes HTTP details)
python main.py 2>&1 | grep "\[STORAGE\]"    # Storage layer

# Enable debug-level logging
LOG_LEVEL=DEBUG python main.py
```

There are no tests, linters, or type-checkers configured in this project.

## Architecture

This is a desktop AI chat application (DeepResearch) built with **Flet** (Flutter-for-Python), powered by the **DeepSeek API**. It supports streaming chat, thinking/reasoning mode, web search, file upload/parsing, context block management, and a knowledge graph extracted from conversations.

### Layer stack (strict top-down dependency)

```
UI (app/ui/)          → Flet views and widgets, event handlers
Controllers (app/controllers/) → Thin bridge: UI events → domain calls → ViewModels
Core (app/core/)      → Business orchestration, depends only on Protocols
Adapters (app/adapters/) → External systems: DeepSeek API, search, file parsers
Storage (app/storage/) → SQLite (conversations) + JSON files (context blocks, KG)
```

Dependencies flow one way: `Storage → Adapters → Core → Controllers → UI`. The full dependency tree is assembled by hand in `main.py:43-113` — this is the single source of truth for "who injects whom."

### Key architectural decisions

**Config is a global mutable module (`config.py`).** Every layer imports `config` directly. Static values (API key, DB path) are loaded from `.env` at import time. Mutable values (`model_type`, `thinking_enabled`, `search_enabled`) are written by the UI and read by Core/Adapters — no dependency injection for config.

**Protocol-based dependency inversion (`app/core/protocols.py`).** Core services depend on `typing.Protocol` classes (`LLMClientProtocol`, `SearchAdapterProtocol`, `FileParserProtocol`, `ConversationRepoProtocol`, `ContextStoreProtocol`), not concrete implementations. Adapters and Storage implement these protocols. This means you can swap the LLM backend or search provider by writing a new adapter — no Core changes needed.

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

**UI layout.** Three-column: sidebar (conversation list) | message list + input area | slide-out drawer panels (context manager, KG manager). The `ChatApp` class in `app/ui/app.py` wires all widget callbacks to controller methods.

### Key files

| File | Role |
|------|------|
| `main.py` | Entry point, dependency tree assembly |
| `config.py` | Global config (`.env` loader + mutable settings) |
| `app/core/conversation_service.py` | Main orchestrator — full send-message pipeline |
| `app/core/context_service.py` | Builds the LLM context (system prompt + history + injections) |
| `app/core/protocols.py` | Abstract interfaces for dependency inversion |
| `app/adapters/deepseek_client.py` | DeepSeek API HTTP client with SSE streaming |
| `app/ui/app.py` | Flet app class — layout, event wiring, streaming UI updates |
| `app/storage/models.py` | Domain data classes shared across all layers |
