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
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static

from .backend import (
    HARNESSES,
    HARNESS_MAP,
    Session,
    TrashEntry,
    delete_trashed,
    empty_trash,
    last_scan_rows,
    list_sessions,
    list_trash,
    pid_to_path,
    preview_session,
    project_for_session,
    restore_session,
    resume_command,
    search_sessions,
    set_name,
    trash_session,
)
from .presentation import (
    CommandSpec,
    filter_commands,
    fuzzy_match,
    session_row_text,
    status_strip_text,
    trash_row_text,
)


# Side-pane previews render a capped number of turns so highlighting a huge
# session while scrolling stays instant; the full transcript opens on Enter.
_PANE_PREVIEW_TURNS = 40

# Above this size, the full-session preview renders as plain text — Rich's
# markdown parser takes >1s on very large transcripts and would freeze the UI.
_MARKDOWN_RENDER_LIMIT = 120_000


# Rendered-Text cache: (sid, width) → (source markdown, rendered Text).
_TEXT_RENDER_CACHE: dict[tuple[str, int], tuple[str, RichText]] = {}


def _preview_renderable(sid: str, md_text: str, width: int) -> RichText:
    """Markdown→Text with caching; re-renders only when content/width change."""
    key = (sid, width)
    cached = _TEXT_RENDER_CACHE.get(key)
    if cached and cached[0] == md_text:
        return cached[1]
    rendered = _markdown_to_text(md_text, width)
    if len(_TEXT_RENDER_CACHE) > 500:
        _TEXT_RENDER_CACHE.clear()
    _TEXT_RENDER_CACHE[key] = (md_text, rendered)
    return rendered


def _markdown_to_text(md_text: str, width: int) -> RichText:
    """Render markdown to a styled Text once, in the calling (worker) thread.

    Rich's Markdown re-parses the source on every __rich_console__ call and
    Textual's measure/layout invokes it several times per frame — handing the
    UI a pre-rendered Text keeps paint time flat.
    """
    from rich.console import Console

    console = Console(
        width=max(20, width),
        force_terminal=True,
        color_system="truecolor",
        no_color=False,
        highlight=False,
    )
    with console.capture() as capture:
        console.print(RichMarkdown(md_text))
    return RichText.from_ansi(capture.get())


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
        yield Static(session_row_text(self.session, show_project=self._show_project))

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
        yield Static(trash_row_text(self.entry))

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


def _session_supports_trash(session: Session) -> bool:
    harness = HARNESS_MAP.get(session.source)
    return harness.supports_trash if harness else True


def _guard_trashable_session(screen: Screen[None], session: Session) -> bool:
    harness = HARNESS_MAP.get(session.source)
    if harness and not harness.supports_trash:
        screen.notify(f"{harness.label} sessions cannot be trashed.", severity="warning")
        return False
    return True


