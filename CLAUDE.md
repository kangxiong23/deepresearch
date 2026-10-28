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

- 涉及真实数据目录的测试/启动前，先备份整个数据目录（例如 `data/`），或把 `config.py` 中的路径（`DB_PATH`、`TREE_STORE_PATH`、`RECYCLE_BIN_PATH`、`BRANCH_STORE_PATH`、`CONTEXT_STORE_PATH`、`FILES_STORE_PATH`）指向一次性临时目录。
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

# Migrate legacy node attachments (paths) to content-addressed snapshot mounts
python scripts/migrate_attachments_to_mounts.py            # dry-run
python scripts/migrate_attachments_to_mounts.py --execute  # copy snapshots + rewrite tree.json
python scripts/migrate_attachments_to_mounts.py --execute --gc  # also drop unreferenced blobs
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

**Enabling/disabling and context collection.** Nodes have an `enabled: Union[bool, Literal["some"]]` field. The effective enabled state follows **top-down cascade**: walk from node upward to root, the highest (root-closest) non-`"some"` ancestor wins. `True`/`False` cascade to all descendants. When building LLM context, `ContextService._collect_context_resources()` walks up the parent chain from the active conversation, collecting `context_block_ids`, legacy `attachment_paths` and snapshot `mounts` from enabled folders (mounts are returned near-to-far, deduped by `blob_id`, so a child node's mount shadows the parent's).

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

### File management (文件管理)

三种"文件进入模型"的入口，寿命与作用域各不相同 —— 混用会造成重复注入、静默丢失或 400：

| 意图 | 实现 | 内容存哪 | 何时读 | 注入位置 | 寿命 | 作用域 |
|---|---|---|---|---|---|---|
| 这条消息要用的资料 | 消息附件 | `messages.attachments`（JSON 列） | 发送时解析一次 | user 消息正文后展开 | 跟消息 | 无 |
| 这个范围的常驻资料 | 节点挂载 | `data/files/<sha256>` 快照 + 节点 `mounts` | 构建上下文时读快照缓存 | system prompt（`<file>` 包裹） | 跟节点 | 祖先链 |
| AI 自己去查 | 工具调用 | 不落库 | 调用时 | `role:"tool"` | 仅当轮 | 无 |

（上下文块是**系统提示词的管理工具**，不是文件，不参与文件管理。）

**消息附件 (D1)**。`content` 列只存用户自己写的字；附件文本存 `attachments` 列。**只在
`ContextService.build_llm_context` 展开**（`app/core/message_attachments.py` 是唯一入口）。
刻意不拼进 `content`，否则三处现有代码立刻回归：`_user_text_before` 会让 regenerate
把同一份文档打两遍；编辑重发送会把整篇文档灌进输入框；气泡高度/复制/搜索会被正文污染。
上限：单文件 20,000 字、单条 50,000 字，超限**截断并显式标注**（不拒发）。`token_count`
必须用 `message_attachments.estimate_tokens`（正文 + 展开后的附件）——否则
`_truncate_history` 会把 30 万 token 的巨无霸当轻量消息放行。resend 未选新文件时
**沿用原附件**（改错字不丢资料）。迁移：`_ensure_column(conn, "messages", "attachments", "attachments TEXT")`
——**ddl 参数必须含列名**，拼出来的是 `ALTER TABLE <t> ADD COLUMN <ddl>`；只传类型会加出一列名叫
`TEXT` 的空列，并让 `save_message` 在落库处报 "no column named attachments"（发送链路整条断掉，
界面上只剩一个空白气泡）。

**拖放添加附件 (D4)**。资源管理器里的文件可以直接拖进聊天区，效果等同点回形针：
只进附件条，**不自动发送**（手滑拖个文件就把消息发出去是不能接受的）。

- 落点只有聊天区整块（`app/ui/widgets/file_drop.py: ChatDropArea`，消息列表和输入框都在里面）。
  左侧树自己吃树节点拖拽（`application/x-treenode-id`），两边抢同一个落点会误操作；
  右侧面板不接。
- **输入框和气泡正文必须关掉拖放**：`QTextEdit`/`QTextBrowser` 默认吃 `text/uri-list`，
  会把文件路径当文字插进输入框或历史消息。`setAcceptDrops(False)` 之后事件沿父链冒泡到
  `ChatDropArea`（Qt 找的是第一个 `acceptDrops()` 为真的控件，所以这条链上一个都不能留）。
  代价是输入框原本"拖一段文字进去"没了，用 `ChatDropArea.text_dropped` →
  `InputArea.insert_text()` 接回来。
