# Fork / Branch Feature — Implementation Plan

Source spec: `分支功能设计说明.md` (in Chinese). Conflict priority declared in the spec:
**Ch3 > Ch1 > Ch4 > Ch5 > Ch2**. Internal contradictions found in the spec are listed in
§12 with the resolution applied (or flagged as open questions for the user).

---

## 1. Terminology (matches spec)

- **Modified node** — a user message that was edited-and-resent, or an assistant message
  that was regenerated. Carries **no marker**; identified by "first node after a fork point".
  Both the pre-edit node (old version) and post-edit node (new version) are modified nodes.
- **Fork point** — the node immediately **preceding** a modified node (its predecessor).
  The fork point records `n` (branch count) and `m` (current branch index). If the modified
  node is the conversation's first message, the fork point is the **conversation node itself**.
- **Invariant (must hold at all times)**: a fork point is always adjacent to its modified
  node (modified node = next node after the fork point).
- **Branch** — a node list starting at a fork point (head included), ending at the next
  fork point or leaf. Path-decomposition storage: `[fp, n1, n2, ...]`.
- **Branch index** — 0-based in storage, created as `max+1`; UI displays `m/n` with m 1-based.
- **Default selection rule** — when expanding a fork point without user choice: largest index.
- **Pre-fork state** — transient window (regenerate only): UI-first, backend-late; nothing
  persisted until streaming completes.

## 2. Current code facts this plan builds on

- `tree.json` holds the full **current chain** per conversation: `MessageNode` children of
  `ConversationNode`, ordered by `sort_order` (flat adjacency list, version 3.0).
- `messages` SQLite table holds message **content**; display and context building are both
  driven by tree order (`get_messages_for_node`, `get_enabled_history_messages` walk
  MessageNodes, then batch-load content by id). → **Switching branches = replacing the
  conversation's MessageNode children in tree.json; no other layer needs changes for
  context/KG/search to stay consistent.**
- Node serialization: `tree_store._node_to_dict` / `_dict_to_node` (tree_store.py:930-999).
- Regenerate today: deletes the last assistant node + bound thinking row, streams a new
  one **in place** (no branch). `regenerate_requested` signal carries no message id;
  `_handle_regenerate` always regenerates the last assistant.
- UI streaming: `_start_stream` / `_start_regenerate_stream` / `_start_continue_stream`
  in `app.py`, bridged by `AsyncStreamWorker` (queued signals) + `StreamRelay`.
  `_load_all_messages` defers rebuilds while `_streaming_message` is set (same guard will
  cover pre-fork). `_post_stream_refresh(aborted)` centralizes post-stream refresh.
- No "edit user message" button exists yet. `InputArea` has `send_requested(str, list)`,
  `stop_requested`, `set_generating`, `clear`.

---

## 3. Data model changes

### 3.1 tree.json — fork fields on MessageNode **and** ConversationNode

Both `MessageNode` and `ConversationNode` gain (defaults preserve backward compat):

| field | default | meaning |
|---|---|---|
| `is_fork_point: bool` | `False` | node acts as fork point |
| `fork_branch_count: int` | `0` | n |
| `fork_current_index: int` | `0` | m (0-based in storage) |

Invariant: `is_fork_point == (fork_branch_count >= 2)` (a fork point with 1 branch is
degraded back to ordinary — spec 3.3.2 step 5). ConversationNode gets the fields because
it can be a fork point when the first message is modified.

UI derives: modified node's `m/n` (chat control) from **predecessor's** fork fields;
fork-point title prefix `<m/n>` (tree panel) from the fork point's own fields.

### 3.2 New SQLite branch store — `app/storage/branch_store.py`

Two tables (DDL added to `database.py` initialization):