class HarnessRailItem(ListItem):
    """Single row in the harness rail sidebar."""

    HARNESS_ALL = ""  # sentinel for the "All" row

    def __init__(self, harness_id: str, icon: str, label: str, color: str, count: int = 0) -> None:
        super().__init__()
        self.harness_id = harness_id
        self._icon = icon
        self._label = label
        self._color = color
        self._count = count

    def compose(self) -> ComposeResult:
        yield Static(self._make_text())

    def _make_text(self, compact: bool = False) -> RichText:
        text = RichText()
        style = self._color if self._color else "bold"
        if compact:
            text.append(self._icon, style=f"bold {style}")
        else:
            text.append(f"{self._icon} ", style=style)
            text.append(self._label, style=f"bold {style}")
            text.append(f"  {self._count}", style="dim")
        return text

    def set_count(self, count: int, compact: bool = False) -> None:
        self._count = count
        try:
            self.query_one(Static).update(self._make_text(compact=compact))
        except Exception:
            pass

    def refresh_label(self, compact: bool = False) -> None:
        try:
            self.query_one(Static).update(self._make_text(compact=compact))
        except Exception:
            pass


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
        "ctrl+s",
        "ctrl+underscore",
        "ctrl+slash",
        "ctrl+b",
        "ctrl+i",
        "ctrl+g",
        "ctrl+k",
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
    #dialog-input > .input--value {
        color: $text;
        text-style: bold;
    }
    #dialog-input > .input--cursor {
        background: $input-cursor-background;
        color: $input-cursor-foreground;
        text-style: $input-cursor-text-style;
    }
    #dialog-input > .input--placeholder {
        color: $text-disabled;
    }
    #dialog-input:ansi {
        background: ansi_default;
        color: ansi_white;
        border: tall ansi_bright_black;
    }
    #dialog-input:ansi:focus {
        background: ansi_default;
        color: ansi_white;
        border: tall ansi_white;
    }
    #dialog-input:ansi > .input--value {
        color: ansi_white;
    }
    #dialog-input:ansi > .input--cursor {
        background: ansi_white;
        color: ansi_black;
    }
    #dialog-input:ansi > .input--placeholder {
        color: ansi_bright_black;
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
    """Yes/No confirmation dialog with real buttons.

    y / Enter-on-Yes confirms; n / Escape cancels; arrows/Tab move focus.
    """

    BINDINGS = [
        Binding("y", "confirm", "Yes", show=False),
        Binding("n", "cancel", "No", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("left,up", "focus_previous", show=False),
        Binding("right,down", "focus_next", show=False),
    ]

    DEFAULT_CSS = """
    ConfirmDialog {
        align: center middle;
    }
    #dialog {
        width: 56;
        height: auto;
        border: heavy $warning;
        background: $panel-darken-1;
        padding: 1 2;
    }
    #confirm-message {
        width: 100%;
        margin-bottom: 1;
        color: $foreground;
    }
    #confirm-hint {
        width: 100%;
        margin-bottom: 1;
        color: $text-muted;
    }
    #confirm-buttons {
        width: 100%;
        height: auto;
        align-horizontal: right;
    }
    #confirm-buttons Button {
        margin-left: 2;
        min-width: 12;
    }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(escape(self._message), id="confirm-message")
            yield Label("y = yes · n / Esc = cancel", id="confirm-hint")
            with Horizontal(id="confirm-buttons"):
                yield Button("Cancel", id="confirm-no")
                yield Button("Delete", variant="error", id="confirm-yes")

    def on_mount(self) -> None:
        # Default focus on the safe option
        self.query_one("#confirm-no", Button).focus()

    @on(Button.Pressed, "#confirm-yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#confirm-no")
    def _no(self) -> None:
        self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class PaletteInput(Input):
    """Search input for the fuzzy command palette."""

    def on_key(self, event) -> None:
        palette = getattr(self.screen, "move_palette", None)
        accept = getattr(self.screen, "accept_palette", None)
        cancel = getattr(self.screen, "cancel_palette", None)
        if event.key == "down":
            if callable(palette):
                palette(1)
            event.prevent_default()
        elif event.key == "up":
            if callable(palette):
                palette(-1)
            event.prevent_default()
        elif event.key == "enter":
            if callable(accept):
                accept()
            event.prevent_default()
        elif event.key == "escape":
            if callable(cancel):
                cancel()
            event.stop()


class CommandPaletteScreen(ModalScreen[str | None]):
    """Fuzzy command palette for high-value actions."""

    DEFAULT_CSS = """
    CommandPaletteScreen {
        align: center middle;
        background: $background 78%;
    }
    #palette {
        width: 82;
        max-width: 96%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    #palette-title {
        height: 1;
        color: $foreground;
        text-style: bold;
        margin-bottom: 1;
    }
    #palette-input {
        height: 3;
        border: tall $primary 40%;
        background: $panel-darken-2;
        color: $text;
        text-style: bold;
        padding: 0 1;
        margin-bottom: 1;
    }
    #palette-input:focus {
        border: tall $primary;
        background: $boost;
        color: $text;
    }
    #palette-list {
        height: 11;
        border: none;
        background: $surface;
    }
    #palette-list ListItem {
        padding: 0 1;
    }
    #palette-list ListItem.--highlight {
        background: $primary 35%;
    }
    #palette-hint {
        height: 1;
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, commands: list[CommandSpec], title: str, hint: str = "") -> None:
        super().__init__()
        self._commands = commands
        self._title = title
        self._hint = hint or "Type or use arrows • Enter runs • Esc closes"
        self._visible: list[CommandSpec] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="palette"):
            yield Label(self._title, id="palette-title")
            yield PaletteInput(placeholder="Filter commands…", id="palette-input")
            yield ListView(id="palette-list")
            yield Label(self._hint, id="palette-hint")

    def on_mount(self) -> None:
        self.query_one("#palette-input", PaletteInput).focus()
        self._render_commands("")

    # NB: must not be named `_render` — that shadows Textual's Widget._render()
    def _render_commands(self, query: str) -> None:
        self._visible = filter_commands(query, self._commands)
        lv = self.query_one("#palette-list", ListView)
        lv.clear()
        if not self._visible:
            lv.append(ListItem(Label("No matches", classes="dim")))
            lv.index = None
            return
        for cmd in self._visible:
            details = cmd.key.replace("_", " ")
            if cmd.context:
                details = f"{details} · {' · '.join(cmd.context)}"
            lv.append(
                ListItem(
                    Static(
                        RichText.from_markup(
                            f"[bold]{escape(cmd.label)}[/bold] [dim]{escape(details)}[/dim]"
                        )
                    )
                )
            )
        lv.index = 0 if query.strip() else None

    def move_palette(self, direction: int) -> None:
        lv = self.query_one("#palette-list", ListView)
        if not self._visible:
            return
        if lv.index is None:
            lv.index = 0
            return
        lv.index = (lv.index + direction) % len(self._visible)

    def accept_palette(self) -> None:
        if not self._visible:
            return
        lv = self.query_one("#palette-list", ListView)
        if lv.index is None:
            return
        idx = lv.index
        idx = max(0, min(idx, len(self._visible) - 1))
        self.dismiss(self._visible[idx].key)

    def cancel_palette(self) -> None:
        self.dismiss(None)

    @on(Input.Changed, "#palette-input")
    def _changed(self, event: Input.Changed) -> None:
        self._render_commands(event.value)

    @on(Input.Submitted, "#palette-input")
    def _submitted(self, event: Input.Submitted) -> None:
        self.accept_palette()

    @on(ListView.Selected, "#palette-list")
    def _selected(self, event: ListView.Selected) -> None:
        self.accept_palette()


def run_command_palette(app, title: str, commands: list[CommandSpec], dispatch, hint: str = "Type to filter • Enter to run • Esc to cancel"):
    def _on_select(key: str | None) -> None:
        if key:
            dispatch(key)

    app.push_screen(CommandPaletteScreen(commands, title, hint), _on_select)


# ── Context menu (positioned modal) ──────────────────────────────────────────


