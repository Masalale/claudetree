"""Session management backend — reads Claude Code .jsonl files directly."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

HOME = Path.home()
PROJECTS_DIR = HOME / ".claude" / "projects"
NAMES_DIR = HOME / ".claude" / "session-names"
TRASH_DIR = HOME / ".claude" / "trash"


def pid_to_path(project_id: str) -> str:
    """Convert a Claude project-id back to a human-readable path.

    Claude encodes the absolute project path as the directory name by replacing
    every '/' with '-'.  The leading '-' maps back to the root '/'.
    Example: '-home-ngash-myapp' → '~/myapp'
    """
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

    @property
    def project_path(self) -> str:
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

    @property
    def project_path(self) -> str:
        return pid_to_path(self.project_id)


# ── Age helpers ─────────────────────────────────────────────────────────────


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


# ── Name management ──────────────────────────────────────────────────────────


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


def project_for_session(sid: str) -> Optional[str]:
    for f in PROJECTS_DIR.glob(f"*/{sid}.jsonl"):
        return f.parent.name
    return None


# ── JSONL parsing ────────────────────────────────────────────────────────────


def _parse_jsonl(filepath: str):
    """Return (last_time, msg_count, first_user_msg)."""
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
                    rt = r.get("type", "")
                    if rt in ("user", "assistant"):
                        cnt += 1
                        if not first and rt == "user":
                            c = r.get("message", {}).get("content", "")
                            if isinstance(c, str):
                                t = c
                            else:
                                t = next(
                                    (
                                        x.get("text", "")
                                        for x in c
                                        if isinstance(x, dict)
                                        and x.get("type") == "text"
                                    ),
                                    "",
                                )
                            first = t.strip().replace("\n", " ")[:60]
                except Exception:
                    pass
    except Exception:
        pass
    return last_time, cnt, first


# ── Session listing ──────────────────────────────────────────────────────────


def list_sessions(
    cwd: Optional[str] = None, all_projects: bool = False
) -> list[Session]:
    NAMES_DIR.mkdir(parents=True, exist_ok=True)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    if cwd is None:
        cwd = os.getcwd()
    cur = cwd.replace("/", "-")
    rows = []
    for pd in PROJECTS_DIR.glob("*"):
        if not pd.is_dir():
            continue
        pid = pd.name
        if not all_projects and pid != cur:
            continue
        names = get_names(pid)
        for f in pd.glob("*.jsonl"):
            sid = f.stem
            last_time, cnt, first = _parse_jsonl(str(f))
            if not cnt:
                continue
            age = _compute_age(last_time) if last_time else "?"
            rows.append(
                Session(
                    sid=sid,
                    name=names.get(sid, ""),
                    first_msg=first,
                    age=age,
                    msgs=cnt,
                    project_id=pid,
                    sort_time=str(last_time),
                )
            )
    rows.sort(key=lambda r: r.sort_time, reverse=True)
    return rows


def list_trash() -> list[TrashEntry]:
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for f in TRASH_DIR.glob("*.jsonl"):
        sid = f.stem
        meta = f.with_suffix(".meta")
        pid, name, when = "", "", ""
        mtime = f.stat().st_mtime
        if meta.exists():
            try:
                m = json.loads(meta.read_text())
                pid = m.get("project_id", "")
                name = m.get("name", "")
                ts = m.get("trashed_at", 0)
                if ts:
                    d = datetime.now(timezone.utc) - datetime.fromtimestamp(
                        ts, tz=timezone.utc
                    )
                    when = f"{d.days}d ago" if d.days else f"{d.seconds // 3600}h ago"
            except Exception:
                pass
        rows.append((mtime, TrashEntry(sid=sid, name=name, project_id=pid, when=when)))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [r[1] for r in rows]


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
    if all_projects:
        search_path = str(PROJECTS_DIR)
    else:
        search_path = str(PROJECTS_DIR / cur)
        if not os.path.exists(search_path):
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
        cmd.extend([query, search_path])
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return []
    files = {f for f in result.stdout.strip().splitlines() if f.endswith(".jsonl")}
    if not files:
        return []
    rows = []
    for filepath in files:
        p = Path(filepath)
        pid = p.parent.name
        sid = p.stem
        names = get_names(pid)
        last_time, cnt, first = _parse_jsonl(filepath)
        if not cnt:
            continue
        age = _compute_age(last_time) if last_time else "?"
        rows.append(
            Session(
                sid=sid,
                name=names.get(sid, ""),
                first_msg=first,
                age=age,
                msgs=cnt,
                project_id=pid,
                sort_time=str(last_time),
            )
        )
    rows.sort(key=lambda r: r.sort_time, reverse=True)
    return rows


# ── Preview ──────────────────────────────────────────────────────────────────

# Per-message character cap — keeps rendering snappy for large sessions
_MSG_CAP = 3000
_TURN_CAP = 30


def preview_session(sid: str) -> str:
    """Return a markdown-formatted preview of the session for the Markdown widget."""
    filepath = next(
        (str(f) for f in PROJECTS_DIR.glob(f"*/{sid}.jsonl")),
        str(TRASH_DIR / f"{sid}.jsonl"),
    )
    if not os.path.exists(filepath):
        return f"*Session not found: {sid}*"
    p = Path(filepath)
    pid = p.parent.name
    names = get_names(pid)
    name = names.get(sid, "")
    raw_path = "/" + pid[1:].replace("-", "/") if pid.startswith("-") else pid
    short_path = raw_path.replace(str(HOME), "~")

    parts: list[str] = []

    # ── Header ───────────────────────────────────────────────────────────────
    if name:
        parts.append(f"## {name}")
        parts.append(f"`{short_path}`")
    else:
        parts.append(f"## `{short_path}`")
    parts.append("")
    parts.append("---")
    parts.append("")

    # ── Conversation turns ────────────────────────────────────────────────────
    n = 0
    with open(filepath, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                rt = r.get("type", "")
                if rt not in ("user", "assistant"):
                    continue
                c = r.get("message", {}).get("content", "")
                if isinstance(c, str):
                    t = c
                else:
                    t = next(
                        (
                            x.get("text", "")
                            for x in c
                            if isinstance(x, dict) and x.get("type") == "text"
                        ),
                        "",
                    )
                t = t.strip()
                if not t:
                    continue
                n += 1

                if rt == "user":
                    # User messages in a blockquote so they stand out visually
                    parts.append("**You**")
                    parts.append("")
                    msg = t[:_MSG_CAP]
                    if len(t) > _MSG_CAP:
                        msg += f"\n\n*… ({len(t) - _MSG_CAP} more chars)*"
                    quoted = "\n".join(
                        f"> {ln}" if ln.strip() else ">" for ln in msg.split("\n")
                    )
                    parts.append(quoted)
                else:
                    # Claude's responses are already markdown — render as-is
                    parts.append("**Claude**")
                    parts.append("")
                    msg = t[:_MSG_CAP]
                    if len(t) > _MSG_CAP:
                        msg += f"\n\n*… ({len(t) - _MSG_CAP} more chars)*"
                    parts.append(msg)

                parts.append("")
                parts.append("---")
                parts.append("")

                if n >= _TURN_CAP:
                    parts.append(f"*Preview capped at {_TURN_CAP} messages.*")
                    break
            except Exception:
                pass

    return "\n".join(parts)


# ── Trash operations ─────────────────────────────────────────────────────────


def trash_session(sid: str) -> None:
    pid = project_for_session(sid)
    if not pid:
        raise ValueError(f"Session not found: {sid}")
    f = PROJECTS_DIR / pid / f"{sid}.jsonl"
    if not f.exists():
        raise ValueError(f"Session file missing: {f}")
    names = get_names(pid)
    name = names.get(sid, "")
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    meta = {
        "project_id": pid,
        "name": name,
        "trashed_at": int(datetime.now().timestamp()),
    }
    (TRASH_DIR / f"{sid}.meta").write_text(json.dumps(meta))
    f.rename(TRASH_DIR / f"{sid}.jsonl")
    sidecar = PROJECTS_DIR / pid / sid
    if sidecar.is_dir():
        import shutil

        shutil.move(str(sidecar), str(TRASH_DIR / sid))
    rm_name(pid, sid)


def restore_session(sid: str) -> None:
    src = TRASH_DIR / f"{sid}.jsonl"
    if not src.exists():
        raise ValueError(f"Not in trash: {sid}")
    pid, name = "", ""
    meta_file = TRASH_DIR / f"{sid}.meta"
    if meta_file.exists():
        try:
            m = json.loads(meta_file.read_text())
            pid = m.get("project_id", "")
            name = m.get("name", "")
        except Exception:
            pass
    if not pid:
        pid = os.getcwd().replace("/", "-")
    dest_dir = PROJECTS_DIR / pid
    dest_dir.mkdir(parents=True, exist_ok=True)
    src.rename(dest_dir / f"{sid}.jsonl")
    sidecar = TRASH_DIR / sid
    if sidecar.is_dir():
        import shutil

        shutil.move(str(sidecar), str(dest_dir / sid))
    meta_file.unlink(missing_ok=True)
    if name:
        set_name(pid, sid, name)


def empty_trash() -> int:
    count = 0
    for f in TRASH_DIR.glob("*.jsonl"):
        f.unlink()
        count += 1
    for f in TRASH_DIR.glob("*.meta"):
        f.unlink()
    return count