```sql
-- Archived node metadata (spec 3.4.2 "nodes 表")
CREATE TABLE IF NOT EXISTS fork_nodes (
    node_id             TEXT PRIMARY KEY,
    conversation_id     TEXT NOT NULL,
    title               TEXT NOT NULL DEFAULT '',
    preview             TEXT NOT NULL DEFAULT '',
    summary             TEXT NOT NULL DEFAULT '',
    parent_id           TEXT,
    sort_order          INTEGER NOT NULL DEFAULT 0,
    enabled             INTEGER NOT NULL DEFAULT 1,   -- 1/0/"some"→2
    incomplete          INTEGER NOT NULL DEFAULT 0,
    thinking_message_id TEXT,
    is_fork_point       INTEGER NOT NULL DEFAULT 0,
    fork_branch_count   INTEGER NOT NULL DEFAULT 0,
    fork_current_index  INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fork_nodes_conv ON fork_nodes (conversation_id);

-- Branch membership (spec 3.4.3 "branch_nodes 表", path-decomposition lists)
CREATE TABLE IF NOT EXISTS branch_nodes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    fork_point_id   TEXT NOT NULL,
    branch_index    INTEGER NOT NULL,
    node_id         TEXT NOT NULL,
    position        INTEGER NOT NULL,
    UNIQUE (fork_point_id, branch_index, node_id),
    UNIQUE (fork_point_id, branch_index, position)
);
CREATE INDEX IF NOT EXISTS idx_branch_nodes_fp ON branch_nodes (fork_point_id);
```

- A branch list = rows with same `(fork_point_id, branch_index)`, ordered by `position`.
  The head element is the fork point itself (list starts with `fork_point_id`).
- When the conversation node is the fork point, `fork_point_id == conversation_id`.
- `fork_nodes` is the archive of metadata (title/preview/summary/enabled/sort_order/...)
  for nodes no longer present in tree.json; `branch_nodes` is the archive of the chains
  themselves. `tree.json` = working copy, DB = archive (spec 3.4).

---

## 4. TreeStore changes (storage layer)

1. **Fork fields**: serialization in `_node_to_dict` / `_dict_to_node`, plus
   `update_node` whitelist, `_create_default_tree` (version stays "3.0" — additive fields,
   no migration bump needed; `_dict_to_node` defaults handle old files).
2. **Dirty marking** (spec 3.4.5): `_dirty_conversations: set[str]` + public
   `dirty_conversations`, `mark_conversation_dirty(conv_id)`, `clear_dirty(conv_id)`.
   Auto-marked at the end of every tree-mutating op: `create_node`, `update_node`,
   `move_node`, `delete_node`, `soft_delete_node`, `restore_from_trash`,
   `set_node_enabled_cascade_down`, `mark_incomplete`. Affected conversation id:
   node is ConversationNode → its own id; MessageNode → `parent_id`; folder ops → none.
3. **Chain helpers**:
   - `get_conversation_chain(conv_id) -> list[MessageNode]` (sorted children).
   - `replace_conversation_chain(conv_id, nodes: list[MessageNode])` — removes existing
     MessageNode children, inserts new ones with renumbered `sort_order`, rebuilds cache,
     saves. (BranchService uses this for switch/create; dirty-marking NOT applied here —
     these calls *are* sync triggers.)
   - `is_modified_node(node)` / `find_fork_point_of(node)` helpers (successor/fork-point
     adjacency checks used by delete & drag logic).

## 5. New Core service — `app/core/branch_service.py` (pure logic, no UI)

Depends on: `tree_store`, `branch_store`, `message_repo`. All sync (fast local ops).
`ConversationService` holds an instance and delegates.

### 5.1 Sync (spec 3.4) — `sync_dirty_conversations()`

For each dirty conversation (or an explicit conv_id):
1. Upsert every node of the current tree.json chain into `fork_nodes`
   (title/preview/summary/sort_order/enabled/incomplete/thinking_message_id/fork fields).
2. For each fork point **in the current chain**: ensure a branch record exists for its
   current branch that matches the chain tail below it; rewrite that branch list to match
   tree.json (spec 3.4.2 step 4: "确保当前链路上每个分叉点的分支节点列表与 tree.json 一致").