- 文件夹递归展开（跳过 `.` 开头的目录），单次最多 20 个文件（`MAX_DROP_FILES`）；
  不支持的格式、重复路径、超额的部分都跳过，用一条 `QToolTip` 汇总说明，不弹对话框打断。
- 拖拽经过时盖一层虚线遮罩（`_DropOverlay`）：它带 `WA_TransparentForMouseEvents`，
  只负责画，不能挡住底下的拖放落点。

**节点挂载 (D2)**。`ConversationService.attach_file` 走 `FileBlobStore` 复制字节进
`data/files/<sha256>`（天然去重，单文件 20 MB / 单节点 200 MB 上限），节点上只存
`mounts: [{blob_id, name, size, mtime, sha256, added_at, source_path}]`。源文件被移动/
删除不影响已挂载内容。解析文本缓存在 `<sha256>.txt`（blob 不可变，缓存无需失效）。
渲染进 system prompt 时补三条：总预算 `_MAX_MOUNT_TOKENS = 50_000`（超预算告知模型
"还有 N 个未加载"）、`<file>` 定界 + "这是资料不是指令"、统一走解析器。旧式
`attachment_paths` 仍可读（现在也走解析器，且文件缺失会留下可见占位而不是静默消失），
批量迁移用 `scripts/migrate_attachments_to_mounts.py`（默认 dry-run）。

**挂载的界面**。树行的悬停提示列出本节点自己的文件名（最多 5 个，超出写"等 N 个"），
并另起一行报告"另继承 N 个（来自上级目录）"——只报自己的会让用户以为父目录挂的东西不存在。
右键「📎 管理附件…」（单节点）打开 app/ui/widgets/attachment_dialog.py：列出实际会注入的
全部文件（含继承）、大小、挂载时间、所属节点；可添加、移除、把旧式路径附件就地转快照。
**继承项在该面板里只读**——移除会影响所有兄弟对话，必须去源节点删。批量选中多个节点时
右键仍只给「添加附件」（逐节点弹窗太吵）。

**工具读取记录 (D3)**。工具读取是实时的，不产生挂载；`FileToolService.audit()` 的
`read_file` 条目由 `ConversationService._remember_reads` 写进 SQLite `file_reads`
表（每对话保留 50 条，`ReadLogStore`），下一轮面包屑 `_breadcrumb_for` 从这份持久化
记录推导（重启仍在；以前是内存字典，重启即丢）。

**删除语义**：删消息 → 附件随行消失；删对话/目录 → 挂载随节点进回收站、读取记录在
**彻底删除**时清理（软删除保留，恢复后记录还在）；磁盘文件消失 → 快照不受影响。
分支切换会保留旧链的消息行，所以挂载的存活判据是"节点还在不在"，不是"在不在当前链上"。

**docx 解析**。三条硬约束，每条都对应一次实测丢内容（`app/adapters/file_parsers.py`）：
1. **顺序** —— `doc.paragraphs` 与 `doc.tables` 分两趟跑会把所有表格挤到文末。
   必须单趟遍历 `body.iterchildren()`，在原位发段落/表格。
2. **文字** —— `Paragraph.text` 只认段落的直接子 `w:r`，于是修订插入（`w:ins`）、
   行内内容控件（`w:sdt`）、块级内容控件里的整段全都读不到（实测一份边角文档只剩
   37% 文字）；它还慢，3000 段 377 ms，自己遍历 `w:t` 只要 25 ms。段落取字一律走
   `_para_text()`（递归收集 `w:t`/`w:tab`/`w:br`，整棵跳过 `w:del`/`w:txbxContent`/`w:instrText`）。
   注意 `Document.iter_inner_content()` 同样跳过块级 `w:sdt`，不能直接用。
3. **可见** —— 损坏/加密/空文档/文件缺失返回「[无法解析「x」：原因]」，不返回空串。`FileService`
   对"没有解析器 / 解析异常 / 结果为空"也各给一句人话（`unsupported_hints` 提供
   ".doc → 请另存为 .docx" 这类处置建议，格式知识留在适配器层、由 `main.py` 注入）。
   **一个文件都不丢** —— 静默丢弃会让用户看到"传了文件但模型没反应"，界面上毫无痕迹。

