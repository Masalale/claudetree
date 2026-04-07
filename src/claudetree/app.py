"""Claudetree Textual TUI — lazygit-style Claude Code session manager."""

from __future__ import annotations

import os
import re
from typing import Optional

from rich.markup import escape
from rich.markdown import Markdown as RichMarkdown
from rich.text import Text as RichText
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.timer import Timer
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static

from .backend import (
    Session,
    TrashEntry,
    PROJECTS_DIR,
    empty_trash,
    list_sessions,
    list_trash,
    pid_to_path,
    preview_session,
    project_for_session,
    restore_session,
    search_sessions,
    set_name,
    trash_session,
)


# ── Custom list items ─────────────────────────────────────────────────────────


class SessionItem(ListItem):
    class RightClicked(Message):
        def __init__(self, session: Session, x: int, y: int) -> None:
            super().__init__()
            self.session = session
            self.x = x
            self.y = y

    def __init__(self, session: Session, show_project: bool = True) -> None:
        super().__init__()
        self.session = session
        self._show_project = show_project

    def compose(self) -> ComposeResult:
        s = self.session
        markup = f"[dim]{s.age}[/dim]  "
        name_or_sid = s.name if s.name else s.sid[:24]
        markup += f"[bold cyan]{escape(name_or_sid)}[/bold cyan]  "
        if self._show_project and s.project_id:
            markup += f"[bold magenta]{escape(s.project_path)}[/bold magenta]"
        yield Label(markup)

    def on_mouse_down(self, event) -> None:
        if event.button == 3:
            self.post_message(
                self.RightClicked(
                    self.session,
                    self.region.x + event.x,
                    self.region.y + event.y,
                )
            )
            event.stop()


class TrashItem(ListItem):
    class RightClicked(Message):
        def __init__(self, entry: TrashEntry, x: int, y: int) -> None:
            super().__init__()
            self.entry = entry
            self.x = x
            self.y = y

    def __init__(self, entry: TrashEntry) -> None:
        super().__init__()
        self.entry = entry

    def compose(self) -> ComposeResult:
        e = self.entry
        markup = (
            f"[dim]{e.when:>8}[/dim]  "
            f"[bold red]{escape(e.name or e.sid[:24] + '...')}[/bold red]  "
            f"[dim]{escape(e.project_path)}[/dim]"
        )
        yield Label(markup)

    def on_mouse_down(self, event) -> None:
        if event.button == 3:
            self.post_message(
                self.RightClicked(
                    self.entry,
                    self.region.x + event.x,
                    self.region.y + event.y,
                )
            )
            event.stop()


# ── Filter input ──────────────────────────────────────────────────────────────


class FilterInput(Input):
    """Input that forwards ↑↓/Enter/screen-bindings, and handles escape locally."""

    class Cancelled(Message):
        """Fired when the user presses Escape — close the filter bar."""

    BINDINGS = [Binding("ctrl+a", "route_toggle_all", show=False)]

    _PASSTHROUGH = {
        "ctrl+d",
        "ctrl+r",
        "ctrl+t",
        "ctrl+n",
        "ctrl+s",
        "ctrl+underscore",
        "ctrl+slash",
        "ctrl+b",
        "ctrl+i",
        "ctrl+g",
        "alt+c",
        "alt+r",
    }

    def action_route_toggle_all(self) -> None:
        action_toggle_all = getattr(self.screen, "action_toggle_all", None)
        if callable(action_toggle_all):
            action_toggle_all()

    def on_key(self, event) -> None:
        if event.key == "down":
            move_list = getattr(self.screen, "move_list", None)
            if callable(move_list):
                move_list(1)
            event.prevent_default()
        elif event.key == "up":
            move_list = getattr(self.screen, "move_list", None)
            if callable(move_list):
                move_list(-1)
            event.prevent_default()
        elif event.key == "enter":
            activate_list = getattr(self.screen, "activate_list", None)
            if callable(activate_list):
                activate_list()
            event.prevent_default()
        elif event.key == "escape":
            # Post Cancelled instead of letting escape reach screen's quit binding
            self.post_message(self.Cancelled())
            event.stop()
        elif event.key in self._PASSTHROUGH:
            event.stop(False)


# ── Modal dialogs ─────────────────────────────────────────────────────────────


