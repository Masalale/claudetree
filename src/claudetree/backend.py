"""Session management backend — reads session data from multiple AI coding tools."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

HOME = Path.home()

# ── Directory constants ────────────────────────────────────────────────────

# Claude Code
PROJECTS_DIR = HOME / ".claude" / "projects"
TRANSCRIPTS_DIR = HOME / ".claude" / "transcripts"
NAMES_DIR = HOME / ".claude" / "session-names"
TRASH_DIR = HOME / ".claude" / "trash"

# Hermes
HERMES_SESSIONS_DIR = HOME / ".hermes" / "sessions"

# PI
PI_SESSIONS_DIR = HOME / ".pi" / "agent" / "sessions"

# GitHub Copilot CLI
COPILOT_DB = HOME / ".copilot" / "session-store.db"

# Opencode
OPENCODE_STORAGE = HOME / ".local" / "share" / "opencode" / "storage"

# Codex CLI
CODEX_DIR = HOME / ".codex"

# ── Source labels ──────────────────────────────────────────────────────────

SOURCE_CLAUDE = "claude"
SOURCE_HERMES = "hermes"
SOURCE_PI = "pi"
SOURCE_COPILOT = "copilot"
SOURCE_OPENCODE = "opencode"
SOURCE_CODEX = "codex"


# ── Harness registry ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Harness:
    """Metadata for one AI coding tool harness."""
    id: str
    label: str
    color: str             # Rich color name used for display
    icon: str              # single-char glyph (falls back gracefully)
    supports_trash: bool
    resume_cmd: tuple[str, ...]  # {sid} replaced at runtime; empty = no CLI resume

    @property
    def can_resume(self) -> bool:
        return bool(self.resume_cmd)

    def build_resume_cmd(self, sid: str) -> list[str]:
        return [part.replace("{sid}", sid) for part in self.resume_cmd]


HARNESSES: list[Harness] = [
    Harness(SOURCE_CLAUDE,   "Claude Code", "cyan",    "★", True, ("claude",   "--resume",  "{sid}")),
    Harness(SOURCE_OPENCODE, "Opencode",    "yellow",  "▲", True, ("opencode", "--session", "{sid}")),
    Harness(SOURCE_COPILOT,  "Copilot",     "blue",    "◉", True, ("copilot",  "--resume={sid}")),
    Harness(SOURCE_PI,       "PI",          "green",   "π", True, ("pi",       "--session", "{sid}")),
    Harness(SOURCE_HERMES,   "Hermes",      "magenta", "⚡", True, ("hermes",   "--resume",  "{sid}")),
    Harness(SOURCE_CODEX,    "Codex",       "white",   "◆", True, ("codex",    "resume",    "{sid}")),
]

HARNESS_MAP: dict[str, Harness] = {h.id: h for h in HARNESSES}


# ── WSL directory scanning ─────────────────────────────────────────────────

def _scan_mnt_dirs(subpath: str) -> list[Path]:
    """Find directories matching <subpath> under /mnt/*/Users/*/ (WSL Windows)."""
    dirs: list[Path] = []
    mnt = Path("/mnt")
    if not mnt.is_dir():
        return dirs
    try:
        for drive in mnt.iterdir():
            users = drive / "Users"
            if not users.is_dir():
                continue
            try:
                for user_dir in users.iterdir():
                    if not user_dir.is_dir():
                        continue
                    p = user_dir / subpath
                    if p.is_dir() and p not in dirs:
                        dirs.append(p)
            except PermissionError:
                pass
    except PermissionError:
        pass
    return dirs


def _all_project_dirs() -> list[Path]:
    dirs: list[Path] = []
    if PROJECTS_DIR.is_dir():
        dirs.append(PROJECTS_DIR)
    for p in _scan_mnt_dirs(".claude/projects"):
        if p not in dirs:
            dirs.append(p)
    return dirs


def _all_transcript_dirs() -> list[Path]:
    dirs: list[Path] = []
    if TRANSCRIPTS_DIR.is_dir():
        dirs.append(TRANSCRIPTS_DIR)
    for p in _scan_mnt_dirs(".claude/transcripts"):
        if p not in dirs:
            dirs.append(p)
    return dirs


# ── Data classes ───────────────────────────────────────────────────────────

def pid_to_path(project_id: str) -> str:
    """Convert a Claude project-id back to a human-readable path."""
    if project_id.startswith("-"):
        raw = "/" + project_id[1:].replace("-", "/")
    else:
        raw = project_id
    return raw.replace(str(HOME), "~")


def pi_encode_cwd(cwd: str) -> str:
    """Encode a cwd the way PI names its per-project session directories."""
    return "-" + cwd.replace("/", "-") + "-"


def pi_decode_pid(project_id: str) -> str:
    """Decode a PI session-directory name back to a path."""
    raw = project_id[1:-1].replace("-", "/") if len(project_id) > 2 else project_id
    return raw.rstrip("/").replace(str(HOME), "~") or "/"


def _project_path_for(source: str, project_id: str) -> str:
    if source == SOURCE_OPENCODE:
        return _opencode_project_path(project_id)
    if source == SOURCE_PI:
        return pi_decode_pid(project_id)
    if source in (SOURCE_COPILOT, SOURCE_CODEX):
        # project_id is already an absolute path for sqlite-backed harnesses
        return project_id.replace(str(HOME), "~") if project_id else ""
    return pid_to_path(project_id)


@dataclass
class Session:
    sid: str
    name: str
    first_msg: str
    age: str
    msgs: int
    project_id: str
    sort_time: str
    source: str = SOURCE_CLAUDE
    mtime_secs: float = 0.0   # file mtime; 0 = unknown

    @property
    def project_path(self) -> str:
        return _project_path_for(self.source, self.project_id)

    @property
    def display_label(self) -> str:
        return self.name if self.name else self.first_msg or self.sid[:24]


@dataclass
class TrashEntry:
    sid: str
    name: str
    project_id: str
    when: str
    source: str = SOURCE_CLAUDE

    @property
    def project_path(self) -> str:
        return _project_path_for(self.source, self.project_id)


# ── Age helpers ────────────────────────────────────────────────────────────

def _compute_age(last_time) -> str:
    try:
        if isinstance(last_time, (int, float)):
            dt = datetime.fromtimestamp(last_time / 1000, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(last_time).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            # Naive timestamps (Hermes) are written in local time
            dt = dt.astimezone()
        d = datetime.now(timezone.utc) - dt
        if d.days:
            return f"{d.days}d"
        if d.seconds > 3600:
            return f"{d.seconds // 3600}h"
        return f"{d.seconds // 60}m"
    except Exception:
        return "?"


# ── Name management ────────────────────────────────────────────────────────

def get_names(project_id: str) -> dict[str, str]:
    nf = NAMES_DIR / f"{project_id}.json"
    if nf.exists():
        try:
            return json.loads(nf.read_text())
        except Exception:
            pass
    return {}


def set_name(project_id: str, sid: str, name: str) -> None:
    NAMES_DIR.mkdir(parents=True, exist_ok=True)
    nf = NAMES_DIR / f"{project_id}.json"
    d = get_names(project_id)
    d[sid] = name
    nf.write_text(json.dumps(d, indent=2))


def rm_name(project_id: str, sid: str) -> None:
    nf = NAMES_DIR / f"{project_id}.json"
    if nf.exists():
        d = get_names(project_id)
        d.pop(sid, None)
        nf.write_text(json.dumps(d, indent=2))


# ── File finding ───────────────────────────────────────────────────────────

def _find_session_file(sid: str) -> Optional[Path]:
    """Find a JSONL session file across all sources (not Opencode/Copilot)."""
    for base in _all_project_dirs():
        for f in base.glob(f"*/{sid}.jsonl"):
            return f
    for base in _all_transcript_dirs():
        f = base / f"{sid}.jsonl"
        if f.exists():
            return f
    if HERMES_SESSIONS_DIR.is_dir():
        f = HERMES_SESSIONS_DIR / f"{sid}.jsonl"
        if f.exists():
            return f
    return _find_pi_session(sid)


def _find_pi_session(sid: str) -> Optional[Path]:
    """Find a PI session file. PI sids are the UUID suffix of the filename."""
    if not PI_SESSIONS_DIR.is_dir():
        return None
    for f in PI_SESSIONS_DIR.glob(f"*/*{sid}.jsonl"):
        return f
    return None


def _pi_sid_from_path(p: Path) -> str:
    """Extract the resumable sid (UUID part) from a PI session filename."""
    stem = p.stem
    return stem.rsplit("_", 1)[-1] if "_" in stem else stem


def _find_opencode_session(sid: str) -> Optional[Path]:
    """Find an Opencode session JSON file."""
    sess_dir = OPENCODE_STORAGE / "session"
    if not sess_dir.is_dir():
        return None
    for proj_dir in sess_dir.iterdir():
        if not proj_dir.is_dir():
            continue
        f = proj_dir / f"{sid}.json"
        if f.exists():
            return f
    return None


def _names_bucket_for_source(source: str, project_id: str) -> str:
    """Custom names are kept per-project for Claude, per-harness otherwise."""
    return project_id if source == SOURCE_CLAUDE else source


def _trash_meta_path(sid: str) -> Path:
    return TRASH_DIR / f"{sid}.meta"


def _trash_jsonl_path(sid: str) -> Path:
    return TRASH_DIR / f"{sid}.jsonl"


def _trash_opencode_dir(sid: str) -> Path:
    return TRASH_DIR / f"{sid}.opencode"


def _read_trash_meta(sid: str) -> dict:
    meta = _trash_meta_path(sid)
    if not meta.exists():
        return {}
    try:
        return json.loads(meta.read_text())
    except Exception:
        return {}


def project_for_session(sid: str) -> Optional[str]:
    f = _find_session_file(sid)
    if f:
        source = _detect_source(str(f))
        return _names_bucket_for_source(source, f.parent.name)
    f = _find_opencode_session(sid)
    if f:
        return _names_bucket_for_source(SOURCE_OPENCODE, f.parent.name)
    if _copilot_session_row(sid) is not None:
        return _names_bucket_for_source(SOURCE_COPILOT, "")
    if _codex_thread_row(sid) is not None:
        return _names_bucket_for_source(SOURCE_CODEX, "")
    return None


def session_cwd(sid: str, source: str) -> Optional[str]:
    """Best-effort absolute working directory for a session, for chdir-on-resume."""
    if source == SOURCE_OPENCODE:
        pid = project_for_session(sid)
        return os.path.expanduser(_opencode_project_path(pid)) if pid else None
    if source == SOURCE_COPILOT:
        row = _copilot_session_row(sid)
        return row[1] if row else None
    if source == SOURCE_CODEX:
        row = _codex_thread_row(sid)
        return _win_to_wsl_path(row[0]) if row and row[0] else None
    f = _find_session_file(sid)
    if not f:
        return None
    if source == SOURCE_PI:
        return os.path.expanduser(pi_decode_pid(f.parent.name))
    if source == SOURCE_CLAUDE and f.parent.name != "transcripts":
        return os.path.expanduser(pid_to_path(f.parent.name))
    return None


# ── Content extraction ─────────────────────────────────────────────────────

def _extract_text(content) -> str:
    """Get plain text from a content value (str or list of content blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return next(
            (x.get("text", "") for x in content
             if isinstance(x, dict) and x.get("type") == "text"),
            "",
        )
    return ""


# ── Source-specific parsers ────────────────────────────────────────────────
# Each returns (last_time, msg_count, first_user_msg)

def _parse_claude_jsonl(filepath: str):
    """Parse Claude Code projects and transcripts JSONL format."""
    last_time, cnt, first = "", 0, ""
    fallback_prompt = ""
    try:
        with open(filepath, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    if r.get("timestamp"):
                        last_time = r["timestamp"]
                    rt = r.get("type", "")
                    if rt in ("user", "assistant"):
                        cnt += 1
                        if not first and rt == "user":
                            c = r.get("message", {}).get("content", "") or r.get("content", "")
                            first = _extract_text(c).strip().replace("\n", " ")[:60]
                    elif rt == "last-prompt" and not fallback_prompt:
                        fallback_prompt = str(r.get("lastPrompt", "")).strip().replace("\n", " ")[:60]
                except Exception:
                    pass
    except Exception:
        pass
    if not cnt and fallback_prompt:
        cnt = 1
        first = fallback_prompt
    return last_time, cnt, first


def _parse_hermes_jsonl(filepath: str):
    """Parse Hermes sessions: records use role/content (no type field)."""
    last_time, cnt, first = "", 0, ""
    try:
        with open(filepath, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    if r.get("timestamp"):
                        last_time = r["timestamp"]
                    role = r.get("role", "")
                    if role in ("user", "assistant"):
                        cnt += 1
                        if not first and role == "user":
                            first = _extract_text(r.get("content", "")).strip().replace("\n", " ")[:60]
                except Exception:
                    pass
    except Exception:
        pass
    return last_time, cnt, first


def _parse_pi_jsonl(filepath: str):
    """Parse PI sessions: type=message with nested message.role/content."""
    last_time, cnt, first = "", 0, ""
    try:
        with open(filepath, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    if r.get("timestamp"):
                        last_time = r["timestamp"]
                    if r.get("type") != "message":
                        continue
                    msg = r.get("message", {})
                    role = msg.get("role", "")
                    if role in ("user", "assistant"):
                        cnt += 1
                        if not first and role == "user":
                            first = _extract_text(msg.get("content", "")).strip().replace("\n", " ")[:60]
                except Exception:
                    pass
    except Exception:
        pass
    return last_time, cnt, first


def _opencode_is_subagent(session_file: Path) -> bool:
    """True if an Opencode session is a child (Task/subagent) session."""
    try:
        return bool(json.loads(session_file.read_text()).get("parentID"))
    except Exception:
        return False


def _parse_opencode_session(session_file: Path):
    """Parse an Opencode session JSON + count its messages.

    Returns (last_time, msg_count, first_user_msg, title).
    """
    try:
        data = json.loads(session_file.read_text())
    except Exception:
        return "", 0, "", ""

    title = data.get("title", "")
    time_info = data.get("time", {})
    last_time = time_info.get("updated") or time_info.get("created") or ""

    sid = data.get("id", session_file.stem)
    msg_dir = OPENCODE_STORAGE / "message" / sid
    cnt = 0
    first = ""
    if msg_dir.is_dir():
        msgs: list[tuple[int, dict]] = []
        for mf in msg_dir.iterdir():
            if not mf.suffix == ".json":
                continue
            try:
                m = json.loads(mf.read_text())
                created = m.get("time", {}).get("created", 0)
                msgs.append((created, m))
            except Exception:
                pass
        msgs.sort(key=lambda x: x[0])
        for _, m in msgs:
            role = m.get("role", "")
            if role in ("user", "assistant"):
                cnt += 1
                if not first and role == "user":
                    # Get text from parts directory
                    mid = m.get("id", "")
                    part_dir = OPENCODE_STORAGE / "part" / mid
                    if part_dir.is_dir():
                        for pf in part_dir.iterdir():
                            if pf.suffix == ".json":
                                try:
                                    pd = json.loads(pf.read_text())
                                    if pd.get("type") == "text":
                                        first = pd.get("text", "").strip().replace("\n", " ")[:60]
                                        break
                                except Exception:
                                    pass
    return last_time, cnt, first, title


# ── Copilot (sqlite session store) ────────────────────────────────────────

def _copilot_connect() -> Optional[sqlite3.Connection]:
    if not COPILOT_DB.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{COPILOT_DB}?mode=ro", uri=True)
        conn.execute("SELECT 1 FROM sessions LIMIT 1")
        return conn
    except sqlite3.Error:
        return None


def _copilot_session_row(sid: str) -> Optional[tuple]:
    """Return (id, cwd, summary, updated_at) for a Copilot session, or None."""
    conn = _copilot_connect()
    if conn is None:
        return None
    try:
        cur = conn.execute(
            "SELECT id, cwd, summary, updated_at FROM sessions WHERE id = ?", (sid,)
        )
        return cur.fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _copilot_turns(conn: sqlite3.Connection, sid: str) -> list[tuple[str, str]]:
    """Return ordered (user_message, assistant_response) pairs for a session."""
    try:
        cur = conn.execute(
            "SELECT user_message, assistant_response FROM turns "
            "WHERE session_id = ? ORDER BY turn_index",
            (sid,),
        )
        return [(u or "", a or "") for u, a in cur.fetchall()]
    except sqlite3.Error:
        return []


def _list_copilot_sessions(names: dict[str, str]) -> list[Session]:
    conn = _copilot_connect()
    if conn is None:
        return []
    rows: list[Session] = []
    try:
        cur = conn.execute("SELECT id, cwd, summary, updated_at FROM sessions")
        for sid, cwd, summary, updated_at in cur.fetchall():
            turns = _copilot_turns(conn, sid)
            cnt = sum((1 if u else 0) + (1 if a else 0) for u, a in turns)
            if not cnt:
                continue
            first = next((u for u, _ in turns if u), "")
            first = first.strip().replace("\n", " ")[:60]
            last_time = updated_at or ""
            rows.append(Session(
                sid=sid, name=names.get(sid, "") or (summary or ""),
                first_msg=first, age=_compute_age(last_time) if last_time else "?",
                msgs=cnt, project_id=cwd or "",
                sort_time=str(last_time), source=SOURCE_COPILOT,
            ))
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return rows


# Tables holding a Copilot session's data, with their session-key column.
_COPILOT_SESSION_TABLES: tuple[tuple[str, str], ...] = (
    ("sessions", "id"),
    ("turns", "session_id"),
    ("checkpoints", "session_id"),
    ("session_files", "session_id"),
    ("session_refs", "session_id"),
    ("forge_trajectory_events", "session_id"),
)


def _copilot_connect_rw() -> Optional[sqlite3.Connection]:
    if not COPILOT_DB.exists():
        return None
    try:
        conn = sqlite3.connect(COPILOT_DB, timeout=5)
        conn.execute("SELECT 1 FROM sessions LIMIT 1")
        return conn
    except sqlite3.Error:
        return None


def _copilot_bundle_path(sid: str) -> Path:
    return TRASH_DIR / f"{sid}.copilot.json"


def _trash_copilot_session(sid: str) -> None:
    """Move a Copilot session out of its db into a restorable trash bundle."""
    row = _copilot_session_row(sid)
    if row is None:
        raise ValueError(f"Copilot session not found: {sid}")
    conn = _copilot_connect_rw()
    if conn is None:
        raise ValueError("Cannot open Copilot's database for writing.")

    _, cwd, summary, _ = row
    names = get_names("copilot")
    name = names.get(sid, "") or (summary or "")

    bundle: dict = {"sid": sid, "tables": {}}
    try:
        for table, key in _COPILOT_SESSION_TABLES:
            try:
                cur = conn.execute(f"SELECT * FROM {table} WHERE {key} = ?", (sid,))
                cols = [d[0] for d in cur.description]
                bundle["tables"][table] = {
                    "key": key,
                    "columns": cols,
                    "rows": [list(r) for r in cur.fetchall()],
                }
            except sqlite3.Error:
                pass

        TRASH_DIR.mkdir(parents=True, exist_ok=True)
        _copilot_bundle_path(sid).write_text(json.dumps(bundle))
        _trash_meta_path(sid).write_text(json.dumps({
            "project_id": cwd or "",
            "name": name,
            "trashed_at": int(datetime.now().timestamp()),
            "source": SOURCE_COPILOT,
        }))

        with conn:
            for table, key in _COPILOT_SESSION_TABLES:
                try:
                    conn.execute(f"DELETE FROM {table} WHERE {key} = ?", (sid,))
                except sqlite3.Error:
                    pass
            # FTS index rows for this session (ignore if schema differs)
            try:
                conn.execute("DELETE FROM search_index WHERE session_id = ?", (sid,))
            except sqlite3.Error:
                pass
    finally:
        conn.close()
    rm_name("copilot", sid)


def _restore_copilot_session(sid: str, name: str) -> None:
    bundle_file = _copilot_bundle_path(sid)
    if not bundle_file.exists():
        raise ValueError(f"Not in trash: {sid}")
    bundle = json.loads(bundle_file.read_text())
    conn = _copilot_connect_rw()
    if conn is None:
        raise ValueError("Cannot open Copilot's database for writing.")
    try:
        with conn:
            for table, info in bundle.get("tables", {}).items():
                cols = info.get("columns", [])
                if not cols:
                    continue
                placeholders = ", ".join("?" for _ in cols)
                col_list = ", ".join(cols)
                for r in info.get("rows", []):
                    try:
                        conn.execute(
                            f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})",
                            r,
                        )
                    except sqlite3.Error:
                        pass
    finally:
        conn.close()
    bundle_file.unlink(missing_ok=True)
    if name:
        set_name("copilot", sid, name)


def _preview_copilot_bundle(sid: str) -> Optional[str]:
    """Preview a trashed Copilot session from its trash bundle."""
    bundle_file = _copilot_bundle_path(sid)
    if not bundle_file.exists():
        return None
    try:
        bundle = json.loads(bundle_file.read_text())
    except Exception:
        return f"*Error reading trashed Copilot session: {sid}*"

    meta = _read_trash_meta(sid)
    name = meta.get("name", "")
    short_path = (meta.get("project_id") or "").replace(str(HOME), "~")
    parts: list[str] = []
    if name:
        parts.append(f"## {name}")
        parts.append(f"`{short_path}` [copilot]")
    else:
        parts.append(f"## `{short_path}`")
        parts.append("[copilot]")

    turns_info = bundle.get("tables", {}).get("turns", {})
    cols = turns_info.get("columns", [])
    n = 0
    if "user_message" in cols and "assistant_response" in cols:
        ui, ai = cols.index("user_message"), cols.index("assistant_response")
        ti = cols.index("turn_index") if "turn_index" in cols else None
        rows = turns_info.get("rows", [])
        if ti is not None:
            rows = sorted(rows, key=lambda r: r[ti] or 0)
        for r in rows:
            u, a = (r[ui] or "").strip(), (r[ai] or "").strip()
            if u:
                parts.append("**You**")
                parts.append(_quote(u[:500]))
                n += 1
            if a:
                parts.append("**Copilot**")
                parts.append(a[:2000])
                n += 1
    if n == 0:
        parts.append("*No usable chat messages found.*")
    return "\n".join(parts)


def _search_copilot(query: str, use_regex: bool, case_mode: str) -> list[str]:
    """Return Copilot session ids whose turns match the query."""
    pattern = _compile_search_pattern(query, use_regex, case_mode)
    conn = _copilot_connect()
    if conn is None:
        return []
    sids: list[str] = []
    try:
        cur = conn.execute(
            "SELECT DISTINCT session_id, user_message, assistant_response FROM turns"
        )
        seen: set[str] = set()
        for sid, u, a in cur.fetchall():
            if sid in seen:
                continue
            if pattern.search(u or "") or pattern.search(a or ""):
                seen.add(sid)
                sids.append(sid)
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return sids


# ── Codex CLI (sqlite threads + rollout JSONL) ────────────────────────────

def _win_to_wsl_path(p: str) -> str:
    """Normalize a Windows path (from a Windows-side db) to a WSL/posix path."""
    if "\\" not in p and ":" not in p[:2]:
        return p
    p = p.replace("\\", "/")
    if p.startswith("//?/"):
        p = p[4:]
    if p.startswith("UNC/"):
        p = "//" + p[4:]
    low = p.lower()
    if low.startswith("//wsl.localhost/") or low.startswith("//wsl$/"):
        bits = p.split("/", 4)  # ['', '', host, distro, rest]
        return "/" + bits[4] if len(bits) > 4 else p
    if len(p) > 1 and p[1] == ":":
        return "/mnt/" + p[0].lower() + p[2:]
    return p


def _codex_session_dirs() -> list[Path]:
    """Rollout-file directories for codex (local + Windows side)."""
    dirs: list[Path] = []
    for root in [CODEX_DIR, *_scan_mnt_dirs(".codex")]:
        d = root / "sessions"
        if d.is_dir():
            dirs.append(d)
    return dirs


def _codex_dbs() -> list[Path]:
    """Find codex state dbs (local + Windows side), newest schema version each."""
    dbs: list[Path] = []
    for root in [CODEX_DIR, *_scan_mnt_dirs(".codex")]:
        if not root.is_dir():
            continue
        best: tuple[int, Optional[Path]] = (-1, None)
        for f in root.glob("state_*.sqlite"):
            try:
                ver = int(f.stem.split("_")[1])
            except (IndexError, ValueError):
                continue
            if ver > best[0]:
                best = (ver, f)
        if best[1] is not None:
            dbs.append(best[1])
    return dbs


def _sqlite_ro(path: Path) -> Optional[sqlite3.Connection]:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.execute("SELECT 1")
        return conn
    except sqlite3.Error:
        return None


def _parse_codex_rollout(path: Path):
    """Count user/assistant messages in a codex rollout file. Returns count."""
    cnt = 0
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    if r.get("type") != "response_item":
                        continue
                    pl = r.get("payload", {})
                    if pl.get("type") != "message":
                        continue
                    if pl.get("role") in ("user", "assistant") and _codex_text(pl):
                        cnt += 1
                except Exception:
                    pass
    except OSError:
        pass
    return cnt


def _codex_text(payload: dict) -> str:
    """Extract display text from a codex message payload, skipping context blobs."""
    for block in payload.get("content", []):
        if isinstance(block, dict) and block.get("type") in ("input_text", "output_text"):
            text = block.get("text", "").strip()
            if text and not text.startswith("<"):
                return text
    return ""


def _codex_thread_row(sid: str) -> Optional[tuple]:
    """Return (cwd, title, first_user_message, updated_at, rollout_path) or None."""
    for db in _codex_dbs():
        conn = _sqlite_ro(db)
        if conn is None:
            continue
        try:
            cur = conn.execute(
                "SELECT cwd, title, first_user_message, updated_at, rollout_path "
                "FROM threads WHERE id = ?",
                (sid,),
            )
            row = cur.fetchone()
            if row:
                return row
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    return None


def _epoch_to_iso(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return ""


def _list_codex_sessions(names: dict[str, str]) -> list[Session]:
    rows: list[Session] = []
    seen: set[str] = set()
    for db in _codex_dbs():
        conn = _sqlite_ro(db)
        if conn is None:
            continue
        try:
            cur = conn.execute(
                "SELECT id, cwd, title, first_user_message, updated_at, rollout_path "
                "FROM threads WHERE archived = 0"
            )
            for sid, cwd, title, first, updated_at, rollout in cur.fetchall():
                if sid in seen:
                    continue
                seen.add(sid)
                rollout_file = Path(_win_to_wsl_path(rollout or ""))
                cnt = _parse_codex_rollout(rollout_file) if rollout_file.is_file() else 0
                if not cnt and not (first or "").strip():
                    continue
                iso = _epoch_to_iso(updated_at)
                rows.append(Session(
                    sid=sid, name=names.get(sid, "") or (title or ""),
                    first_msg=(first or "").strip().replace("\n", " ")[:60],
                    age=_compute_age(iso) if iso else "?",
                    msgs=cnt or 1, project_id=_win_to_wsl_path(cwd or ""),
                    sort_time=iso, source=SOURCE_CODEX,
                ))
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    return rows


def _preview_codex(sid: str, row: tuple) -> str:
    cwd, title, first, _, rollout = row
    names = get_names("codex")
    name = names.get(sid, "") or (title or "")
    short_path = _win_to_wsl_path(cwd or "").replace(str(HOME), "~")

    parts: list[str] = []
    if name:
        parts.append(f"## {name}")
        parts.append(f"`{short_path}` [codex]")
    else:
        parts.append(f"## `{short_path}`")
        parts.append("[codex]")

    n = 0
    rollout_file = Path(_win_to_wsl_path(rollout or ""))
    if rollout_file.is_file():
        try:
            with open(rollout_file, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                        if r.get("type") != "response_item":
                            continue
                        pl = r.get("payload", {})
                        if pl.get("type") != "message":
                            continue
                        role = pl.get("role", "")
                        if role not in ("user", "assistant"):
                            continue
                        text = _codex_text(pl)
                        if not text:
                            continue
                        n += 1
                        if role == "user":
                            parts.append("**You**")
                            parts.append(_quote(text[:500]))
                        else:
                            parts.append("**Codex**")
                            parts.append(text[:2000])
                    except Exception:
                        pass
        except OSError:
            pass
    if n == 0 and (first or "").strip():
        parts.append("**You**")
        parts.append(_quote(first.strip()[:500]))
        n += 1
    if n == 0:
        parts.append("*No usable chat messages found.*")
    return "\n".join(parts)


def _compile_search_pattern(query: str, use_regex: bool, case_mode: str):
    import re as _re

    flags = 0
    if case_mode == "ignore" or (
        case_mode == "smart" and not any(ch.isupper() for ch in query)
    ):
        flags = _re.IGNORECASE
    try:
        return _re.compile(query if use_regex else _re.escape(query), flags)
    except _re.error:
        return _re.compile(_re.escape(query), flags)


# ── Codex trash (uses Codex's own archived flag) ──────────────────────────

def _codex_set_archived(sid: str, archived: bool) -> bool:
    """Toggle a codex thread's archived flag. Returns True if a row changed."""
    ts = int(datetime.now().timestamp()) if archived else None
    for db in _codex_dbs():
        conn = _sqlite_ro(db)
        if conn is None:
            continue
        try:
            hit = conn.execute("SELECT 1 FROM threads WHERE id = ?", (sid,)).fetchone()
        except sqlite3.Error:
            hit = None
        finally:
            conn.close()
        if not hit:
            continue
        try:
            wconn = sqlite3.connect(db, timeout=5)
            with wconn:
                wconn.execute(
                    "UPDATE threads SET archived = ?, archived_at = ? WHERE id = ?",
                    (1 if archived else 0, ts, sid),
                )
            wconn.close()
            return True
        except sqlite3.Error:
            pass
    return False


def _codex_archived_entries() -> list[TrashEntry]:
    rows: list[TrashEntry] = []
    for db in _codex_dbs():
        conn = _sqlite_ro(db)
        if conn is None:
            continue
        try:
            cur = conn.execute(
                "SELECT id, title, cwd, archived_at FROM threads WHERE archived = 1"
            )
            for sid, title, cwd, archived_at in cur.fetchall():
                when = ""
                if archived_at:
                    d = datetime.now(timezone.utc) - datetime.fromtimestamp(
                        int(archived_at), tz=timezone.utc
                    )
                    when = f"{d.days}d ago" if d.days else f"{d.seconds // 3600}h ago"
                rows.append(TrashEntry(
                    sid=sid, name=title or "", project_id=_win_to_wsl_path(cwd or ""),
                    when=when, source=SOURCE_CODEX,
                ))
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    return rows


def _delete_codex_session(sid: str) -> None:
    """Permanently delete a codex thread row and its rollout file."""
    for db in _codex_dbs():
        conn = _sqlite_ro(db)
        if conn is None:
            continue
        try:
            row = conn.execute(
                "SELECT rollout_path FROM threads WHERE id = ?", (sid,)
            ).fetchone()
        except sqlite3.Error:
            row = None
        finally:
            conn.close()
        if not row:
            continue
        try:
            wconn = sqlite3.connect(db, timeout=5)
            with wconn:
                wconn.execute("DELETE FROM threads WHERE id = ?", (sid,))
            wconn.close()
        except sqlite3.Error:
            continue
        rollout = Path(_win_to_wsl_path(row[0] or ""))
        if rollout.is_file():
            rollout.unlink(missing_ok=True)
        return


# ── Resume command resolution ─────────────────────────────────────────────

def resume_command(sid: str, source: str) -> Optional[list[str]]:
    """Resolve the command that resumes a session, or None if unavailable."""
    h = HARNESS_MAP.get(source)
    if h and h.resume_cmd:
        cmd = h.build_resume_cmd(sid)
        if source == SOURCE_CLAUDE:
            cmd[0] = os.environ.get("CLAUDE_CMD", cmd[0])
        return cmd
    return None


def _search_codex(query: str, use_regex: bool, case_mode: str) -> list[str]:
    """Codex content search over thread metadata columns (sqlite-side)."""
    pattern = _compile_search_pattern(query, use_regex, case_mode)
    sids: list[str] = []
    for db in _codex_dbs():
        conn = _sqlite_ro(db)
        if conn is None:
            continue
        try:
            cur = conn.execute(
                "SELECT id, title, first_user_message, preview FROM threads WHERE archived = 0"
            )
            for sid, title, first, preview in cur.fetchall():
                if sid in sids:
                    continue
                if any(pattern.search(v or "") for v in (title, first, preview)):
                    sids.append(sid)
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    return sids


# ── Opencode project metadata cache ───────────────────────────────────────

_opencode_projects_cache: Optional[dict[str, str]] = None


def _opencode_project_path(project_hash: str) -> str:
    """Map an Opencode project hash to its worktree path."""
    global _opencode_projects_cache
    if _opencode_projects_cache is None:
        _opencode_projects_cache = {}
        proj_dir = OPENCODE_STORAGE / "project"
        if proj_dir.is_dir():
            for pf in proj_dir.iterdir():
                if pf.suffix == ".json":
                    try:
                        d = json.loads(pf.read_text())
                        pid = d.get("id", "")
                        wt = d.get("worktree", "")
                        if pid and wt:
                            _opencode_projects_cache[pid] = wt
                    except Exception:
                        pass
    raw = _opencode_projects_cache.get(project_hash, project_hash)
    return raw.replace(str(HOME), "~")


# ── Session listing ────────────────────────────────────────────────────────

def list_sessions(
    cwd: Optional[str] = None,
    all_projects: bool = False,
    harness_filter: Optional[str] = None,
) -> list[Session]:
    NAMES_DIR.mkdir(parents=True, exist_ok=True)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    if cwd is None:
        cwd = os.getcwd()
    cur = cwd.replace("/", "-")
    rows: list[Session] = []
    _hf = harness_filter  # shorthand

    # ── Claude Code projects ──
    if not _hf or _hf == SOURCE_CLAUDE:
        for base in _all_project_dirs():
            for pd in base.glob("*"):
                if not pd.is_dir():
                    continue
                pid = pd.name
                if not all_projects and pid != cur:
                    continue
                names = get_names(pid)
                for f in pd.glob("*.jsonl"):
                    sid = f.stem
                    last_time, cnt, first = _parse_claude_jsonl(str(f))
                    if not cnt:
                        continue
                    age = _compute_age(last_time) if last_time else "?"
                    rows.append(Session(
                        sid=sid, name=names.get(sid, ""), first_msg=first,
                        age=age, msgs=cnt, project_id=pid,
                        sort_time=str(last_time), source=SOURCE_CLAUDE,
                        mtime_secs=f.stat().st_mtime,
                    ))

        # ── Claude Code transcripts ──
        transcript_names = get_names("transcripts")
        for base in _all_transcript_dirs():
            for f in base.glob("*.jsonl"):
                sid = f.stem
                last_time, cnt, first = _parse_claude_jsonl(str(f))
                if not cnt:
                    continue
                age = _compute_age(last_time) if last_time else "?"
                rows.append(Session(
                    sid=sid, name=transcript_names.get(sid, ""), first_msg=first,
                    age=age, msgs=cnt, project_id="transcripts",
                    sort_time=str(last_time), source=SOURCE_CLAUDE,
                    mtime_secs=f.stat().st_mtime,
                ))

    # ── Hermes ──
    if (not _hf or _hf == SOURCE_HERMES) and HERMES_SESSIONS_DIR.is_dir():
        hermes_names = get_names("hermes")
        for f in HERMES_SESSIONS_DIR.glob("*.jsonl"):
            sid = f.stem
            last_time, cnt, first = _parse_hermes_jsonl(str(f))
            if not cnt:
                continue
            age = _compute_age(last_time) if last_time else "?"
            rows.append(Session(
                sid=sid, name=hermes_names.get(sid, ""), first_msg=first,
                age=age, msgs=cnt, project_id="hermes",
                sort_time=str(last_time), source=SOURCE_HERMES,
                mtime_secs=f.stat().st_mtime,
            ))

    # ── PI ──
    if (not _hf or _hf == SOURCE_PI) and PI_SESSIONS_DIR.is_dir():
        pi_names = get_names("pi")
        for f in PI_SESSIONS_DIR.glob("*/*.jsonl"):
            sid = _pi_sid_from_path(f)
            last_time, cnt, first = _parse_pi_jsonl(str(f))
            if not cnt:
                continue
            age = _compute_age(last_time) if last_time else "?"
            rows.append(Session(
                sid=sid, name=pi_names.get(sid, ""), first_msg=first,
                age=age, msgs=cnt, project_id=f.parent.name,
                sort_time=str(last_time), source=SOURCE_PI,
                mtime_secs=f.stat().st_mtime,
            ))

    # ── Copilot ──
    if not _hf or _hf == SOURCE_COPILOT:
        rows.extend(_list_copilot_sessions(get_names("copilot")))

    # ── Codex ──
    if not _hf or _hf == SOURCE_CODEX:
        rows.extend(_list_codex_sessions(get_names("codex")))

    # ── Opencode ──
    sess_base = OPENCODE_STORAGE / "session"
    if (not _hf or _hf == SOURCE_OPENCODE) and sess_base.is_dir():
        opencode_names = get_names("opencode")
        for proj_dir in sess_base.iterdir():
            if not proj_dir.is_dir():
                continue
            proj_hash = proj_dir.name
            for sf in proj_dir.glob("*.json"):
                sid = sf.stem
                if _opencode_is_subagent(sf):
                    continue
                last_time, cnt, first, title = _parse_opencode_session(sf)
                if not cnt:
                    continue
                age = _compute_age(last_time) if last_time else "?"
                display_name = opencode_names.get(sid, "") or title
                rows.append(Session(
                    sid=sid, name=display_name, first_msg=first,
                    age=age, msgs=cnt, project_id=proj_hash,
                    sort_time=str(last_time), source=SOURCE_OPENCODE,
                    mtime_secs=sf.stat().st_mtime,
                ))

    rows.sort(key=lambda r: r.sort_time, reverse=True)
    return rows


# ── Trash ──────────────────────────────────────────────────────────────────

def list_trash() -> list[TrashEntry]:
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[float, TrashEntry]] = []
    seen: set[str] = set()

    for meta in TRASH_DIR.glob("*.meta"):
        sid = meta.stem
        data = _read_trash_meta(sid)
        if not data:
            continue
        source = data.get("source", SOURCE_CLAUDE)
        session_path = _trash_jsonl_path(sid)
        if source == SOURCE_OPENCODE:
            session_path = _trash_opencode_dir(sid) / "session.json"
        mtime = session_path.stat().st_mtime if session_path.exists() else meta.stat().st_mtime
        pid = data.get("project_id", "")
        name = data.get("name", "")
        when = ""
        ts = data.get("trashed_at", 0)
        if ts:
            d = datetime.now(timezone.utc) - datetime.fromtimestamp(ts, tz=timezone.utc)
            when = f"{d.days}d ago" if d.days else f"{d.seconds // 3600}h ago"
        rows.append(
            (
                mtime,
                TrashEntry(
                    sid=sid,
                    name=name,
                    project_id=pid,
                    when=when,
                    source=source,
                ),
            )
        )
        seen.add(sid)

    for f in TRASH_DIR.glob("*.jsonl"):
        sid = f.stem
        if sid in seen:
            continue
        rows.append(
            (
                f.stat().st_mtime,
                TrashEntry(sid=sid, name="", project_id="", when="", source=SOURCE_CLAUDE),
            )
        )
    rows.sort(key=lambda r: r[0], reverse=True)
    out = [r[1] for r in rows]
    # Sqlite-flagged trash: codex archived threads
    out.extend(e for e in _codex_archived_entries() if e.sid not in seen)
    return out


# ── Content search ─────────────────────────────────────────────────────────

def search_sessions(
    query: str,
    cwd: Optional[str] = None,
    all_projects: bool = False,
    use_regex: bool = True,
    case_mode: str = "smart",
) -> list[Session]:
    if not query:
        return []
    if cwd is None:
        cwd = os.getcwd()
    cur = cwd.replace("/", "-")

    # Collect search paths for rg
    if all_projects:
        search_paths = [str(b) for b in _all_project_dirs()]
        search_paths += [str(b) for b in _all_transcript_dirs()]
        if HERMES_SESSIONS_DIR.is_dir():
            search_paths.append(str(HERMES_SESSIONS_DIR))
        if PI_SESSIONS_DIR.is_dir():
            search_paths.append(str(PI_SESSIONS_DIR))
        # Codex: rollout files hold the full conversation content
        for codex_sessions in _codex_session_dirs():
            search_paths.append(str(codex_sessions))
        # Opencode: search the parts directory for content
        parts_dir = OPENCODE_STORAGE / "part"
        if parts_dir.is_dir():
            search_paths.append(str(parts_dir))
    else:
        search_paths = [str(b / cur) for b in _all_project_dirs() if (b / cur).is_dir()]
        if not search_paths:
            return []

    try:
        cmd = ["rg", "--files-with-matches"]
        if case_mode == "ignore":
            cmd.append("--ignore-case")
        elif case_mode == "match":
            cmd.append("--case-sensitive")
        else:
            cmd.append("--smart-case")
        if not use_regex:
            cmd.append("--fixed-strings")
        cmd.append(query)
        cmd.extend(search_paths)
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return []

    # Map files back to sessions
    rows: list[Session] = []
    seen_sids: set[str] = set()

    for filepath in result.stdout.strip().splitlines():
        if not filepath:
            continue
        p = Path(filepath)

        # Opencode parts: trace back to session
        if str(OPENCODE_STORAGE / "part") in filepath:
            # part file path: .../part/<msgID>/<partID>.json
            msg_id = p.parent.name
            # Find which session owns this message
            oc_sid = _opencode_msg_to_session(msg_id)
            if oc_sid and oc_sid not in seen_sids:
                seen_sids.add(oc_sid)
                sf = _find_opencode_session(oc_sid)
                if sf and not _opencode_is_subagent(sf):
                    last_time, cnt, first, title = _parse_opencode_session(sf)
                    if cnt:
                        age = _compute_age(last_time) if last_time else "?"
                        oc_names = get_names("opencode")
                        rows.append(Session(
                            sid=oc_sid, name=oc_names.get(oc_sid, "") or title,
                            first_msg=first, age=age, msgs=cnt,
                            project_id=sf.parent.name, sort_time=str(last_time),
                            source=SOURCE_OPENCODE,
                        ))
            continue

        if not filepath.endswith(".jsonl"):
            continue

        # Codex rollout files: map back to the thread via the UUID suffix
        if any(str(d) in filepath for d in _codex_session_dirs()):
            cx_sid = p.stem[-36:]
            if cx_sid in seen_sids:
                continue
            seen_sids.add(cx_sid)
            cx_row = _codex_thread_row(cx_sid)
            if cx_row:
                cwd, title, first, updated_at, _ = cx_row
                cx_names = get_names("codex")
                iso = _epoch_to_iso(updated_at)
                rows.append(Session(
                    sid=cx_sid, name=cx_names.get(cx_sid, "") or (title or ""),
                    first_msg=(first or "").strip().replace("\n", " ")[:60],
                    age=_compute_age(iso) if iso else "?",
                    msgs=_parse_codex_rollout(p), project_id=_win_to_wsl_path(cwd or ""),
                    sort_time=iso, source=SOURCE_CODEX,
                ))
            continue

        source = _detect_source(filepath)
        sid = _pi_sid_from_path(p) if source == SOURCE_PI else p.stem
        if sid in seen_sids:
            continue
        seen_sids.add(sid)

        pid = p.parent.name
        parser = _parser_for_source(source)
        last_time, cnt, first = parser(filepath)
        if not cnt:
            continue
        names = get_names(_names_bucket_for_source(source, pid))
        age = _compute_age(last_time) if last_time else "?"
        rows.append(Session(
            sid=sid, name=names.get(sid, ""), first_msg=first,
            age=age, msgs=cnt, project_id=pid,
            sort_time=str(last_time), source=source,
        ))

    # Sqlite-backed harnesses are searched separately from ripgrep
    if all_projects:
        for hits_fn, list_fn, bucket in (
            (_search_copilot, _list_copilot_sessions, "copilot"),
            (_search_codex, _list_codex_sessions, "codex"),
        ):
            hits = set(hits_fn(query, use_regex, case_mode))
            if not hits:
                continue
            for s in list_fn(get_names(bucket)):
                if s.sid in hits and s.sid not in seen_sids:
                    seen_sids.add(s.sid)
                    rows.append(s)

    rows.sort(key=lambda r: r.sort_time, reverse=True)
    return rows


def _detect_source(filepath: str) -> str:
    """Detect session source from file path."""
    if str(HERMES_SESSIONS_DIR) in filepath:
        return SOURCE_HERMES
    if str(PI_SESSIONS_DIR) in filepath:
        return SOURCE_PI
    return SOURCE_CLAUDE


def _parser_for_source(source: str):
    """Return the appropriate JSONL parser for a source."""
    if source == SOURCE_HERMES:
        return _parse_hermes_jsonl
    if source == SOURCE_PI:
        return _parse_pi_jsonl
    return _parse_claude_jsonl


def _opencode_msg_to_session(msg_id: str) -> Optional[str]:
    """Given an Opencode message ID, find which session it belongs to."""
    # Search across all session message dirs
    msg_base = OPENCODE_STORAGE / "message"
    if not msg_base.is_dir():
        return None
    for sess_dir in msg_base.iterdir():
        if not sess_dir.is_dir():
            continue
        msg_file = sess_dir / f"{msg_id}.json"
        if msg_file.exists():
            return sess_dir.name
    return None


# ── Preview ────────────────────────────────────────────────────────────────

_TURN_CAP = 999999


def preview_session(sid: str) -> str:
    """Return a markdown-formatted preview of the session."""
    # Try Opencode first (JSON-based, not JSONL)
    oc_file = _find_opencode_session(sid)
    if oc_file:
        return _preview_opencode(sid, oc_file)

    # Copilot (sqlite-based)
    copilot_row = _copilot_session_row(sid)
    if copilot_row is not None:
        return _preview_copilot(sid, copilot_row)

    # Codex (sqlite + rollout JSONL)
    codex_row = _codex_thread_row(sid)
    if codex_row is not None:
        return _preview_codex(sid, codex_row)

    meta = _read_trash_meta(sid)
    if meta.get("source") == SOURCE_OPENCODE:
        bundle = _trash_opencode_dir(sid)
        session_file = bundle / "session.json"
        if session_file.exists():
            return _preview_opencode_from_roots(
                sid,
                session_file,
                bundle / "message",
                bundle / "part",
            )
    if meta.get("source") == SOURCE_COPILOT:
        bundle_preview = _preview_copilot_bundle(sid)
        if bundle_preview is not None:
            return bundle_preview

    # JSONL-based sources
    found = _find_session_file(sid)
    filepath = str(found) if found else str(_trash_jsonl_path(sid))
    if not os.path.exists(filepath):
        return f"*Session not found: {sid}*"

    if found:
        source = _detect_source(filepath)
    else:
        # Trashed files lose their path — trust the trash metadata
        source = meta.get("source", SOURCE_CLAUDE)
    p = Path(filepath)
    pid = meta.get("project_id", p.parent.name) if not found else p.parent.name
    names = get_names(_names_bucket_for_source(source, pid))
    name = names.get(sid, "") or meta.get("name", "")
    short_path = _project_path_for(source, pid)

    parts: list[str] = []
    source_badge = f"[{source}]"

    if name:
        parts.append(f"## {name}")
        parts.append(f"`{short_path}` {source_badge}")
    else:
        parts.append(f"## `{short_path}`")
        parts.append(source_badge)

    if source == SOURCE_HERMES:
        _preview_hermes_body(filepath, parts)
    elif source == SOURCE_PI:
        _preview_pi_body(filepath, parts)
    else:
        _preview_claude_body(filepath, parts)

    return "\n".join(parts)


def _preview_copilot(sid: str, row: tuple) -> str:
    """Preview a Copilot session from its sqlite turns."""
    _, cwd, summary, _ = row
    names = get_names("copilot")
    name = names.get(sid, "") or (summary or "")
    short_path = (cwd or "").replace(str(HOME), "~")

    parts: list[str] = []
    if name:
        parts.append(f"## {name}")
        parts.append(f"`{short_path}` [copilot]")
    else:
        parts.append(f"## `{short_path}`")
        parts.append("[copilot]")

    conn = _copilot_connect()
    turns = _copilot_turns(conn, sid) if conn else []
    if conn:
        conn.close()

    n = 0
    for user_msg, assistant_msg in turns:
        if user_msg.strip():
            parts.append("**You**")
            parts.append(_quote(user_msg.strip()[:500]))
            n += 1
        if assistant_msg.strip():
            parts.append("**Copilot**")
            parts.append(assistant_msg.strip()[:2000])
            n += 1
    if n == 0:
        parts.append("*No usable chat messages found.*")
    return "\n".join(parts)


def _preview_claude_body(filepath: str, parts: list[str]) -> None:
    """Append Claude Code session preview lines."""
    n = 0
    with open(filepath, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                rt = r.get("type", "")
                if rt == "last-prompt":
                    prompt = str(r.get("lastPrompt", "")).strip()
                    if prompt:
                        parts.append("**You**")
                        parts.append("> " + "\n> ".join(prompt.split("\n")))
                        n += 1
                    continue
                if rt not in ("user", "assistant"):
                    continue
                c = r.get("message", {}).get("content", "") or r.get("content", "")
                t = _extract_text(c).strip()
                if not t:
                    continue
                n += 1
                if rt == "user":
                    parts.append("**You**")
                    parts.append(_quote(t[:500]))
                else:
                    parts.append("**Claude**")
                    parts.append(t[:2000])
                if n >= _TURN_CAP:
                    parts.append(f"*… {_TURN_CAP} messages shown *")
                    break
            except Exception:
                pass
    if n == 0:
        parts.append("*No usable chat messages found in this session file.*")


def _preview_hermes_body(filepath: str, parts: list[str]) -> None:
    """Append Hermes session preview lines."""
    n = 0
    with open(filepath, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                role = r.get("role", "")
                if role not in ("user", "assistant"):
                    continue
                t = _extract_text(r.get("content", "")).strip()
                if not t:
                    continue
                n += 1
                if role == "user":
                    parts.append("**You**")
                    parts.append(_quote(t[:500]))
                else:
                    parts.append("**Assistant**")
                    parts.append(t[:2000])
                if n >= _TURN_CAP:
                    break
            except Exception:
                pass
    if n == 0:
        parts.append("*No usable chat messages found.*")


def _preview_pi_body(filepath: str, parts: list[str]) -> None:
    """Append PI session preview lines."""
    n = 0
    with open(filepath, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get("type") != "message":
                    continue
                msg = r.get("message", {})
                role = msg.get("role", "")
                if role not in ("user", "assistant"):
                    continue
                t = _extract_text(msg.get("content", "")).strip()
                if not t:
                    continue
                n += 1
                if role == "user":
                    parts.append("**You**")
                    parts.append(_quote(t[:500]))
                else:
                    parts.append("**Assistant**")
                    parts.append(t[:2000])
                if n >= _TURN_CAP:
                    break
            except Exception:
                pass
    if n == 0:
        parts.append("*No usable chat messages found.*")


def _preview_opencode(sid: str, session_file: Path) -> str:
    return _preview_opencode_from_roots(
        sid,
        session_file,
        OPENCODE_STORAGE / "message",
        OPENCODE_STORAGE / "part",
    )


def _preview_opencode_from_roots(
    sid: str,
    session_file: Path,
    message_root: Path,
    part_root: Path,
) -> str:
    """Preview an Opencode session by reading its message + part files."""
    try:
        data = json.loads(session_file.read_text())
    except Exception:
        return f"*Error reading Opencode session: {sid}*"

    title = data.get("title", "")
    proj_hash = data.get("projectID") or data.get("projectId") or session_file.parent.name
    proj_path = _opencode_project_path(proj_hash)

    parts: list[str] = []
    if title:
        parts.append(f"## {title}")
        parts.append(f"`{proj_path}` [opencode]")
    else:
        parts.append(f"## `{proj_path}`")
        parts.append("[opencode]")

    msg_dir = message_root / sid
    if not msg_dir.is_dir():
        parts.append("*No messages found for this session.*")
        return "\n".join(parts)

    # Load and sort messages by creation time
    msgs: list[tuple[int, dict]] = []
    for mf in msg_dir.iterdir():
        if mf.suffix != ".json":
            continue
        try:
            m = json.loads(mf.read_text())
            created = m.get("time", {}).get("created", 0)
            msgs.append((created, m))
        except Exception:
            pass
    msgs.sort(key=lambda x: x[0])

    n = 0
    for _, m in msgs:
        role = m.get("role", "")
        if role not in ("user", "assistant"):
            continue

        # Get text from parts
        mid = m.get("id", "")
        part_dir = part_root / mid
        text = ""
        if part_dir.is_dir():
            for pf in sorted(part_dir.iterdir()):
                if pf.suffix != ".json":
                    continue
                try:
                    pd = json.loads(pf.read_text())
                    if pd.get("type") == "text":
                        text = pd.get("text", "").strip()
                        break
                except Exception:
                    pass

        if not text:
            continue
        n += 1

        if role == "user":
            parts.append("**You**")
            parts.append(_quote(text[:500]))
        else:
            parts.append("**Assistant**")
            parts.append(text[:2000])

        if n >= _TURN_CAP:
            break

    if n == 0:
        parts.append("*No usable chat messages found.*")

    return "\n".join(parts)


def _quote(text: str) -> str:
    """Markdown blockquote a string, handling blank lines."""
    return "\n".join(
        f"> {ln}" if ln.strip() else ">" for ln in text.split("\n")
    )


# ── Trash operations ──────────────────────────────────────────────────────

def _restore_dir_for_source(source: str, pid: str) -> Path:
    """Return the directory a session should be restored to."""
    if source == SOURCE_CLAUDE and pid == "transcripts":
        return TRANSCRIPTS_DIR
    if source == SOURCE_HERMES:
        return HERMES_SESSIONS_DIR
    if source == SOURCE_PI:
        return PI_SESSIONS_DIR / pid
    # Claude Code (and unknown) → project dir
    return PROJECTS_DIR / pid


def _trash_opencode_session(sid: str, session_file: Path) -> None:
    proj_hash = session_file.parent.name
    _, _, _, title = _parse_opencode_session(session_file)
    names = get_names(_names_bucket_for_source(SOURCE_OPENCODE, proj_hash))
    name = names.get(sid, "") or title

    bundle = _trash_opencode_dir(sid)
    if bundle.exists():
        raise ValueError(f"Session already in trash: {sid}")
    (bundle / "part").mkdir(parents=True, exist_ok=True)

    meta = {
        "project_id": proj_hash,
        "name": name,
        "trashed_at": int(datetime.now().timestamp()),
        "source": SOURCE_OPENCODE,
    }
    _trash_meta_path(sid).write_text(json.dumps(meta))

    session_file.rename(bundle / "session.json")

    msg_dir = OPENCODE_STORAGE / "message" / sid
    moved_msg_dir = bundle / "message" / sid
    moved_msg_dir.parent.mkdir(parents=True, exist_ok=True)
    if msg_dir.is_dir():
        msg_dir.rename(moved_msg_dir)
        for msg_file in moved_msg_dir.glob("*.json"):
            part_dir = OPENCODE_STORAGE / "part" / msg_file.stem
            if part_dir.is_dir():
                part_dir.rename(bundle / "part" / msg_file.stem)

    rm_name(_names_bucket_for_source(SOURCE_OPENCODE, proj_hash), sid)


def _restore_opencode_session(sid: str, proj_hash: str, name: str) -> None:
    bundle = _trash_opencode_dir(sid)
    session_src = bundle / "session.json"
    if not session_src.exists():
        raise ValueError(f"Not in trash: {sid}")

    dest_dir = OPENCODE_STORAGE / "session" / proj_hash
    dest_dir.mkdir(parents=True, exist_ok=True)
    session_src.rename(dest_dir / f"{sid}.json")

    msg_src = bundle / "message" / sid
    msg_dest = OPENCODE_STORAGE / "message" / sid
    if msg_src.is_dir():
        msg_dest.parent.mkdir(parents=True, exist_ok=True)
        msg_src.rename(msg_dest)

    part_src = bundle / "part"
    if part_src.is_dir():
        part_root = OPENCODE_STORAGE / "part"
        part_root.mkdir(parents=True, exist_ok=True)
        for child in part_src.iterdir():
            child.rename(part_root / child.name)

    shutil.rmtree(bundle, ignore_errors=True)
    if name:
        set_name(_names_bucket_for_source(SOURCE_OPENCODE, proj_hash), sid, name)


def trash_session(sid: str) -> None:
    oc_file = _find_opencode_session(sid)
    if oc_file:
        _trash_opencode_session(sid, oc_file)
        return

    f = _find_session_file(sid)
    if not f:
        if _copilot_session_row(sid) is not None:
            _trash_copilot_session(sid)
            return
        if _codex_thread_row(sid) is not None:
            # Codex's own archived flag IS the trash; custom names survive restore
            if not _codex_set_archived(sid, True):
                raise ValueError(f"Could not archive Codex session: {sid}")
            return
        raise ValueError(f"Session not found: {sid}")
    pid = f.parent.name
    source = _detect_source(str(f))
    names = get_names(_names_bucket_for_source(source, pid))
    name = names.get(sid, "")
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    meta = {
        "project_id": pid,
        "name": name,
        "trashed_at": int(datetime.now().timestamp()),
        "source": source,
    }
    _trash_meta_path(sid).write_text(json.dumps(meta))
    f.rename(_trash_jsonl_path(sid))
    sidecar = f.parent / sid
    if sidecar.is_dir():
        shutil.move(str(sidecar), str(TRASH_DIR / sid))
    rm_name(_names_bucket_for_source(source, pid), sid)


def restore_session(sid: str) -> None:
    meta = _read_trash_meta(sid)
    source = meta.get("source", SOURCE_CLAUDE)
    pid = meta.get("project_id", "")
    name = meta.get("name", "")

    if not meta:
        # Sqlite-flagged trash (codex archived threads) has no meta file
        if _codex_set_archived(sid, False):
            return

    if source == SOURCE_OPENCODE:
        if not pid:
            raise ValueError(f"Missing project for trashed Opencode session: {sid}")
        _restore_opencode_session(sid, pid, name)
        _trash_meta_path(sid).unlink(missing_ok=True)
        return

    if source == SOURCE_COPILOT:
        _restore_copilot_session(sid, name)
        _trash_meta_path(sid).unlink(missing_ok=True)
        return

    src = _trash_jsonl_path(sid)
    if not src.exists():
        raise ValueError(f"Not in trash: {sid}")
    meta_file = _trash_meta_path(sid)
    if not pid:
        pid = os.getcwd().replace("/", "-")
    dest_dir = _restore_dir_for_source(source, pid)
    dest_dir.mkdir(parents=True, exist_ok=True)
    src.rename(dest_dir / f"{sid}.jsonl")
    sidecar = TRASH_DIR / sid
    if sidecar.is_dir():
        shutil.move(str(sidecar), str(dest_dir / sid))
    meta_file.unlink(missing_ok=True)
    if name:
        set_name(_names_bucket_for_source(source, pid), sid, name)


def delete_trashed(sid: str) -> None:
    """Permanently delete one trashed session, whatever its harness."""
    meta = _read_trash_meta(sid)
    source = meta.get("source", "")

    if source == SOURCE_OPENCODE:
        shutil.rmtree(_trash_opencode_dir(sid), ignore_errors=True)
    elif source == SOURCE_COPILOT:
        _copilot_bundle_path(sid).unlink(missing_ok=True)
    elif meta:
        _trash_jsonl_path(sid).unlink(missing_ok=True)
        shutil.rmtree(TRASH_DIR / sid, ignore_errors=True)
    else:
        # No meta file: stray jsonl or codex archived thread
        stray = _trash_jsonl_path(sid)
        if stray.exists():
            stray.unlink(missing_ok=True)
        elif _codex_thread_row(sid) is not None:
            _delete_codex_session(sid)
    _trash_meta_path(sid).unlink(missing_ok=True)


def empty_trash() -> int:
    entries = list_trash()
    for e in entries:
        delete_trashed(e.sid)
    return len(entries)