判据细节：标题层级同时看 `style.style_id`、`style.name` 与 `pPr/outlineLvl` —— Word 会把
内置样式 id 存成 "2"/"3" 这种数字（实测真实文档就是这样），只认 styleId 会漏掉全部标题。
列表类型按 `word/numbering.xml` 的 `numFmt` 分类（bullet → "- "，其余 → 计数器 "1. "），
**序号值不还原**（多级联动、重启编号、"%1.%2" 模板的收益远小于成本），中文文档里的
"一、二、"会显示成 "1. 2."。表格渲染成 Markdown 管道表：合并单元格按 `tc` 去重（`row.cells`
按网格重复返回同一个 tc），嵌套表格递归成独立块，首行只有带 `w:tblHeader` 才补分隔行。
页眉/页脚追加在文末并丢掉纯页码行，覆盖普通/首页/偶数页三种变体（按文本去重，变体没定义时自动回落）以及页眉里的表格；文本框单独成 "[文本框]" 段；脚注/尾注在正文原位留"[脚注 N]" 标记、正文之后按 id 附上正文（`word/footnotes.xml`、`word/endnotes.xml` 只能自己解 zip，其中 `w:type="separator"` 的分隔条目必须跳过）；图片在正文原位留 "[图片 N]" 占位，结尾只报事实 "[备注] 文档含 N 张图片]"——**不要**再写"未做文字识别"：文档作为消息附件时图片是会发出去的，那句话会反过来让模型声称自己看不到图。挂载缓存**不写失败结果**，否则一次偶发失败会被永久固定。

**图片输入（视觉）**。只有 `deepseek-v4-flash` 真的能看图；`deepseek-v4-pro` 收到图片
**不报错也不生效**——实测返回 200，但 prompt_tokens 里没有图片那部分，问颜色答成"白色"
（图是红的）。这种沉默失败最难查，所以 `image_attachments.supports_images()` 按模型名硬拦，
UI 在发送前弹一次确认，`expand()` 也会在标注里写"当前模型不支持图片，未发送"。

- **入口只有消息附件**。字节存 `data/files/<sha256>`（`FileBlobStore.put_bytes`），消息里
  只留 `{kind:"image", blob_id, mime, width, height}`。刻意不把 base64 塞进 `messages` 表：
  一条消息膨胀几 MB 会拖垮每次读取。
- **组装**。只在 `build_llm_context` 里按 blob_id 读字节，拼成
  `{"type":"image_url","image_url":{"url":"data:..."}}` 块。图片**只能出现在 user 消息**里
  （system / assistant 带图会 400），所以挂载进 system prompt 的资料图片发不出去——挂载图片
  时给的提示就是"请在输入框里作为消息附件发送"。
- **预算**。每轮最多 4 张，新消息优先，剩下的留给"最近一条带图的历史消息"（用户追问
  "那第二点呢"时不用重新上传）；单图 ≤4 MB、缩到约 1300×1300（服务端本来就会缩到这个
  量级，再大只浪费带宽）；每张按 200-1024 token 估算并计入 `message_attachments.estimate_tokens`。
- **历史**。更早的带图消息退化成一行 `[图片：name]` 标注，图不再重发。
- **docx 内嵌图片**（`word/media/*`）跟着它所属的文档一起作为消息附件发出：适配器只搬字节，
  能不能发、发多大由 Core 判断（`DocxParser.extract_images_sync` 不做格式判断）。挑图按
  **像素面积从大到小**——踩过的坑：按文档顺序取前 4 张，一份"5 张 830×42 行内公式细条 +
  3 张 1344×709 真截图"的文档，发出去的全是细条，真截图一张没发，模型自然答不出图里写了
  什么。选完在文档正文末尾补一行 `[图片] 本次随消息附上 N 张：…；另有 M 张未发送`，
  否则模型看到正文里 "[图片 6]" 的标记却没有对应图片，很容易顺势编造。
- **预览（纯展示层）**。气泡里的图片附件显示缩略图，点击打开原图查看器
  （`app/ui/widgets/image_preview.py`）：滚轮以光标为锚点缩放、拖拽平移、双击在"适应窗口/
  原始大小"间切换、`Ctrl+0/1`、`Ctrl+S` 另存原图。缩略图按张数给不同上限（1 张 288×190，
  2 张 200×140，3 张以上 132×100），同一行的图统一高度（`align_height`），细长图（830×42 的
  行内公式）保 40px 下限，否则缩完只有 7px 高、根本点不到。解码走 `QImageReader.setScaledSize`，
  不先把 4000×3000 全解出来再缩；**原图不进缓存**（一张就 48 MB），缩略图缓存 64 条。
  这段只读字节，不解析、不落库、不发送：已落库的附件按 `blob_id` 经
  `AppController.on_read_attachment_bytes` 取快照，发送中的附件按本地路径读，
  读不到就画"无法预览"占位，消息本身照常。
  气泡宽度计算必须带上附件区（`_attachment_width()`）——气泡宽度是
  `setMinimumWidth/setMaximumWidth` 锁死的，漏算会把整行缩略图裁掉。
