"""Pure presentation helpers for Claudetree's command-center UI."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

from rich.text import Text

from .backend import (
    Session, TrashEntry,
    SOURCE_CLAUDE,
    HARNESS_MAP,
)


@dataclass(frozen=True)
class CommandSpec:
    key: str
    label: str
    keywords: tuple[str, ...] = ()
    context: tuple[str, ...] = ()


def _compact(text: str, limit: int = 72) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


def _project_label(session: Session) -> str:
    if session.project_id:
        return session.project_path
    return "unknown project"


def harness_pill(source: str) -> Text:
    """Return a styled inline badge for the harness, e.g. '★ Claude Code'."""
    h = HARNESS_MAP.get(source)
    if not h:
        return Text(f"[{source}]", style="dim")
    pill = Text()
    pill.append(f"{h.icon} {h.label}", style=h.color)
    return pill


def _session_primary_label(session: Session) -> str:
    return session.name or session.first_msg or session.sid[:24]


_ACTIVE_SECS = 300    # 5 min — session counts as "active"
_HEAVY_MSGS  = 200    # msg count threshold for "heavy" badge


def _session_badges(session: Session) -> Text:
    """Return inline badge glyphs: active indicator and/or heavy marker."""
    badges = Text()
    if session.mtime_secs and (time.time() - session.mtime_secs) < _ACTIVE_SECS:
        badges.append(" ●", style="bold green")
    if session.msgs >= _HEAVY_MSGS:
        badges.append(" ↟", style="bold yellow")
    return badges


def session_row_text(session: Session, show_project: bool = True) -> Text:
    h = HARNESS_MAP.get(session.source)
    label_style = f"bold {h.color}" if h else "bold cyan"

    text = Text()
    text.append(f"{session.age:>4}  ", style="dim")
    text.append(_session_primary_label(session), style=label_style)
    text.append_text(_session_badges(session))
    text.append(f"  {session.msgs} msgs", style="dim")
    if session.source != SOURCE_CLAUDE:
        text.append("  ")
        text.append_text(harness_pill(session.source))
    if show_project and session.project_id:
        text.append(f"  {_project_label(session)}", style="magenta")
    if session.first_msg:
        snippet = _compact(session.first_msg, 88)
        text.append("\n")
        text.append(f"↳ {snippet}", style="dim")
    return text


def trash_row_text(entry: TrashEntry) -> Text:
    text = Text()
    text.append(f"{entry.when:>8}  ", style="dim")
    label = entry.name or entry.sid[:24]
    text.append(label, style="bold red")
    text.append(f"  {entry.project_path if entry.project_id else 'unknown project'}", style="dim")
    return text


def status_strip_text(
    *,
    screen_label: str,
    scope_label: str = "",
    sort_label: str = "",
    filter_label: str = "",
    count: int | None = None,
    mode_label: str = "",
    command_hint: str = "",
) -> Text:
    text = Text()
    text.append(f"{screen_label}", style="bold")
    if scope_label:
        text.append(" · ", style="dim")
        text.append(f"scope {scope_label}", style="cyan")
    if sort_label:
        text.append(" · ", style="dim")
        text.append(f"sort {sort_label}", style="magenta")
    if filter_label:
        text.append(" · ", style="dim")
        text.append(f"filter {filter_label}", style="green")
    if count is not None:
        text.append(" · ", style="dim")
        text.append(f"{count} items", style="yellow")
    if mode_label:
        text.append(" · ", style="dim")
        text.append(mode_label, style="blue")
    if command_hint:
        text.append(" · ", style="dim")
        text.append(command_hint, style="dim")
    return text


def _token_score(query: str, candidate: str) -> int:
    if not query:
        return 0
    q = query.lower().strip()
    c = candidate.lower().strip()
    if not q or not c:
        return 10_000
    if q in c:
        return max(0, len(c) - len(q))
    score = 0
    pos = 0
    for ch in q:
        idx = c.find(ch, pos)
        if idx == -1:
            return 10_000
        score += idx - pos
        pos = idx + 1
    return score + (len(c) - len(q))


def fuzzy_match(query: str, haystack: str) -> bool:
    """fzf-style match: every query word must appear as a substring or
    an in-order character subsequence of the haystack."""
    hs = haystack.lower()
    return all(_token_score(w, hs) < 10_000 for w in query.lower().split())


def filter_commands(query: str, commands: Iterable[CommandSpec]) -> list[CommandSpec]:
    cmds = list(commands)
    if not query.strip():
        return cmds
    ranked: list[tuple[int, int, CommandSpec]] = []
    q = query.strip()
    for idx, cmd in enumerate(cmds):
        haystacks = [cmd.label, cmd.key, *cmd.keywords, *cmd.context]
        score = min(_token_score(q, h) for h in haystacks if h)
        if score < 10_000:
            ranked.append((score, idx, cmd))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in ranked]
