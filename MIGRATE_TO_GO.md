# claudetree — Full Migration to Go + Bubbletea

You are rewriting **claudetree** — a Claude Code session manager TUI — from Python/Textual to Go using the Charm stack (Bubbletea + Lipgloss + Bubbles + Glamour). The full source of truth is documented below. Produce a working, single-binary Go project with zero runtime errors.

---

## Project Overview

**claudetree** reads Claude Code's raw `.jsonl` session files from disk and presents a lazygit-style terminal UI for browsing, searching, renaming, trashing, and resuming Claude sessions. The binary is installed as `cc`.

On `Enter` (resume), the TUI exits and the binary `exec`s into `claude --resume <sid>` (replacing itself). On `ctrl+n` (new), it exec-s `claude`.

---

## Filesystem Layout (source of truth — do not change these paths)

```
~/.claude/projects/          # Claude's session store
    <project-id>/
        <session-id>.jsonl   # one file per session
~/.claude/session-names/     # claudetree's name store
    <project-id>.json        # {"<sid>": "my name", ...}
~/.claude/trash/             # claudetree's trash bin
    <sid>.jsonl
    <sid>.meta               # {"project_id":"...","name":"...","trashed_at":<unix>}
```

**Project ID encoding:** Claude encodes the absolute project path as the directory name by replacing every `/` with `-`. Leading `-` maps to root `/`.
- `-home-user-myapp` → `/home/user/myapp` → display as `~/myapp`
- Reverse: take cwd, call `strings.ReplaceAll(cwd, "/", "-")` to get the project ID for filtering.

---

## Data Models

```go
type Session struct {
    SID        string
    Name       string  // from session-names, empty if not set
    FirstMsg   string  // first user message, truncated to 60 chars
    Age        string  // "5m", "2h", "3d"
    Msgs       int     // total user+assistant turns
    ProjectID  string
    SortTime   string  // raw timestamp string for sorting (descending = newest first)
}

func (s Session) ProjectPath() string { /* pid_to_path */ }
func (s Session) DisplayLabel() string { /* name if set, else first_msg, else sid[:24] */ }

type TrashEntry struct {
    SID       string
    Name      string
    ProjectID string
    When      string  // "2d ago", "5h ago"
}

func (e TrashEntry) ProjectPath() string
```

---

## JSONL Parsing (backend)

Each `.jsonl` line is a JSON object. Relevant fields:

```json
{"type": "user"|"assistant", "timestamp": <ms-epoch or ISO string>, "message": {"content": "<string or array>"}}
```

- `content` is either a plain string or an array of `{"type":"text","text":"..."}` objects — extract the first `text` block.
- Count lines where `type` is `"user"` or `"assistant"` as `Msgs`.
- `FirstMsg`: first line where `type == "user"`, content stripped, newlines replaced with spaces, truncated to 60 chars.
- `SortTime`: the `timestamp` field of the last line that has one (use as-is for string sort; ISO strings sort lexicographically).
- Skip lines that fail JSON parse silently.

**Age calculation:**
- If timestamp looks like a large integer (>1e10), treat as milliseconds since epoch.
- Otherwise parse as ISO 8601.
- Age = now − timestamp: `"Xd"` if ≥1 day, `"Xh"` if ≥1 hour, `"Xm"` otherwise.

---

## Preview Generation

Generate a markdown string for a given session:

```
## <name>           (or ## `<project-path>` if unnamed)
`<project-path>`    (only if named)

---

**You**

> <user message, capped at 3000 chars>
> ...

---

**Claude**

<assistant message, capped at 3000 chars>

---

... (capped at 30 turns total)
*Preview capped at 30 messages.*
```

Render this markdown in the preview pane using **glamour** (`github.com/charmbracelet/glamour`).

---

## Core Operations

### list_sessions(cwd string, allProjects bool) []Session
- Glob `~/.claude/projects/*/` for project dirs.
- If `!allProjects`, only include the dir matching `strings.ReplaceAll(cwd, "/", "-")`.
- For each `.jsonl` in the project dir: parse, skip if `Msgs == 0`.
- Load names from `~/.claude/session-names/<pid>.json`.
- Sort by `SortTime` descending.
- Skip any `.jsonl` that also exists in trash (i.e. `~/.claude/trash/<sid>.jsonl` exists).

### list_trash() []TrashEntry
- Glob `~/.claude/trash/*.jsonl`.
- For each, read the `.meta` sidecar if present.
- Sort by `trashed_at` descending (fall back to file mtime).

### search_sessions(query string, cwd string, allProjects bool, useRegex bool, caseMode string) []Session
- Run: `rg --files-with-matches [flags] <query> <search_path>`
- `caseMode`: `"smart"` → `--smart-case`, `"ignore"` → `--ignore-case`, `"match"` → `--case-sensitive`
- `!useRegex` → add `--fixed-strings`
- Filter result paths to those ending in `.jsonl`.
- Parse each matched file into a Session (same as list_sessions).
- Sort by SortTime descending.
- If `rg` is not found, return empty slice with no error.