class InputDialog(ModalScreen[str | None]):
    """Single-input prompt dialog."""

    DEFAULT_CSS = """
    InputDialog {
        align: center middle;
        background: $background 70%;
    }
    #dialog {
        width: 62;
        height: 7;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    #dialog Label {
        margin-bottom: 1;
        color: $foreground;
    }
    #dialog-input {
        background: $panel-darken-2;
        color: $text;
        text-style: bold;
        border: tall $primary 60%;
    }
    #dialog-input:focus {
        background: $boost;
        color: $text;
        border: tall $primary;
    }
    """

    def __init__(self, prompt: str, initial: str = "") -> None:
        super().__init__()
        self._prompt = prompt
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._prompt)
            yield Input(value=self._initial, id="dialog-input")

    def on_mount(self) -> None:
        self.query_one("#dialog-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value if value else None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class ConfirmDialog(ModalScreen[bool]):
    """Yes/No confirmation dialog."""

    DEFAULT_CSS = """
    ConfirmDialog {
        align: center middle;
        background: $background 70%;
    }
    #dialog {
        width: 56;
        height: 7;
        border: round $warning;
        background: $surface;
        padding: 1 2;
    }
    #dialog Label {
        margin-bottom: 1;
        color: $foreground;
    }
    #confirm-input {
        background: $panel-darken-2;
        color: $text;
        text-style: bold;
        border: tall $warning 60%;
    }
    #confirm-input:focus {
        background: $boost;
        color: $text;
        border: tall $warning;
    }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(escape(self._message))
            yield Input(
                placeholder="type y + Enter to confirm, Escape cancels",
                id="confirm-input",
            )

    def on_mount(self) -> None:
        self.query_one("#confirm-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip().lower() == "y")

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(False)


# ── Inline context-menu with backdrop ────────────────────────────────────────


class _ContextBackdrop(Static):
    """Full-screen transparent layer behind the context menu.

    Captures any click outside the menu and hides it — prevents
    accidental interaction with underlying session items.
    """

    DEFAULT_CSS = """
    _ContextBackdrop {
        layer: backdrop;
        display: none;
        width: 100%;
        height: 100%;
        background: $background 40%;
    }
    """

    def on_mouse_down(self, event) -> None:
        # Hide backdrop + menu on click outside
        self.display = False
        try:
            self.screen.query_one(ContextMenuWidget).display = False
            self.screen.query_one("#sessions", ListView).focus()
        except Exception:
            pass
        event.stop()


class _MenuList(ListView):
    """ListView inside ContextMenuWidget — escape hides the parent menu."""

    def on_key(self, event) -> None:
        if event.key == "escape":
            parent = self.parent
            if isinstance(parent, ContextMenuWidget):
                parent.hide()
            event.stop()


class ContextMenuWidget(Vertical):
    """Inline floating context menu in the overlay layer.

    No ModalScreen — no screen push/pop, no blank-screen artefacts.
    Paired with _ContextBackdrop to block clicks on the underlying list.
    """

    class Chosen(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    DEFAULT_CSS = """
    ContextMenuWidget {
        layer: overlay;
        display: none;
        background: $surface;
        border: round $primary;
        width: 26;
        padding: 0;
    }
    ContextMenuWidget ListView {
        border: none;
        padding: 0;
        background: $surface;
        height: auto;
    }
    ContextMenuWidget ListItem {
        padding: 0 1;
    }
    ContextMenuWidget ListItem.--highlight {
        background: $primary 40%;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._options: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        yield _MenuList(id="ctx-list")

    def show(self, options: list[tuple[str, str]], x: int, y: int) -> None:
        self._options = options
        lv = self.query_one("#ctx-list", _MenuList)
        lv.clear()
        for label, _ in options:
            lv.append(ListItem(Label(label)))
        n = len(options)
        h = n + 2
        lv.styles.height = h
        self.styles.height = h
        sw, sh = self.app.size.width, self.app.size.height
        self.styles.offset = (min(x, max(0, sw - 28)), min(y, max(0, sh - h - 2)))
        # Show backdrop first (behind menu in DOM order)
        try:
            self.screen.query_one(_ContextBackdrop).display = True
        except Exception:
            pass
        self.display = True
        lv.index = 0
        lv.focus()

    def hide(self) -> None:
        self.display = False
        try:
            self.screen.query_one(_ContextBackdrop).display = False
        except Exception:
            pass
        try:
            self.screen.query_one("#sessions", ListView).focus()
        except Exception:
            pass

    @on(ListView.Selected, "#ctx-list")
    def _item_selected(self, event: ListView.Selected) -> None:
        idx = self.query_one("#ctx-list", ListView).index
        self.hide()
        if idx is not None and 0 <= idx < len(self._options):
            self.post_message(self.Chosen(self._options[idx][1]))


# ── Session preview + confirmation screen ────────────────────────────────────


class SessionPreviewScreen(Screen[None]):
    BINDINGS = [
        Binding("enter", "confirm", "Resume", show=True),
        Binding("escape", "cancel", "Back", show=True),
        Binding("ctrl+f", "focus_find", "Find text", show=True),
        Binding("ctrl+i", "cycle_case_mode", "Case mode", show=False),
        Binding("ctrl+g", "toggle_regex", "Regex", show=False),
        Binding("alt+c", "cycle_case_mode", "Case mode", show=True),
        Binding("alt+r", "toggle_regex", "Regex", show=True),
        Binding("ctrl+c", "quit_app", "Quit", show=False),
    ]

    DEFAULT_CSS = """
    SessionPreviewScreen {
        background: $surface;
    }
    #session-title {
        height: 3;
        background: $panel;
        border-bottom: solid $panel-darken-1;
        padding: 0 2;
        content-align: left middle;
    }
    #find-bar {
        height: 3;
        border-bottom: solid $panel-darken-1;
    }
    #find-input {
        width: 1fr;
        height: 3;
        border: tall $primary 40%;
        background: $panel-darken-2;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }
    #find-input:focus {
        border: tall $primary;
        background: $boost;
        color: $text;
    }
    #match-info {
        width: 20;
        height: 3;
        content-align: center middle;
        color: $text-muted;
        background: $panel;
        border-left: solid $panel-darken-1;
    }
    #preview-scroll {
        height: 1fr;
    }
    #preview {
        padding: 1 2;
    }
    """

    def __init__(self, session: Session, search_term: str = "") -> None:
        super().__init__()
        self._session = session
        self._initial_search = search_term
        self._raw_text: str = ""
        self._matches: list[int] = []
        self._match_idx: int = 0
        self._search_term: str = search_term
        self._case_modes = ["smart", "ignore", "match"]
        self._case_mode_idx: int = 0
        self._regex_mode: bool = True

    @property
    def _case_mode(self) -> str:
        return self._case_modes[self._case_mode_idx]

    def _flags_for(self, term: str) -> int:
        if self._case_mode == "match":
            return 0
        if self._case_mode == "ignore":
            return re.IGNORECASE
        return 0 if any(ch.isupper() for ch in term) else re.IGNORECASE

    def compose(self) -> ComposeResult:
        s = self._session
        label = s.name if s.name else (s.first_msg[:70] if s.first_msg else s.sid[:24])
        yield Header(show_clock=False)
        yield Label(
            f"  {escape(label)}   [dim]{s.age}  {s.msgs} msgs  {escape(s.project_path)}[/dim]",
            id="session-title",
        )
        with Horizontal(id="find-bar"):
            yield Input(
                value=self._initial_search,
                placeholder="Find in preview (regex). Ctrl+I toggles case; n/N jumps.",
                id="find-input",
            )
            yield Label("", id="match-info")
        with VerticalScroll(id="preview-scroll"):
            yield Static("Loading…", id="preview")
        yield Footer()

    def on_mount(self) -> None:
        self.app.sub_title = f"preview — {self._session.display_label}"
        self._load_preview()
        self.query_one("#preview-scroll", VerticalScroll).focus()

    @work(thread=True)
    def _load_preview(self) -> None:
        try:
            text = preview_session(self._session.sid)
        except Exception as e:
            text = f"*Error: {e}*"
        self._raw_text = text
        self.app.call_from_thread(self._render_preview)

    def _render_preview(self) -> None:
        term = self._search_term
        preview = self.query_one("#preview", Static)
        mi = self.query_one("#match-info", Label)

        if not term:
            self._matches = []
            preview.update(RichMarkdown(self._raw_text))
            case_indicator = f"[dim]case:{self._case_mode}[/dim]"
            regex_indicator = (
                "[dim]regex[/dim]" if self._regex_mode else "[dim]literal[/dim]"
            )
            mi.update(
                f"[dim]Ctrl+F find • Ctrl+I/Alt+C case • Ctrl+G/Alt+R regex • n/N next[/dim]  {case_indicator} {regex_indicator}"
            )
            return

        flags = self._flags_for(term)
        if self._regex_mode:
            try:
                pattern = re.compile(term, flags)
            except re.error:
                pattern = re.compile(re.escape(term), flags)
        else:
            pattern = re.compile(re.escape(term), flags)

        lines = self._raw_text.split("\n")
        rt = RichText()
        match_lines: list[int] = []

        for i, line in enumerate(lines):
            if pattern.search(line):
                match_lines.append(i)
                pos = 0
                for m in pattern.finditer(line):
                    rt.append(line[pos : m.start()])
                    rt.append(m.group(), style="bold black on yellow")
                    pos = m.end()
                rt.append(line[pos:])
            else:
                rt.append(line)
            if i < len(lines) - 1:
                rt.append("\n")

        self._matches = match_lines
        preview.update(rt)

        if match_lines:
            self._match_idx = min(self._match_idx, len(match_lines) - 1)
            case_indicator = f"[dim]{self._case_mode}[/dim] "
            regex_indicator = (
                "[dim]re[/dim] " if self._regex_mode else "[dim]lit[/dim] "
            )
            mi.update(
                f"{case_indicator}{regex_indicator}[dim]{self._match_idx + 1}/{len(match_lines)}[/dim]"
            )
            self._scroll_to_line(match_lines[self._match_idx])
        else:
            case_indicator = f"[dim]{self._case_mode}[/dim] "
            regex_indicator = (
                "[dim]re[/dim] " if self._regex_mode else "[dim]lit[/dim] "
            )
            mi.update(f"{case_indicator}{regex_indicator}[dim]no match[/dim]")

    def _scroll_to_line(self, line_idx: int) -> None:
        self.query_one("#preview-scroll", VerticalScroll).scroll_to(
            y=line_idx, animate=True
        )

    def _step_match(self, delta: int) -> None:
        if not self._matches:
            return
        self._match_idx = (self._match_idx + delta) % len(self._matches)
        mi = self.query_one("#match-info", Label)
        case_indicator = f"[dim]{self._case_mode}[/dim] "
        regex_indicator = "[dim]re[/dim] " if self._regex_mode else "[dim]lit[/dim] "
        mi.update(
            f"{case_indicator}{regex_indicator}[dim]{self._match_idx + 1}/{len(self._matches)}[/dim]"
        )
        self._scroll_to_line(self._matches[self._match_idx])

    @on(Input.Changed, "#find-input")
    def _find_changed(self, event: Input.Changed) -> None:
        self._search_term = event.value
        self._match_idx = 0
        if self._raw_text:
            self._render_preview()

    def on_key(self, event) -> None:
        # n/N navigate only when find input does NOT have focus
        if self.focused is not self.query_one("#find-input", Input):
            if event.key == "n":
                self._step_match(1)
                event.stop()
            elif event.key == "N":
                self._step_match(-1)
                event.stop()

    def action_focus_find(self) -> None:
        self.query_one("#find-input", Input).focus()

    def action_cycle_case_mode(self) -> None:
        self._case_mode_idx = (self._case_mode_idx + 1) % len(self._case_modes)
        self.notify(f"Find case mode: {self._case_mode}", timeout=1.4)
        if self._raw_text:
            self._render_preview()

    def action_toggle_regex(self) -> None:
        self._regex_mode = not self._regex_mode
        mode = "regex" if self._regex_mode else "literal"
        self.notify(f"Find mode: {mode}", timeout=1.4)
        if self._raw_text:
            self._render_preview()

    def action_confirm(self) -> None:
        self.app.exit(("resume", self._session.sid))

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def action_quit_app(self) -> None:
        self.app.exit(None)


# ── Directory picker screen ───────────────────────────────────────────────────


class DirectoryPickerScreen(Screen[None]):
    """Pick a project directory to filter sessions by — yazi/lazyvim style."""

    BINDINGS = [
        Binding("escape", "show_all", "All projects", show=True),
        Binding("ctrl+c", "quit_app", "Quit", show=False),
    ]

    DEFAULT_CSS = """
    DirectoryPickerScreen {
        background: $surface;
    }
    #dir-filter {
        height: 3;
        border: none;
        border-bottom: solid $panel-darken-1;
        background: $boost;
        color: $foreground;
        padding: 0 1;
    }
    #dir-list {
        height: 1fr;
    }
    """

    def __init__(self, cwd: str) -> None:
        super().__init__()
        self._cwd = cwd
        self._all_dirs: list[tuple[str, str, int]] = []  # (display, pid, count)
        self._dirs: list[tuple[str, str, int]] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Input(placeholder="filter directories…", id="dir-filter")
        yield ListView(id="dir-list")
        yield Footer()

    def on_mount(self) -> None:
        self.app.sub_title = "pick directory"
        self._build_dirs()
        self.query_one("#dir-filter", Input).focus()

    def _build_dirs(self) -> None:
        sessions = list_sessions(cwd=self._cwd, all_projects=True)
        counts: dict[str, int] = {}
        for s in sessions:
            counts[s.project_id] = counts.get(s.project_id, 0) + 1
        self._all_dirs = sorted(
            [(pid_to_path(pid), pid, cnt) for pid, cnt in counts.items()],
            key=lambda x: x[0],
        )
        self._dirs = self._all_dirs[:]
        self._render_list()

    def _render_list(self) -> None:
        lv = self.query_one("#dir-list", ListView)
        lv.clear()
        for display, _, count in self._dirs:
            lv.append(
                ListItem(
                    Label(
                        f"{escape(display)}  [dim]{count} session{'s' if count != 1 else ''}[/dim]"
                    )
                )
            )

    @on(Input.Changed, "#dir-filter")
    def _filter(self, event: Input.Changed) -> None:
        q = event.value.lower()
        self._dirs = (
            [(d, p, c) for d, p, c in self._all_dirs if q in d.lower()]
            if q
            else self._all_dirs[:]
        )
        self._render_list()

    @on(Input.Submitted, "#dir-filter")
    def _submit(self, event: Input.Submitted) -> None:
        lv = self.query_one("#dir-list", ListView)
        if lv.index is not None:
            self._select(lv.index)
        elif self._dirs:
            self._select(0)

    @on(ListView.Selected, "#dir-list")
    def _selected(self, event: ListView.Selected) -> None:
        idx = self.query_one("#dir-list", ListView).index
        if idx is not None:
            self._select(idx)

    def on_key(self, event) -> None:
        fi = self.query_one("#dir-filter", Input)
        if self.focused is fi:
            if event.key == "down":
                self.query_one("#dir-list", ListView).action_cursor_down()
                event.prevent_default()
            elif event.key == "up":
                self.query_one("#dir-list", ListView).action_cursor_up()
                event.prevent_default()

    def _select(self, idx: int) -> None:
        if 0 <= idx < len(self._dirs):
            _, pid, _ = self._dirs[idx]
            cwd = ("/" + pid[1:].replace("-", "/")) if pid.startswith("-") else pid
            self.app.switch_screen(BrowseScreen(all_projects=False, cwd=cwd))

    def action_show_all(self) -> None:
        self.app.switch_screen(BrowseScreen(all_projects=True, cwd=self._cwd))

    def action_quit_app(self) -> None:
        self.app.exit(None)


# ── Shared layout CSS ─────────────────────────────────────────────────────────

_SPLIT_CSS = """
#main {
    height: 1fr;
}
#left {
    width: 50%;
    border-right: solid $panel-darken-1;
    layout: vertical;
}
#sessions {
    height: 1fr;
    border: none;
}
#right {
    width: 50%;
}
#preview-scroll {
    height: 1fr;
}
#preview {
    padding: 0 1;
}
"""

_SORT_CYCLE = ["folder_asc", "folder_desc", "recent", "oldest"]
_SORT_LABEL = {
    "folder_asc": "↓ Folder",
    "folder_desc": "↑ Folder",
    "recent": "↓ Recent",
    "oldest": "↑ Oldest",
}


# ── Browse screen ─────────────────────────────────────────────────────────────


class BrowseScreen(Screen[None]):
    """Main session list — vim-style / filter, ctrl+a directory picker."""

    BINDINGS = [
        Binding("ctrl+d", "trash_session", "Trash", show=True),
        Binding("ctrl+r", "rename_session", "Rename", show=True),
        Binding("ctrl+t", "open_trash", "Trash bin", show=True),
        Binding("ctrl+a", "toggle_all", "Dir filter", show=True),
        Binding("ctrl+s", "cycle_sort", "Sort", show=True),
        Binding("ctrl+underscore", "content_search", "Search", show=True),
        Binding("ctrl+slash", "content_search", "Search", show=False),
        Binding("ctrl+n", "new_session", "New", show=True),
        Binding("ctrl+c", "quit_app", "Quit", show=False),
        Binding("escape", "quit_app", "Quit", show=False),
        Binding("/", "start_filter", "Filter", show=True),
    ]

    DEFAULT_CSS = (
        _SPLIT_CSS
        + """
    BrowseScreen {
        layers: base backdrop overlay;
    }
    #filter-bar {
        height: 3;
        border-top: solid $panel-darken-1;
        display: none;
    }
    #filter-bar FilterInput {
        height: 3;
        border: none;
        background: $boost;
        color: $foreground;
        padding: 0 1;
    }
    #filter-label {
        width: 3;
        height: 3;
        content-align: center middle;
        color: $text-muted;
        background: $panel;
    }
    """
    )

    def __init__(self, all_projects: bool = True, cwd: Optional[str] = None) -> None:
        super().__init__()
        self._all_projects = all_projects
        self._cwd = cwd or os.getcwd()
        self._sessions: list[Session] = []
        self._filtered: list[Session] = []
        self._preview_timer: Optional[Timer] = None
        self._sort: str = "folder_asc"
        self._ctx_session: Optional[Session] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield ListView(id="sessions")
                with Horizontal(id="filter-bar"):
                    yield Label("/", id="filter-label")
                    yield FilterInput(placeholder="filter sessions…", id="filter")
            with VerticalScroll(id="preview-scroll"):
                yield Static("", id="preview")
        yield Footer()
        yield _ContextBackdrop()
        yield ContextMenuWidget()

    def on_mount(self) -> None:
        self._update_subtitle()
        self._load()
        self.query_one("#sessions", ListView).focus()

    def _update_subtitle(self) -> None:
        short = self._cwd.replace(str(os.path.expanduser("~")), "~")
        if self._all_projects:
            self.app.sub_title = f"all projects  {_SORT_LABEL[self._sort]}"
        else:
            self.app.sub_title = f"{short}  {_SORT_LABEL[self._sort]}"

    def _load(self) -> None:
        self._sessions = list_sessions(cwd=self._cwd, all_projects=self._all_projects)
        fi = self.query_one("#filter", FilterInput)
        self._apply_filter(fi.value)

    def _apply_filter(self, query: str) -> None:
        q = query.lower().split()
        if q:
            self._filtered = [
                s
                for s in self._sessions
                if all(
                    w in f"{s.name} {s.first_msg} {s.project_path}".lower() for w in q
                )
            ]
        else:
            self._filtered = list(self._sessions)

        if self._sort == "oldest":
            self._filtered.sort(key=lambda s: s.sort_time)
        elif self._sort == "folder_asc":
            self._filtered.sort(key=lambda s: s.sort_time, reverse=True)
            self._filtered.sort(key=lambda s: s.project_path.lower())
        elif self._sort == "folder_desc":
            self._filtered.sort(key=lambda s: s.sort_time, reverse=True)
            self._filtered.sort(key=lambda s: s.project_path.lower(), reverse=True)

        lv = self.query_one("#sessions", ListView)
        lv.clear()
        for s in self._filtered:
            lv.append(SessionItem(s, show_project=self._all_projects))
        if self._filtered:
            self._load_preview(self._filtered[0].sid)
        else:
            self.query_one("#preview", Static).update("*No sessions found.*")

    def _update_preview(self, sid: str) -> None:
        if self._preview_timer is not None:
            self._preview_timer.stop()
        self._preview_timer = self.set_timer(0.08, lambda: self._load_preview(sid))

    @work(thread=True, exclusive=True)
    def _load_preview(self, sid: str) -> None:
        try:
            renderable = RichMarkdown(preview_session(sid))
        except Exception as e:
            renderable = f"*Error: {e}*"
        self.app.call_from_thread(self.query_one("#preview", Static).update, renderable)

    def _current_session(self) -> Optional[Session]:
        item = self.query_one("#sessions", ListView).highlighted_child
        return item.session if isinstance(item, SessionItem) else None

    # ── filter bar (vim-style /) ───────────────────────────────────────────────

    def action_start_filter(self) -> None:
        bar = self.query_one("#filter-bar")
        bar.display = True
        fi = self.query_one("#filter", FilterInput)
        fi.focus()

    @on(FilterInput.Cancelled)
    def _filter_cancelled(self) -> None:
        self._hide_filter()

    def _hide_filter(self) -> None:
        fi = self.query_one("#filter", FilterInput)
        fi.value = ""
        self._apply_filter("")
        self.query_one("#filter-bar").display = False
        self.query_one("#sessions", ListView).focus()

    # ── list navigation (called by FilterInput) ───────────────────────────────

    def move_list(self, direction: int) -> None:
        lv = self.query_one("#sessions", ListView)
        lv.action_cursor_down() if direction > 0 else lv.action_cursor_up()

    def activate_list(self) -> None:
        # Called by FilterInput on Enter — close filter bar then open preview
        self._hide_filter()
        s = self._current_session()
        if s:
            self.app.push_screen(SessionPreviewScreen(s))

    # ── events ───────────────────────────────────────────────────────────────

    @on(Input.Changed, "#filter")
    def _filter_changed(self, event: Input.Changed) -> None:
        self._apply_filter(event.value)

    @on(ListView.Highlighted, "#sessions")
    def _session_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, SessionItem):
            self._update_preview(event.item.session.sid)

    @on(ListView.Selected, "#sessions")
    def _session_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, SessionItem):
            self.app.push_screen(SessionPreviewScreen(event.item.session))

    # ── actions ───────────────────────────────────────────────────────────────

    def action_trash_session(self) -> None:
        s = self._current_session()
        if not s:
            return
        lv = self.query_one("#sessions", ListView)
        idx = lv.index or 0
        try:
            trash_session(s.sid)
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")
            return
        self._load()
        lv = self.query_one("#sessions", ListView)
        if self._filtered:
            lv.index = min(idx, len(self._filtered) - 1)
        self.notify("Trashed.", timeout=1)

    def action_rename_session(self) -> None:
        s = self._current_session()
        if not s:
            return

        def on_rename(new_name: Optional[str]) -> None:
            if new_name:
                pid = project_for_session(s.sid)
                if pid:
                    set_name(pid, s.sid, new_name)
                    self._load()
                    self.notify(f"Renamed: {new_name}", timeout=2)

        self.app.push_screen(InputDialog("New name:", initial=s.name), on_rename)

    def action_open_trash(self) -> None:
        self.app.push_screen(TrashScreen(cwd=self._cwd))

    def action_toggle_all(self) -> None:
        """Open directory picker (or switch back to all projects)."""
        if not self._all_projects:
            self.app.switch_screen(BrowseScreen(all_projects=True, cwd=self._cwd))
        else:
            self.app.push_screen(DirectoryPickerScreen(cwd=self._cwd))

    def action_cycle_sort(self) -> None:
        idx = _SORT_CYCLE.index(self._sort)
        self._sort = _SORT_CYCLE[(idx + 1) % len(_SORT_CYCLE)]
        self._apply_filter(self.query_one("#filter", FilterInput).value)
        self._update_subtitle()
        self.notify(f"Sort: {_SORT_LABEL[self._sort]}", timeout=1)

    def action_content_search(self) -> None:
        def on_query(query: Optional[str]) -> None:
            if query:
                self.app.push_screen(
                    ContentSearchScreen(
                        query=query,
                        all_projects=self._all_projects,
                        cwd=self._cwd,
                    )
                )

        self.app.push_screen(InputDialog("Search session content (ripgrep):"), on_query)

    def action_new_session(self) -> None:
        self.app.exit(("new",))

    def action_quit_app(self) -> None:
        self.app.exit(None)

    # ── right-click ──────────────────────────────────────────────────────────

    @on(SessionItem.RightClicked)
    def _session_right_clicked(self, event: SessionItem.RightClicked) -> None:
        self._ctx_session = event.session
        self.query_one(ContextMenuWidget).show(
            [
                ("Resume", "resume"),
                ("Rename", "rename"),
                ("Trash", "trash"),
                ("New session", "new"),
            ],
            event.x,
            event.y,
        )

    @on(ContextMenuWidget.Chosen)
    def _ctx_chosen(self, event: ContextMenuWidget.Chosen) -> None:
        s = self._ctx_session
        if not s:
            return
        if event.value == "resume":
            self.app.push_screen(SessionPreviewScreen(s))
        elif event.value == "rename":

            def on_rename(new_name: Optional[str]) -> None:
                if new_name:
                    pid = project_for_session(s.sid)
                    if pid:
                        set_name(pid, s.sid, new_name)
                        self._load()
                        self.notify(f"Renamed: {new_name}", timeout=2)

            self.app.push_screen(InputDialog("New name:", initial=s.name), on_rename)
        elif event.value == "trash":
            lv = self.query_one("#sessions", ListView)
            idx = lv.index or 0
            try:
                trash_session(s.sid)
            except Exception as e:
                self.notify(f"Error: {e}", severity="error")
                return
            self._load()
            lv = self.query_one("#sessions", ListView)
            if self._filtered:
                lv.index = min(idx, len(self._filtered) - 1)
            self.notify("Trashed.", timeout=1)
        elif event.value == "new":
            self.app.exit(("new",))


