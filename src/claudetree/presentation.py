"""Pure presentation helpers for Claudetree's command-center UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from rich.text import Text

from .backend import Session, TrashEntry, pid_to_path


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


def _project_label(project_id: str) -> str:
    return pid_to_path(project_id) if project_id else "unknown project"


def _session_primary_label(session: Session) -> str:
    return session.name or session.first_msg or session.sid[:24]


def session_row_text(session: Session, show_project: bool = True) -> Text:
    text = Text()
    text.append(f"{session.age:>4}  ", style="dim")
    text.append(_session_primary_label(session), style="bold cyan")
    text.append(f"  {session.msgs} msgs", style="dim")
    if show_project and session.project_id:
        text.append(f"  {_project_label(session.project_id)}", style="magenta")
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
    text.append(f"  {_project_label(entry.project_id)}", style="dim")
    return text


def status_strip_text(
    *,
    screen_label: str,
    scope_label: str,
    sort_label: str,
    filter_label: str = "",
    count: int | None = None,
    mode_label: str = "",
    command_hint: str = "",
) -> Text:
    text = Text()
    text.append(f"{screen_label}", style="bold")
    text.append(" · ", style="dim")
    text.append(f"scope {scope_label}", style="cyan")
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