### trash_session(sid string) error
1. Find `~/.claude/projects/*/<sid>.jsonl`.
2. Write `.meta` to trash dir: `{"project_id":"...","name":"...","trashed_at":<unix>}`.
3. Move `.jsonl` to `~/.claude/trash/<sid>.jsonl`.
4. If `~/.claude/projects/<pid>/<sid>/` directory exists (sidecar), move it to `~/.claude/trash/<sid>/`.
5. Remove the name entry from session-names.

### restore_session(sid string) error
1. Find `~/.claude/trash/<sid>.jsonl`.
2. Read `.meta` for `project_id` and `name`.
3. Move `.jsonl` back to `~/.claude/projects/<pid>/<sid>.jsonl`.
4. If sidecar dir exists in trash, move it back.
5. Delete `.meta`.
6. If name non-empty, write it back to session-names.

### empty_trash() int
- Delete all `*.jsonl` and `*.meta` in trash dir.
- Return count of `.jsonl` files deleted.

### set_name / rm_name
- Read/write `~/.claude/session-names/<pid>.json` as `map[string]string`.
- Use file locking or atomic write (write to temp + rename).

### project_for_session(sid string) string
- Glob `~/.claude/projects/*/<sid>.jsonl`, return the parent dir name.

---

## TUI — Screen Architecture

Use **Bubbletea** (`github.com/charmbracelet/bubbletea`). Model the app as a stack of screens managed by a `screenStack []tea.Model` or a single top-level model with a `currentScreen` enum + embedded sub-models.

### Screens

#### 1. BrowseScreen (default)
**Layout:** split pane — left 45% session list, right 55% live markdown preview.

**Header:** `claudetree` title + subtitle showing current mode (`all projects ↓ Recent` or `~/myapp ↓ Recent`).

**Session list items:** `[dim]  5m  42msgs[/dim]  [cyan bold]<name>[/cyan]  [dim]<first_msg[:35]>[/dim]  [dim]<project_path>[/dim]`
- If unnamed: `[dim]  5m  42msgs[/dim]  <first_msg[:50]>  [dim]<project_path>[/dim]`
- Show `project_path` column only when `allProjects == true`.

**Filter bar (vim `/`):** hidden by default. Press `/` to show. A text input appears at the bottom of the left pane. Typing filters the list in real time against `name + first_msg + project_path` (all words must match, case-insensitive). `Escape` clears filter and hides bar. `↑`/`↓` while in filter input move the list cursor. `Enter` in filter input closes the bar and opens preview.

**Sort cycling (`ctrl+s`):** cycles `recent → oldest → msgs↓ → msgs↑`. Updates subtitle.

**Right-click context menu:** on mouse button 3 over a session item, show an inline floating menu (positioned at mouse coords, clipped to terminal bounds) with options: `Resume`, `Rename`, `Trash`, `New session`. Click outside or `Escape` hides it. Use a backdrop layer to intercept outside clicks.

**Keybindings:**

| Key | Action |
|-----|--------|
| `enter` | Open SessionPreviewScreen |
| `ctrl+d` | Trash current session |
| `ctrl+r` | Open rename dialog |
| `ctrl+t` | Open TrashScreen |
| `ctrl+a` | Open DirectoryPickerScreen (or back to all-projects if already filtered) |
| `ctrl+s` | Cycle sort order |
| `ctrl+/` or `ctrl+_` | Open content search dialog → ContentSearchScreen |
| `ctrl+n` | Exit app → exec `claude` (new session) |
| `/` | Open filter bar |
| `escape` | Quit (if no filter bar open) |
| `q` | Quit |

**Live preview:** debounce 80ms after cursor move, load preview in goroutine, update right pane.

---

#### 2. SessionPreviewScreen
**Layout:** full screen. Title bar with session label + metadata. Find bar (hidden by default). Scrollable markdown preview. Footer with keybindings.

**Find (ctrl+f):** shows a text input. As user types, highlight all matches in the rendered text (yellow background, black text). Show match count `3/12` in a right-aligned label next to the input. `n`/`N` navigate next/prev match (only when find input is NOT focused). `Escape` in find input closes it and clears highlights.

**Case mode (ctrl+i or alt+c):** cycles `smart → ignore → match`. Show indicator in match-count label.

**Regex toggle (ctrl+g or alt+r):** toggles regex vs literal search. When regex is invalid, show `[invalid regex]` in match-count label.

**Scroll to match:** animate scroll to the matched line.

**Keybindings:**

| Key | Action |
|-----|--------|
| `enter` | Resume (exit app → exec `claude --resume <sid>`) |
| `escape` | Back to previous screen |
| `ctrl+f` | Focus find input |
| `ctrl+i` / `alt+c` | Cycle case mode |
| `ctrl+g` / `alt+r` | Toggle regex/literal |
| `n` / `N` | Next / prev match (when find input not focused) |
| `ctrl+c` / `q` | Quit app |

---