3. Clear dirty flag. — Called **only** on the three spec triggers: branch switch, branch
   delete, branch create (also on conversation/folder delete as a cascade cleanup).

### 5.2 Branch create — `create_branch(conv_id, fork_point_id, new_chain, node_updates)`

1. `ensure_branches_for(conv, fp)`: if no branch rows exist for `fp`, archive the current
   tree.json chain as branch 0 (count=1, index=0).
2. Append new branch with `index = count`; set `count += 1`, `index = new`.
3. Upsert new-chain node metadata into `fork_nodes`.
4. `replace_conversation_chain(conv, new_chain)` in tree.json.
5. Rebuild fork fields on all fork points in the new chain.

Used by **edit-resend at send time** (branch committed immediately, spec 5.3) and by
**regenerate at stream completion** (pre-fork commit, spec 3.1.4).

### 5.3 Extend — `extend_current_branch(conv_id, appended_nodes)`

Edit-resend streaming completion: append the new assistant node (+ any thinking binding)
to the current branch's list and to tree.json chain; clear the user node's `incomplete`.

### 5.4 Branch delete — `delete_branch(conv_id, fork_point_id, branch_index)`

Only reachable for **modified nodes** (the branch's first node after the fork point).
**Modified nodes are hard-deleted** — never sent to the recycle bin; the UI shows a
confirm dialog first (spec 3.3.1 删除方式).

1. Remove all rows of the branch list; collect its node ids.
2. **Cascade**: any branch whose fork point is inside the deleted list is deleted too
   (recursively) — spec 3.3.2 step 3.
3. Decrement `fork_branch_count` on the fork point.
4. **Degrade**: if count drops to 1 → clear `is_fork_point` / count / index on the fork
   point (tree.json + fork_nodes); the modified node becomes ordinary.
5. **Auto-switch** (user ruling Q1 / spec 3.3.2 step 7 = 3.3.3): switch to the **previous
   branch** (index-1); if the deleted branch was the first, switch to the largest-index
   remaining branch — via the switch path (§5.5).
6. **Physical deletion** (user ruling Q5): delete `messages` rows and `fork_nodes` rows
   of deleted-list nodes, EXCEPT nodes still referenced by the new tree.json chain, a
   remaining branch list, or a recycle-bin entry. (In practice the modified node itself
   is never in the recycle bin anymore, so its row is deleted too.)
7. Rewrite tree.json chain.

### 5.4a Fork-point deletion — `delete_fork_point(conv_id, fork_point_id)`

Spec 3.3.4 (user ruling Q2). Deleting a fork point must NOT change the modified node's
identity — the fork-point "signpost" role migrates to the deleted fork point's
**predecessor** in the current chain (or the conversation node if the fork point is the
conversation's first message):

1. New fork point P = predecessor of the deleted fork point F (in the current chain);
   if F has no predecessor → P = conversation node.
2. Re-anchor all of F's branch lists to P (head becomes P, F removed from each list).
   Remove F from its parent branch list (F was the tail of that list).
3. If P was an ordinary node: P inherits F's `fork_branch_count` / `fork_current_index`
   and becomes a fork point.
4. If P was already a fork point: F's branch data merges into P (branch indexes continue
   after P's existing ones), counts add up, current index stays on the user's active
   branch.
5. Delete F itself via the normal node-delete flow (soft delete to recycle bin, like any
   ordinary node).

### 5.5 Branch switch — `switch_branch(conv_id, fork_point_id, target_index)`

Spec 3.4.4 flow:
1. `sync_dirty_conversations()` (save current working state first).
2. Read target branch list `[fp, n1, n2, ...]`.
3. **Nested expansion** (spec 3.5.3 step 4): if the list's tail node is a fork point,
   append its **largest-index** branch list (minus head), recursively, until leaf.
4. Reconstruct full chain as `MessageNode`s: metadata from `fork_nodes` (or live tree
   nodes when present in tree.json), previews + thinking bindings from `messages`/`fork_nodes`.
5. Update `fork_current_index` on every fork point in the new chain (including the
   conversation node when it is the root fork point) in tree.json + fork_nodes.
6. `replace_conversation_chain` in tree.json; sync chain metadata back to `fork_nodes`
   (spec 2.4 — user edits made while viewing another branch propagate to shared nodes).
7. Return the new chain (UI refreshes message list + tree panel).

### 5.6 Drag & drop support (spec 3.6) — pure-logic helpers

- `migrate_branch_data(conv_id, old_fork_id, new_fork_id)` — all branch rows under
  `old_fork_id` re-anchored to `new_fork_id` (positions unchanged); fork fields move to
  the new node; old node cleared. Used for "dropped into between fork point & modified
  node" (inserted node becomes new fork point) and "fork point moved away" (new fork
  point = modified node's new predecessor).
- `transfer_branch_group(conv_id, fork_point_id, target_conv_id, target_fork_point_id)`
  — cross-conversation whole migration of a modified node's entire branch group
  (spec 3.6.1 row "其他对话的被修改节点").
- `drop_into_fork_point_position(conv_id, dest_before_node_id, inserted_node_id)` —
  the "between fork point and modified node" special case (old fork point degrades,
  data migrates to inserted node, modified node stays).
- UI (TreePanel dropEvent) classifies each drag per the spec table and calls these.
  Rejections (modified node same-conversation; fork point into its own head position)
  are refused before any mutation.

### 5.7 Conversation/folder delete cascade — `delete_conversation_branches(conv_id)`

Deleting a conversation (or folder containing conversations) whose subtree contains fork
points deletes all their branch data (spec 3.3.1 second scenario): remove `branch_nodes`
rows, `fork_nodes` rows, orphan message rows for the conversation.

---

## 6. ConversationService integration (core orchestration)

1. **`regenerate_message` — fork mode** (spec Ch4). New signature keeps
   `(session_id, message_id)` but `message_id` becomes **the clicked assistant node**
   (UI now passes it; default stays "last assistant" only as fallback). Behavior:
   - Determine fork point = predecessor of the target assistant node (conversation node
     if first message).
   - **Pre-fork**: build LLM context from tree.json history (which still contains the old
     assistant — the chain is untouched until completion); stream; on `is_done` persist
     thinking row + new assistant node and call `BranchService.create_branch`. Nothing is
     persisted before completion; on abort the generator ends without persisting and the
     UI reverts (tree.json unchanged → safe on crash, safe on abort).
   - The old in-place delete-and-recreate behavior is removed for complete nodes.
     Incomplete-turn "重新生成/继续" (`_resend_current_turn`, `continue_message`) is a
     different UI path and stays untouched.
2. **`resend_edited_message(session_id, original_user_id, new_text, files)`** (spec Ch5):
   - Validate the target is a complete user node (not incomplete → spec 3.2.1).
   - Create **new** user Message + MessageNode (new id — old row must survive in the
     archived branch), marked `incomplete=True` (spec 5.4), title/preview from new text.
   - `create_branch(conv, fork_point, [.., fp, new_user])` **immediately** (spec 5.3.1 —
     no pre-fork for edit-resend).
   - Stream the assistant response (reuse the shared pipeline from `send_message`;
     extract `_stream_assistant_response` helper to avoid duplication); on completion
     `extend_current_branch` + clear incomplete (spec 5.5 note).
3. **`switch_branch(conv_id, fork_point_id, target_index)`** — thin wrapper over
   `BranchService.switch_branch` (spec 3.5).
4. **Delete integration** (spec 3.3): `delete_node` / `soft_delete_node` route through
   BranchService when the deleted node is:
   - a **modified node** → **hard delete** (never to recycle bin; UI confirms first) +
     `delete_branch` cascade + auto-switch (user rulings Q1/Q3);
   - a **fork point** → `delete_fork_point` (§5.4a: branch data transferred to the
     predecessor, modified node's identity unchanged — user ruling Q2); fork point
     itself goes through the normal soft-delete flow;
   - a **conversation/folder** with fork descendants → branch data deleted at
     soft-delete time (spec 3.3.1 note); restoring the conversation from the recycle
     bin clears all fork markers on its chain (restored conversation is ordinary).
5. **`cleanup_incomplete_nodes`** (spec 5.5): incomplete nodes that are modified nodes
   (successor of a fork point) → `delete_branch` (the incomplete branch is removed, chain
   auto-switches); other incomplete nodes → existing plain soft-delete path.
6. **`get_effective_enabled_messages`** gains fork info: while walking each conversation's
   chain, attach to each message `fork_m/fork_n/fork_point_id` (0/0/"" when its
   predecessor is not a fork point) and `conversation_id`. MessageVM carries them.
7. **`switch_conversation` / meta updates / search / KG / context**: no changes — all
   read tree.json, which always holds the current chain (verified §2).

---

## 7. UI layer

### 7.1 ViewModels + controller

- `MessageVM` += `conversation_id: str`, `fork_m: int = 0`, `fork_n: int = 0`,
  `fork_point_id: str = ""` (fork info of the *modified* message, from its predecessor).
- `TreeNodeVM` += `fork_display: str = ""` (`"<m/n> "` prefix for fork points).
- `AppController` additions: `on_switch_branch(fork_point_id, delta)`,
  `on_resend_edited_message(session_id, original_user_id, text, files)` (async),
  `on_regenerate_message(session_id, message_id)` (message_id now explicit),
  `on_enter_edit_state`/`on_exit_edit_state` (UI-local, may skip controller).

### 7.2 ChatMessage (`chat_message.py`)

- **"✏️ 修改" button** on complete user messages (hidden while streaming/incomplete/
  pre-fork/edit-state) → new `edit_requested = Signal(str)` (message id).
- **`ForkControl` widget** (new small QFrame): `<` button, `m/n` label, `>` button,
  ~1-char gaps, disabled edges (`m==1` → `<` disabled; `m==n` → `>` disabled); emits
  `fork_nav(int delta)`. Placed **below the modified message bubble** (spec 3.5.1);
  shown only when `fork_n > 0`.
- **"放弃本次修改" button** — visible only in the **edit-resend paused** state (spec
  3.1.6/5.4, user ruling): after stopping generation on an edited-and-resent message.
  Clicking hard-deletes the new modified node + its branch (spec 3.3) and reverts to the
  previous branch. Regenerate flow has no such button (stop = instant revert, spec 4.3).
- `set_translucent(True/False)` — 50% opacity for edit state (spec 5.1.3/5.1.4).
- Regenerate signal now emits `(message_id)` so the clicked assistant is targeted.

### 7.3 InputArea (`input_area.py`)

- `enter_edit_mode(text)` — fill text, show **✕ button** (top-right of input row,
  spec 5.1.5), send button labeled for edit mode.
- `exit_edit_mode(keep_text=True)` — hide ✕, restore send style; text kept (spec 5.2).
- `edit_cancel_requested = Signal()`.

### 7.4 ChatApp (`app.py`) — state machine + gating

New state: `_edit_node_id: str | None` and `_prefork: PreForkState | None` where
`PreForkState = {target_message_id, fork_point_id, streaming_msg, thinking_ref,
hidden_widgets: list[ChatMessage]}` (regenerate pre-fork has only one phase —
streaming; stop/error exits it entirely, spec 4.3).

- **Gating helper** `_can_mutate_tree(allow_send=False)` → checked at every handler that
  triggers chat-window redraw or tree mutation (spec 3.1.3 list): send-new-message,
  conversation switch, branch switch, delete, toggle-enable, new conv/folder, rename,
  drag, batch ops. During pre-fork: everything blocked except stop. During edit state:
  everything blocked except **sending the edited message** (spec 5.2). `TreePanel`
  additionally gets `set_ops_locked(bool)` (disables drag-drop, context menu, checkboxes
  — enforced inside the panel, not just at ChatApp handlers).
- `_load_all_messages` rebuild guard extended: defer while `_prefork is not None`
  (same mechanism as `_streaming_message`).
- **Regenerate dispatch** (`_handle_regenerate`): if the clicked assistant belongs to an
  incomplete turn (no tree node / user predecessor incomplete) → existing
  `_resend_current_turn` path (no branch). If complete → **pre-fork flow**:
  `_start_prefork_regenerate(session_id, message_id)`:
  1. Snapshot widgets after the target assistant; hide them in-place (keep refs).
  2. Insert streaming assistant bubble at the target position (reuse `_start_stream`
     stream wiring).
  3. Completion → `on_regenerate_message` finalize (BranchService.create_branch inside)
     → `_load_tree` + `_load_all_messages` (fresh chain).
  4. **Stop/error → discard & revert** (user ruling / spec 4.3; NO paused state, NO
     incomplete marks): remove streaming bubble, restore hidden widgets,
     `_prefork = None`. Nothing was persisted — tree.json untouched.
- **Edit flow** (edit-resend stop = paused state, spec 5.4):
  - `edit_requested(msg_id)` → if input non-empty → confirm dialog (spec 5.1.2) →
    `InputArea.enter_edit_mode(content)` + bubble & tree-title translucent +
    `_edit_node_id = msg_id` + gating on.
  - ✕ → `exit_edit_mode(keep_text=True)`, restore translucency, gating off.
  - Send while editing → `_start_edit_resend_stream`: service `resend_edited_message`;
    the edited bubble is updated in place (new text), stream follows; on completion
    refresh (branch already committed at send). Stop mid-stream → **paused state**
    (spec 5.4): user node `incomplete`, partial bubble kept, 继续生成/重新生成/放弃本次修改
    shown. `放弃本次修改` → service deletes the incomplete modified node + its branch
    (spec 3.3, hard delete) → chain reverts to previous branch → full refresh.
- **Branch switch** (`_handle_branch_switch(fork_point_id, delta)`): gated (no
  streaming/pre-fork/edit) → `on_switch_branch` → `_load_tree` + `_load_all_messages`
  (anchor-scroll preserved). Nested fork points reset to largest branch per switch path.
- **`_handle_stop`** during **pre-fork (regenerate)**: discard & revert (spec 4.3 — NOT
  the incomplete-turn path, no incomplete marks). During **edit-resend stream**: normal
  incomplete-turn path (spec 5.4 paused state).
- **Tree panel refresh** already re-renders fork titles via `fork_display`.

### 7.5 TreePanel (`tree_panel.py`)

- Title rendering appends `fork_display` for fork points (spec 3.5.2 — display only).
- `set_edited_node(node_id)` — translucent styling for the edited node's title.
- `set_ops_locked(bool)` — dropEvent rejection, context-menu disabled entries, checkbox
  blocking during pre-fork.
- `dropEvent` classification per §5.6 (Phase 4); confirmation dialog when dragging a
  cross-conversation modified node (spec 3.6.4).

---

## 8. Scripts & docs

- `scripts/sync_db_with_tree.py` — orphan detection now keeps rows referenced by
  `fork_nodes` / `branch_nodes`; still prunes true orphans.
- `CLAUDE.md` — document fork architecture, new files, sync triggers, invariants.
- `README.md` (optional) — feature note.

## 9. Tests

New unit tests (pure logic, no Qt):
- `tests/test_branch_store.py` — DDL round-trip, branch list read/write, uniqueness.
- `tests/test_branch_service.py` — create (first fork archives current chain; index
  increments), switch (chain rebuild, nested expansion to largest, metadata sync, dirty
  sync first), delete (cascade, degrade at count==1, auto-switch previous/last, physical
  row cleanup), fork-point delete (data transfer to predecessor, merge into existing fork
  point, conversation-node fallback), migrate/transfer helpers (3.6), dirty marking.
- Extend `tests/test_conversation_service.py` — regenerate fork finalize (nothing
  persisted on abort; branch created on done), `resend_edited_message` (new ids, old
  content preserved, incomplete flag, extend on completion), branch-aware
  `cleanup_incomplete_nodes`, delete-cascade routing.
- Extend `tests/test_tree_store.py` — fork-field serialization round-trip, chain
  replace/renumber, dirty marking on mutating ops.
- Existing 51 tests must stay green (fork fields are defaulted, no behavior change
  without forks).

Manual verification checklist (phase 3+): edit-resend, regenerate pre-fork with
stop/continue/regenerate/abandon, nested fork switch, tree-panel markers, drag combos,
startup with leftover incomplete fork nodes.

---

## 10. Implementation phases

| Phase | Scope | Exit criteria |
|---|---|---|
| **P0** Storage | models fork fields; branch_store tables; TreeStore fork serialization + dirty marking + chain helpers | branch_store + tree_store tests green |
| **P1** BranchService | sync, create, extend, delete, switch (nested), migrate/transfer, conversation cascade | branch_service tests green |
| **P2** Core integration | regenerate fork mode, resend_edited, delete routing, branch-aware cleanup, fork info in VMs | conversation_service tests green; existing 51 green |
| **P3** UI | edit state, ForkControl, pre-fork machine + gating, tree markers, controller wiring | manual checklist: edit/regenerate/switch flows |
| **P4** Drag & drop | 3.6 classification + special cases + confirm dialog | drag logic tests + manual combos |
| **P5** Integration | scripts, CLAUDE.md, orphan cleanup review, full regression, polish | full suite green, app smoke-tested |

Each phase commits separately; P0–P2 are purely backend (UI untouched → low risk).

---

## 11. Non-goals / unchanged behavior

- Incomplete-turn "继续生成 / 重新生成 / 重新发送" flows (no branch) — unchanged.
- `search`, `KG`, context-block injection, multi-conversation aggregation — unchanged
  (all tree.json-driven; a conversation's chain is always the current branch).
- No config changes. No tree.json version bump (additive fields with defaults).
- Flet legacy untouched.

---

## 12. Spec contradictions & open questions (user review)

**User rulings (2026-08-03) — all questions closed, spec updated to match:**

1. **Delete-branch auto-switch target** → follow spec 3.3.2 step 7 (= 3.3.3 after
   update): switch to the **previous branch**; if the deleted branch was the first,
   switch to the **last remaining branch**.
2. **Deleting a fork point** → new spec 3.3.4: the fork point's identity and **all its
   branch data transfer to its predecessor** in the current chain (or the conversation
   node if it is the first message); the modified node's identity is unchanged. `delete_fork_point` (§5.4a).
3. **Deleting a modified node** → **hard delete**, never to the recycle bin, with a
   confirm dialog (spec 3.3.1 删除方式). Restore-from-trash no longer involves branch
   data for modified nodes.
4. **Nested-fork expansion on branch switch uses the largest index** — confirmed.
5. **Deleted branch's message rows are physically deleted** (spec 3.3.2 step 2), except
   nodes still referenced by the current chain, other branches, or recycle-bin entries.
6. **Stop-generation semantics** (overrides chapter priority for this case): regenerate
   pre-fork stop → **discard & revert** (spec 4.3 — no paused state, no incomplete
   marks, no abandon button); edit-resend stop → **paused state** (spec 5.4 — normal
   incomplete turn, plus the 放弃本次修改 button which performs the spec-3.3 branch
   deletion and reverts to the previous branch). Spec 3.1.5/3.1.6 rewritten accordingly.

**R2** — 4.2 says fork point of a regenerated assistant = its predecessor, regardless
of type (user OR assistant OR conversation node). Adopted as-is.
