"""CLI entry point for claudetree."""

from __future__ import annotations

import os
import sys


def main() -> None:
    args = sys.argv[1:]

    # ── Internal sub-commands (called by scripts / power users) ─────────────
    if args and args[0].startswith("_"):
        _run_internal(args)
        return

    # ── Public commands ──────────────────────────────────────────────────────
    cmd = args[0] if args else ""

    if cmd in ("", "ls", "list"):
        _picker()

    elif cmd in ("rm", "delete", "trash"):
        if len(args) >= 2:
            from .backend import trash_session

            try:
                trash_session(args[1])
                print("Trashed.")
            except Exception as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            _picker(initial_screen="trash")

    elif cmd == "restore":
        if len(args) >= 2:
            from .backend import restore_session

            try:
                restore_session(args[1])
                print("Restored.")
            except Exception as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            _picker(initial_screen="trash")

    elif cmd == "empty":
        from .backend import list_trash, empty_trash

        entries = list_trash()
        if not entries:
            print("Trash is empty.")
            return
        try:
            ans = input(f"Permanently delete {len(entries)} session(s)? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return
        if ans.strip().lower() == "y":
            n = empty_trash()
            print(f"Done. Deleted {n} session(s).")
        else:
            print("Cancelled.")

    elif cmd in ("help", "-h", "--help"):
        _print_help()

    else:
        print(f"Unknown command: {cmd!r}  (try 'cc help')", file=sys.stderr)
        sys.exit(1)


def _picker(initial_screen: str = "browse", all_projects: bool = True) -> None:
    from .app import ClaudetreeApp

    app = ClaudetreeApp(
        initial_screen=initial_screen,
        all_projects=all_projects,
        cwd=os.getcwd(),
    )
    result = app.run()
    if result is None:
        return
    action = result[0]
    if action == "resume":
        if len(result) < 2:
            print("Error: missing session id for resume action", file=sys.stderr)
            sys.exit(1)
        sid = result[1]
        source = result[2] if len(result) >= 3 else "claude"
        from .backend import HARNESS_MAP, SOURCE_CLAUDE
        h = HARNESS_MAP.get(source)
        if h:
            cmd = h.build_resume_cmd(sid)
            # Allow CLAUDE_CMD override for Claude Code only
            if source == SOURCE_CLAUDE:
                cmd[0] = os.environ.get("CLAUDE_CMD", cmd[0])
        else:
            # Fallback: legacy behaviour
            claude = os.environ.get("CLAUDE_CMD", "claude")
            cmd = [claude, "--resume", sid]
        os.execvp(cmd[0], cmd)
    elif action == "new":
        claude = os.environ.get("CLAUDE_CMD", "claude")
        os.execvp(claude, [claude])


def _run_internal(args: list[str]) -> None:
    """Internal commands for shell scripts and backward compat."""
    import json
    from .backend import (
        list_sessions,
        list_trash,
        search_sessions,
        preview_session,
        trash_session,
        restore_session,
        empty_trash,
        set_name,
        project_for_session,
    )

    cmd = args[0]
    cwd = os.getcwd()

    if cmd == "_list_json":
        all_flag = args[1] if len(args) > 1 else ""
        data = list_sessions(cwd=cwd, all_projects=(all_flag == "-a"))
        print(json.dumps([s.__dict__ for s in data]))

    elif cmd == "_list_trash_json":
        data = list_trash()
        print(json.dumps([e.__dict__ for e in data]))

    elif cmd == "_rg":
        query = args[1] if len(args) > 1 else ""
        all_flag = args[2] if len(args) > 2 else ""
        data = search_sessions(query, cwd=cwd, all_projects=(all_flag == "-a"))
        print(json.dumps([s.__dict__ for s in data]))

    elif cmd == "_preview":
        sid = args[1] if len(args) > 1 else ""
        print(preview_session(sid))

    elif cmd == "_trash":
        sid = args[1] if len(args) > 1 else ""
        trash_session(sid)

    elif cmd == "_restore":
        sid = args[1] if len(args) > 1 else ""
        restore_session(sid)

    elif cmd == "_empty_silent":
        empty_trash()

    elif cmd == "_setname":
        sid = args[1] if len(args) > 1 else ""
        name = " ".join(args[2:]) if len(args) > 2 else ""
        pid = project_for_session(sid)
        if pid and name:
            set_name(pid, sid, name)

    else:
        print(f"Unknown internal command: {cmd}", file=sys.stderr)
        sys.exit(1)


def _print_help() -> None:
    print("""\
cc — Claude Code session manager (claudetree)

  cc              Browse and resume sessions
  cc rm [id]      Trash a session (opens trash bin if no id)
  cc restore [id] Restore from trash (opens trash bin if no id)
  cc empty        Empty trash permanently
  cc help         This help

Keybindings (in picker):
  enter      Resume session       d       Trash session
  r          Rename session       t       Open trash bin
  a          Scope picker         o       Cycle sort
  s          Search content       p       Session menu
  /          Filter current list  q       Quit

Preview find mode:
  f          Focus find box (supports regex)
  c          Cycle case mode (smart/ignore/match)
  r          Toggle regex/literal mode
  n / N      Next / previous match

Search screen:
  enter      Run search           s       Edit query
  d          Trash result         b       Back
  r          Toggle regex         c       Cycle case mode
""")


if __name__ == "__main__":
    main()
