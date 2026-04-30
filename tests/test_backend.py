from __future__ import annotations

import importlib
import sys
from pathlib import Path


LAST_PROMPT = "Confirm if the documents according to the original Azike concept document"


def _load_backend_with_home(tmp_path: Path):
    """Import claudetree.backend with Path.home() redirected to tmp_path."""
    sys.modules.pop("claudetree.backend", None)
    Path.home = lambda: tmp_path  # type: ignore[assignment]
    return importlib.import_module("claudetree.backend")


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
