<div align="center">

# claudetree

**One terminal UI for every AI coding session on your machine.**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](LICENSE)
[![Alpha](https://img.shields.io/badge/status-alpha-orange?style=flat-square)]()

[Getting Started](#getting-started) · [Harnesses](#supported-harnesses) · [Usage](#usage) · [Keybindings](#keybindings) · [How it works](#how-it-works)

</div>

> [!WARNING]
> This project is in early alpha. Expect bugs and breaking changes.

Browse, search, rename, resume, and clean up sessions from **Claude Code, Opencode, GitHub Copilot CLI, PI, Hermes, and Codex CLI** — across every project on your machine, without leaving the terminal.

```
claudetree
┌──────────┬──────────────────────────┬──────────────────────────────────┐
│ ▦ All 89 │ / filter sessions...     │                                  │
│ ★ Claude │──────────────────────────│  session preview                 │
│ ▲ Opencode  4m  fix auth   ~/proj   │  ──────────────────────────────  │
│ ◉ Copilot  13h  WSL setup  ~/wsl    │  You                             │
│ π PI       1d  C# drills   ~/excs   │  > what was I working on again?  │
│ ⚡ Hermes   3d  docker dbg  ~/dock   │                                  │
│ ◆ Codex    5d  adb bugfix  /mnt/c   │                                  │
└──────────┴──────────────────────────┴──────────────────────────────────┘
```

## Supported harnesses

| Harness | Sessions read from | Resume | Trash strategy |
|---------|-------------------|--------|----------------|
| ★ Claude Code | `~/.claude/projects/`, `~/.claude/transcripts/` | `claude --resume <sid>` | move file to `~/.claude/trash/` |
| ▲ Opencode | `~/.local/share/opencode/storage/` | `opencode --session <sid>` | move session/message/part bundle |
| ◉ Copilot CLI | `~/.copilot/session-store.db` (sqlite) | `copilot --resume=<sid>` | rows dumped to a restorable bundle, then removed |
| π PI | `~/.pi/agent/sessions/` | `pi --session <sid>` | move file to `~/.claude/trash/` |
| ⚡ Hermes | `~/.hermes/sessions/` | `hermes --resume <sid>` | move file to `~/.claude/trash/` |
| ◆ Codex CLI | `~/.codex/state_*.sqlite` + rollout files | `codex resume <sid>` | Codex's own `archived` flag |

**Full parity everywhere**: every harness supports list, preview, fuzzy filter, content search, rename, trash, restore, and delete-forever. Resume uses each harness's native command, and claudetree `cd`s into the session's original project directory before exec-ing.

**Main sessions only** — subagent/sidechain sessions are hidden, matching each harness's own resume picker: Opencode Task children (`parentID`), Claude Code subagent transcripts, and non-UUID files in Claude dirs (e.g. `ses_*` transcript dumps imported from other tools, which `claude --resume` cannot open).

**WSL aware** — on WSL, Windows-side stores under `/mnt/*/Users/*/` (`.claude`, `.codex`) are scanned too, with Windows paths translated to their WSL equivalents.

## Features

- **Instant startup** — the previous scan's results paint immediately; a background rescan refreshes them. Session files are parsed once and cached by mtime (`~/.cache/claudetree/`), so rescans cost stat calls, not full reads
- **Harness rail** — filter sessions by tool with a click, the palette, or keys `1`-`7` (instant, no rescan)
- **Split-pane browser** — sessions on the left, live markdown preview on the right
- **Fuzzy filter** — `/` filter does fzf-style subsequence matching (`dckr` finds `docker debugging`)
- **Full-text search** — ripgrep-powered search inside session *content* across all harnesses (sqlite stores searched natively, Codex rollouts via ripgrep)
- **Rename sessions** — names persist in `~/.claude/session-names/`, per harness
- **Trash & restore** — soft-delete sessions from any harness and restore them anytime; sqlite-backed harnesses use their own native soft-delete flags or restorable bundles
- **Sort modes** — cycle through Folder (A-Z/Z-A), Recent, Oldest
- **Directory filter** — view sessions from the current project or all projects
- **Find in preview** — regex/literal search with case-mode controls and `n`/`N` navigation
- **Command palette** — `ctrl+k` fuzzy access to every action
- **Right-click context menu** — full mouse support for all major actions
- **Zero network** — reads directly from local session stores, no API calls

## Getting started

### Prerequisites

| Dependency | Purpose | Required |
|------------|---------|----------|
| [Python 3.11+](https://www.python.org/) | Runtime | Yes |
| At least one harness CLI (`claude`, `opencode`, `copilot`, `pi`, `hermes`, `codex`) | Session resumption | Yes |
| [ripgrep](https://github.com/BurntSushi/ripgrep) | Content search | Optional |

```bash
# Human
git clone https://github.com/Masalale/claudetree ~/claudetree && cd ~/claudetree && ./install.sh
```
```
# Agent (non-interactive)
git clone https://github.com/Masalale/claudetree ~/claudetree && cd ~/claudetree && make install
```

## Uninstall

```bash
# Human
cd ~/claudetree && ./uninstall.sh
```
```
# Agent (non-interactive)
cd ~/claudetree && ./uninstall.sh --yes
```

`--yes` removes the binary and cleans `~/.local/bin` from shell config. Session data is preserved.

## Usage

```bash
claudetree                  # open the session browser
claudetree ls               # same as above
claudetree list             # same as above
claudetree rm [sid]         # trash a session by ID, or open trash browser
claudetree delete [sid]     # alias for rm
claudetree trash [sid]      # alias for rm
claudetree restore [sid]    # restore a session, or open the trash browser
claudetree empty            # permanently empty trash (asks for confirmation)
claudetree help             # show help
```

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `CLAUDE_CMD` | `claude` | Claude binary used for resume and new-session handoff |

> [!TIP]
> Set `CLAUDE_CMD` if your Claude binary lives somewhere else, for example `CLAUDE_CMD=/usr/local/bin/claude claudetree`.

## Keybindings

### Browse

| Key | Action |
|-----|--------|
| `j` / `k` or `↑` / `↓` | Move through sessions |
| `enter` | Open preview |
| `1` - `7` | Harness filter (`1` = all, then rail order) |
| `/` | Filter sessions (fuzzy) |
| `o` / `ctrl+s` | Cycle sort order |
| `d` / `ctrl+d` | Trash session |
| `r` / `ctrl+r` | Rename session |
| `t` / `ctrl+t` | Open trash bin |
| `a` / `ctrl+a` | Toggle project scope / pick directory |
| `s` / `ctrl+/` | Search session content |
| `p` | Session menu |
| `ctrl+k` | Command palette |
| `q` | Quit |

### Preview

| Key | Action |
|-----|--------|
| `enter` | Resume the session |
| `f` / `ctrl+f` | Find in preview |
| `n` / `N` | Next / previous match |
| `c` / `ctrl+i` | Cycle case mode |
| `r` / `ctrl+g` | Toggle regex / literal search |
| `esc` | Back |

### Search

| Key | Action |
|-----|--------|
| `enter` | Run search |
| `d` / `ctrl+d` | Trash selected result |
| `s` / `ctrl+/` | Focus search input |
| `c` / `ctrl+i` | Cycle case mode |
| `r` / `ctrl+g` | Toggle regex / literal |
| `b` / `esc` | Back |

### Trash

| Key | Action |
|-----|--------|
| `enter` / `r` | Restore session |
| `d` / `ctrl+d` | Delete forever |
| `e` / `ctrl+e` | Empty trash |
| `b` / `esc` | Back |

## How it works

claudetree reads session data directly from each harness's local store:

```text
~/.claude/projects/                      # Claude Code: .jsonl per session, grouped by project id
~/.claude/transcripts/                   # Claude Code: imported transcripts
~/.local/share/opencode/storage/         # Opencode: session/message/part JSON files
~/.copilot/session-store.db              # Copilot CLI: sqlite (opened read-only)
~/.pi/agent/sessions/<encoded-cwd>/      # PI: .jsonl per session, grouped by project path
~/.hermes/sessions/                      # Hermes: .jsonl per session
~/.codex/state_*.sqlite                  # Codex CLI: thread index (messages in rollout .jsonl)

~/.claude/session-names/                 # claudetree: custom names (per project / harness)
~/.claude/trash/                         # claudetree: soft-deleted sessions + metadata
```

Project IDs are encoded paths: `-home-you-app` maps back to `/home/you/app` (PI wraps them as `--home-you-app--`).

When you resume a session, claudetree switches to the session's project directory, then execs into the harness's native resume command so it can find the session.

Copilot and Codex sessions live in sqlite databases. Reads are always done through read-only connections; trash uses the safest write available per store: Codex's own `archived` flag, and for Copilot a full row dump into `~/.claude/trash/<sid>.copilot.json` that restores byte-identically.

## Tech stack

- Python 3.11+ (stdlib `sqlite3` for Copilot)
- [Textual](https://textual.textualize.io/)
- [Rich](https://rich.readthedocs.io/)
- ripgrep (optional)

## Build

```bash
make install    # install to ~/.local/bin/claudetree
make uninstall  # remove from ~/.local/bin/claudetree
```
