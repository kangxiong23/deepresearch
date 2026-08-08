# AGENTS.md

DeepResearch — PySide6 desktop AI chat app powered by the DeepSeek API (streaming, thinking mode, web search, tree-based conversations, fork/branch feature, knowledge graph). Read `CLAUDE.md` for the complete architecture, fork/branch spec, and UI details; `AGENTS.md` is the compact subset that prevents costly mistakes.

## Commands

```bash
pip install -r requirements.txt
python main.py                       # run app (LOG_LEVEL=DEBUG for debug logs)
python -m unittest tests.test_tree_store -v
python -m unittest tests.test_conversation_service -v
python -m unittest tests.test_branch_service -v
python -m unittest discover tests -v # full suite (unittest, NOT pytest)
```

Logs are layer-tagged; filter with grep: `[CORE ]` orchestration, `[ADPTR]` HTTP/parsers, `[STORAGE]`, `[TREE]`, `[CTRL]`, `[CTX ]`.

## Data safety (critical)

Any run or test that could modify persisted data (`data/`: SQLite, `tree.json`, `recycle_bin.json`, branch storage, context blocks) **must first back up `data/`**, or point `config.py` paths at a temp dir (as `tests/` do with `ConfigScope`). `assemble_app()` / `initialize_database()` run migrations that can rewrite `tree.json` — never run them on real data without a backup.

## Architecture

- **Strict layers, top-down only**: `Storage → Adapters → Core → Controllers → UI`. The full dependency injection tree is hand-assembled in `main.py:53-133` — the single source of truth for wiring.
- **`config.py` is a global mutable module.** The UI writes `model_type`, `thinking_enabled`, `reasoning_effort`, `search_enabled`; Core/Adapters read them fresh on every call. These four vars are the only frontend↔backend sync point. `ConfigScope` provides per-thread overrides (tests).
- **Chains are per-conversation `MessageNode` children in `tree.json` — the single source of truth** for message existence, ordering, and enablement. SQLite `messages` table holds rows only (content); orphan rows drift from the tree and are pruned by `scripts/sync_db_with_tree.py`.
- **THINKING messages (LLM drafts) never enter the LLM context** — display-only, bound to their assistant node via `thinking_message_id`.
- **Fork/branch (分叉) is the most complex subsystem.** Before touching `app/core/branch_service.py`, `app/storage/branch_store.py`, or fork UI (`_ForkControl`, TreePanel drop/drag), read `分支功能设计说明.md`, `FORK_PLAN.md`, and the CLAUDE.md fork section.

## One-time maintenance scripts (dry-run by default, `--execute` to apply)

```bash
python scripts/sync_db_with_tree.py [--execute]      # prune orphan message rows
python scripts/fix_thinking_order.py [--execute]     # fix thinking-before-assistant ordering
```

## Gotchas

- `deepseek-v4-pro`/`deepseek-v4-flash` behave identically re: thinking. When `thinking_enabled` is falsy the payload sends `thinking: {type: "disabled"}` and **must NOT** include `reasoning_effort` (API rejects it); when enabled send `{type: "enabled"}` plus `reasoning_effort` (`low|high|max`).
- `temperature` is only sent when `thinking_enabled` is off (flash and pro alike); thinking mode → no `temperature`.
- Web search has **no model gating** — both models get it. Search results (external Bing/DDG/Arxiv adapters) are injected as text into the user message; the payload itself has no search field.
- `app/ui_flet_legacy/` is dead Flet code — never imported. Requirements still include Flet (unused).
- `DEEPSEEK_API_KEY` is required; load `DEEPSEEK_API_KEY` etc. from `.env` (`.env.example` is documented in README but not checked in).