# ── Content search screen ─────────────────────────────────────────────────────


class ContentSearchScreen(Screen[None]):
    BINDINGS = [
        Binding("ctrl+d", "trash_session", "Trash", show=True),
        Binding("ctrl+underscore", "new_search", "Search", show=True),
        Binding("ctrl+slash", "new_search", "Search", show=False),
        Binding("ctrl+g", "toggle_regex", "Regex", show=False),
        Binding("ctrl+i", "toggle_case_mode", "Case mode", show=False),
        Binding("alt+r", "toggle_regex", "Regex", show=True),
        Binding("alt+c", "toggle_case_mode", "Case mode", show=True),
        Binding("ctrl+b", "back", "Back", show=True),
        Binding("ctrl+c", "quit_app", "Quit", show=False),
        Binding("escape", "back", "Back", show=False),
    ]

    DEFAULT_CSS = (
        _SPLIT_CSS
        + """
    ContentSearchScreen {
        layers: base backdrop overlay;
    }
    ContentSearchScreen #search {
        height: 3;
        border: tall $primary 40%;
        background: $panel-darken-2;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }
    ContentSearchScreen #search:focus {
        border: tall $primary;
        background: $boost;
        color: $text;
    }
    """
    )

    def __init__(
        self,
        query: str = "",
        all_projects: bool = False,
        cwd: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._query = query
        self._all_projects = all_projects
        self._cwd = cwd or os.getcwd()
        self._sessions: list[Session] = []
        self._preview_timer: Optional[Timer] = None
        self._ctx_session: Optional[Session] = None
        self._regex_mode: bool = True
        self._case_modes = ["smart", "ignore", "match"]
        self._case_mode_idx: int = 0

    @property
    def _case_mode(self) -> str:
        return self._case_modes[self._case_mode_idx]

    def _search_mode_label(self) -> str:
        regex = "regex" if self._regex_mode else "literal"
        return f"{regex} • case:{self._case_mode}"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield FilterInput(
                    value=self._query,
                    placeholder="rg query — Enter search • Ctrl+G/Alt+R regex • Ctrl+I/Alt+C case",
                    id="search",
                )
                yield ListView(id="sessions")
            with VerticalScroll(id="preview-scroll"):
                yield Static("", id="preview")
        yield Footer()
        yield _ContextBackdrop()
        yield ContextMenuWidget()

    def on_mount(self) -> None:
        self.app.sub_title = f"search: {self._query}"
        if self._query:
            self._run_search(self._query)
        self.query_one("#search", FilterInput).focus()

    def _run_search(self, query: str) -> None:
        self._query = query
        self._sessions = search_sessions(
            query,
            cwd=self._cwd,
            all_projects=self._all_projects,
            use_regex=self._regex_mode,
            case_mode=self._case_mode,
        )
        lv = self.query_one("#sessions", ListView)
        lv.clear()
        for s in self._sessions:
            lv.append(SessionItem(s, show_project=self._all_projects))
        if self._sessions:
            self._load_preview(self._sessions[0].sid)
        else:
            self.query_one("#preview", Static).update(f"*No results for: {query}*")
        self.app.sub_title = f"search: {query}  ({self._search_mode_label()})"

    def _current_session(self) -> Optional[Session]:
        item = self.query_one("#sessions", ListView).highlighted_child
        return item.session if isinstance(item, SessionItem) else None

    def move_list(self, direction: int) -> None:
        lv = self.query_one("#sessions", ListView)
        lv.action_cursor_down() if direction > 0 else lv.action_cursor_up()

    def activate_list(self) -> None:
        fi = self.query_one("#search", FilterInput)
        if self.focused is fi:
            query = fi.value.strip()
            if query:
                self._run_search(query)
            self.query_one("#sessions", ListView).focus()
            return
        s = self._current_session()
        if s:
            self.app.push_screen(SessionPreviewScreen(s, search_term=self._query))

    @on(FilterInput.Cancelled)
    def _search_cancelled(self) -> None:
        self.action_back()

    def _update_preview(self, sid: str) -> None:
        if self._preview_timer is not None:
            self._preview_timer.stop()
        self._preview_timer = self.set_timer(0.08, lambda: self._load_preview(sid))

    @work(thread=True, exclusive=True)
    def _load_preview(self, sid: str) -> None:
        try:
            renderable = RichMarkdown(preview_session(sid))
        except Exception as e:
            renderable = f"*Error: {e}*"
        self.app.call_from_thread(self.query_one("#preview", Static).update, renderable)

    @on(Input.Submitted, "#search")
    def _search_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self._run_search(query)
        self.query_one("#sessions", ListView).focus()

    @on(ListView.Highlighted, "#sessions")
    def _session_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, SessionItem):
            self._update_preview(event.item.session.sid)

    @on(ListView.Selected, "#sessions")
    def _session_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, SessionItem):
            # Pass ripgrep query as initial search term in preview
            self.app.push_screen(
                SessionPreviewScreen(event.item.session, search_term=self._query)
            )

    def action_trash_session(self) -> None:
        s = self._current_session()
        if not s:
            return
        lv = self.query_one("#sessions", ListView)
        idx = lv.index or 0
        try:
            trash_session(s.sid)
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")
            return
        self._run_search(self.query_one("#search", FilterInput).value)
        lv = self.query_one("#sessions", ListView)
        if self._sessions:
            lv.index = min(idx, len(self._sessions) - 1)
        self.notify("Trashed.", timeout=1)

    def action_new_search(self) -> None:
        self.query_one("#search", FilterInput).focus()

    def action_toggle_regex(self) -> None:
        self._regex_mode = not self._regex_mode
        mode = "regex" if self._regex_mode else "literal"
        self.notify(f"Search mode: {mode}", timeout=1.4)
        q = self.query_one("#search", FilterInput).value.strip()
        if q:
            self._run_search(q)

    def action_toggle_case_mode(self) -> None:
        self._case_mode_idx = (self._case_mode_idx + 1) % len(self._case_modes)
        self.notify(f"Search case mode: {self._case_mode}", timeout=1.4)
        q = self.query_one("#search", FilterInput).value.strip()
        if q:
            self._run_search(q)

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_quit_app(self) -> None:
        self.app.exit(None)

    @on(SessionItem.RightClicked)
    def _session_right_clicked(self, event: SessionItem.RightClicked) -> None:
        self._ctx_session = event.session
        self.query_one(ContextMenuWidget).show(
            [("Resume", "resume"), ("Trash", "trash")],
            event.x,
            event.y,
        )

    @on(ContextMenuWidget.Chosen)
    def _ctx_chosen(self, event: ContextMenuWidget.Chosen) -> None:
        s = self._ctx_session
        if not s:
            return
        if event.value == "resume":
            self.app.push_screen(SessionPreviewScreen(s, search_term=self._query))
        elif event.value == "trash":
            lv = self.query_one("#sessions", ListView)
            idx = lv.index or 0
            try:
                trash_session(s.sid)
            except Exception as e:
                self.notify(f"Error: {e}", severity="error")
                return
            self._run_search(self.query_one("#search", FilterInput).value)
            lv = self.query_one("#sessions", ListView)
            if self._sessions:
                lv.index = min(idx, len(self._sessions) - 1)
            self.notify("Trashed.", timeout=1)


