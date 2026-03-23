#!/usr/bin/env bash
# claudetree uninstall script
set -euo pipefail

# ── Colour helpers ─────────────────────────────────────────────────────────────
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
red()    { printf '\033[31m%s\033[0m\n' "$*"; }
bold()   { printf '\033[1m%s\033[0m\n'  "$*"; }

# ── Uninstall package ──────────────────────────────────────────────────────────
bold "claudetree uninstaller"
echo ""
echo "Uninstalling claudetree..."

# Run pip uninstall and capture output to check result
set +e
PIP_OUT=$(pip uninstall claudetree -y 2>&1)
PIP_RET=$?
set -e

if [ $PIP_RET -eq 0 ]; then
    green "  ✓ claudetree uninstalled"
elif echo "$PIP_OUT" | grep -q "not installed"; then
    yellow "  ! claudetree was not installed (nothing to remove)"
else
    yellow "  ! pip uninstall encountered an issue (package may already be gone)"
fi

# ── Preserve user data ─────────────────────────────────────────────────────────
echo ""
yellow "Session data in ~/.claude/ is untouched (session names, trash)"

# ── PATH cleanup prompt ────────────────────────────────────────────────────────
echo ""
echo "Configuring shell..."

# Detect shell RC (same logic as install.sh)
if [ -n "${ZSH_VERSION:-}" ] || [ "$(basename "${SHELL:-}")" = "zsh" ]; then
    SHELL_RC="$HOME/.zshrc"
elif [ -n "${BASH_VERSION:-}" ]; then
    SHELL_RC="$HOME/.bashrc"
else
    SHELL_RC="$HOME/.profile"
fi

PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'

# Ask user if they want to remove PATH line
if grep -qF "$PATH_LINE" "$SHELL_RC" 2>/dev/null; then
    echo ""
    printf "Remove ~/.local/bin from PATH in $SHELL_RC? [y/N] "
    read -r response
    
    if [ "$response" = "y" ] || [ "$response" = "Y" ]; then
        # Remove the PATH line using grep inverse
        grep -vF "$PATH_LINE" "$SHELL_RC" > "$SHELL_RC.tmp"
        mv "$SHELL_RC.tmp" "$SHELL_RC"
        green "  ✓ removed from $SHELL_RC"
    else
        yellow "  ! PATH line left in $SHELL_RC (you can manually remove it)"
    fi
else
    green "  ✓ PATH line not found in $SHELL_RC (already clean)"
fi

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
bold "Done! claudetree is uninstalled."
echo ""

exit 0
