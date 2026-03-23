#!/usr/bin/env bash
# claudetree install script
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Colour helpers ─────────────────────────────────────────────────────────────
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
red()    { printf '\033[31m%s\033[0m\n' "$*"; }
bold()   { printf '\033[1m%s\033[0m\n'  "$*"; }

# ── Dependency checks ──────────────────────────────────────────────────────────
bold "claudetree installer"
echo ""

check() {
    local cmd="$1" label="$2"
    if command -v "$cmd" &>/dev/null; then
        green "  ✓ $label"
        return 0
    else
        red   "  ✗ $label not found"
        return 1
    fi
}

echo "Checking dependencies..."
MISSING=0
check python3 "python3 ≥ 3.11" || MISSING=$((MISSING+1))
check rg      "ripgrep"         || MISSING=$((MISSING+1))
check claude  "claude CLI"      || { yellow "  ! claude CLI not found (install from https://claude.ai/code)"; }

if [ "$MISSING" -gt 0 ]; then
    echo ""
    red "Missing $MISSING required dependency/dependencies. Aborting."
    exit 1
fi

# ── Check Python version ──────────────────────────────────────────────────────
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_OK=$(python3 -c "import sys; print('yes' if sys.version_info >= (3,11) else 'no')")
if [ "$PY_OK" = "no" ]; then
    red "  ✗ Python $PY_VER found, but 3.11+ is required"
    exit 1
fi
green "  ✓ Python $PY_VER"

# ── Install package ─────────────────────────────────────────────────────────────
echo ""
echo "Installing claudetree..."
pip install --user --quiet "$REPO_DIR"
green "  ✓ claudetree installed"

# ── PATH check ─────────────────────────────────────────────────────────────────
echo ""
echo "Configuring shell..."

# Detect shell RC
if [ -n "${ZSH_VERSION:-}" ] || [ "$(basename "${SHELL:-}")" = "zsh" ]; then
    SHELL_RC="$HOME/.zshrc"
elif [ -n "${BASH_VERSION:-}" ]; then
    SHELL_RC="$HOME/.bashrc"
else
    SHELL_RC="$HOME/.profile"
fi

PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'

add_if_missing() {
    local line="$1" file="$2"
    if ! grep -qF "$line" "$file" 2>/dev/null; then
        echo "$line" >> "$file"
        green "  ✓ added to $file"
    else
        green "  ✓ already in $file (skipped)"
    fi
}

add_if_missing "$PATH_LINE" "$SHELL_RC"
export PATH="$HOME/.local/bin:$PATH"

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
bold "Done! Run 'cc' to open claudetree."
echo ""
echo "  If 'cc' is not found, reload your shell:"
echo "    source $SHELL_RC"
echo ""
echo "Keybindings:"
echo "  enter    Resume session      ctrl-d  Trash"
echo "  ctrl-r   Rename              ctrl-t  Trash bin"
echo "  ctrl-a   All projects        ctrl-n  New session"
echo "  ctrl-/   Search content      ctrl-b  Back"
