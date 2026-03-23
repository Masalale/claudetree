"""Claudetree Textual TUI — lazygit-style Claude Code session manager."""
from __future__ import annotations

import os
from typing import Optional

from rich.markup import escape
from rich.markdown import Markdown as RichMarkdown
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static

from .backend import (
    Session,
    TrashEntry,
    empty_trash,
    list_sessions,
    list_trash,
    preview_session,
    project_for_session,
    restore_session,
    search_sessions,
    set_name,
    trash_session,
)


# ── Custom list items ────────────────────────────────────────────────────────

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
        markup = f"[dim]{s.age:>4}  {s.msgs:>3}msgs[/dim]  "
        if s.name:
            markup += f"[bold cyan]{escape(s.name)}[/bold cyan]"
            if s.first_msg:
                markup += f"  [dim]{escape(s.first_msg[:35])}[/dim]"
        else:
            markup += escape(s.first_msg[:50] if s.first_msg else s.sid[:24])
        if self._show_project and s.project_id:
            markup += f"  [dim]{escape(s.project_path)}[/dim]"
        yield Label(markup)

    def on_mouse_down(self, event) -> None:
        if event.button == 3:
            abs_x = self.region.x + event.x
            abs_y = self.region.y + event.y
            self.post_message(self.RightClicked(self.session, abs_x, abs_y))
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
        name = escape(e.name or e.sid[:24] + "...")
        markup = (
            f"[dim]{e.when:>8}[/dim]  "
            f"[bold red]{name}[/bold red]  "
            f"[dim]{escape(e.project_path)}[/dim]"
        )
        yield Label(markup)

    def on_mouse_down(self, event) -> None:
        if event.button == 3:
            abs_x = self.region.x + event.x
            abs_y = self.region.y + event.y
            self.post_message(self.RightClicked(self.entry, abs_x, abs_y))
            event.stop()


# ── Shared filter input that passes arrow keys to the list ──────────────────

class FilterInput(Input):
    """Input that forwards ↑↓, Enter, and screen bindings to the parent screen."""

    # Override Input's built-in ctrl+a (select-all) so it reaches the screen.
    BINDINGS = [
        Binding("ctrl+a", "route_toggle_all", show=False),
    ]

    # Other keys that must reach the screen even when Input has focus.
    _PASSTHROUGH = {"ctrl+d", "ctrl+r", "ctrl+t", "ctrl+n",
                    "ctrl+underscore", "ctrl+slash", "ctrl+b"}

    def action_route_toggle_all(self) -> None:
        if hasattr(self.screen, "action_toggle_all"):
            self.screen.action_toggle_all()

    def on_key(self, event) -> None:
        if event.key == "down":
            self.screen.move_list(1)
            event.prevent_default()
        elif event.key == "up":
            self.screen.move_list(-1)
            event.prevent_default()
        elif event.key == "enter":
            self.screen.activate_list()
            event.prevent_default()
        elif event.key in self._PASSTHROUGH:
            # Let the event bubble up to the screen's binding handlers
            event.stop(False)


# ── Modal dialogs ────────────────────────────────────────────────────────────

