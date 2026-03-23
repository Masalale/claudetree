# claudetree

**Ever spent 20 minutes trying to find that one Claude session?**

I built claudetree because I was tired of losing context, opening the wrong chat, and repeating myself to Claude.

claudetree gives you a clean terminal UI to find, search, rename, resume, and clean up your Claude Code sessions fast.

```
  claudetree
 ┌──────────────────────────┬──────────────────────────────────┐
 │ filter sessions...       │                                  │
 │──────────────────────────│  ────────────────────────────    │
 │  4m  201msgs  openclaw   │  openclaw tui fix                │
 │ 13h   56msgs  WSL setup  │  ~/claudetree                    │
 │  1d   32msgs  auth refac │  ────────────────────────────    │
 │  3d    8msgs  docker dbg │  You:                            │
 │                          │    the tui wasn't rendering      │
 │                          │    ctrl-/ on the sessions list   │
 │                          │                                  │
 │                          │  Claude:                         │
 │                          │    The issue is in the key       │
 │                          │    binding — ctrl+underscore...  │
 └──────────────────────────┴──────────────────────────────────┘
 ^d Trash  ^r Rename  ^t Trash bin  ^a All  ^/ Search  ^n New
```

---

## The Problem

If you use Claude Code a lot, you know this pain:

- You know you solved something before, but can't find the session
- Your session list gets messy fast
- You reopen old chats and waste time figuring out "where were we"
- You end up repeating context instead of building

That adds up. It's frustrating. And it breaks flow.

---

## The Fix

claudetree helps you stay in flow:

- **Browse** sessions with preview, age, and message count
- **Search** session content (not just titles)
- **Resume** the exact session you need
- **Rename** sessions so they actually make sense
- **Trash/restore** old sessions to keep things clean

No hype. Just useful session management.

---

## Why this exists

I work across a lot of projects. I kept losing good conversations. I'd remember we solved something, but not *where*. I'd waste time reopening the wrong sessions, re-explaining context, losing momentum.

claudetree is the tool I wished I had — a simple way to make session history usable.

---

## How it works

claudetree reads your Claude sessions directly from disk — no API, no cloud, no account.

**All your sessions, one place.**

When you use Claude Code in any directory — `~/projects/app`, `~/work/api`, `/tmp/quick-test` — it stores sessions in `~/.claude/projects/`. claudetree scans ALL of them.

**Works everywhere you use Claude Code:**

- VS Code
- VS Code Insiders
- VSCodium
- Antigravity
- Cursor
- Any VS Code fork
- The CLI

Same sessions, same storage (`~/.claude/projects/`). claudetree sees them all.

```
~/.claude/
├── projects/           ← sessions from EVERY project you've used Claude Code in
│   ├── -home-you-app/     (~/app)
│   ├── -home-you-work-api/ (~/work/api)
│   └── ...
├── session-names/      ← custom names you assign (persisted)
└── trash/               ← soft-deleted sessions (restore anytime)
```

So when you open claudetree, you're not just seeing sessions from the current directory — you're seeing your entire Claude Code history across every project on your machine.

**What it does:**

- Shows every session from every project — age, message count, first message
- Search inside session content using ripgrep (not just titles)
- Rename sessions with names that persist in `~/.claude/session-names/`
- Trash sessions to `~/.claude/trash/` with metadata — restore anytime
- Toggle `ctrl-a` to filter current directory vs all projects
- When you hit Enter, it exits and runs `claude --resume <id>`

**What it doesn't do:**

- No network requests
- No Claude API calls
- No sending your data anywhere

You stay in control. Claude Code takes over once you pick a session.

---

## Requirements

| Dependency | Purpose |
|------------|---------|
| Python ≥ 3.11 | Runtime |
| [ripgrep](https://github.com/BurntSushi/ripgrep) | Content search |
| [Claude CLI](https://claude.ai/code) | Session resumption |

---

## Install

```bash
git clone https://github.com/ngash/claudetree ~/claudetree
cd ~/claudetree && ./install.sh
```

One-liner:

```bash
git clone https://github.com/ngash/claudetree ~/claudetree && ~/claudetree/install.sh
```

### ripgrep

```bash
# Ubuntu/Debian
sudo apt install ripgrep

# macOS
brew install ripgrep

# Arch
sudo pacman -S ripgrep
```

---

## Uninstall

```bash
pip uninstall claudetree          # quick remove
~/claudetree/uninstall.sh        # interactive (also cleans up PATH)
```

Your session data in `~/.claude/` stays untouched.

---

## Usage

```bash
cc              # open session picker
cc rm [id]     # trash a session
cc restore [id] # restore from trash
cc empty       # empty trash permanently
cc help        # show help
```

---

## Keybindings

### Browse mode

| Key | Action |
|-----|--------|
| `enter` | Resume session |
| `ctrl-d` | Trash session |
| `ctrl-r` | Rename session |
| `ctrl-t` | Open trash bin |
| `ctrl-a` | Toggle all projects |
| `ctrl-/` | Search content |
| `ctrl-n` | New session |
| `↑ / ↓` | Navigate |
| `escape` | Quit |

### Search mode

| Key | Action |
|-----|--------|
| `enter` (on result) | Resume session |
| `enter` (in input) | Run search |
| `ctrl-d` | Trash session |
| `ctrl-/` | Re-focus search |
| `ctrl-b` | Back to browse |

### Trash bin

| Key | Action |
|-----|--------|
| `enter` | Restore session |
| `ctrl-d` | Delete forever |
| `ctrl-e` | Empty all trash |
| `ctrl-b` | Back to browse |

---

## Tech Stack

- **Python ≥ 3.11**
- **[Textual](https://github.com/textualize/textual)** — TUI framework
- **[ripgrep](https://github.com/BurntSushi/ripgrep)** — full-text search
- **[Claude CLI](https://claude.ai/code)** — session resumption

---

## Future

I'm exploring a bigger idea: **opentree** — one session manager for multiple AI coding harnesses (Claude Code, OpenCode, Gemini, Kilo Code, etc.).

For now, this project focuses on **Claude Code**, and doing that well.

If that vision interests you, I'd love to talk.

---

## Contributing

If this pain is familiar, I'd love your help.

- Open an issue
- Share your workflow pain points
- Submit a PR
- Suggest features

Let's build a better session experience together.

---

## License

MIT
