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
│                          │                                  │
│                          │  Claude:                         │
│                          │    The issue is in the key       │
│                          │    binding — ctrl+underscore...  │
└──────────────────────────┴──────────────────────────────────┘
 enter:preview  ^d:trash  ^r:rename  ^t:trash-bin  ^/:search  ^n:new
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

### Installation

```bash
git clone https://github.com/Masalale/claudetree ~/claudetree
cd ~/claudetree
make install
```

This builds the binary and copies it to `~/.local/bin/cc`. Make sure that directory is in your `PATH`:

<details>
<summary>Add ~/.local/bin to PATH</summary>

**bash:**
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

**zsh:**
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

</details>

### Install ripgrep (optional, for content search)

```bash
# Ubuntu/Debian
sudo apt install ripgrep

# macOS
brew install ripgrep

# Arch
sudo pacman -S ripgrep
```

## Usage

```bash
cc                  # open the session browser
cc rm [sid]         # trash a session by ID, or open the trash browser
cc restore [sid]    # restore a session, or open the trash browser
cc empty            # permanently empty trash (asks for confirmation)
cc help             # show help
```

> [!TIP]
> Set the `CLAUDE_CMD` environment variable to override the claude binary path, e.g. `CLAUDE_CMD=/usr/local/bin/claude cc`.

## Keybindings

### Browse

| Key | Action |
|-----|--------|
| `j` / `k` or `↑` / `↓` | Navigate sessions |
| `enter` | Open full-screen preview |
| `/` | Filter sessions (live, multi-word AND) |
| `ctrl+s` | Cycle sort order |
| `ctrl+d` | Trash session |
| `ctrl+r` | Rename session |
| `ctrl+t` | Open trash bin |
| `ctrl+a` | Directory picker / toggle all projects |
| `ctrl+/` | Content search (ripgrep) |
| `ctrl+n` | Start new Claude session |
| `q` / `esc` | Quit |

### Preview

| Key | Action |
|-----|--------|
| `enter` | Resume session (`claude --resume <id>`) |
| `ctrl+f` | Open find bar |
| `n` / `N` | Next / previous match |
| `ctrl+i` | Cycle case mode (smart → ignore → match) |
| `ctrl+g` | Toggle regex / literal |
| `esc` | Back to browse |

### Search

| Key | Action |
|-----|--------|
| `enter` (in input) | Run search |
| `enter` (on result) | Preview with search term pre-filled |
| `ctrl+d` | Trash selected result |
| `ctrl+/` | Re-focus search input |
| `ctrl+i` / `alt+c` | Cycle case mode |
| `ctrl+g` / `alt+r` | Toggle regex / literal |
| `ctrl+b` / `esc` | Back to browse |

### Trash bin

| Key | Action |
|-----|--------|
| `enter` | Restore session |
| `ctrl+d` | Delete forever |
| `ctrl+e` | Empty all trash |
| `ctrl+b` / `esc` | Back to browse |

## How it works

claudetree reads sessions directly from `~/.claude/projects/` — the same `.jsonl` files Claude Code writes when you work in any editor. No API, no account, no sync.

```
~/.claude/
├── projects/           ← sessions from every project on your machine
│   ├── -home-you-app/
│   ├── -home-you-work-api/
│   └── ...
├── session-names/      ← your custom names (persisted by claudetree)
└── trash/              ← soft-deleted sessions (restorable anytime)
```

This means claudetree works with every Claude Code editor integration: VS Code, Cursor, Windsurf, the CLI, and any fork — they all write to the same place.

When you select a session, claudetree exits cleanly and hands off to `claude --resume <id>`.

## Tech stack

- **[Go 1.22](https://go.dev)** — single binary, fast startup, no runtime required
- **[Bubbletea](https://github.com/charmbracelet/bubbletea)** — TUI framework
- **[Bubbles](https://github.com/charmbracelet/bubbles)** — text input and viewport components
- **[Lipgloss](https://github.com/charmbracelet/lipgloss)** — terminal styling
- **[Glamour](https://github.com/charmbracelet/glamour)** — markdown rendering in the preview pane
- **[ripgrep](https://github.com/BurntSushi/ripgrep)** — full-text search (external binary, optional)

## Build

```bash
make build      # outputs ./bin/cc
make install    # builds and installs to ~/.local/bin/cc
make clean      # removes ./bin/
```
