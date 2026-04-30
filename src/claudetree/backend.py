"""Session management backend — reads session data from multiple AI coding tools."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

HOME = Path.home()

# ── Directory constants ────────────────────────────────────────────────────

# Claude Code
PROJECTS_DIR = HOME / ".claude" / "projects"
TRANSCRIPTS_DIR = HOME / ".claude" / "transcripts"
NAMES_DIR = HOME / ".claude" / "session-names"
TRASH_DIR = HOME / ".claude" / "trash"

# Hermes
HERMES_SESSIONS_DIR = HOME / ".hermes" / "sessions"

# OpenClaw
OPENCLAW_SESSIONS_DIR = HOME / ".openclaw" / "agents" / "main" / "sessions"

# Opencode
OPENCODE_STORAGE = HOME / ".local" / "share" / "opencode" / "storage"

# ── Source labels ──────────────────────────────────────────────────────────

SOURCE_CLAUDE = "claude"
SOURCE_HERMES = "hermes"
SOURCE_OPENCLAW = "openclaw"
SOURCE_OPENCODE = "opencode"


# ── Harness registry ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Harness:
    """Metadata for one AI coding tool harness."""
    id: str
    label: str
    color: str             # Rich color name used for display
    icon: str              # single-char glyph (falls back gracefully)
    supports_trash: bool
    resume_cmd: tuple[str, ...]  # {sid} replaced at runtime

    def build_resume_cmd(self, sid: str) -> list[str]:
        return [part.replace("{sid}", sid) for part in self.resume_cmd]


HARNESSES: list[Harness] = [
    Harness(SOURCE_CLAUDE,   "Claude Code", "cyan",    "★", True,  ("claude",   "--resume", "{sid}")),
    Harness(SOURCE_HERMES,   "Hermes",      "magenta", "⚡", True,  ("hermes",   "resume",   "{sid}")),
    Harness(SOURCE_OPENCLAW, "OpenClaw",    "green",   "◆", True,  ("openclaw", "resume",   "{sid}")),
    Harness(SOURCE_OPENCODE, "Opencode",    "yellow",  "▲", True,  ("opencode", "session",  "resume", "{sid}")),
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
        if self.source == SOURCE_OPENCODE:
            return _opencode_project_path(self.project_id)
        return pid_to_path(self.project_id)

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
        if self.source == SOURCE_OPENCODE:
            return _opencode_project_path(self.project_id)
        return pid_to_path(self.project_id)


# ── Age helpers ────────────────────────────────────────────────────────────

def _compute_age(last_time) -> str:
    try:
        if isinstance(last_time, (int, float)):
            dt = datetime.fromtimestamp(last_time / 1000, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(last_time).replace("Z", "+00:00"))
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
    """Find a JSONL session file across all sources (not Opencode)."""
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
    if OPENCLAW_SESSIONS_DIR.is_dir():
        f = OPENCLAW_SESSIONS_DIR / f"{sid}.jsonl"
        if f.exists():
            return f
    return None


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
    if source == SOURCE_HERMES:
        return "hermes"
    if source == SOURCE_OPENCLAW:
        return "openclaw"
    if source == SOURCE_OPENCODE:
        return "opencode"
    return project_id


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


def _parse_openclaw_jsonl(filepath: str):
    """Parse OpenClaw sessions: type=message with nested message.role/content."""
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

    # ── OpenClaw ──
    if (not _hf or _hf == SOURCE_OPENCLAW) and OPENCLAW_SESSIONS_DIR.is_dir():
        openclaw_names = get_names("openclaw")
        for f in OPENCLAW_SESSIONS_DIR.glob("*.jsonl"):
            sid = f.stem
            last_time, cnt, first = _parse_openclaw_jsonl(str(f))
            if not cnt:
                continue
            age = _compute_age(last_time) if last_time else "?"
            rows.append(Session(
                sid=sid, name=openclaw_names.get(sid, ""), first_msg=first,
                age=age, msgs=cnt, project_id="openclaw",
                sort_time=str(last_time), source=SOURCE_OPENCLAW,
                mtime_secs=f.stat().st_mtime,
            ))

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
    return [r[1] for r in rows]


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
        if OPENCLAW_SESSIONS_DIR.is_dir():
            search_paths.append(str(OPENCLAW_SESSIONS_DIR))
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
            msg_file = OPENCODE_STORAGE / "message"
            # Find which session owns this message
            oc_sid = _opencode_msg_to_session(msg_id)
            if oc_sid and oc_sid not in seen_sids:
                seen_sids.add(oc_sid)
                sf = _find_opencode_session(oc_sid)
                if sf:
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

        sid = p.stem
        if sid in seen_sids:
            continue
        seen_sids.add(sid)

        pid = p.parent.name
        source = _detect_source(filepath)
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

    rows.sort(key=lambda r: r.sort_time, reverse=True)
    return rows


def _detect_source(filepath: str) -> str:
    """Detect session source from file path."""
    if str(HERMES_SESSIONS_DIR) in filepath:
        return SOURCE_HERMES
    if str(OPENCLAW_SESSIONS_DIR) in filepath:
        return SOURCE_OPENCLAW
    return SOURCE_CLAUDE


def _parser_for_source(source: str):
    """Return the appropriate JSONL parser for a source."""
    if source == SOURCE_HERMES:
        return _parse_hermes_jsonl
    if source == SOURCE_OPENCLAW:
        return _parse_openclaw_jsonl
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

    # JSONL-based sources
    found = _find_session_file(sid)
    filepath = str(found) if found else str(_trash_jsonl_path(sid))
    if not os.path.exists(filepath):
        return f"*Session not found: {sid}*"

    source = _detect_source(filepath)
    p = Path(filepath)
    pid = p.parent.name
    names = get_names(_names_bucket_for_source(source, pid))
    name = names.get(sid, "")
    raw_path = "/" + pid[1:].replace("-", "/") if pid.startswith("-") else pid
    short_path = raw_path.replace(str(HOME), "~")

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
    elif source == SOURCE_OPENCLAW:
        _preview_openclaw_body(filepath, parts)
    else:
        _preview_claude_body(filepath, parts)

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


def _preview_openclaw_body(filepath: str, parts: list[str]) -> None:
    """Append OpenClaw session preview lines."""
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
                    parts.append("**Claude**")
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
    if source == SOURCE_OPENCLAW:
        return OPENCLAW_SESSIONS_DIR
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

    if source == SOURCE_OPENCODE:
        if not pid:
            raise ValueError(f"Missing project for trashed Opencode session: {sid}")
        _restore_opencode_session(sid, pid, name)
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


def empty_trash() -> int:
    count = 0
    seen: set[str] = set()

    for meta in TRASH_DIR.glob("*.meta"):
        sid = meta.stem
        seen.add(sid)
        data = _read_trash_meta(sid)
        source = data.get("source", SOURCE_CLAUDE)
        if source == SOURCE_OPENCODE:
            shutil.rmtree(_trash_opencode_dir(sid), ignore_errors=True)
        else:
            _trash_jsonl_path(sid).unlink(missing_ok=True)
            shutil.rmtree(TRASH_DIR / sid, ignore_errors=True)
        meta.unlink(missing_ok=True)
        count += 1

    for f in TRASH_DIR.glob("*.jsonl"):
        if f.stem in seen:
            continue
        f.unlink(missing_ok=True)
        count += 1

    return count
