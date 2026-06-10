"""UI regression tests driven through Textual's pilot.

These guard interaction safety, not pixels — above all that destructive
confirmations default to the safe option.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    """Run the app against an empty fake home so tests never touch real data."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for mod in list(sys.modules):
        if mod.startswith("claudetree"):
            sys.modules.pop(mod)
    import claudetree.backend as backend

    backend._scan_mnt_dirs = lambda subpath: []
    return tmp_path


@pytest.mark.asyncio
async def test_confirm_dialog_defaults_to_cancel(isolated_home):
    from textual.widgets import Button

    from claudetree.app import ClaudetreeApp, ConfirmDialog

    app = ClaudetreeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        results: list[bool] = []
        app.push_screen(ConfirmDialog("Delete everything?"), results.append)
        await pilot.pause(0.3)

        focused = app.focused
        assert isinstance(focused, Button) and focused.id == "confirm-no"

        # A reflexive Enter must NOT confirm a destructive action
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert results == [False]

        # Even an Enter racing the mount must not confirm
        app.push_screen(ConfirmDialog("Race?"), results.append)
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert results == [False, False]


@pytest.mark.asyncio
async def test_confirm_dialog_keys(isolated_home):
    from claudetree.app import ClaudetreeApp, ConfirmDialog

    app = ClaudetreeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        results: list[bool] = []

        for key, expected in (("y", True), ("n", False), ("escape", False)):
            app.push_screen(ConfirmDialog("Sure?"), results.append)
            await pilot.pause(0.25)
            await pilot.press(key)
            await pilot.pause(0.2)
            assert results[-1] is expected, f"key {key!r} gave {results[-1]}"


@pytest.mark.asyncio
async def test_context_menu_opens_and_dismisses(isolated_home):
    """p opens the menu over a session; Escape and outside-click dismiss."""
    import json

    proj = isolated_home / ".claude" / "projects" / "-home-u-x"
    proj.mkdir(parents=True)
    sid = "0fe52dbf-c29e-40e2-ac45-78158f56d0f4"
    (proj / f"{sid}.jsonl").write_text(json.dumps({
        "type": "user",
        "timestamp": "2026-06-01T00:00:00Z",
        "message": {"content": "hello"},
    }) + "\n")

    from claudetree.app import ClaudetreeApp, ContextMenuScreen

    app = ClaudetreeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        scr = app.screen
        for _ in range(50):
            await pilot.pause(0.1)
            if getattr(scr, "_loaded", False) and scr._filtered:
                break
        await pilot.press("p")
        await pilot.pause(0.3)
        assert isinstance(app.screen, ContextMenuScreen)
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert not isinstance(app.screen, ContextMenuScreen)

        await pilot.press("p")
        await pilot.pause(0.3)
        assert isinstance(app.screen, ContextMenuScreen)
        await pilot.click(offset=(95, 28))
        await pilot.pause(0.2)
        assert not isinstance(app.screen, ContextMenuScreen)


@pytest.mark.asyncio
async def test_empty_trash_spares_codex_archived(isolated_home):
    """Emptying the trash must not delete Codex's archived threads."""
    import sqlite3

    codex_dir = isolated_home / ".codex"
    sessions_dir = codex_dir / "sessions"
    sessions_dir.mkdir(parents=True)
    rollout = sessions_dir / "rollout-2026-06-01T00-00-00-11111111-2222-3333-4444-555555555555.jsonl"
    rollout.write_text("{}\n")
    conn = sqlite3.connect(codex_dir / "state_5.sqlite")
    conn.executescript(f"""
        CREATE TABLE threads (
            id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL,
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
            cwd TEXT NOT NULL, title TEXT NOT NULL,
            first_user_message TEXT NOT NULL DEFAULT '',
            preview TEXT NOT NULL DEFAULT '',
            archived INTEGER NOT NULL DEFAULT 0, archived_at INTEGER
        );
        INSERT INTO threads (id, rollout_path, created_at, updated_at, cwd, title, archived, archived_at)
        VALUES ('11111111-2222-3333-4444-555555555555', '{rollout}', 1, 1, '/x', 'Kept', 1, 1);
    """)
    conn.commit()
    conn.close()

    import claudetree.backend as backend

    assert any(e.source == "codex" for e in backend.list_trash())
    backend.empty_trash()
    # Thread row and rollout must survive an empty-trash
    assert backend._codex_thread_row("11111111-2222-3333-4444-555555555555") is not None
    assert rollout.exists()
