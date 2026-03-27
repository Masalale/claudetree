<div align="center">

# claudetree

**A fast, single-binary terminal UI for managing Claude Code sessions.**

[![Go 1.22+](https://img.shields.io/badge/Go-1.22+-00ADD8?style=flat-square&logo=go)](https://go.dev/dl/)
[![Alpha](https://img.shields.io/badge/status-alpha-orange?style=flat-square)]()

[Features](#features) · [Installation](#installation) · [Uninstall](#uninstall) · [Usage](#usage) · [Configuration](#configuration) · [Keybindings](#keybindings) · [How it works](#how-it-works) · [Tech stack](#tech-stack) · [Build](#build)

</div>

> [!WARNING]
> claudetree is in early alpha. Expect bugs and breaking changes.

Browse, search, rename, resume, trash, and restore Claude Code sessions without leaving the terminal.

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

## Features

- Split-pane browser with a live preview
- Full-text search across session content
- Rename, trash, and restore sessions
- Filter by project or browse all projects
- Mouse support for the main session actions
- Zero network calls; reads directly from disk

## Installation

### Prerequisites

| Dependency | Purpose | Required |
|------------|---------|----------|
| [Go 1.22+](https://go.dev/dl/) | Build from source | Yes |
| [Claude CLI](https://claude.ai/code) | Resume/new-session handoff | Yes |
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