class ContextMenuScreen(ModalScreen[str | None]):
    """Floating context menu as a modal screen.

    ModalScreen compositing keeps the underlying screen visible (dimmed);
    a full-screen widget on a layer would blank the text beneath it.
    Clicks outside the menu and Escape dismiss with None.
    """

    DEFAULT_CSS = """
    ContextMenuScreen {
        background: black 50%;
    }
    #ctx-menu {
        width: 24;
        border: heavy $primary;
        background: $panel-darken-1;
        padding: 0;
    }
    #ctx-menu ListView {
        border: none;
        padding: 0;
        background: $panel-darken-1;
        height: auto;
    }
    #ctx-menu ListItem {
        padding: 0 2;
        background: $panel-darken-1;
    }
    #ctx-menu ListItem Label {
        width: 100%;
    }
    #ctx-menu ListItem.--highlight {
        background: $primary 60%;
    }
    """

    def __init__(self, options: list[tuple[str, str]], x: int, y: int) -> None:
        super().__init__()
        self._options = options
        self._x = x
        self._y = y

    def compose(self) -> ComposeResult:
        with Vertical(id="ctx-menu"):
            yield ListView(
                *[ListItem(Label(label)) for label, _ in self._options],
                id="ctx-list",
            )

    def on_mount(self) -> None:
        n = len(self._options)
        h = n + 2
        menu = self.query_one("#ctx-menu", Vertical)
        menu.styles.height = h
        sw, sh = self.app.size.width, self.app.size.height
        menu.styles.offset = (
            min(self._x, max(0, sw - 28)),
            min(self._y, max(0, sh - h - 2)),
        )
        lv = self.query_one("#ctx-list", ListView)
        lv.index = 0
        lv.focus()

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)

    def on_mouse_down(self, event) -> None:
        # Click outside the menu dismisses without acting on what's beneath
        menu = self.query_one("#ctx-menu", Vertical)
        if not menu.region.contains(event.screen_x, event.screen_y):
            event.stop()
            self.dismiss(None)

    @on(ListView.Selected, "#ctx-list")
    def _item_selected(self, event: ListView.Selected) -> None:
        idx = self.query_one("#ctx-list", ListView).index
        if idx is not None and 0 <= idx < len(self._options):
            self.dismiss(self._options[idx][1])
        else:
            self.dismiss(None)


def show_context_menu(screen: Screen, options: list[tuple[str, str]], x: int, y: int, on_choice) -> None:
    """Open the context menu; call on_choice(value) if an option is picked."""

    def _done(value: str | None) -> None:
        if value:
            on_choice(value)

    screen.app.push_screen(ContextMenuScreen(options, x, y), _done)


# ── Session preview + confirmation screen ────────────────────────────────────