# ── Trash screen ──────────────────────────────────────────────────────────────


class TrashScreen(Screen[None]):
    BINDINGS = [
        Binding("ctrl+d", "delete_forever", "Delete forever", show=True),
        Binding("ctrl+e", "empty_all", "Empty trash", show=True),
        Binding("ctrl+b", "back", "Back", show=True),
        Binding("ctrl+c", "quit_app", "Quit", show=False),
        Binding("escape", "back", "Back", show=False),
    ]

    DEFAULT_CSS = (
        _SPLIT_CSS
        + """
    TrashScreen {
        layers: base backdrop overlay;
    }
    """
    )

    def __init__(self, cwd: Optional[str] = None) -> None:
        super().__init__()
        self._cwd = cwd or os.getcwd()
        self._entries: list[TrashEntry] = []
        self._preview_timer: Optional[Timer] = None
        self._ctx_entry: Optional[TrashEntry] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield ListView(id="sessions")
            with VerticalScroll(id="preview-scroll"):
                yield Static("", id="preview")
        yield Footer()
        yield _ContextBackdrop()
        yield ContextMenuWidget()

    def on_mount(self) -> None:
        self.app.sub_title = "trash"
        self._load()
        self.query_one("#sessions", ListView).focus()

    def _load(self) -> None:
        self._entries = list_trash()
        lv = self.query_one("#sessions", ListView)
        lv.clear()
        for e in self._entries:
            lv.append(TrashItem(e))
        if self._entries:
            self._load_preview(self._entries[0].sid)
        else:
            self.query_one("#preview", Static).update("*Trash is empty.*")

    def _current_entry(self) -> Optional[TrashEntry]:
        item = self.query_one("#sessions", ListView).highlighted_child
        return item.entry if isinstance(item, TrashItem) else None

    def _update_preview(self, sid: str) -> None:
        if self._preview_timer is not None:
            self._preview_timer.stop()
        self._preview_timer = self.set_timer(0.08, lambda: self._load_preview(sid))

    @work(thread=True, exclusive=True)
    def _load_preview(self, sid: str) -> None:
        try:
            renderable = RichMarkdown(preview_session(sid))
        except Exception as e:
            renderable = f"*Error: {e}*"
        self.app.call_from_thread(self.query_one("#preview", Static).update, renderable)

    @on(ListView.Highlighted, "#sessions")
    def _entry_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, TrashItem):
            self._update_preview(event.item.entry.sid)

    @on(ListView.Selected, "#sessions")
    def _entry_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, TrashItem):
            self._do_restore(event.item.entry)

    def _do_restore(self, entry: TrashEntry) -> None:
        lv = self.query_one("#sessions", ListView)
        idx = lv.index or 0
        try:
            restore_session(entry.sid)
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")
            return
        self._load()
        lv = self.query_one("#sessions", ListView)
        if self._entries:
            lv.index = min(idx, len(self._entries) - 1)
        self.notify(f"Restored: {entry.name or entry.sid[:16]}", timeout=2)

    def _do_delete(self, e: TrashEntry) -> None:
        def on_confirm(yes: bool | None) -> None:
            if yes:
                from .backend import TRASH_DIR

                (TRASH_DIR / f"{e.sid}.jsonl").unlink(missing_ok=True)
                (TRASH_DIR / f"{e.sid}.meta").unlink(missing_ok=True)
                self._load()
                self.notify("Deleted.", timeout=1)

        self.app.push_screen(
            ConfirmDialog(f"Permanently delete '{e.name or e.sid[:20]}'?"),
            on_confirm,
        )

    def action_delete_forever(self) -> None:
        e = self._current_entry()
        if e:
            self._do_delete(e)

    def action_empty_all(self) -> None:
        if not self._entries:
            self.notify("Trash is already empty.", timeout=2)
            return

        def on_confirm(yes: bool | None) -> None:
            if yes:
                n = empty_trash()
                self._load()
                self.notify(f"Emptied {n} session(s).", timeout=2)

        self.app.push_screen(
            ConfirmDialog(
                f"Permanently delete all {len(self._entries)} trashed session(s)?"
            ),
            on_confirm,
        )

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_quit_app(self) -> None:
        self.app.exit(None)

    @on(TrashItem.RightClicked)
    def _trash_right_clicked(self, event: TrashItem.RightClicked) -> None:
        self._ctx_entry = event.entry
        self.query_one(ContextMenuWidget).show(
            [
                ("Restore", "restore"),
                ("Delete forever", "delete"),
                ("Empty trash", "empty"),
            ],
            event.x,
            event.y,
        )

    @on(ContextMenuWidget.Chosen)
    def _ctx_chosen(self, event: ContextMenuWidget.Chosen) -> None:
        e = self._ctx_entry
        if not e:
            return
        if event.value == "restore":
            self._do_restore(e)
        elif event.value == "delete":
            self._do_delete(e)
        elif event.value == "empty":
            self.action_empty_all()