#### 3. DirectoryPickerScreen
**Layout:** full screen. Filter input at top. List of all project directories with session counts.

List items: `<display_path>  [dim]N sessions[/dim]`

Typing in filter input filters the list. `Enter` or selecting an item switches to `BrowseScreen` scoped to that directory. `Escape` goes back to all-projects BrowseScreen.

---

#### 4. ContentSearchScreen
**Layout:** split pane (same as BrowseScreen). Search input at top of left pane. Results list below. Preview on right.

Behavior: on `Enter` in the search input, run `search_sessions()` and populate the list. `↑`/`↓` in input moves list cursor. `ctrl+/` re-focuses search input. `ctrl+d` trashes current result. `ctrl+b` / `escape` goes back. Results carry the ripgrep query into the preview screen as initial find term.

**Case mode and regex mode** same keybindings as preview screen, applied to the rg call.

---

#### 5. TrashScreen
**Layout:** split pane. Trash list left, preview right.

List items: `[dim]<when>[/dim]  [red bold]<name or sid[:24]>[/red]  [dim]<project_path>[/dim]`

`Enter` on an item restores it. Right-click menu: `Restore`, `Delete forever`, `Empty trash`.

**Keybindings:**

| Key | Action |
|-----|--------|
| `ctrl+d` | Delete forever (with confirmation dialog) |
| `ctrl+e` | Empty all trash (with confirmation dialog) |
| `ctrl+b` / `escape` | Back |

---

### Dialogs

#### InputDialog
Modal overlay (centered, `width: 62`). Shows a prompt label and a text input. `Enter` submits, `Escape` cancels. Returns `(string, bool)` — value and whether confirmed.

#### ConfirmDialog
Modal overlay (centered, `width: 56`, warning border color). Shows message and an input with placeholder `"type y + Enter to confirm, Escape cancels"`. `Enter` submits if value is `"y"`, `Escape` cancels. Returns `bool`.

---

## CLI Interface

Binary name: `cc` (via `go install` / Makefile).

```
cc                  → open BrowseScreen (all projects)
cc rm [sid]         → trash <sid> directly; if no sid, open TrashScreen
cc restore [sid]    → restore <sid>; if no sid, open TrashScreen
cc empty            → prompt then empty_trash()
cc help / -h        → print help
```

After TUI exits:
- `("resume", sid)` → `syscall.Exec("claude", ["claude", "--resume", sid], env)` (respects `$CLAUDE_CMD`)
- `("new",)` → `syscall.Exec("claude", ["claude"], env)`
- `nil` → exit 0

---

## Go Module & Dependencies

```
module github.com/Masalale/claudetree

go 1.22

require (
    github.com/charmbracelet/bubbletea       latest
    github.com/charmbracelet/bubbles         latest  // list, textinput, viewport, spinner
    github.com/charmbracelet/lipgloss        latest
    github.com/charmbracelet/glamour         latest  // markdown rendering
)
```

Binary entry point: `cmd/cc/main.go`.
Backend logic: `internal/backend/`.
TUI screens: `internal/tui/`.

---

## Styling (Lipgloss)

Match the visual feel of the Python version:

- Header background: primary-darken variant (dark blue/purple)
- List highlight: primary at 30% opacity
- Context menu: surface background, rounded border, primary color
- Backdrop: background at 40% opacity
- Find highlight: bold black on yellow
- Muted/dim text: `lipgloss.AdaptiveColor{Light: "#555", Dark: "#777"}`
- Warning/destructive: amber/orange border color for confirm dialogs
- Use `lipgloss.NewStyle().Adaptive(...)` for light/dark terminal compatibility

---

## Error Handling Rules

- All backend errors return `error` — never panic.
- If `rg` is not installed, `search_sessions` returns `[]Session{}` silently.
- Missing `.meta` in trash is non-fatal — use empty strings for name/pid.
- Malformed `.jsonl` lines are skipped silently.
- File operations (trash/restore) must be atomic where possible (write-then-rename).
- All goroutines spawned by the TUI must send results via `tea.Cmd` — never mutate model state directly from a goroutine.

---

## Deliverables

1. `go.mod` + `go.sum`
2. `cmd/cc/main.go` — CLI entry point
3. `internal/backend/backend.go` — all data operations
4. `internal/tui/browse.go` — BrowseScreen
5. `internal/tui/preview.go` — SessionPreviewScreen
6. `internal/tui/dirpicker.go` — DirectoryPickerScreen
7. `internal/tui/search.go` — ContentSearchScreen
8. `internal/tui/trash.go` — TrashScreen
9. `internal/tui/dialogs.go` — InputDialog + ConfirmDialog
10. `internal/tui/contextmenu.go` — ContextMenuWidget
11. `internal/tui/app.go` — root model / screen stack
12. `Makefile` — `make build` → `./bin/cc`, `make install` → copies to `~/.local/bin/cc`

The project must `go build ./...` with zero errors and zero warnings. All screens must be reachable. All keybindings documented above must work.
