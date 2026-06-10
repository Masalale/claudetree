from __future__ import annotations

import importlib
import sys
from pathlib import Path


LAST_PROMPT = "Confirm if the documents according to the original Azike concept document"


def _load_backend_with_home(tmp_path: Path):
    """Import claudetree.backend with Path.home() redirected to tmp_path."""
    sys.modules.pop("claudetree.backend", None)
    Path.home = lambda: tmp_path  # type: ignore[assignment]
    backend = importlib.import_module("claudetree.backend")
    # Isolate tests from real Windows-side data under /mnt
    backend._scan_mnt_dirs = lambda subpath: []
    return backend


def test_list_sessions_includes_prompt_only_session(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    project_dir = tmp_path / ".claude" / "projects" / "-home-ngash-Documents-momentum-azike"
    project_dir.mkdir(parents=True)
    session_id = "0fe52dbf-c29e-40e2-ac45-78158f56d0f4"
    (project_dir / f"{session_id}.jsonl").write_text(
        "{"
        '\"type\":\"last-prompt\",'
        f'\"lastPrompt\":\"{LAST_PROMPT}\",'
        f'\"sessionId\":\"{session_id}\"'
        "}\n",
        encoding="utf-8",
    )

    backend = _load_backend_with_home(tmp_path)
    # Isolate from WSL /mnt/ paths by clamping directory helpers
    monkeypatch.setattr(backend, "_all_project_dirs", lambda: [backend.PROJECTS_DIR])
    monkeypatch.setattr(backend, "_all_transcript_dirs", lambda: [backend.TRANSCRIPTS_DIR])
    rows = backend.list_sessions(all_projects=True)

    assert len(rows) == 1
    assert rows[0].sid == session_id
    assert rows[0].first_msg == LAST_PROMPT[:60]
    assert rows[0].msgs == 1


def test_preview_session_includes_prompt_only_session_text(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    project_dir = tmp_path / ".claude" / "projects" / "-home-ngash-Documents-momentum-azike"
    project_dir.mkdir(parents=True)
    session_id = "0fe52dbf-c29e-40e2-ac45-78158f56d0f4"
    (project_dir / f"{session_id}.jsonl").write_text(
        "{" 
        '\"type\":\"last-prompt\",' 
        f'\"lastPrompt\":\"{LAST_PROMPT}\",' 
        f'\"sessionId\":\"{session_id}\"' 
        "}\n",
        encoding="utf-8",
    )

    backend = _load_backend_with_home(tmp_path)
    text = backend.preview_session(session_id)

    assert LAST_PROMPT in text


# ── Multi-harness coverage ──────────────────────────────────────────────────


def test_pi_sessions_listed_and_previewed(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    proj_dir = tmp_path / ".pi" / "agent" / "sessions" / "--home-ngash-myproj--"
    proj_dir.mkdir(parents=True)
    uuid = "019e3d58-a3ea-7ca3-9bf8-748ee5265b25"
    (proj_dir / f"2026-05-18T23-07-59-850Z_{uuid}.jsonl").write_text(
        '{"type":"session","version":3,"id":"%s","timestamp":"2026-05-18T23:07:59.850Z","cwd":"/home/ngash/myproj"}\n'
        '{"type":"message","id":"a","timestamp":"2026-05-18T23:08:59.320Z",'
        '"message":{"role":"user","content":[{"type":"text","text":"hello pi"}]}}\n'
        '{"type":"message","id":"b","timestamp":"2026-05-18T23:09:01.825Z",'
        '"message":{"role":"assistant","content":[{"type":"text","text":"hi back"}]}}\n'
        % uuid,
        encoding="utf-8",
    )

    backend = _load_backend_with_home(tmp_path)
    monkeypatch.setattr(backend, "_all_project_dirs", lambda: [backend.PROJECTS_DIR])
    monkeypatch.setattr(backend, "_all_transcript_dirs", lambda: [backend.TRANSCRIPTS_DIR])

    rows = [s for s in backend.list_sessions(all_projects=True) if s.source == "pi"]
    assert len(rows) == 1
    s = rows[0]
    assert s.sid == uuid
    assert s.msgs == 2
    assert s.first_msg == "hello pi"
    assert s.project_path == "/home/ngash/myproj"

    text = backend.preview_session(uuid)
    assert "hello pi" in text and "hi back" in text


def _make_copilot_db(tmp_path: Path) -> None:
    import sqlite3

    db_dir = tmp_path / ".copilot"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(db_dir / "session-store.db")
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, cwd TEXT, repository TEXT, host_type TEXT,
            branch TEXT, summary TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL, turn_index INTEGER NOT NULL,
            user_message TEXT, assistant_response TEXT,
            timestamp TEXT DEFAULT (datetime('now')),
            UNIQUE(session_id, turn_index)
        );
        INSERT INTO sessions (id, cwd, summary, updated_at)
        VALUES ('cp-session-1', '/home/user/work', 'Fix the build', '2026-06-01T07:40:08.446Z');
        INSERT INTO turns (session_id, turn_index, user_message, assistant_response)
        VALUES ('cp-session-1', 0, 'please fix my build', 'Done, build fixed.');
        INSERT INTO sessions (id, cwd, summary) VALUES ('cp-empty', '/tmp', NULL);
        """
    )
    conn.commit()
    conn.close()


def test_copilot_sessions_listed_searched_previewed(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _make_copilot_db(tmp_path)

    backend = _load_backend_with_home(tmp_path)
    monkeypatch.setattr(backend, "_all_project_dirs", lambda: [backend.PROJECTS_DIR])
    monkeypatch.setattr(backend, "_all_transcript_dirs", lambda: [backend.TRANSCRIPTS_DIR])

    rows = [s for s in backend.list_sessions(all_projects=True) if s.source == "copilot"]
    assert len(rows) == 1  # empty session skipped
    s = rows[0]
    assert s.sid == "cp-session-1"
    assert s.msgs == 2
    assert s.name == "Fix the build"
    assert s.project_path == "/home/user/work"

    text = backend.preview_session("cp-session-1")
    assert "please fix my build" in text and "Done, build fixed." in text

    hits = backend._search_copilot("fix my BUILD", use_regex=False, case_mode="ignore")
    assert hits == ["cp-session-1"]

    # Trash → bundle written, rows gone, listed in trash, preview still works
    backend.trash_session("cp-session-1")
    assert backend._copilot_session_row("cp-session-1") is None
    entries = backend.list_trash()
    assert any(e.sid == "cp-session-1" and e.source == "copilot" for e in entries)
    assert "please fix my build" in backend.preview_session("cp-session-1")

    # Restore → rows back, preview from db again
    backend.restore_session("cp-session-1")
    assert backend._copilot_session_row("cp-session-1") is not None
    rows = [s for s in backend.list_sessions(all_projects=True) if s.source == "copilot"]
    assert rows and rows[0].msgs == 2
    assert backend.list_trash() == []

    # Trash again, then delete forever
    backend.trash_session("cp-session-1")
    backend.delete_trashed("cp-session-1")
    assert backend.list_trash() == []
    assert backend._copilot_session_row("cp-session-1") is None


def test_hermes_naive_timestamp_gets_real_age(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    backend = _load_backend_with_home(tmp_path)
    # Naive local timestamp (Hermes format) must not blow up into "?"
    from datetime import datetime, timedelta
    recent = (datetime.now() - timedelta(hours=2)).isoformat()
    assert backend._compute_age(recent) != "?"


def test_resume_command_templates(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    backend = _load_backend_with_home(tmp_path)
    cmds = {h.id: h.build_resume_cmd("SID") for h in backend.HARNESSES}
    assert cmds["claude"] == ["claude", "--resume", "SID"]
    assert cmds["opencode"] == ["opencode", "--session", "SID"]
    assert cmds["copilot"] == ["copilot", "--resume=SID"]
    assert cmds["pi"] == ["pi", "--session", "SID"]
    assert cmds["hermes"] == ["hermes", "--resume", "SID"]


def test_pi_trash_and_restore_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    proj_dir = tmp_path / ".pi" / "agent" / "sessions" / "--home-user-proj--"
    proj_dir.mkdir(parents=True)
    uuid = "11111111-2222-3333-4444-555555555555"
    f = proj_dir / f"2026-06-01T00-00-00-000Z_{uuid}.jsonl"
    f.write_text(
        '{"type":"message","id":"a","timestamp":"2026-06-01T00:00:01Z",'
        '"message":{"role":"user","content":[{"type":"text","text":"keep me"}]}}\n',
        encoding="utf-8",
    )

    backend = _load_backend_with_home(tmp_path)
    monkeypatch.setattr(backend, "_all_project_dirs", lambda: [backend.PROJECTS_DIR])
    monkeypatch.setattr(backend, "_all_transcript_dirs", lambda: [backend.TRANSCRIPTS_DIR])

    backend.trash_session(uuid)
    assert not f.exists()
    entries = backend.list_trash()
    assert [e.sid for e in entries] == [uuid]
    assert entries[0].source == "pi"

    backend.restore_session(uuid)
    restored = list(proj_dir.glob("*.jsonl"))
    assert len(restored) == 1
    assert "keep me" in restored[0].read_text()


def test_opencode_subagent_sessions_hidden(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    import json as _json
    storage = tmp_path / ".local" / "share" / "opencode" / "storage"
    sess_dir = storage / "session" / "proj1"
    sess_dir.mkdir(parents=True)
    (sess_dir / "ses_parent.json").write_text(_json.dumps(
        {"id": "ses_parent", "title": "Main", "time": {"updated": 1780000000000}}
    ))
    (sess_dir / "ses_child.json").write_text(_json.dumps(
        {"id": "ses_child", "parentID": "ses_parent", "title": "Subagent task",
         "time": {"updated": 1780000000000}}
    ))
    for sid in ("ses_parent", "ses_child"):
        msg_dir = storage / "message" / sid
        msg_dir.mkdir(parents=True)
        (msg_dir / "msg1.json").write_text(_json.dumps(
            {"id": f"m_{sid}", "role": "user", "time": {"created": 1}}
        ))
        part_dir = storage / "part" / f"m_{sid}"
        part_dir.mkdir(parents=True)
        (part_dir / "p1.json").write_text(_json.dumps({"type": "text", "text": "hi"}))

    backend = _load_backend_with_home(tmp_path)
    monkeypatch.setattr(backend, "_all_project_dirs", lambda: [backend.PROJECTS_DIR])
    monkeypatch.setattr(backend, "_all_transcript_dirs", lambda: [backend.TRANSCRIPTS_DIR])

    rows = [s for s in backend.list_sessions(all_projects=True) if s.source == "opencode"]
    assert [s.sid for s in rows] == ["ses_parent"]


def _make_codex_db(tmp_path: Path) -> None:
    import sqlite3

    codex_dir = tmp_path / ".codex"
    sessions_dir = codex_dir / "sessions" / "2026" / "06"
    sessions_dir.mkdir(parents=True)
    rollout = sessions_dir / "rollout-2026-06-01T00-00-00-11111111-2222-3333-4444-555555555555.jsonl"
    rollout.write_text(
        '{"timestamp":"2026-06-01T00:00:00Z","type":"response_item","payload":'
        '{"type":"message","role":"user","content":[{"type":"input_text","text":"hello codex"}]}}\n'
        '{"timestamp":"2026-06-01T00:00:01Z","type":"response_item","payload":'
        '{"type":"message","role":"assistant","content":[{"type":"output_text","text":"hello back"}]}}\n',
        encoding="utf-8",
    )
    conn = sqlite3.connect(codex_dir / "state_5.sqlite")
    conn.executescript(
        f"""
        CREATE TABLE threads (
            id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL,
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
            cwd TEXT NOT NULL, title TEXT NOT NULL,
            first_user_message TEXT NOT NULL DEFAULT '',
            preview TEXT NOT NULL DEFAULT '',
            archived INTEGER NOT NULL DEFAULT 0, archived_at INTEGER
        );
        INSERT INTO threads (id, rollout_path, created_at, updated_at, cwd, title, first_user_message)
        VALUES ('11111111-2222-3333-4444-555555555555', '{rollout}', 1780000000, 1780000000,
                '/home/user/codexproj', 'Hello thread', 'hello codex');
        """
    )
    conn.commit()
    conn.close()


def test_codex_listed_previewed_trashed(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _make_codex_db(tmp_path)

    backend = _load_backend_with_home(tmp_path)
    monkeypatch.setattr(backend, "_all_project_dirs", lambda: [backend.PROJECTS_DIR])
    monkeypatch.setattr(backend, "_all_transcript_dirs", lambda: [backend.TRANSCRIPTS_DIR])

    sid = "11111111-2222-3333-4444-555555555555"
    rows = [s for s in backend.list_sessions(all_projects=True) if s.source == "codex"]
    assert [s.sid for s in rows] == [sid]
    assert rows[0].msgs == 2
    assert rows[0].project_path == "/home/user/codexproj"

    text = backend.preview_session(sid)
    assert "hello codex" in text and "hello back" in text

    # Trash = codex's own archived flag
    backend.trash_session(sid)
    assert [s for s in backend.list_sessions(all_projects=True) if s.source == "codex"] == []
    assert any(e.sid == sid and e.source == "codex" for e in backend.list_trash())

    backend.restore_session(sid)
    assert [s.sid for s in backend.list_sessions(all_projects=True) if s.source == "codex"] == [sid]

    # Delete forever removes thread row + rollout file
    backend.trash_session(sid)
    backend.delete_trashed(sid)
    assert backend._codex_thread_row(sid) is None
    assert not list((tmp_path / ".codex" / "sessions").rglob("*.jsonl"))


def test_resume_command_resolver(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    backend = _load_backend_with_home(tmp_path)

    assert backend.resume_command("SID", "claude") == ["claude", "--resume", "SID"]
    assert backend.resume_command("SID", "copilot") == ["copilot", "--resume=SID"]
    assert backend.resume_command("SID", "codex") == ["codex", "resume", "SID"]
    assert backend.resume_command("SID", "nonexistent-harness") is None


def test_win_to_wsl_path(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    backend = _load_backend_with_home(tmp_path)
    f = backend._win_to_wsl_path
    assert f("C:\\Users\\Chomz\\.codex\\x.jsonl") == "/mnt/c/Users/Chomz/.codex/x.jsonl"
    assert f("\\\\?\\C:\\Users\\Chomz\\proj") == "/mnt/c/Users/Chomz/proj"
    assert f("\\\\?\\UNC\\wsl.localhost\\FedoraLinux-43\\home\\ngash\\claudetree") == "/home/ngash/claudetree"
    assert f("/home/ngash/already/posix") == "/home/ngash/already/posix"


def test_fuzzy_match():
    from claudetree.presentation import fuzzy_match
    assert fuzzy_match("dckr", "docker debugging ~/dock")        # subsequence
    assert fuzzy_match("docker dbg", "docker debugging ~/dock")  # multi-word
    assert not fuzzy_match("zzz", "docker debugging")
    assert fuzzy_match("", "anything")


def test_non_uuid_claude_files_hidden(tmp_path, monkeypatch):
    """ses_* transcript dumps and other non-UUID files are not Claude sessions."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    proj = tmp_path / ".claude" / "projects" / "-home-u-x"
    proj.mkdir(parents=True)
    line = '{"type":"user","timestamp":"2026-06-01T00:00:00Z","message":{"content":"hi"}}\n'
    (proj / "0fe52dbf-c29e-40e2-ac45-78158f56d0f4.jsonl").write_text(line)
    (proj / "ses_abc123fffe.jsonl").write_text(line)
    tr = tmp_path / ".claude" / "transcripts"
    tr.mkdir(parents=True)
    (tr / "ses_def456fffe.jsonl").write_text(line)

    backend = _load_backend_with_home(tmp_path)
    monkeypatch.setattr(backend, "_all_project_dirs", lambda: [backend.PROJECTS_DIR])
    monkeypatch.setattr(backend, "_all_transcript_dirs", lambda: [backend.TRANSCRIPTS_DIR])

    rows = [s for s in backend.list_sessions(all_projects=True) if s.source == "claude"]
    assert [s.sid for s in rows] == ["0fe52dbf-c29e-40e2-ac45-78158f56d0f4"]


def test_scan_cache_and_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    proj = tmp_path / ".claude" / "projects" / "-home-u-x"
    proj.mkdir(parents=True)
    sid = "0fe52dbf-c29e-40e2-ac45-78158f56d0f4"
    f = proj / f"{sid}.jsonl"
    f.write_text('{"type":"user","timestamp":"2026-06-01T00:00:00Z","message":{"content":"hello"}}\n')

    backend = _load_backend_with_home(tmp_path)
    monkeypatch.setattr(backend, "_all_project_dirs", lambda: [backend.PROJECTS_DIR])
    monkeypatch.setattr(backend, "_all_transcript_dirs", lambda: [backend.TRANSCRIPTS_DIR])

    rows = backend.list_sessions(all_projects=True)
    assert len(rows) == 1
    assert backend.CACHE_FILE.exists()

    # Snapshot replays without touching session files
    snap = backend.last_scan_rows()
    assert [s.sid for s in snap] == [sid]

    # Cache hit: parser not called again for unchanged file
    calls = []
    real = backend._parse_claude_jsonl
    monkeypatch.setattr(backend, "_parse_claude_jsonl", lambda fp: calls.append(fp) or real(fp))
    backend.list_sessions(all_projects=True)
    assert calls == []

    # mtime bump invalidates
    import os as _os
    _os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 10))
    backend.list_sessions(all_projects=True)
    assert len(calls) == 1
