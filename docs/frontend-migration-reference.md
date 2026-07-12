# DeepResearch Frontend Migration Reference

> **Purpose**: Complete feature and parameter reference for migrating the Flet frontend to PySide6.
> **Generated**: 2026-07-10
> **Target Framework**: PySide6 (Qt for Python)
> **Source Framework**: Flet 0.85

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Layout Structure](#2-layout-structure)
3. [Theme System](#3-theme-system)
4. [Sidebar & Tree Panel](#4-sidebar--tree-panel)
5. [Chat Message List](#5-chat-message-list)
6. [Input Area](#6-input-area)
7. [Context Panel (Slide-out Drawer)](#7-context-panel-slide-out-drawer)
8. [Knowledge Graph Panel (Slide-out Drawer)](#8-knowledge-graph-panel-slide-out-drawer)
9. [Dialog Components](#9-dialog-components)
10. [Streaming Response Pipeline](#10-streaming-response-pipeline)
11. [View Models (Data Transfer Objects)](#11-view-models-data-transfer-objects)
12. [Controller Interface](#12-controller-interface)
13. [Configuration State](#13-configuration-state)
14. [Scroll-to-Message (Known Issues)](#14-scroll-to-message-known-issues)
15. [Event Flow Reference](#15-event-flow-reference)

---

## 1. Architecture Overview

### Layer Dependency (Top-Down)

```
UI (app/ui/)                    ← PySide6 widgets & views
Controllers (app/controllers/)  ← Thin bridge: UI events → domain calls → ViewModels
Core (app/core/)                ← Business orchestration, Protocol-based
Adapters (app/adapters/)        ← External systems (DeepSeek API, search, file parsers)
Storage (app/storage/)          ← SQLite + JSON files
```

**Key principle**: UI layer never directly accesses Core, Adapters, or Storage. All domain operations go through `AppController`.

### Dependency Injection Entry Point

`main.py:43-113` assembles the entire object graph. The PySide6 equivalent should replicate this assembly:

```
Storage → Adapters → Core Services → Controllers → UI (MainWindow)
```

### Global Configuration

`config.py` is a **global mutable module** — every layer imports it directly. Static values from `.env`, mutable values written by UI/SettingsController and read by Core/Adapters.

---

## 2. Layout Structure

### Window Configuration

| Property | Value |
|----------|-------|
| Title | `"DeepResearch"` |
| Theme mode | Dark (forced) |
| Minimum width | 900 px |
| Minimum height | 600 px |
| Background color | `#0D0F14` |
| Padding | 0 |
| Custom fonts | JetBrains Mono, Noto Sans SC (loaded from Google Fonts CDN) |

### Three-Column Layout

```
┌──────────────┬───┬──────────────────────────────┬──────────────────┐
│              │   │                              │                  │
│   Sidebar    │ V │      Chat Area (Stack)       │  Context Panel   │
│   (260px)    │ D │  ┌─────────────────────────┐ │  or KG Panel     │
│              │ I │  │  Message ListView       │ │  (380px, slide-  │
│  ┌─────────┐ │ V │  │  (expand, fill)        │ │   out drawer)    │
│  │ Header  │ │ I │  │                        │ │                  │
│  ├─────────┤ │ D │  ├─────────────────────────┤ │                  │
│  │Buttons  │ │ E │  │  InputArea             │ │                  │
│  ├─────────┤ │ R │  │  (bottom, fixed)       │ │                  │
│  │TreePanel│ │   │  └─────────────────────────┘ │                  │
│  │(expand) │ │   │                              │                  │
│  ├─────────┤ │   │  EmptyHint (Stack overlay)   │                  │
│  │Trash    │ │   │  "DR" logo + text,           │                  │
│  │Context  │ │   │  visible when no messages    │                  │
│  │KG entry │ │   │                              │                  │
│  └─────────┘ │   │                              │                  │
│              │   │                              │                  │
└──────────────┴───┴──────────────────────────────┴──────────────────┘
```

**Qt equivalent**: `QMainWindow` with `QSplitter` for resizable columns.

### Sidebar Width

- Fixed at **260px**
- Background: `#13161D`
- Right border: 1px `#252A38`

### Drawer Panel Width

- Context Panel and KG Panel: **380px**
- Background: `#13161D`
- Left border: 1px `#252A38`
- Visibility toggled via `visible` property (slide-out from right)

### Chat Area

- Uses a **Stack** (overlay) pattern: message ListView on bottom, empty-state hint on top
- Empty hint: centered logo (64×64 rounded container with "DR" text) + "DeepResearch" title + subtitle "选择或新建一个对话以开始"
- Empty hint visibility toggled based on whether messages exist

---

## 3. Theme System

### Color Palette

All colors from `app/ui/theme.py` → `Colors` class:

#### Background Layers
| Token | Hex | Usage |
|-------|-----|-------|
| `BG_BASE` | `#0D0F14` | Deepest background (window bg) |
| `BG_SURFACE` | `#13161D` | Card / panel background |
| `BG_ELEVATED` | `#1A1E28` | Elevated elements / input fields |
| `BG_OVERLAY` | `#1F2433` | Hover / selected state |

#### Primary & Accent
| Token | Hex | Usage |
|-------|-----|-------|
| `PRIMARY` | `#4A9EFF` | Main interactive color (cold steel blue) |
| `PRIMARY_DIM` | `#2E6FCC` | Pressed state |
| `PRIMARY_GLOW` | `#4A9EFF22` | Glow background (low opacity) |
| `ACCENT` | `#64FFDA` | Mint green — thinking mode / special highlight |

#### Text Hierarchy
| Token | Hex | Usage |
|-------|-----|-------|
| `TEXT_PRIMARY` | `#E8EAF0` | Primary text |
| `TEXT_SECONDARY` | `#7A8099` | Secondary / placeholder |
| `TEXT_DISABLED` | `#3D4255` | Disabled state |
| `TEXT_CODE` | `#A8C4E8` | Code block / monospace content |

#### Role Indicators
| Token | Hex | Usage |
|-------|-----|-------|
| `ROLE_USER` | `#4A9EFF` | User message indicator |
| `ROLE_ASSISTANT` | `#64FFDA` | Assistant message indicator |
| `ROLE_THINKING` | `#9C7FE8` | Thinking block indicator (purple) |

#### Status Colors
| Token | Hex | Usage |
|-------|-----|-------|
| `SUCCESS` | `#4CAF82` | Success state |
| `WARNING` | `#E8A838` | Warning state |
| `ERROR` | `#F06B6B` | Error / danger state |
| `INFO` | `#4A9EFF` | Info state |

#### Checkbox States (Tree Node Enabled Indicator)
| Token | Hex | Usage |
|-------|-----|-------|
| `CHECKBOX_ENABLED` | `#4CAF82` | Green — enabled |
| `CHECKBOX_DISABLED` | `#3D4255` | Dim — disabled |
| `CHECKBOX_SOME` | `#E8A838` | Amber — partially enabled |

#### Borders & Dividers
| Token | Hex | Usage |
|-------|-----|-------|
| `BORDER` | `#252A38` | Normal border |
| `BORDER_FOCUS` | `#4A9EFF` | Focused border |
| `DIVIDER` | `#1E2230` | Divider line |

### Typography

| Token | Value |
|-------|-------|
| Body font | `"Noto Sans SC"` |
| Monospace font | `"JetBrains Mono"` |
| `SIZE_XS` | 11px |
| `SIZE_SM` | 12px |
| `SIZE_MD` | 14px |
| `SIZE_LG` | 16px |
| `SIZE_XL` | 18px |
| `SIZE_XXL` | 22px |

### Spacing Scale

| Token | Value |
|-------|-------|
| `XS` | 4px |
| `SM` | 8px |
| `MD` | 12px |
| `LG` | 16px |
| `XL` | 24px |
| `XXL` | 32px |

### Border Radius Presets

| Token | Values |
|-------|--------|
| `SM` | 4px all corners |
| `MD` | 8px all corners |
| `LG` | 12px all corners |
| `XL` | 16px all corners |
| `BUBBLE_USER` | 12px top-left, 12px top-right, 12px bottom-left, **2px** bottom-right |
| `BUBBLE_ASST` | 12px top-left, 12px top-right, **2px** bottom-left, 12px bottom-right |

### Border Presets

| Token | Description |
|-------|-------------|
| `DEFAULT` | 1px `BORDER` on all sides |
| `FOCUS` | 1px `BORDER_FOCUS` on all sides |
| `BOTTOM_ONLY` | 1px `DIVIDER` on bottom only |
| `LEFT_ACCENT` | 2px `PRIMARY` on left only |
| `LEFT_THINKING` | 2px `ROLE_THINKING` on left only |

---

## 4. Sidebar & Tree Panel

### Sidebar Layout (Top-to-Bottom)

```
┌──────────────────────────────┐
│  [DR] DeepResearch   (Header)│
├──────────────────────────────┤
│  [＋对话]  [📁文件夹] (Buttons)│
├──────────────────────────────┤
│  对话历史            (Label)  │
├──────────────────────────────┤
│                              │
│  TreePanel (expandable)      │
│  ├─ 📁 Folder                │
│  │  ├─ 📄 Conversation (3)   │
│  │  │  ├─ 👤 user: ...       │
│  │  │  └─ 🤖 assistant: ... │
│  │  └─ 📄 Conversation (1)   │
│  └─ 📁 Another Folder        │
│                              │
├──────────────────────────────┤
│  🗑️ 回收站          (Entry)  │
│  上下文管理          (Entry)  │
│  知识图谱            (Entry)  │
└──────────────────────────────┘
```

### Header

- 32×32 "DR" logo container (rounded, `PRIMARY_GLOW` bg, `PRIMARY` border)
- "DeepResearch" text, `SIZE_LG`, monospace, weight 600, `TEXT_PRIMARY`
- Padding: `LG` all sides (16px)
- Bottom border: `BOTTOM_ONLY`

### Action Buttons Row

Two side-by-side `ElevatedButton` widgets:
1. **"对话"** (New Conversation) — icon `ADD`, triggers `on_new_conversation()`
2. **"文件夹"** (New Folder) — icon `CREATE_NEW_FOLDER`, triggers `on_create_folder(None, "新文件夹")`

Style:
- Background: `PRIMARY`
- Overlay (hover/press): `PRIMARY_DIM`
- Border radius: 8px
- Internal padding: 12px horizontal, 10px vertical
- Elevation: 0
- Text: `SIZE_SM`, white (`BG_BASE`), weight 500

Container padding: `LG` left/right, `MD` top/bottom

### Section Label

- Text: "对话历史"
- `SIZE_XS`, monospace, `TEXT_DISABLED`

### Bottom Entry Points (in order)

Each is a clickable `Container` with icon + text + chevron:

1. **Trash (回收站)**: icon `DELETE_OUTLINE`, calls `on_open_trash()`
2. **Context Manager (上下文管理)**: icon `LAYERS_OUTLINED`, calls `on_open_context_panel()`
3. **Knowledge Graph (知识图谱)**: icon `ACCOUNT_TREE_OUTLINED`, calls `on_open_kg_panel()`

Each entry has top border `DIVIDER`, padding `LG` left / `MD` right / `MD` top-bottom.

---

### TreePanel (`app/ui/widgets/tree_panel.py`)

#### Data Flow

1. `ChatApp._load_tree()` calls `controller.get_tree()` → returns `list[TreeNodeVM]` (DFS-sorted)
2. Passed to `Sidebar.load_tree(nodes)` → delegates to `TreePanel.load_tree(nodes)`
3. `TreePanel._rebuild()` converts flat DFS list into nested `ExpansionTile` widgets

#### Node Types & Icons

| Node Type | Icon | Description |
|-----------|------|-------------|
| `folder` | 📁 | Directory, can contain sub-folders + conversations |
| `conversation` | 📄 | Conversation container, shows message count |
| `message` (user) | 👤 | User message leaf node |
| `message` (assistant) | 🤖 | Assistant message leaf node |
| `message` (thinking) | 🧠 | Thinking message leaf node |
| `message` (system) | ⚙️ | System message leaf node |
| `message` (default) | 💬 | Fallback message icon |

#### Enabled State Checkbox (Per-Node Indicator)

Replaces old emoji indicators. Uses `ft.Icon` with three states:

| State | Icon | Color | Tooltip |
|-------|------|-------|---------|
| `True` | `CHECK_BOX` | `CHECKBOX_ENABLED` (#4CAF82) | "已启用 — 点击禁用" |
| `False` | `CHECK_BOX_OUTLINE_BLANK` | `CHECKBOX_DISABLED` (#3D4255) | "已禁用 — 点击启用" |
| `"some"` | `INDETERMINATE_CHECK_BOX` | `CHECKBOX_SOME` (#E8A838) | "部分启用 — 点击全部启用" |

Click behavior: Checkbox is clickable, triggers `on_toggle_enabled(node_id)` which performs two-way cascade (top-down set + bottom-up recompute).

#### Node Row Structure

```
[indent(depth × 18px)] [checkbox(16px)] [type_icon(14px)] [title(expand, ellipsis)] [timestamp(mono, xs)]
```

- Depth indentation: `depth * 18px` via spacer Container
- Active node: `TEXT_PRIMARY` text, `BG_OVERLAY` background, 2px `PRIMARY` left border
- Inactive node: `TEXT_SECONDARY` text, transparent background
- Row padding: `SM` left-right, 6px top-bottom
- Tooltip: title + enabled state description

#### Expansion Behavior (Folders & Conversations)

- `ExpansionTile` wraps nodes that have children
- Expansion state tracked in `_expanded_ids: set[str]`
- Trailing chevron icon: `CHEVRON_RIGHT`, 16px, `TEXT_DISABLED`
- Initially expanded if node ID in `_expanded_ids`

#### Right-Click Context Menu

**Folder nodes:**
1. "新建文件夹" — icon `CREATE_NEW_FOLDER` → `on_new_folder(node_id)`
2. "新建对话" — icon `ADD_COMMENT` → `on_new_conversation(node_id)`
3. --- separator ---
4. "重命名" — icon `EDIT` → `on_rename_node(node_id)`
5. "启用/禁用" — icon `TOGGLE_ON`/`TOGGLE_OFF` → `on_toggle_enabled(node_id)`
6. --- separator ---
7. "管理上下文块" — icon `LAYERS_OUTLINED` → `on_manage_context(node_id)`
8. "添加附件" — icon `ATTACH_FILE` → `on_attach_file(node_id)`
9. --- separator ---
10. "删除" — icon `DELETE`, `ERROR` color → `on_delete_node(node_id)`

**Conversation nodes:**
1. "重命名" — `on_rename_node(node_id)`
2. "启用/禁用" — `on_toggle_enabled(node_id)`
3. --- separator ---
4. "删除" — `on_delete_node(node_id)`

**Message nodes (simplified):**
1. "启用/禁用" — `on_toggle_enabled(node_id)`
2. --- separator ---
3. "删除" — `on_delete_node(node_id)`

#### Drag & Drop

- Group name: `"tree_drag"`
- All non-message nodes are `Draggable`
- Folder nodes also act as `DragTarget`
- Drag feedback: semi-transparent container with icon + title, 85% opacity
- Original position: 30% opacity placeholder
- On drop: calls `on_move_node(dragged_id, target_folder_id)`
- **Message nodes do NOT support drag** (they are immutable leaves)

#### Click Behavior

- **Conversation/Message nodes**: calls `on_switch_conversation(node_id)` → loads messages into chat area
- **Folder nodes**: expansion/collapse handled by `ExpansionTile` (no message loading)
- Active node is visually highlighted (left border + background)

---

## 5. Chat Message List

### ChatMessage Widget (`app/ui/widgets/chat_message.py`)

Extends `ft.Container`. Each message bubble consists of:

#### Structure (Vertical Column, Top-to-Bottom)

1. **Header Row**: Role badge + spacer (expand)
2. **Content Body**: `ft.Markdown` widget (GitHub-flavored, `atom-one-dark` code theme)
3. **Action Buttons Row**: (assistant messages only)

#### Role Badge (`_role_badge()`)

| Role | Label | Color | Font |
|------|-------|-------|------|
| `user` | "YOU" | `ROLE_USER` (#4A9EFF) | Mono, XS, bold |
| `assistant` | "DEEP" | `ROLE_ASSISTANT` (#64FFDA) | Mono, XS, bold |
| `thinking` | "THINKING" | `ROLE_THINKING` (#9C7FE8) | Mono, XS, bold |
| other | "???" | `TEXT_SECONDARY` | Mono, XS, bold |

Badge style: 6px left-right, 2px top-bottom padding, 1px border all sides, `SM` border radius.

#### Bubble Styling Per Role

**User messages:**
- Background: `BG_ELEVATED` (#1A1E28)
- Border: 1px `BORDER` on all sides + **1px `ROLE_USER` with alpha `55`** on left
- Border radius: `BUBBLE_USER` (12,12,12,2)
- Margin: `XS` top-bottom (4px)

**Assistant messages:**
- Background: `BG_SURFACE` (#13161D)
- Border: 1px `BORDER` on top/bottom/right + **2px `ROLE_ASSISTANT`** on left
- Border radius: `BUBBLE_ASST` (12,12,2,12)
- Margin: `XS` top-bottom (4px)

**Internal padding (both roles):**
- Left/Right: `LG` (16px)
- Top/Bottom: `MD` (12px)

**Internal spacing:**
- Column spacing: `SM` (8px)
- Between badge row and markdown: column spacing
- Between markdown and action row: column spacing

#### Action Buttons (Assistant Messages Only)

Three `IconButton` widgets in a `Row`:

| Button | Icon | Size | Tooltip | Handler |
|--------|------|------|---------|---------|
| Copy | `CONTENT_COPY_OUTLINED` | 14px | "复制" | Copies `current_content` to clipboard |
| Regenerate | `REFRESH_OUTLINED` | 14px | "重新生成" | Triggers `_stream_regenerate()` |
| Remember (KG) | `BOOKMARK_ADD_OUTLINED` | 14px | "记住这段对话（写入知识图谱）" | Triggers KG extraction |

All buttons: `TEXT_SECONDARY` color, 4px padding all sides.

#### Remember Button States

| State | Icon | Color | Disabled |
|-------|------|-------|----------|
| Default | `BOOKMARK_ADD_OUTLINED` | `TEXT_SECONDARY` | No |
| Extracting | `HOURGLASS_EMPTY` | `WARNING` | Yes |
| Success | `BOOKMARK_ADDED` | `SUCCESS` | No |
| Failure | `BOOKMARK_OUTLINED` | `ERROR` | No |

Method: `set_remember_done(success: bool)` called from external async task.

#### Streaming Support

- `start_stream()`: Sets `_is_streaming = True`, clears buffer
- `append_stream(delta)`: Appends text to markdown widget's value
- `finalize_stream()`: Sets `_is_streaming = False`
- `current_content` property: Returns current buffer text

#### Message ID

Each ChatMessage has a `message_id` attribute (matches SQLite `messages.id`). Passed as `key` to the Flet `Container` superclass.

### ThinkingBlock Widget

A collapsible block displayed before assistant response during reasoning mode.

#### Structure

1. **Toggle Header** (clickable):
   - Role badge "THINKING"
   - Spacer (expand)
   - Toggle icon (`EXPAND_LESS` when expanded, `EXPAND_MORE` when collapsed)

2. **Content Body** (toggle visibility):
   - `ft.Text` widget, monospace, `SIZE_SM`, `TEXT_SECONDARY`
   - Buffer-based streaming via `append_text(delta)`

#### Collapse Behavior

- Default: **expanded** (`_expanded = True`)
- Click header toggles visibility of content body
- Toggle icon rotates between `EXPAND_LESS` / `EXPAND_MORE`

#### Streaming

- `append_text(delta)`: Appends to internal buffer, updates text widget
- No `start_stream()`/`finalize_stream()` — uses same buffer pattern

### Message List Rendering (`ChatApp._rebuild_message_list`)

1. Clear all controls from ListView
2. Iterate `message_vms` list, create `ChatMessage` for each
3. Build cumulative offset map for scroll targeting (height estimation based)
4. If `scroll_to_id` provided: disable `auto_scroll`, calculate target offset, schedule scroll task

### Empty State Overlay

Displayed when no messages loaded:
- 64×64 rounded container: "DR" text (32px, monospace, bold, `PRIMARY`), `PRIMARY_GLOW` background, `PRIMARY` border
- "DeepResearch" title: `SIZE_XXL`, monospace, weight 600, `TEXT_PRIMARY`
- Subtitle: "选择或新建一个对话以开始", `SIZE_MD`, `TEXT_SECONDARY`
- Centered in available space

---

## 6. Input Area

`InputArea` (`app/ui/widgets/input_area.py`) — bottom input composite.

### Layout (Top-to-Bottom)

```
┌──────────────────────────────────────────────┐
│  AttachmentBar  (file chips, optional)       │
├──────────────────────────────────────────────┤
│  [TextField (multiline, expand)] [Send Btn] │
├──────────────────────────────────────────────┤
│  [📎] [THINK] [SEARCH] [spacer] [Model ▼]  │
└──────────────────────────────────────────────┘
```

Background: `BG_SURFACE`, top border: 1px `BORDER`
Padding: `LG` left-right, `MD` top-bottom

### TextField

| Property | Value |
|----------|-------|
| Hint text | "输入消息，Shift+Enter 换行，Enter 发送..." |
| Multiline | Yes |
| Min lines | 1 |
| Max lines | 8 |
| Border | `InputBorder.NONE` |
| Text style | `SIZE_MD`, `TEXT_PRIMARY`, body font |
| Hint style | `TEXT_DISABLED`, `SIZE_MD` |
| Cursor color | `PRIMARY` |
| Background | transparent |
| Submit behavior | `on_submit` triggers send, `shift_enter=True` (Shift+Enter for newline) |

### Send/Stop Button

Circular `IconButton`:
- **Idle state**: `SEND_ROUNDED` icon, `PRIMARY` color, tooltip "发送 (Enter)"
- **Generating state**: `STOP_CIRCLE_OUTLINED` icon, `ERROR` color, tooltip "停止生成"
- Background: `PRIMARY_GLOW`
- Shape: `CircleBorder()`
- Padding: 10px all sides

Toggled via `set_generating(bool)` method.

### Toolbar Row

| Control | Type | Details |
|---------|------|---------|
| Attach File Button | `IconButton` | Icon `ATTACH_FILE_OUTLINED`, 16px, `TEXT_SECONDARY`. Opens tkinter native file dialog (Flet FilePicker broken in 0.85) |
| THINK Toggle | `_ToggleChip` | Icon `PSYCHOLOGY_OUTLINED`, active color `ROLE_THINKING` (#9C7FE8) |
| SEARCH Toggle | `_ToggleChip` | Icon `TRAVEL_EXPLORE_OUTLINED`, active color `ACCENT` (#64FFDA) |
| Spacer | `Container(expand=True)` | Fills remaining space |
| Model Selector | `_ModelSelector` | Dropdown with options |

### Toggle Chip (`_ToggleChip`)

Custom toggle button widget:

| Property | Active State | Inactive State |
|----------|-------------|----------------|
| Border color | Active color | `BORDER` |
| Background | Active color at 13% opacity | transparent |
| Icon color | Active color | `TEXT_DISABLED` |
| Text color | Active color | `TEXT_DISABLED` |
| Padding | 8px horizontal, 4px vertical | same |
| Border radius | `SM` (4px) | same |
| Border width | 1px all sides | same |
| Font | Mono, `SIZE_XS` | same |

Click toggles `_active` bool and calls `on_change(bool)` callback.

### Model Selector (`_ModelSelector`)

Dropdown with preset options:

| Key | Label |
|-----|-------|
| `deepseek-v4-flash` | "Flash" |
| `deepseek-v4-pro` | "Pro" |

Style:
- Width: 150px
- Text: `SIZE_SM`, monospace, `TEXT_PRIMARY`
- Background: `BG_ELEVATED`
- Border: `BORDER` (normal), `PRIMARY` (focused)
- Border radius: 8px
- Content padding: 10px left-right, 6px top-bottom

On change: writes to `app_config.model_type`, notifies `SettingsController.on_change_model()`.

### Attachment Bar (`_AttachmentBar`)

Horizontal row of file chips. Each chip:

```
[📎 icon(12px)] [filename(SIZE_XS, ellipsis)] [✕ remove button(10px)]
```

Chip style:
- Padding: 6px left, 2px right, 3px top-bottom
- Border: 1px `BORDER` all sides
- Border radius: `SM`
- Remove button: `CLOSE` icon, 10px, `TEXT_DISABLED`

Methods:
- `add_file(name, abs_path)`: Adds chip, stores (display_name, abs_path) tuple
- `remove(name)`: Removes chip by display name
- `get_files()`: Returns list of absolute paths
- `clear_files()`: Clears all

**File dialog**: Uses `tkinter.filedialog.askopenfilenames()` in a daemon thread (Flet 0.85 `FilePicker` has compatibility issues). Filter: `*.txt *.md *.json *.docx *.pdf`.

### Config Toggle Handlers

- THINK toggle → `app_config.thinking_enabled = val` → `SettingsController.on_toggle_thinking(val)`
- SEARCH toggle → `app_config.search_enabled = val` → `SettingsController.on_toggle_search(val)`

---

## 7. Context Panel (Slide-out Drawer)

`ContextPanel` (`app/ui/widgets/context_panel.py`) — right-side slide-out panel, 380px wide.

### Sections (Top-to-Bottom, Scrollable)

#### Header

- Icon `LAYERS_OUTLINED` (18px, `PRIMARY`) + "上下文管理" title (`SIZE_LG`, monospace, weight 600) + close button (`CLOSE` icon)
- Bottom border: `BOTTOM_ONLY`

#### Active Context Blocks List

- Section label: "已选上下文块" (`SIZE_XS`, mono, `TEXT_DISABLED`)
- `ListView` (height: 200px), each item is a `_ContextBlockItem`:

**Block Item Row:**
```
[Switch(0.75 scale)] [Label(SIZE_SM, weight 500)]  [Remove IconButton]
                     [Preview(SIZE_XS, mono, 2-line ellipsis)]
```

- Switch: `PRIMARY` active color, 0.75 scale
- Remove button: `REMOVE_CIRCLE_OUTLINE`, 16px, `ERROR` color
- Item padding: `SM` all sides
- Bottom border: `DIVIDER`

#### Save as Template Section

- Template name `TextField` (placeholder: "输入模板名称，保存当前块为模板...")
  - `SIZE_XS`, `BG_ELEVATED` bg, border radius 6px
- Save button: `BOOKMARK_ADD_OUTLINED` icon, `ACCENT` color, 18px
  - Triggers `on_apply_template("__save__:name")`

#### Add Text Block Section

- Section label: "添加文本块"
- Multiline `TextField` (min 2, max 5 lines, expand)
  - `SIZE_SM`, `BG_ELEVATED` bg, border radius 8px
- Add button: `ADD_CIRCLE_OUTLINE`, `PRIMARY`, 20px

#### Preview Section

- Section label: "拼接预览"
- Preformatted text area (height: 120px)
  - `SIZE_XS`, mono, `TEXT_CODE` color
  - Background: `BG_BASE`
  - Left accent border: 2px `PRIMARY_DIM`

#### Template Library

- Section label: "模板库"
- `ListView` (height: 180px), each item is a `_TemplateItem`:

**Template Item Row:**
```
[📄 icon(16px, PRIMARY)] [Name(SIZE_SM)] [Apply Btn] [Delete Btn]
                         [Description(SIZE_XS, ellipsis)]
```

- Apply button: outlined `ElevatedButton`, "应用" text, `SIZE_XS`, mono, `PRIMARY` color, 1px `PRIMARY` border, 4px radius
- Delete button: `DELETE_OUTLINE`, 14px, `ERROR`

### Visibility

- Panel defaults to `visible=False`
- `show()` / `hide()` toggle visibility
- Triggers `page.update()` after toggle

---

## 8. Knowledge Graph Panel (Slide-out Drawer)

`KGPanel` (`app/ui/widgets/kg_panel.py`) — right-side slide-out panel, 380px wide.

### Sections

#### Header

- Icon `ACCOUNT_TREE_OUTLINED` (18px, `ACCENT`) + "知识图谱" title + stats text + close button
- Stats format: `"{N} 实体 · {M} 关系"` (`SIZE_XS`, mono, `TEXT_SECONDARY`)
- Bottom border: `BOTTOM_ONLY`

#### Tab Bar (Custom Implementation)

Two `TextButton` tabs: "实体" and "关系"

- Active tab: `PRIMARY` text color
- Inactive tab: `TEXT_SECONDARY` text color
- Tab bar bottom border: `DIVIDER`

**Note**: Flet 0.85 `Tabs.content` is broken; this uses custom tab switching with `Stack` overlay. Two `Container` widgets (entities list, relations list) are stacked; only the active one is `visible=True`.

#### Entity List

Each entity item:

```
[Type Badge] [Name(SIZE_SM, weight 500)]  [Delete IconButton]
             [Description(SIZE_XS, ellipsis)]
```

- Type badge: entity type text (`SIZE_XS`, mono, `ACCENT`), 1px `ACCENT` border, 4px left-right, 2px top-bottom padding, 2px radius
- Delete button: `DELETE_OUTLINE`, 14px, `ERROR`, tooltip "删除实体（同时删除相关关系）"
- Item padding: `MD` left, `SM` right, `SM` top-bottom
- Bottom border: `DIVIDER`

#### Relation List

Each relation item:

```
SourceName [REL_TYPE] TargetName    [Delete IconButton]
Description (optional)
```

- Source entity: `SIZE_SM`, `PRIMARY`, weight 500
- Relation type: `SIZE_XS`, mono, `TEXT_SECONDARY`, wrapped in brackets
- Target entity: `SIZE_SM`, `ACCENT`, weight 500
- Description: `SIZE_XS`, `TEXT_DISABLED`
- Delete button: `DELETE_OUTLINE`, 14px, `ERROR`

#### Empty State

When list is empty, shows centered hint text:
- Entities: "暂无实体\n点击 AI 回复下方的书签按钮开始提取"
- Relations: "暂无关系"

### Visibility

- Same pattern as ContextPanel: `visible=False` default, `show()`/`hide()` toggle
- `load_data(entities, relations, stats)` loads all data at once

---

## 9. Dialog Components

All dialogs use Flet's `AlertDialog` (modal). PySide6 equivalent: `QDialog`.

### Rename Dialog (`show_rename_dialog`)

| Property | Value |
|----------|-------|
| Title | "重命名" |
| Content | `TextField` with current name, autofocus, `PRIMARY` border |
| Actions | "取消" (cancel), "确认" (confirm) |
| On Confirm | Calls `on_confirm(new_title)` if non-empty |

### New Folder Dialog (`show_new_folder_dialog`)

| Property | Value |
|----------|-------|
| Title | "新建目录" |
| Content | `TextField` with default title ("新文件夹"), autofocus |
| Actions | "取消", "创建" |
| On Confirm | Calls `on_confirm(name)` if non-empty |

### Confirm Dialog (`show_confirm_dialog`)

| Property | Value |
|----------|-------|
| Title | Configurable |
| Message | Configurable (`SIZE_MD`, `TEXT_SECONDARY`) |
| Confirm text | Configurable (default "确认") |
| Danger mode | Confirm button uses `ERROR` color if `danger=True` |
| On Confirm | Closes dialog, calls `on_confirm()` |

### Recycle Bin Dialog (`show_recycle_bin_dialog`)

| Property | Value |
|----------|-------|
| Title | "🗑️ 回收站" |
| Content height | 400px |
| Content width | 500px |
| Empty state | Centered "回收站为空" text |

Each entry row:

```
[icon(📁/📄)] [title(expand, ellipsis)] [deleted_at(mono, XS)] [Restore Btn] [Delete Forever Btn]
```

- Restore button: `RESTORE` icon, 16px, `SUCCESS` color, tooltip "恢复"
- Delete forever button: `DELETE_FOREVER` icon, 16px, `ERROR` color, tooltip "彻底删除"

Actions:
- "清空回收站" (`ERROR` color, left-aligned) → confirm → `on_clear_all()`
- Spacer
- "关闭" → close dialog

### Context Block Manager Dialog (`show_context_block_manager_dialog`)

| Property | Value |
|----------|-------|
| Title | "管理上下文 — {folder_title}" (ellipsis overflow) |
| Content height | 350px |
| Content width | 480px |
| Empty state | "暂无可用上下文块" |

Each block: `Checkbox` with label `"{label} — {preview[:40]}"`:
- `PRIMARY` fill color
- `SIZE_SM` label style, `TEXT_PRIMARY`
- Checked state tracked in `selected_ids: set[str]`

Actions: "取消", "保存" → calls `on_save(list(selected_ids))`

---

## 10. Streaming Response Pipeline

### Send Message Flow

1. User types text + optional files, presses Enter or clicks Send
2. `InputArea._handle_send()` → `on_send(text, files)`
3. `ChatApp._handle_send()`:
   - Auto-creates conversation if `_current_session_id` is empty
   - Appends user `ChatMessage` to list
   - Clears input, sets generating state
   - Calls `page.run_task(_stream_response, session_id, text, files)`

### Stream Response (`_stream_response`)

```
async for chunk_vm in controller.on_send_message(session_id, text, files):
    if chunk_vm.is_done: break

    if chunk_vm.chunk_type == "thinking":
        create or append to ThinkingBlock
        insert ThinkingBlock before assistant bubble
    else:
        assistant_msg.append_stream(chunk_vm.delta)

    page.update()
    await asyncio.sleep(0)  # yield to event loop
```

**Key behaviors:**
- ThinkingBlock is created lazily on first thinking chunk
- ThinkingBlock is inserted before the assistant message (second-to-last position)
- Assistant `ChatMessage` is pre-created and appended before streaming starts
- `page.update()` called after every chunk for real-time rendering
- `finally` block always calls `finalize_stream()`, resets generating state, refreshes tree

### Regenerate Flow (`_stream_regenerate`)

Similar to send, but:
- Called from assistant message's regenerate button
- Uses `controller.on_regenerate_message(session_id, "")`
- Old messages are deleted by Core layer before regeneration
- New assistant message appended to existing list

### Stop Generation

- `ChatApp._handle_stop()`:
  - Calls `controller.on_stop_generation()` (sets `asyncio.Event` in Core)
  - Resets input area state
  - Finalizes streaming message

### Async Model

- Flet uses `page.run_task()` to schedule coroutines on the event loop
- PySide6 equivalent: `QThread` for background work + signals/slots for UI updates, or `QAsync` with `asyncio`

---

## 11. View Models (Data Transfer Objects)

All VMs are plain `@dataclass` classes in `app/controllers/view_models.py`.

### ConversationVM
```python
id: str
title: str
preview: str          # last message first N chars
updated_at: str       # formatted "MM-DD HH:MM"
```

### MessageVM
```python
id: str
role: str             # "user" | "assistant" | "thinking"
content: str
is_thinking: bool = False
created_at: str = ""  # formatted "MM-DD HH:MM"
```

### ConversationDetailVM
```python
id: str
title: str
messages: list[MessageVM]
```

### StreamChunkVM
```python
delta: str
is_done: bool = False
chunk_type: str = "text"   # "text" | "thinking"
message_id: str = ""
```

### ContextBlockVM
```python
id: str
label: str
preview: str          # first N chars
enabled: bool = True
source: str = ""      # "manual" | "file" | "template"
```

### TemplateVM
```python
id: str
name: str
description: str = ""
```

### TreeNodeVM
```python
id: str
title: str
node_type: str                           # "folder" | "conversation" | "message"
parent_id: str | None = None
enabled: bool | str = True               # True, False, or "some"
sort_order: int = 0
preview: str = ""                        # summary / message preview / empty
updated_at: str = ""                     # formatted time string
has_children: bool = False               # pre-computed
depth: int = 0                           # indentation level, pre-computed
message_count: int = 0                   # conversation nodes only
context_block_count: int = 0             # folder nodes only
attachment_count: int = 0                # folder nodes only
role: str = ""                           # message nodes only
```

### TrashEntryVM
```python
id: str                                  # TrashEntry ID
node_id: str                             # original node ID
title: str
node_type: str                           # "folder" | "conversation"
json_path: str                           # human-readable original path
deleted_at: str                          # formatted time string
```

---

## 12. Controller Interface

`AppController` — the single bridge between UI and Core. PySide6 UI should call these exact methods.

### Conversation Lifecycle

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `on_new_conversation(parent_id?)` | `str \| None` | `str` (session_id) | Create new conversation |
| `on_switch_conversation(session_id)` | `str` | `ConversationDetailVM` | Load conversation history |
| `on_delete_conversation(session_id)` | `str` | `None` | Delete conversation |
| `on_load_messages_for_node(node_id)` | `str` | `list[MessageVM]` | Load messages for any node type |
| `on_get_multi_conversation_messages()` | — | `list[MessageVM]` | All enabled messages merged |
| `on_load_history()` | — | `list[ConversationVM]` | [Deprecated] List all conversations |

### Message Generation

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `on_send_message(session_id, text, files)` | `str, str, list[str]` | `AsyncGenerator[StreamChunkVM]` | Send message, stream response |
| `on_regenerate_message(session_id, message_id)` | `str, str` | `AsyncGenerator[StreamChunkVM]` | Regenerate last response |
| `on_stop_generation()` | — | `None` | Abort current stream |

### Tree Operations

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `get_tree()` | — | `list[TreeNodeVM]` | Full tree as DFS-sorted flat list |
| `on_create_folder(parent_id, title)` | `str\|None, str` | `TreeNodeVM` | Create new folder |
| `on_rename_node(node_id, new_title)` | `str, str` | `None` | Rename node |
| `on_toggle_enabled(node_id)` | `str` | `None` | Toggle enabled with cascade |
| `on_move_node(node_id, target_parent_id, position?)` | `str, str, int\|None` | `None` | Move node via drag-drop |
| `on_soft_delete_node(node_id, mode?)` | `str, str` | `None` | Soft delete to recycle bin |
| `on_update_context_blocks(folder_id, block_ids)` | `str, list[str]` | `None` | Update folder context blocks |
| `on_get_folder_context(folder_id)` | `str` | `dict` | Get folder context info |
| `on_attach_file(folder_id, file_path)` | `str, str` | `None` | Attach file to folder |
| `on_detach_file(folder_id, file_path)` | `str, str` | `None` | Detach file from folder |

### Recycle Bin

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `on_list_trash()` | — | `list[TrashEntryVM]` | List all trash entries |
| `on_restore_from_trash(trash_entry_id, new_parent_id?)` | `str, str\|None` | `None` | Restore from trash |
| `on_permanently_delete(trash_entry_id)` | `str` | `None` | Permanent delete |
| `on_clear_trash()` | — | `int` | Clear all trash, returns count |

### Context Blocks

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `on_add_text_block(text)` | `str` | `dict` | Add custom text block |
| `on_remove_context_block(block_id)` | `str` | `None` | Remove context block |
| `on_toggle_context_block(block_id, enabled)` | `str, bool` | `None` | Toggle block enabled |
| `on_get_context_blocks()` | — | `list[dict]` | Get all blocks |
| `on_get_context_preview()` | — | `str` | Get assembled context preview |

### Templates

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `on_get_templates()` | — | `list[dict]` | List all templates |
| `on_apply_template(template_id)` | `str` | `None` | Apply template blocks |
| `on_delete_template(template_id)` | `str` | `None` | Delete template |
| `on_save_current_as_template(name, description?)` | `str, str` | `dict` | Save current blocks as template |

### Knowledge Graph

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `on_remember_conversation(session_id)` | `str` | `str` (async) | Extract KG from conversation |
| `on_get_all_entities()` | — | `list[dict]` | Get all KG entities |
| `on_get_all_relations()` | — | `list[dict]` | Get all KG relations |
| `on_get_kg_stats()` | — | `dict` | Get entity/relation counts |
| `on_delete_kg_entity(entity_name)` | `str` | `None` | Delete entity + relations |
| `on_delete_kg_relation(relation_id)` | `str` | `None` | Delete single relation |

### Settings Controller (`SettingsController`)

| Method | Args | Description |
|--------|------|-------------|
| `on_change_model(model_type)` | `str` | Writes `config.model_type` |
| `on_toggle_thinking(enabled)` | `bool` | Writes `config.thinking_enabled` |
| `on_toggle_search(enabled)` | `bool` | Writes `config.search_enabled` |

---

## 13. Configuration State

### Static (from `.env`, read-only at runtime)

| Variable | Default | Description |
|----------|---------|-------------|
| `DEEPSEEK_API_KEY` | `""` | DeepSeek API key |
| `DEEPSEEK_BASE_URL` | `"https://api.deepseek.com"` | API base URL |
| `DB_PATH` | `data/deepresearch.db` | SQLite database path |
| `CONTEXT_STORE_PATH` | `data/context` | Context blocks JSON directory |
| `TREE_STORE_PATH` | `data/tree` | Tree JSON + recycle bin directory |
| `KG_STORE_PATH` | `data/knowledge_graph` | Knowledge graph storage |
| `LOG_LEVEL` | `"INFO"` | Logging level |

### Mutable (read/write by UI at runtime)

| Variable | Default | Type | Written By |
|----------|---------|------|------------|
| `model_type` | `"deepseek-v4-pro"` | `str` | ModelSelector dropdown |
| `thinking_enabled` | `False` | `bool` | THINK toggle chip |
| `search_enabled` | `False` | `bool` | SEARCH toggle chip |
| `multi_conv_mode` | `True` | `bool` | Config (Phase 5 feature flag) |
| `kg_injection_enabled` | `True` | `bool` | Config |
| `max_tokens` | `8192` | `int` | `.env` |
| `temperature` | `1.0` | `float` | `.env` |
| `max_history_tokens` | `32000` | `int` | `.env` |
| `search_max_results` | `5` | `int` | `.env` |
| `kg_max_inject` | `10` | `int` | `.env` |
| `kg_extract_recent_n` | `6` | `int` | `.env` |

---

## 14. Scroll-to-Message (Known Issues)

### Current State

The scroll-to-message feature in Flet 0.85 has known framework-level limitations:

- **`scroll_to(scroll_key=...)`**: Confirmed **broken** in Flet 0.85 — all 7 tested key type combinations produce 0 scroll events. This is a framework bug, not application code issue.
- **`scroll_to(offset=...)`**: Works correctly — this is the only viable scrolling method.
- **`on_size_change`**: Unreliable for newly created controls in ListView — typically only 0-1 events fire out of expected N.

### Current Implementation (Height Estimation)

The app uses content-based height estimation to calculate scroll offsets:

- **Fixed overhead per message**: User messages ~68px, Assistant messages ~104px (includes action buttons)
- **Line height**: Markdown text ~21px/line, code blocks ~17px/line
- **Characters per line**: ~70 ASCII chars (accounting for ~570px available width)
- **CJK handling**: CJK characters counted as 2× width for wrapping calculation
- **Race condition guard**: Generation counter ensures cancelled async scroll tasks don't corrupt state

### Migration Notes

When migrating to PySide6, prefer `QScrollArea.ensureWidgetVisible()` or `QAbstractItemView.scrollTo()` with model indices for precise targeting. These Qt APIs are mature and reliable — no estimation needed.

---

## 15. Event Flow Reference

### Tree Node Click → Message Display

```
TreePanel._on_node_click(node)
  → Sidebar callback: on_switch_conversation(node.id)
    → ChatApp._handle_switch_conversation(node.id)
      → Determine load_id and focus_id from node type
      → If multi_conv_mode:
          controller.on_get_multi_conversation_messages()
        Else:
          controller.on_load_messages_for_node(load_id)
      → _rebuild_message_list(messages, scroll_to_id=focus_id)
      → Sidebar.set_active(node.id)
      → page.update()
```

### Send Message → Stream Display

```
InputArea._handle_send()
  → ChatApp._handle_send(text, files)
    → Auto-create conversation if needed
    → Append user ChatMessage
    → InputArea.clear() + set_generating(True)
    → page.run_task(_stream_response, session_id, text, files)
      → Create ThinkingBlock (placeholder)
      → Create assistant ChatMessage (streaming)
      → async for chunk in controller.on_send_message(...):
          → Update ThinkingBlock or ChatMessage
          → page.update()
      → finally: finalize_stream(), reset generating, refresh tree
```

### Tree CRUD → Refresh

```
Any tree mutation (create/rename/delete/toggle/move)
  → controller.on_*(...)
  → ChatApp._load_tree()
    → controller.get_tree()
    → Sidebar.load_tree(nodes)
      → TreePanel.load_tree(nodes)
        → _rebuild() → DFS → ExpansionTile widgets
  → If multi_conv_mode and toggle_enabled:
      → _refresh_multi_conv_messages()
```

### Context Panel → Refresh

```
Sidebar "上下文管理" click
  → ChatApp._handle_open_context_panel()
    → _refresh_context_panel()
      → controller.on_get_context_blocks() → ContextBlockVMs
      → controller.on_get_context_preview() → preview text
      → controller.on_get_templates() → TemplateVMs
      → context_panel.load_blocks() + load_templates() + set_preview()
    → context_panel.show()
```

### KG Panel → Refresh

```
Sidebar "知识图谱" click
  → ChatApp._handle_open_kg_panel()
    → controller.on_get_all_entities()
    → controller.on_get_all_relations()
    → controller.on_get_kg_stats()
    → kg_panel.load_data(entities, relations, stats)
    → kg_panel.show()
```

### Remember (KG Extraction)

```
ChatMessage "remember" button click
  → ChatApp._handle_remember()
    → Button enters loading state (HOURGLASS_EMPTY, WARNING)
    → page.run_task(_do_remember, session_id, msg)
      → controller.on_remember_conversation(session_id)
        → KnowledgeService.extract_and_save(recent N messages)
      → msg.set_remember_done(success)
        → Button shows BOOKMARK_ADDED (SUCCESS) or BOOKMARK_OUTLINED (ERROR)
```

---

## Appendix: File Reference

| File | Role |
|------|------|
| `app/ui/app.py` | Main `ChatApp` class — layout assembly, event wiring, streaming orchestration |
| `app/ui/theme.py` | Colors, Fonts, Spacing, Radius, Borders constants + theme builder |
| `app/ui/widgets/chat_message.py` | `ChatMessage`, `ThinkingBlock`, `_role_badge()` |
| `app/ui/widgets/sidebar.py` | `Sidebar`, `ConversationItem` (deprecated) |
| `app/ui/widgets/tree_panel.py` | `TreePanel` — tree rendering, context menus, drag-drop |
| `app/ui/widgets/input_area.py` | `InputArea`, `_ToggleChip`, `_ModelSelector`, `_AttachmentBar` |
| `app/ui/widgets/context_panel.py` | `ContextPanel`, `_ContextBlockItem`, `_TemplateItem` |
| `app/ui/widgets/kg_panel.py` | `KGPanel` — entity/relation lists, custom tab switching |
| `app/ui/widgets/dialogs.py` | 5 dialog functions: rename, new folder, confirm, recycle bin, context block manager |
| `app/controllers/app_controller.py` | `AppController` — all UI→Core bridge methods |
| `app/controllers/settings_controller.py` | `SettingsController` — config mutation |
| `app/controllers/view_models.py` | All ViewModel dataclasses |
| `config.py` | Global configuration module |
| `app/storage/models.py` | Domain data classes |
| `app/core/protocols.py` | Abstract interfaces for dependency inversion |