# ── App ───────────────────────────────────────────────────────────────────────


class ClaudetreeApp(App[tuple[str, str] | tuple[str] | None]):
    TITLE = "claudetree"
    CSS = """
    /* NOTE: do NOT set 'layers' on Screen globally — it breaks Screen.render()
       in Textual 8.x. Each screen that uses ContextMenuWidget declares its own
       'layers: base backdrop overlay' in its DEFAULT_CSS.
       backdrop < overlay ensures the dim layer never covers the menu. */

    Screen {
        background: $surface;
    }
    Header {
        background: $primary-darken-2;
    }

    /* Fix invisible input text across all terminal themes.
       Uses Textual 8.x variables: $boost (elevated surface), $foreground (text),
       $border / $border-blurred (border colors). */
    Input {
        background: $panel-darken-2;
        color: $text;
        border: tall $primary 40%;
        padding: 0 1;
    }
    Input:focus {
        background: $boost;
        color: $text;
        border: tall $primary;
    }
    Input > .input--value {
        color: $text;
        text-style: bold;
    }
    Input > .input--cursor {
        background: $input-cursor-background;
        color: $input-cursor-foreground;
        text-style: $input-cursor-text-style;
    }
    Input > .input--placeholder {
        color: $text-disabled;
    }
    Input > .input--suggestion {
        color: $text-disabled;
    }
    Input:ansi {
        background: ansi_default;
        color: ansi_white;
        border: tall ansi_bright_black;
    }
    Input:ansi:focus {
        background: ansi_default;
        color: ansi_white;
        border: tall ansi_white;
    }
    Input:ansi > .input--cursor {
        background: ansi_white;
        color: ansi_black;
    }
    Input:ansi > .input--selection {
        background: ansi_bright_black;
    }
    Input:ansi > .input--placeholder,
    Input:ansi > .input--suggestion {
        color: ansi_bright_black;
    }

    #filter-bar FilterInput {
        border: tall $primary 40%;
        background: $panel-darken-2;
        color: $text;
        text-style: bold;
    }
    #filter-bar FilterInput:focus {
        border: tall $primary;
        background: $boost;
        color: $text;
    }
    #filter-bar FilterInput:ansi {
        border: tall ansi_bright_black;
        background: ansi_default;
        color: ansi_white;
    }
    #filter-bar FilterInput:ansi:focus {
        border: tall ansi_white;
        background: ansi_default;
        color: ansi_white;
    }
    #dir-filter {
        border: tall $primary 40%;
        background: $panel-darken-2;
        color: $text;
        text-style: bold;
    }
    #dir-filter:focus {
        border: tall $primary;
        background: $boost;
        color: $text;
    }
    #dir-filter:ansi {
        border: tall ansi_bright_black;
        background: ansi_default;
        color: ansi_white;
    }
    #dir-filter:ansi:focus {
        border: tall ansi_white;
        background: ansi_default;
        color: ansi_white;
    }
    
    ListView > ListItem {
        padding: 0 1;
    }
    ListView > ListItem.--highlight {
        background: $primary 30%;
    }
    """

    def __init__(
        self,
        initial_screen: str = "browse",
        all_projects: bool = True,
        cwd: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._initial_screen = initial_screen
        self._all_projects = all_projects
        self._cwd = cwd or os.getcwd()

    def on_mount(self) -> None:
        self.register_theme(
            Theme(
                name="monokai",
                primary="#A6E22E",
                secondary="#F92672",
                accent="#E6DB74",
                foreground="#F8F8F2",
                background="#272822",
                success="#A6E22E",
                warning="#E6DB74",
                error="#F92672",
                surface="#272822",
                panel="#272822",
                dark=True,
            )
        )
        self.theme = "monokai"
        if self._initial_screen == "trash":
            self.push_screen(TrashScreen(cwd=self._cwd))
        else:
            self.push_screen(
                BrowseScreen(all_projects=self._all_projects, cwd=self._cwd)
            )