class SessionPreviewScreen(Screen[None]):
    BINDINGS = [
        Binding("enter", "confirm", "Resume", show=True),
        Binding("escape", "cancel", "Back", show=True),
        Binding("f", "focus_find", "Find text", show=True),
        Binding("ctrl+f", "focus_find", "Find text", show=False),
        Binding("p", "open_palette", "Actions", show=True),
        Binding("ctrl+k", "open_palette", "Actions", show=False),
        Binding("c", "cycle_case_mode", "Case mode", show=True),
        Binding("ctrl+i", "cycle_case_mode", "Case mode", show=False),
        Binding("r", "toggle_regex", "Regex", show=True),
        Binding("ctrl+g", "toggle_regex", "Regex", show=False),
        Binding("alt+c", "cycle_case_mode", "Case mode", show=False),
        Binding("alt+r", "toggle_regex", "Regex", show=False),
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
                placeholder="Find in preview. Enter applies; r toggles regex; c toggles case.",
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
            if len(self._raw_text) > _MARKDOWN_RENDER_LIMIT:
                preview.update(RichText(self._raw_text))
            else:
                preview.update(RichMarkdown(self._raw_text))
            case_indicator = f"[dim]case:{self._case_mode}[/dim]"
            regex_indicator = (
                "[dim]regex[/dim]" if self._regex_mode else "[dim]literal[/dim]"
            )
            mi.update(
                f"[dim]f find • c case • r regex • n/N next[/dim]  {case_indicator} {regex_indicator}"
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

    def action_open_palette(self) -> None:
        commands = [
            CommandSpec("resume", "Resume session", keywords=("open", "launch")),
            CommandSpec("find", "Find in preview", keywords=("search", "text", "match")),
            CommandSpec("back", "Back", keywords=("close", "cancel")),
            CommandSpec("quit", "Quit app", keywords=("exit",)),
        ]

        def dispatch(key: str) -> None:
            if key == "resume":
                self.action_confirm()
            elif key == "find":
                self.action_focus_find()
            elif key == "back":
                self.action_cancel()
            elif key == "quit":
                self.action_quit_app()

        run_command_palette(
            self.app,
            "Preview commands",
            commands,
            dispatch,
            hint="Enter runs • Esc closes • type to filter",
        )

    def action_confirm(self) -> None:
        if resume_command(self._session.sid, self._session.source) is None:
            harness = HARNESS_MAP.get(self._session.source)
            label = harness.label if harness else self._session.source
            self.notify(
                f"No way to resume this {label} session was found "
                f"(is the {label} app installed?).",
                severity="warning",
            )
            return
        self.app.exit(("resume", self._session.sid, self._session.source))

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

    @work(thread=True, exclusive=True)
    def _build_dirs(self) -> None:
        sessions = list_sessions(cwd=self._cwd, all_projects=True)
        counts: dict[str, int] = {}
        for s in sessions:
            counts[s.project_id] = counts.get(s.project_id, 0) + 1
        all_dirs = sorted(
            [(pid_to_path(pid), pid, cnt) for pid, cnt in counts.items()],
            key=lambda x: x[0],
        )
        self.app.call_from_thread(self._apply_dirs, all_dirs)

    def _apply_dirs(self, all_dirs: list[tuple[str, str, int]]) -> None:
        self._all_dirs = all_dirs
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
#status-strip {
    height: 1;
    padding: 0 1;
    background: $panel;
    border-bottom: solid $panel-darken-1;
    content-align: left middle;
    color: $text;
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
    """Main session list — filter current list with /, open scope picker with a."""

    BINDINGS = [
        Binding("p", "open_session_menu", "Menu", show=True),
        Binding("ctrl+k", "open_palette", "Palette", show=False),
        Binding("d", "trash_session", "Trash", show=True),
        Binding("ctrl+d", "trash_session", "Trash", show=False),
        Binding("r", "rename_session", "Rename", show=True),
        Binding("ctrl+r", "rename_session", "Rename", show=False),
        Binding("t", "open_trash", "Trash bin", show=True),
        Binding("ctrl+t", "open_trash", "Trash bin", show=False),
        Binding("a", "toggle_all", "Scope", show=True),
        Binding("ctrl+a", "toggle_all", "Scope", show=False),
        Binding("o", "cycle_sort", "Sort", show=True),
        Binding("ctrl+s", "cycle_sort", "Sort", show=False),
        Binding("s", "content_search", "Search", show=True),
        Binding("ctrl+underscore", "content_search", "Search", show=False),
        Binding("ctrl+slash", "content_search", "Search", show=False),
        Binding("ctrl+c", "quit_app", "Quit", show=False),
        Binding("q", "quit_app", "Quit", show=False),
        Binding("/", "start_filter", "Filter", show=True),
        Binding("1", "select_harness(0)", "All", show=False),
        Binding("2", "select_harness(1)", "Harness 1", show=False),
        Binding("3", "select_harness(2)", "Harness 2", show=False),
        Binding("4", "select_harness(3)", "Harness 3", show=False),
        Binding("5", "select_harness(4)", "Harness 4", show=False),
        Binding("6", "select_harness(5)", "Harness 5", show=False),
        Binding("7", "select_harness(6)", "Harness 6", show=False),
    ]

    DEFAULT_CSS = (
        _SPLIT_CSS
        + """
    #status-strip {
        height: 1;
        padding: 0 1;
        background: $panel;
        border-bottom: solid $panel-darken-1;
        content-align: left middle;
        color: $text;
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
    /* harness rail */
    BrowseScreen #rail {
        width: 18;
        border-right: solid $panel-darken-1;
        layout: vertical;
    }
    BrowseScreen #harness-rail {
        height: 1fr;
        border: none;
    }
    BrowseScreen #left {
        width: 1fr;
    }
    """
    )

    _HINTS = [
        "enter=resume  /=filter  p=menu",
        "d=trash  r=rename  t=trash-bin",
        "a=scope  s=search  o=sort",
        "1-7=harness filter",
    ]

    def __init__(self, all_projects: bool = True, cwd: Optional[str] = None) -> None:
        super().__init__()
        self._all_projects = all_projects
        self._cwd = cwd or os.getcwd()
        self._all_sessions: list[Session] = []
        self._sessions: list[Session] = []
        self._filtered: list[Session] = []
        self._preview_timer: Optional[Timer] = None
        self._hint_timer: Optional[Timer] = None
        self._hint_idx: int = 0
        self._sort: str = "folder_asc"
        self._harness_filter: Optional[str] = None  # None = All harnesses
        self._rail_collapsed: bool = False
        self._loaded: bool = False
        self._restore_index: Optional[int] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label("", id="status-strip")
        with Horizontal(id="main"):
            with Vertical(id="rail"):
                yield ListView(id="harness-rail")
            with Vertical(id="left"):
                yield ListView(id="sessions")
                with Horizontal(id="filter-bar"):
                    yield Label("/", id="filter-label")
                    yield FilterInput(placeholder="filter sessions…", id="filter")
            with VerticalScroll(id="preview-scroll"):
                yield Static("", id="preview")
        yield Footer()

    def on_mount(self) -> None:
        self._update_subtitle()
        self._update_status()
        self._populate_rail()
        # Paint the previous scan's results instantly; refresh in background
        snapshot = last_scan_rows() if self._all_projects else []
        if snapshot:
            self._apply_sessions(snapshot)
        else:
            self.query_one("#preview", Static).update(RichMarkdown("*Scanning sessions…*"))
        self._load()
        self.query_one("#sessions", ListView).focus()
        self._hint_timer = self.set_interval(4, self._rotate_hint)

    def _rotate_hint(self) -> None:
        self._hint_idx = (self._hint_idx + 1) % len(self._HINTS)
        self._update_status()

    def on_resize(self, event) -> None:
        collapsed = self.app.size.width < 80
        if collapsed != self._rail_collapsed:
            self._rail_collapsed = collapsed
            rail_widget = self.query_one("#rail")
            if collapsed:
                rail_widget.styles.width = 5
            else:
                rail_widget.styles.width = 18
            # Re-render rail items in compact/full mode
            self._refresh_rail_labels()

    def _update_subtitle(self) -> None:
        short = self._cwd.replace(str(os.path.expanduser("~")), "~")
        scope = "all projects" if self._all_projects else short
        if self._harness_filter:
            h = HARNESS_MAP.get(self._harness_filter)
            harness_part = f"  [{h.icon} {h.label}]" if h else f"  [{self._harness_filter}]"
        else:
            harness_part = ""
        self.app.sub_title = f"{scope}  {_SORT_LABEL[self._sort]}{harness_part}"

    def _status_label(self) -> str:
        scope = "all projects" if self._all_projects else self._cwd.replace(str(os.path.expanduser("~")), "~")
        if self._harness_filter:
            h = HARNESS_MAP.get(self._harness_filter)
            label = h.label if h else self._harness_filter
            return f"{label} · {scope}"
        return scope

    def _update_status(self) -> None:
        filter_value = self.query_one("#filter", FilterInput).value.strip()
        self.query_one("#status-strip", Label).update(
            status_strip_text(
                screen_label="browse",
                scope_label=self._status_label(),
                sort_label=_SORT_LABEL[self._sort],
                filter_label=filter_value or "—",
                count=len(self._filtered),
                command_hint=self._HINTS[self._hint_idx],
            )
        )

    def _populate_rail(self) -> None:
        rail = self.query_one("#harness-rail", ListView)
        rail.clear()
        rail.append(HarnessRailItem(HarnessRailItem.HARNESS_ALL, "▦", "All", ""))
        for h in HARNESSES:
            rail.append(HarnessRailItem(h.id, h.icon, h.label, h.color))
        self._sync_rail_highlight()

    def _sync_rail_highlight(self) -> None:
        rail = self.query_one("#harness-rail", ListView)
        target = self._harness_filter or HarnessRailItem.HARNESS_ALL
        for i, item in enumerate(rail.children):
            if isinstance(item, HarnessRailItem) and item.harness_id == target:
                rail.index = i
                break

    def _update_rail_counts(self) -> None:
        counts: dict[str, int] = {}
        for s in self._all_sessions:
            counts[s.source] = counts.get(s.source, 0) + 1
        total = len(self._all_sessions)
        rail = self.query_one("#harness-rail", ListView)
        for item in rail.children:
            if isinstance(item, HarnessRailItem):
                if item.harness_id == HarnessRailItem.HARNESS_ALL:
                    item.set_count(total, compact=self._rail_collapsed)
                else:
                    item.set_count(counts.get(item.harness_id, 0), compact=self._rail_collapsed)

    def _refresh_rail_labels(self) -> None:
        rail = self.query_one("#harness-rail", ListView)
        for item in rail.children:
            if isinstance(item, HarnessRailItem):
                item.refresh_label(compact=self._rail_collapsed)

    def _load(self, keep_index: bool = False) -> None:
        if keep_index:
            self._restore_index = self.query_one("#sessions", ListView).index or 0
        self._fetch_sessions()

    @work(thread=True, exclusive=True, group="session-load")
    def _fetch_sessions(self) -> None:
        rows = list_sessions(cwd=self._cwd, all_projects=self._all_projects)
        self.app.call_from_thread(self._apply_sessions, rows)

    def _apply_sessions(self, rows: list[Session]) -> None:
        self._loaded = True
        self._all_sessions = rows
        self._update_rail_counts()
        self._refilter()

    def _refilter(self) -> None:
        """Re-derive the visible list from the cached session set."""
        if self._harness_filter:
            self._sessions = [s for s in self._all_sessions if s.source == self._harness_filter]
        else:
            self._sessions = list(self._all_sessions)
        fi = self.query_one("#filter", FilterInput)
        self._apply_filter(fi.value)
        self._update_status()

    def _apply_filter(self, query: str) -> None:
        if query.strip():
            self._filtered = [
                s
                for s in self._sessions
                if fuzzy_match(query, f"{s.name} {s.first_msg} {s.project_path}")
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
        lv.extend(SessionItem(s, show_project=self._all_projects) for s in self._filtered)
        if self._filtered:
            idx = 0
            if self._restore_index is not None:
                idx = min(self._restore_index, len(self._filtered) - 1)
                self._restore_index = None
            lv.index = idx
            self._load_preview(self._filtered[idx].sid)
        else:
            self.query_one("#preview", Static).update(self._empty_state_msg())
        self._update_status()

    def _empty_state_msg(self) -> str:
        if not self._loaded:
            return "*Scanning sessions…*"
        if self._harness_filter:
            h = HARNESS_MAP.get(self._harness_filter)
            label = f"{h.icon} {h.label}" if h else self._harness_filter
            return f"*No {label} sessions found.*\n\nStart a new session or switch harness in the rail."
        scope = "this project" if not self._all_projects else "any project"
        return f"*No sessions found in {scope}.*\n\nUse a to change scope."

    def _update_preview(self, sid: str) -> None:
        if self._preview_timer is not None:
            self._preview_timer.stop()
        self._preview_timer = self.set_timer(0.05, lambda: self._load_preview(sid))

    @work(thread=True, exclusive=True)
    def _load_preview(self, sid: str) -> None:
        width = self.query_one("#preview", Static).size.width or 80
        try:
            renderable: object = _preview_renderable(
                sid, preview_session(sid, max_turns=_PANE_PREVIEW_TURNS), width
            )
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
        self._update_status()
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

    @on(ListView.Selected, "#harness-rail")
    def _harness_rail_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, HarnessRailItem):
            self._set_harness_filter(event.item.harness_id or None)

    def _set_harness_filter(self, harness_id: Optional[str]) -> None:
        self._harness_filter = harness_id
        # Instant: refilter the cached list; no disk rescan needed
        self._refilter()
        self._update_subtitle()
        self._sync_rail_highlight()
        self.query_one("#sessions", ListView).focus()

    def action_select_harness(self, idx: int) -> None:
        """Jump to a harness via number keys: 1 = All, 2.. follow rail order."""
        if idx == 0:
            self._set_harness_filter(None)
        elif idx - 1 < len(HARNESSES):
            self._set_harness_filter(HARNESSES[idx - 1].id)

    # ── actions ───────────────────────────────────────────────────────────────

    def action_trash_session(self) -> None:
        s = self._current_session()
        if not s:
            return
        if not _guard_trashable_session(self, s):
            return
        try:
            trash_session(s.sid)
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")
            return
        self._load(keep_index=True)
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
                    self._load(keep_index=True)
                    self.notify(f"Renamed: {new_name}", timeout=2)

        self.app.push_screen(InputDialog("New name:", initial=s.name), on_rename)

    def action_open_trash(self) -> None:
        self.app.push_screen(TrashScreen(cwd=self._cwd))

    def action_toggle_all(self) -> None:
        """Open the scope picker. Escape in the picker returns to all projects."""
        self.app.push_screen(DirectoryPickerScreen(cwd=self._cwd))

    def action_cycle_sort(self) -> None:
        idx = _SORT_CYCLE.index(self._sort)
        self._sort = _SORT_CYCLE[(idx + 1) % len(_SORT_CYCLE)]
        self._apply_filter(self.query_one("#filter", FilterInput).value)
        self._update_subtitle()
        self._update_status()
        self.notify(f"Sort: {_SORT_LABEL[self._sort]}", timeout=1)

    def action_content_search(self) -> None:
        self.app.push_screen(
            ContentSearchScreen(
                all_projects=self._all_projects,
                cwd=self._cwd,
            )
        )

    def _session_menu_options(self, session: Session) -> list[tuple[str, str]]:
        options = [
            ("Resume", "resume"),
            ("Rename", "rename"),
        ]
        if _session_supports_trash(session):
            options.append(("Trash", "trash"))
        return options

    def _show_session_menu(self, session: Session, x: int | None = None, y: int | None = None) -> None:
        if x is None or y is None:
            lv = self.query_one("#sessions", ListView)
            item = lv.highlighted_child
            if isinstance(item, SessionItem):
                x = item.region.x + min(4, max(0, item.region.width - 1))
                y = item.region.y + 1
            else:
                x = self.app.size.width // 2 - 10
                y = self.app.size.height // 2 - 2
        show_context_menu(
            self,
            self._session_menu_options(session),
            x,
            y,
            lambda value: self._handle_menu_choice(session, value),
        )

    def action_open_session_menu(self) -> None:
        session = self._current_session()
        if not session:
            return
        self._show_session_menu(session)

    def action_open_palette(self) -> None:
        # Core commands
        commands = [
            CommandSpec("resume", "Resume session", keywords=("open", "launch")),
            CommandSpec("rename", "Rename session", keywords=("label", "name")),
            CommandSpec("trash", "Trash session", keywords=("delete", "remove")),
            CommandSpec("filter", "Filter current list", keywords=("find", "query")),
            CommandSpec("scope", "Change directory scope", keywords=("all projects", "dir")),
            CommandSpec("sort", "Cycle sort order", keywords=("recent", "oldest", "folder")),
            CommandSpec("trashbin", "Open trash bin", keywords=("restore", "recovery")),
            CommandSpec("quit", "Quit app", keywords=("exit",)),
        ]
        # Harness filter commands — show only present harnesses
        harness_counts: dict[str, int] = {}
        for s in self._all_sessions:
            harness_counts[s.source] = harness_counts.get(s.source, 0) + 1
        if self._harness_filter:
            commands.append(CommandSpec(
                "harness_all", "Show all harnesses",
                keywords=("all", "reset", "filter"),
            ))
        for h in HARNESSES:
            cnt = harness_counts.get(h.id, 0)
            if cnt:
                commands.append(CommandSpec(
                    f"harness_{h.id}", f"{h.icon} Show only {h.label}  ({cnt})",
                    keywords=("filter", "show", h.label.lower(), h.id),
                ))

        def dispatch(key: str) -> None:
            if key == "resume":
                s = self._current_session()
                if s:
                    self.app.push_screen(SessionPreviewScreen(s))
            elif key == "rename":
                self.action_rename_session()
            elif key == "trash":
                self.action_trash_session()
            elif key == "filter":
                self.action_start_filter()
            elif key == "scope":
                self.action_toggle_all()
            elif key == "sort":
                self.action_cycle_sort()
            elif key == "trashbin":
                self.action_open_trash()
            elif key == "quit":
                self.action_quit_app()
            elif key == "harness_all":
                self._set_harness_filter(None)
            elif key.startswith("harness_"):
                self._set_harness_filter(key[len("harness_"):])

        run_command_palette(self.app, "Command palette", commands, dispatch,
                            hint="Enter runs • Esc closes • type to filter")

    def action_quit_app(self) -> None:
        self.app.exit(None)

    # ── right-click ──────────────────────────────────────────────────────────

    @on(SessionItem.RightClicked)
    def _session_right_clicked(self, event: SessionItem.RightClicked) -> None:
        self._show_session_menu(event.session, event.x, event.y)

    def _handle_menu_choice(self, s: Session, value: str) -> None:
        if value == "resume":
            self.app.push_screen(SessionPreviewScreen(s))
        elif value == "rename":

            def on_rename(new_name: Optional[str]) -> None:
                if new_name:
                    pid = project_for_session(s.sid)
                    if pid:
                        set_name(pid, s.sid, new_name)
                        self._load(keep_index=True)
                        self.notify(f"Renamed: {new_name}", timeout=2)

            self.app.push_screen(InputDialog("New name:", initial=s.name), on_rename)
        elif value == "trash":
            if not _guard_trashable_session(self, s):
                return
            try:
                trash_session(s.sid)
            except Exception as e:
                self.notify(f"Error: {e}", severity="error")
                return
            self._load(keep_index=True)
            self.notify("Trashed.", timeout=1)


# ── Content search screen ─────────────────────────────────────────────────────


class ContentSearchScreen(Screen[None]):
    BINDINGS = [
        Binding("d", "trash_session", "Trash", show=True),
        Binding("ctrl+d", "trash_session", "Trash", show=False),
        Binding("s", "new_search", "Edit query", show=True),
        Binding("ctrl+underscore", "new_search", "Search", show=False),
        Binding("ctrl+slash", "new_search", "Search", show=False),
        Binding("r", "toggle_regex", "Regex", show=True),
        Binding("ctrl+g", "toggle_regex", "Regex", show=False),
        Binding("c", "toggle_case_mode", "Case mode", show=True),
        Binding("ctrl+i", "toggle_case_mode", "Case mode", show=False),
        Binding("alt+r", "toggle_regex", "Regex", show=False),
        Binding("alt+c", "toggle_case_mode", "Case mode", show=False),
        Binding("b", "back", "Back", show=True),
        Binding("ctrl+b", "back", "Back", show=False),
        Binding("ctrl+c", "quit_app", "Quit", show=False),
        Binding("escape", "back", "Back", show=False),
    ]

    DEFAULT_CSS = (
        _SPLIT_CSS
        + """
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
        self._regex_mode: bool = True
        self._case_modes = ["smart", "ignore", "match"]
        self._case_mode_idx: int = 0

    @property
    def _case_mode(self) -> str:
        return self._case_modes[self._case_mode_idx]

    def _search_mode_label(self) -> str:
        regex = "regex" if self._regex_mode else "literal"
        return f"{regex} • case:{self._case_mode}"

    def _scope_label(self) -> str:
        if self._all_projects:
            return "all projects"
        return self._cwd.replace(str(os.path.expanduser("~")), "~")

    def _update_status(self) -> None:
        query = self.query_one("#search", FilterInput).value.strip()
        self.query_one("#status-strip", Label).update(
            status_strip_text(
                screen_label="search",
                scope_label=self._scope_label(),
                filter_label=query,
                count=len(self._sessions),
                mode_label=self._search_mode_label(),
                command_hint="enter=search  s=edit-query  d=trash  r=regex  c=case  b=back",
            )
        )

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label("", id="status-strip")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield FilterInput(
                    value=self._query,
                    placeholder="Type a query, then Enter to search. From results: s edits, d trashes.",
                    id="search",
                )
                yield ListView(id="sessions")
            with VerticalScroll(id="preview-scroll"):
                yield Static("", id="preview")
        yield Footer()

    def on_mount(self) -> None:
        self.app.sub_title = f"search: {self._query}"
        if self._query:
            self._run_search(self._query)
        else:
            self._update_status()
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
        lv.extend(SessionItem(s, show_project=self._all_projects) for s in self._sessions)
        if self._sessions:
            lv.index = 0
            self._load_preview(self._sessions[0].sid)
        else:
            self.query_one("#preview", Static).update(f"*No results for: {query}*")
        self.app.sub_title = f"search: {query}  ({self._search_mode_label()})"
        self._update_status()

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
        self._preview_timer = self.set_timer(0.05, lambda: self._load_preview(sid))

    @work(thread=True, exclusive=True)
    def _load_preview(self, sid: str) -> None:
        width = self.query_one("#preview", Static).size.width or 80
        try:
            renderable: object = _preview_renderable(
                sid, preview_session(sid, max_turns=_PANE_PREVIEW_TURNS), width
            )
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
        if not _guard_trashable_session(self, s):
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
        search = self.query_one("#search", FilterInput)
        search.focus()
        search.cursor_position = len(search.value)

    def action_toggle_regex(self) -> None:
        self._regex_mode = not self._regex_mode
        mode = "regex" if self._regex_mode else "literal"
        self.notify(f"Search mode: {mode}", timeout=1.4)
        q = self.query_one("#search", FilterInput).value.strip()
        if q:
            self._run_search(q)
        else:
            self._update_status()

    def action_toggle_case_mode(self) -> None:
        self._case_mode_idx = (self._case_mode_idx + 1) % len(self._case_modes)
        self.notify(f"Search case mode: {self._case_mode}", timeout=1.4)
        q = self.query_one("#search", FilterInput).value.strip()
        if q:
            self._run_search(q)
        else:
            self._update_status()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_quit_app(self) -> None:
        self.app.exit(None)

    @on(SessionItem.RightClicked)
    def _session_right_clicked(self, event: SessionItem.RightClicked) -> None:
        s = event.session
        options = [("Resume", "resume")]
        if _session_supports_trash(s):
            options.append(("Trash", "trash"))
        show_context_menu(
            self, options, event.x, event.y,
            lambda value: self._handle_menu_choice(s, value),
        )

    def _handle_menu_choice(self, s: Session, value: str) -> None:
        if value == "resume":
            self.app.push_screen(SessionPreviewScreen(s, search_term=self._query))
        elif value == "trash":
            if not _guard_trashable_session(self, s):
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


# ── Trash screen ──────────────────────────────────────────────────────────────


class TrashScreen(Screen[None]):
    BINDINGS = [
        Binding("d", "delete_forever", "Delete forever", show=True),
        Binding("ctrl+d", "delete_forever", "Delete forever", show=False),
        Binding("e", "empty_all", "Empty trash", show=True),
        Binding("ctrl+e", "empty_all", "Empty trash", show=False),
        Binding("r", "restore_session", "Restore", show=True),
        Binding("ctrl+r", "restore_session", "Restore", show=False),
        Binding("b", "back", "Back", show=True),
        Binding("ctrl+b", "back", "Back", show=False),
        Binding("ctrl+c", "quit_app", "Quit", show=False),
        Binding("escape", "back", "Back", show=False),
        Binding("/", "start_filter", "Filter", show=True),
    ]

    DEFAULT_CSS = (
        _SPLIT_CSS
        + """
    #filter-bar {
        height: 3;
        border-top: solid $panel-darken-1;
        display: none;
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

    def __init__(self, cwd: Optional[str] = None) -> None:
        super().__init__()
        self._cwd = cwd or os.getcwd()
        self._all_entries: list[TrashEntry] = []
        self._entries: list[TrashEntry] = []
        self._preview_timer: Optional[Timer] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label("", id="status-strip")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield ListView(id="sessions")
                with Horizontal(id="filter-bar"):
                    yield Label("/", id="filter-label")
                    yield FilterInput(placeholder="filter trash…", id="filter")
            with VerticalScroll(id="preview-scroll"):
                yield Static("", id="preview")
        yield Footer()

    def on_mount(self) -> None:
        self.app.sub_title = "trash"
        self._load()
        self.query_one("#sessions", ListView).focus()

    def _update_status(self) -> None:
        filter_value = self.query_one("#filter", FilterInput).value.strip()
        self.query_one("#status-strip", Label).update(
            status_strip_text(
                screen_label="trash",
                filter_label=filter_value,
                count=len(self._entries),
                command_hint="enter/r=restore  d=delete  e=empty  b=back",
            )
        )

    def _load(self) -> None:
        self._all_entries = list_trash()
        self._apply_filter(self.query_one("#filter", FilterInput).value)

    def _apply_filter(self, query: str) -> None:
        q = query.lower().strip()
        if q:
            self._entries = [
                entry
                for entry in self._all_entries
                if q in f"{entry.name or ''} {entry.sid}".lower()
            ]
        else:
            self._entries = list(self._all_entries)

        lv = self.query_one("#sessions", ListView)
        lv.clear()
        lv.extend(TrashItem(entry) for entry in self._entries)
        if self._entries:
            lv.index = 0
            self._load_preview(self._entries[0].sid)
        else:
            self.query_one("#preview", Static).update("*Trash is empty.*")
        self._update_status()

    def _current_entry(self) -> Optional[TrashEntry]:
        item = self.query_one("#sessions", ListView).highlighted_child
        return item.entry if isinstance(item, TrashItem) else None

    def action_start_filter(self) -> None:
        bar = self.query_one("#filter-bar")
        bar.display = True
        self.query_one("#filter", FilterInput).focus()

    @on(FilterInput.Cancelled)
    def _filter_cancelled(self) -> None:
        self._hide_filter()

    def _hide_filter(self) -> None:
        fi = self.query_one("#filter", FilterInput)
        fi.value = ""
        self._apply_filter("")
        self.query_one("#filter-bar").display = False
        self.query_one("#sessions", ListView).focus()

    def move_list(self, direction: int) -> None:
        lv = self.query_one("#sessions", ListView)
        lv.action_cursor_down() if direction > 0 else lv.action_cursor_up()

    def activate_list(self) -> None:
        entry = self._current_entry()
        self._hide_filter()
        if entry:
            self._do_restore(entry)

    def _update_preview(self, sid: str) -> None:
        if self._preview_timer is not None:
            self._preview_timer.stop()
        self._preview_timer = self.set_timer(0.05, lambda: self._load_preview(sid))

    @work(thread=True, exclusive=True)
    def _load_preview(self, sid: str) -> None:
        width = self.query_one("#preview", Static).size.width or 80
        try:
            renderable: object = _preview_renderable(
                sid, preview_session(sid, max_turns=_PANE_PREVIEW_TURNS), width
            )
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

    @on(Input.Changed, "#filter")
    def _filter_changed(self, event: Input.Changed) -> None:
        self._apply_filter(event.value)

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
                try:
                    delete_trashed(e.sid)
                except Exception as err:
                    self.notify(f"Error: {err}", severity="error")
                    return
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

    def action_restore_session(self) -> None:
        entry = self._current_entry()
        if entry:
            self._do_restore(entry)

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
        entry = event.entry
        show_context_menu(
            self,
            [
                ("Restore", "restore"),
                ("Delete forever", "delete"),
                ("Empty trash", "empty"),
            ],
            event.x,
            event.y,
            lambda value: self._handle_menu_choice(entry, value),
        )

    def _handle_menu_choice(self, e: TrashEntry, value: str) -> None:
        if value == "restore":
            self._do_restore(e)
        elif value == "delete":
            self._do_delete(e)
        elif value == "empty":
            self.action_empty_all()


# ── App ───────────────────────────────────────────────────────────────────────


class ClaudetreeApp(App[tuple[str, str] | tuple[str] | None]):
    TITLE = "claudetree"
    CSS = """
    Screen {
        background: $surface;
    }
    /* Modal screens need translucent backgrounds so the screen beneath
       stays visible. The opaque Screen rule above would otherwise override
       their DEFAULT_CSS (app CSS wins at equal specificity) and blank the
       UI behind menus and dialogs. */
    ContextMenuScreen {
        background: black 50%;
    }
    InputDialog, ConfirmDialog {
        background: black 55%;
    }
    CommandPaletteScreen {
        background: black 55%;
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