class InputDialog(ModalScreen):
    """Single-input prompt dialog. Dismisses with the entered string or None."""

    DEFAULT_CSS = """
    InputDialog {
        align: center middle;
    }
    #dialog {
        width: 60;
        height: 7;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    #dialog Label {
        margin-bottom: 1;
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
            self.dismiss(None)


class ConfirmDialog(ModalScreen):
    """Yes/No confirmation dialog."""

    DEFAULT_CSS = """
    ConfirmDialog {
        align: center middle;
    }
    #dialog {
        width: 54;
        height: 7;
        border: round $warning;
        background: $surface;
        padding: 1 2;
    }
    #dialog Label {
        margin-bottom: 1;
    }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"{escape(self._message)}")
            yield Input(placeholder="y to confirm, any other key cancels", id="confirm-input")

    def on_mount(self) -> None:
        self.query_one("#confirm-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip().lower() == "y")

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(False)


# ── Context menu ─────────────────────────────────────────────────────────────

class _MenuList(ListView):
    """ListView that forwards escape up to the parent ContextMenu."""
    def on_key(self, event) -> None:
        if event.key == "escape":
            self.screen.dismiss(None)
            event.stop()


class ContextMenu(ModalScreen):
    """Floating right-click context menu."""

    BINDINGS = [Binding("escape", "dismiss_menu", show=False)]

    DEFAULT_CSS = """
    ContextMenu {
        background: transparent;
        align: left top;
    }
    #menu-list {
        background: $surface;
        border: round $primary;
        width: 28;
    }
    #menu-list > ListItem {
        padding: 0 1;
    }
    """

    def __init__(self, options: list[tuple[str, str]], x: int = 2, y: int = 2) -> None:
        super().__init__()
        self._options = options
        self._x = x
        self._y = y

    def compose(self) -> ComposeResult:
        with _MenuList(id="menu-list"):
            for label, _ in self._options:
                yield ListItem(Label(label))

    def on_mount(self) -> None:
        menu = self.query_one("#menu-list")
        # Set explicit height so layout doesn't collapse
        menu.styles.height = len(self._options) + 2
        sw, sh = self.app.size.width, self.app.size.height
        x = min(self._x, max(0, sw - 30))
        y = min(self._y, max(0, sh - len(self._options) - 4))
        menu.styles.offset = (x, y)
        menu.focus()

    @on(ListView.Selected, "#menu-list")
    def menu_selected(self, event: ListView.Selected) -> None:
        idx = self.query_one("#menu-list", ListView).index
        if idx is not None and 0 <= idx < len(self._options):
            self.dismiss(self._options[idx][1])
        else:
            self.dismiss(None)

    def action_dismiss_menu(self) -> None:
        self.dismiss(None)


# ── Shared CSS for split-pane screens ────────────────────────────────────────

_SPLIT_CSS = """
#main {
    height: 1fr;
}
#left {
    width: 45%;
    border-right: solid $panel-darken-1;
    layout: vertical;
}
#sessions {
    height: 1fr;
    border: none;
}
#right {
    width: 55%;
}
#preview-scroll {
    height: 1fr;
}
#preview {
    padding: 0 1;
}
"""


# ── Browse screen ────────────────────────────────────────────────────────────

class BrowseScreen(Screen):
    BINDINGS = [
        Binding("ctrl+d",           "trash_session",   "Trash",       show=True),
        Binding("ctrl+r",           "rename_session",  "Rename",      show=True),
        Binding("ctrl+t",           "open_trash",      "Trash bin",   show=True),
        Binding("ctrl+a",           "toggle_all",      "Dir filter",  show=True),
        Binding("ctrl+underscore",  "content_search",  "Search",      show=True),
        Binding("ctrl+slash",       "content_search",  "Search",      show=False),
        Binding("ctrl+n",           "new_session",     "New",         show=True),
        Binding("escape",           "quit_app",        "Quit",        show=False),
    ]

    DEFAULT_CSS = _SPLIT_CSS + """
    BrowseScreen #filter {
        height: 3;
        border: none;
        border-bottom: solid $panel-darken-1;
    }
    """

    def __init__(self, all_projects: bool = True, cwd: Optional[str] = None) -> None:
        super().__init__()
        self._all_projects = all_projects
        self._cwd = cwd or os.getcwd()
        self._sessions: list[Session] = []
        self._filtered: list[Session] = []
        self._preview_timer: Optional[Timer] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield FilterInput(placeholder="filter sessions...", id="filter")
                yield ListView(id="sessions")
            with VerticalScroll(id="preview-scroll"):
                yield Static("", id="preview")
        yield Footer()

    def on_mount(self) -> None:
        self._update_subtitle()
        self._load()
        self.query_one("#filter", FilterInput).focus()

    def _update_subtitle(self) -> None:
        short_cwd = self._cwd.replace(str(os.path.expanduser("~")), "~")
        if self._all_projects:
            self.app.sub_title = "all projects"
        else:
            self.app.sub_title = short_cwd

    def _load(self) -> None:
        self._sessions = list_sessions(cwd=self._cwd, all_projects=self._all_projects)
        filt = self.query_one("#filter", FilterInput)
        self._apply_filter(filt.value)

    def _apply_filter(self, query: str) -> None:
        q = query.lower().split()
        # Also match project path so you can filter by dir name
        if q:
            self._filtered = [
                s for s in self._sessions
                if all(w in f"{s.name} {s.first_msg} {s.project_path}".lower() for w in q)
            ]
        else:
            self._filtered = list(self._sessions)
        lv = self.query_one("#sessions", ListView)
        lv.clear()
        for s in self._filtered:
            lv.append(SessionItem(s, show_project=self._all_projects))
        if self._filtered:
            self._load_preview(self._filtered[0].sid)
        else:
            self.query_one("#preview", Static).update("*No sessions found.*")

    def _update_preview(self, sid: str) -> None:
        """Debounced preview update — waits 80 ms before loading."""
        if self._preview_timer is not None:
            self._preview_timer.stop()
        self._preview_timer = self.set_timer(0.08, lambda: self._load_preview(sid))

    @work(thread=True, exclusive=True)
    def _load_preview(self, sid: str) -> None:
        """Load and render preview in a background thread."""
        try:
            text = preview_session(sid)
            renderable = RichMarkdown(text)
        except Exception as e:
            renderable = f"*Error: {e}*"
        self.app.call_from_thread(self.query_one("#preview", Static).update, renderable)

    def _current_session(self) -> Optional[Session]:
        lv = self.query_one("#sessions", ListView)
        item = lv.highlighted_child
        if item and isinstance(item, SessionItem):
            return item.session
        return None

    # called by FilterInput
    def move_list(self, direction: int) -> None:
        lv = self.query_one("#sessions", ListView)
        if direction > 0:
            lv.action_cursor_down()
        else:
            lv.action_cursor_up()

    def activate_list(self) -> None:
        s = self._current_session()
        if s:
            self.app.exit(("resume", s.sid))

    @on(Input.Changed, "#filter")
    def filter_changed(self, event: Input.Changed) -> None:
        self._apply_filter(event.value)

    @on(ListView.Highlighted, "#sessions")
    def session_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item and isinstance(event.item, SessionItem):
            self._update_preview(event.item.session.sid)

    @on(ListView.Selected, "#sessions")
    def session_selected(self, event: ListView.Selected) -> None:
        if event.item and isinstance(event.item, SessionItem):
            self.app.exit(("resume", event.item.session.sid))

    def action_trash_session(self) -> None:
        s = self._current_session()
        if not s:
            return
        lv = self.query_one("#sessions", ListView)
        idx = lv.index or 0
        try:
            trash_session(s.sid)
        except Exception as e:
            self.notify(f"Error trashing: {e}", severity="error")
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
        # Toggle: global ↔ current-directory-only
        self.app.switch_screen(
            BrowseScreen(all_projects=not self._all_projects, cwd=self._cwd)
        )


    def action_content_search(self) -> None:
        def on_query(query: Optional[str]) -> None:
            if query:
                self.app.push_screen(
                    ContentSearchScreen(query=query, all_projects=self._all_projects, cwd=self._cwd)
                )

        self.app.push_screen(InputDialog("Search session content (ripgrep):"), on_query)

    def action_new_session(self) -> None:
        self.app.exit(("new",))

    def action_quit_app(self) -> None:
        self.app.exit(None)

    @on(SessionItem.RightClicked)
    def session_right_clicked(self, event: SessionItem.RightClicked) -> None:
        s = event.session
        options = [
            ("Resume",      "resume"),
            ("Rename",      "rename"),
            ("Trash",       "trash"),
            ("New session", "new"),
        ]

        def on_choice(value: Optional[str]) -> None:
            if value == "resume":
                self.app.exit(("resume", s.sid))
            elif value == "rename":
                def on_rename(new_name: Optional[str]) -> None:
                    if new_name:
                        pid = project_for_session(s.sid)
                        if pid:
                            set_name(pid, s.sid, new_name)
                            self._load()
                            self.notify(f"Renamed: {new_name}", timeout=2)
                self.app.push_screen(InputDialog("New name:", initial=s.name), on_rename)
            elif value == "trash":
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
            elif value == "new":
                self.app.exit(("new",))

        self.app.push_screen(ContextMenu(options, x=event.x, y=event.y), on_choice)


# ── Content search screen ────────────────────────────────────────────────────

class ContentSearchScreen(Screen):
    BINDINGS = [
        Binding("ctrl+d",          "trash_session",  "Trash",   show=True),
        Binding("ctrl+underscore", "new_search",     "Search",  show=True),
        Binding("ctrl+slash",      "new_search",     "Search",  show=False),
        Binding("ctrl+b",          "back",           "Back",    show=True),
        Binding("escape",          "back",           "Back",    show=False),
    ]

    DEFAULT_CSS = _SPLIT_CSS + """
    ContentSearchScreen #search {
        height: 3;
        border: none;
        border-bottom: solid $panel-darken-1;
    }
    """

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

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield FilterInput(value=self._query, placeholder="rg query — Enter to search", id="search")
                yield ListView(id="sessions")
            with VerticalScroll(id="preview-scroll"):
                yield Static("", id="preview")
        yield Footer()

    def on_mount(self) -> None:
        self.app.sub_title = f"search: {self._query}"
        if self._query:
            self._run_search(self._query)
        filt = self.query_one("#search", FilterInput)
        filt.focus()

    def _run_search(self, query: str) -> None:
        self._sessions = search_sessions(query, cwd=self._cwd, all_projects=self._all_projects)
        lv = self.query_one("#sessions", ListView)
        lv.clear()
        sp = self._all_projects
        for s in self._sessions:
            lv.append(SessionItem(s, show_project=sp))
        if self._sessions:
            self._load_preview(self._sessions[0].sid)
        else:
            self.query_one("#preview", Static).update(f"*No results for: {query}*")
        self.app.sub_title = f"search: {query}"

    def _current_session(self) -> Optional[Session]:
        lv = self.query_one("#sessions", ListView)
        item = lv.highlighted_child
        if item and isinstance(item, SessionItem):
            return item.session
        return None

    def move_list(self, direction: int) -> None:
        lv = self.query_one("#sessions", ListView)
        if direction > 0:
            lv.action_cursor_down()
        else:
            lv.action_cursor_up()

    def activate_list(self) -> None:
        # When the search input has focus, Enter runs the search
        fi = self.query_one("#search", FilterInput)
        if self.focused is fi:
            query = fi.value.strip()
            if query:
                self._run_search(query)
            self.query_one("#sessions", ListView).focus()
            return
        s = self._current_session()
        if s:
            self.app.exit(("resume", s.sid))

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
    def search_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self._run_search(query)
        self.query_one("#sessions", ListView).focus()

    @on(ListView.Highlighted, "#sessions")
    def session_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item and isinstance(event.item, SessionItem):
            self._update_preview(event.item.session.sid)

    @on(ListView.Selected, "#sessions")
    def session_selected(self, event: ListView.Selected) -> None:
        if event.item and isinstance(event.item, SessionItem):
            self.app.exit(("resume", event.item.session.sid))

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

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(SessionItem.RightClicked)
    def session_right_clicked(self, event: SessionItem.RightClicked) -> None:
        s = event.session
        options = [("Resume", "resume"), ("Trash", "trash")]

        def on_choice(value: Optional[str]) -> None:
            if value == "resume":
                self.app.exit(("resume", s.sid))
            elif value == "trash":
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

        self.app.push_screen(ContextMenu(options, x=event.x, y=event.y), on_choice)


# ── Trash screen ─────────────────────────────────────────────────────────────

class TrashScreen(Screen):
    BINDINGS = [
        Binding("ctrl+d", "delete_forever", "Delete forever", show=True),
        Binding("ctrl+e", "empty_all",      "Empty trash",    show=True),
        Binding("ctrl+b", "back",           "Back",           show=True),
        Binding("escape", "back",           "Back",           show=False),
    ]

    DEFAULT_CSS = _SPLIT_CSS

    def __init__(self, cwd: Optional[str] = None) -> None:
        super().__init__()
        self._cwd = cwd or os.getcwd()
        self._entries: list[TrashEntry] = []
        self._preview_timer: Optional[Timer] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield ListView(id="sessions")
            with VerticalScroll(id="preview-scroll"):
                yield Static("", id="preview")
        yield Footer()

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
        lv = self.query_one("#sessions", ListView)
        item = lv.highlighted_child
        if item and isinstance(item, TrashItem):
            return item.entry
        return None

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
    def entry_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item and isinstance(event.item, TrashItem):
            self._update_preview(event.item.entry.sid)

    @on(ListView.Selected, "#sessions")
    def entry_selected(self, event: ListView.Selected) -> None:
        """Enter key on trash item → restore it."""
        if event.item and isinstance(event.item, TrashItem):
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

    def action_delete_forever(self) -> None:
        e = self._current_entry()
        if not e:
            return

        def on_confirm(yes: bool) -> None:
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

    def action_empty_all(self) -> None:
        if not self._entries:
            self.notify("Trash is already empty.", timeout=2)
            return

        def on_confirm(yes: bool) -> None:
            if yes:
                n = empty_trash()
                self._load()
                self.notify(f"Emptied {n} session(s).", timeout=2)

        self.app.push_screen(
            ConfirmDialog(f"Permanently delete all {len(self._entries)} trashed session(s)?"),
            on_confirm,
        )

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(TrashItem.RightClicked)
    def trash_item_right_clicked(self, event: TrashItem.RightClicked) -> None:
        e = event.entry
        options = [
            ("Restore",      "restore"),
            ("Delete forever", "delete"),
            ("Empty trash",  "empty"),
        ]

        def on_choice(value: Optional[str]) -> None:
            if value == "restore":
                self._do_restore(e)
            elif value == "delete":
                def on_confirm(yes: bool) -> None:
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
            elif value == "empty":
                if not self._entries:
                    self.notify("Trash is already empty.", timeout=2)
                    return
                def on_confirm(yes: bool) -> None:
                    if yes:
                        n = empty_trash()
                        self._load()
                        self.notify(f"Emptied {n} session(s).", timeout=2)
                self.app.push_screen(
                    ConfirmDialog(f"Permanently delete all {len(self._entries)} trashed session(s)?"),
                    on_confirm,
                )

        self.app.push_screen(ContextMenu(options, x=event.x, y=event.y), on_choice)


# ── App ───────────────────────────────────────────────────────────────────────

class ClaudetreeApp(App):
    TITLE = "claudetree"
    CSS = """
    Screen {
        background: $surface;
    }
    Header {
        background: $primary-darken-2;
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
        all_projects: bool = True,   # global view by default
        cwd: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._initial_screen = initial_screen
        self._all_projects = all_projects
        self._cwd = cwd or os.getcwd()

    def on_mount(self) -> None:
        if self._initial_screen == "trash":
            self.push_screen(TrashScreen(cwd=self._cwd))
        else:
            self.push_screen(BrowseScreen(all_projects=self._all_projects, cwd=self._cwd))