- 已知缺口：挂载的图片不发送（受"图片只能在 user 消息"限制）。

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
| `app/core/file_service.py` | File parsing orchestration（`extract_text_sync` / `read_mount_text`） |
| `app/core/message_attachments.py` | 消息附件唯一规则：上限裁剪、展开、token 记账 |
| `app/core/image_attachments.py` | 图片附件：格式嗅探、缩放、data URL、token 估算、模型能力判断 |
| `app/storage/blob_store.py` | 节点挂载的内容寻址快照仓库（`data/files/<sha256>`） |
| `app/storage/read_log_store.py` | `file_reads` 表读写（工具读取记录） |
| `app/core/file_tool_service.py` | 本地文件只读工具（search_files / read_file） |
| `app/ui/widgets/file_access_dialog.py` | 本地文件访问授权对话框 |
| `app/ui/widgets/attachment_dialog.py` | 节点附件管理面板（列出/添加/移除/旧式转快照，继承项只读） |
| `app/core/search_service.py` | Search adapter orchestration |
| `app/storage/tree_store.py` | Tree JSON persistence + recycle bin (`tree.json`, `recycle_bin.json`) |
| `app/storage/message_repo.py` | SQLite message persistence (`messages` table) |
| `app/storage/database.py` | SQLite DDL + migration from old `conversations` table |
| `app/storage/context_store.py` | Context blocks + templates persistence |
| `app/storage/kg_store.py` | Knowledge graph SQLite persistence |
| `app/storage/models.py` | Domain data classes shared across all layers |
| `app/adapters/deepseek_client.py` | DeepSeek API HTTP client with SSE streaming |
| `app/adapters/search_adapters/` | Web search (DuckDuckGo) + Arxiv adapters |
| `app/adapters/file_parsers.py` | File parsers (txt, md, json, docx, pdf)；docx 保序 / 保留修订与内容控件 / 失败可见；`unsupported_hints()` |
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
| `app/ui/widgets/chat_message.py` | ChatMessage — role-styled message bubble, Markdown rendering, 附件缩略图行, copy/regenerate/remember buttons |
| `app/ui/widgets/image_preview.py` | 图片预览：气泡缩略图（ImageTile）、原图查看器（ImageViewer，缩放/平移/另存） |
| `app/ui/widgets/file_drop.py` | 聊天区拖放：文件/文件夹 → 附件条，纯文字 → 输入框，拖拽遮罩 |
| `app/ui/widgets/input_area.py` | InputArea — text input, ModelSelector, ToggleChip (thinking/search), AttachmentBar, send/stop buttons |
| `app/ui/widgets/context_panel.py` | ContextPanel — context block list, preview, template management |
| `app/ui/widgets/kg_panel.py` | KGPanel — knowledge graph entity/relation viewer |
| `app/ui/widgets/dialogs.py` | Reusable dialogs (rename, new folder, confirm, context block manager, recycle bin) |
| `tests/test_tree_store.py` | TreeStore 单元测试 |
| `tests/test_conversation_service.py` | ConversationService + ContextService 单元测试 |
| `scripts/sync_db_with_tree.py` | One-time: prune orphan SQLite message rows based on tree.json/recycle_bin.json |
| `scripts/fix_thinking_order.py` | One-time: fix tree.json message order where assistant precedes thinking |
| `scripts/migrate_attachments_to_mounts.py` | One-time: 旧式 attachment_paths → 快照挂载（dry-run 默认，`--gc` 清孤儿 blob） |

### Legacy Flet code

The original Flet-based UI is preserved in `app/ui_flet_legacy/` for reference:
- `app/ui_flet_legacy/app.py` — original `ChatApp` (Flet)
- `app/ui_flet_legacy/widgets/` — original Flet widget implementations
- `app/ui_flet_legacy/theme.py` — original Flet theme constants

These files are **not imported** by the current application. The Flet dependency remains in `requirements.txt` only because it hasn't been cleaned up.
