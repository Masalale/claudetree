<div align="center">

# claudetree

**A fast, single-binary terminal UI for managing your Claude Code sessions.**

[![Go 1.22+](https://img.shields.io/badge/Go-1.22+-00ADD8?style=flat-square&logo=go)](https://go.dev/dl/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](LICENSE)
[![Alpha](https://img.shields.io/badge/status-alpha-orange?style=flat-square)]()

[Getting Started](#getting-started) · [Usage](#usage) · [Keybindings](#keybindings) · [How it works](#how-it-works)

</div>

> [!WARNING]
> This project is in early alpha. Expect bugs and breaking changes.

Browse, search, rename, resume, and clean up Claude Code sessions across every project on your machine — without leaving the terminal.

```
claudetree
┌──────────────────────────┬──────────────────────────────────┐
│ / filter sessions...     │                                  │
│──────────────────────────│  openclaw tui fix                │
│  4m  201msgs  openclaw   │  ~/claudetree                    │
│ 13h   56msgs  WSL setup  │  ────────────────────────────    │
│  1d   32msgs  auth refac │  You:                            │
│  3d    8msgs  docker dbg │    the tui wasn't rendering      │
│                          │    ctrl-/ on the sessions list   │
└──────────────────────────┴──────────────────────────────────┘
```

## Why

If you use Claude Code heavily, you know the pain: you solved something last week but can't find the session. Your list is full of untitled chats. You re-explain context instead of building.

claudetree is the session manager Claude Code is missing — a keyboard-driven way to find the session you need and get back to work immediately.

## Features

- **Split-pane browser** — sessions on the left, live markdown preview on the right
- **Full-text search** — ripgrep-powered search inside session *content*, not just titles
- **Rename sessions** — names persist in `~/.claude/session-names/`
- **Trash & restore** — soft-delete sessions and restore them anytime
- **Sort modes** — cycle through Recent, Oldest, Most messages, Fewest messages
- **Directory filter** — view sessions from the current project or all projects
- **Find in preview** — regex/literal search with case-mode controls and `n`/`N` navigation
- **Right-click context menu** — full mouse support for all major actions
- **Zero network** — reads directly from `~/.claude/projects/`, no API calls

## Getting started

### Prerequisites

| Dependency | Purpose | Required |
|------------|---------|----------|
| [Go 1.22+](https://go.dev/dl/) | Build from source | Yes |
| [Claude CLI](https://claude.ai/code) | Session resumption | Yes |
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

`--yes` removes the binary and cleans `~/.local/bin` from shell config. Session data in `~/.claude/` is preserved.

## Usage

```bash
cc                  # open the session browser
cc ls               # same as cc
cc list             # same as cc
cc rm [sid]         # trash a session by ID, or open trash browser
cc delete [sid]     # alias for rm
cc trash [sid]      # alias for rm
cc restore [sid]    # restore a session, or open the trash browser
cc empty            # permanently empty trash (asks for confirmation)
cc help             # show help
```

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `CLAUDE_CMD` | `claude` | Claude binary used for resume and new-session handoff |

> [!TIP]
> Set `CLAUDE_CMD` if your Claude binary lives somewhere else, for example `CLAUDE_CMD=/usr/local/bin/claude cc`.

## Keybindings

### Browse

| Key | Action |
|-----|--------|
| `j` / `k` or `↑` / `↓` | Move through sessions |
| `enter` | Open preview |
| `/` | Filter sessions |
| `ctrl+s` | Cycle sort order |
| `ctrl+d` | Trash session |
| `ctrl+r` | Rename session |
| `ctrl+t` | Open trash bin |
| `ctrl+a` | Toggle project scope / pick directory |
| `ctrl+/` | Search session content |
| `ctrl+n` | Start a new Claude session |
| `q` / `esc` | Quit |

### Preview

| Key | Action |
|-----|--------|
| `enter` | Resume the session |
| `ctrl+f` | Find in preview |
| `n` / `N` | Next / previous match |
| `ctrl+i` | Cycle case mode |
| `ctrl+g` | Toggle regex / literal search |
| `esc` | Back |

### Search

| Key | Action |
|-----|--------|
| `enter` | Run search |
| `ctrl+d` | Trash selected result |
| `ctrl+/` | Focus search input |
| `ctrl+i` / `alt+c` | Cycle case mode |
| `ctrl+g` / `alt+r` | Toggle regex / literal |
| `ctrl+b` / `esc` | Back |

### Trash

| Key | Action |
|-----|--------|
| `enter` | Restore session |
| `ctrl+d` | Delete forever |
| `ctrl+e` | Empty trash |
| `ctrl+b` / `esc` | Back |

## How it works

claudetree reads Claude Code session files directly from disk:

```text
~/.claude/
├── projects/        # session .jsonl files, grouped by project id
├── session-names/   # custom session names
└── trash/           # soft-deleted sessions and metadata
```

Project IDs are encoded paths: `-home-you-app` maps back to `/home/you/app`.

When you resume a session, claudetree exits cleanly and execs into `claude --resume <sid>`.

> [!IMPORTANT]
> The Go implementation is the source of truth; legacy Python/Textual artifacts may still exist in the repository for reference.

## Tech stack

- Go 1.22
- Bubbletea
- Bubbles
- Lipgloss
- Glamour
- ripgrep (optional)

## Build

```bash
make build      # build ./bin/cc
make install    # build and copy to ~/.local/bin/cc
make uninstall  # remove ~/.local/bin/cc
make clean      # remove ./bin/
```
