"""MuxTer application: 20/80 horizontal split over sessions and live terminal."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Input, Static

from .discovery import discover as default_discover
from .model import Session, SessionStore
from .session_io import SessionConnection, kill_bare, kill_session
from .terminal_screen import TerminalPane
from .widgets import SessionList


class StatusBar(Static):
    """Status bar: mode, selected session tty/shell, last refresh age."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("id", "status-bar")
        super().__init__("not connected", *args, **kwargs)
        self.mode = "view"
        self.session: Session | None = None
        self.last_refresh: str = ""

    def render_text(self) -> str:
        if self.session is None:
            return f" {self.mode}  |  no session  |  refreshed {self.last_refresh}"
        s = self.session
        bits = [self.mode, s.kind, s.name]
        if s.tty:
            bits.append(s.tty)
        if s.shell:
            bits.append(s.shell)
        bits.append(f"refreshed {self.last_refresh}")
        return "  ".join(bits)

    def update_bar(self, mode: str | None = None, session: Session | None = None,
                   age: str | None = None) -> None:
        if mode is not None:
            self.mode = mode
        if session is not None:
            self.session = session
        if age is not None:
            self.last_refresh = age
        self.update(self.render_text())


class MuxterApp(App):
    """Two-pane terminal-session monitor: 20% list, 80% live terminal."""

    CSS = """
    Horizontal {
        width: 1fr;
        height: 1fr;
    }
    #session-list {
        width: 20%;
        height: 1fr;
        border: round $primary;
    }
    #terminal-pane {
        width: 1fr;
        height: 1fr;
        border: round $secondary;
        padding: 0 1;
    }
    #filter-input {
        dock: top;
        height: 3;
    }
    #status-bar {
        dock: bottom;
        height: 1;
        background: $panel;
        color: $text;
    }
    """

    BINDINGS: ClassVar = [
        Binding("q", "quit", "Quit"),
        Binding("slash", "filter", "Filter"),
        Binding("j", "cursor_down", "", show=False),
        Binding("k", "cursor_up", "", show=False),
        Binding("enter", "connect", "", show=False),
        Binding("i", "interact", "Interact"),
        Binding("escape", "view", "", show=False),
        Binding("r", "run_command", "Run"),
        Binding("K", "kill", "Kill"),
        Binding("c", "clear_pane", "", show=False),
    ]

    def __init__(self, store: SessionStore | None = None):
        super().__init__()
        self.store = store or SessionStore(default_discover)
        self.connection: SessionConnection | None = None
        self._mode = "view"
        self._last_refresh = 0.0

    def compose(self) -> ComposeResult:
        yield StatusBar()
        with Horizontal():
            yield SessionList(id="session-list")
            yield TerminalPane(id="terminal-pane")
        yield Input(placeholder="filter sessions…", id="filter-input", disabled=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.session_list = self.query_one("#session-list", SessionList)
        self.terminal = self.query_one("#terminal-pane", TerminalPane)
        self.filter_input = self.query_one("#filter-input", Input)
        self.status = self.query_one("#status-bar", StatusBar)
        self.store.subscribe(self._on_sessions_changed)
        await self.store.refresh_now()
        self._refresh_status()
        self.set_interval(1.0, self._tick)

    async def _on_sessions_changed(self, added: list[Session], removed: list[Session]) -> None:
        await self.session_list.set_sessions(self.store.sessions)
        self._last_refresh = time.time()
        self._refresh_status()

    def _refresh_status(self) -> None:
        age = int(time.time() - self._last_refresh)
        self.status.update_bar(age=f"{age}s ago")

    def _tick(self) -> None:
        self._refresh_status()

    # -- actions -------------------------------------------------------------

    def action_cursor_down(self) -> None:
        self.session_list.action_cursor_down()
        self._select_current()

    def action_cursor_up(self) -> None:
        self.session_list.action_cursor_up()
        self._select_current()

    def _select_current(self) -> None:
        session = self.session_list.selected_session
        if session is not None:
            self.status.update_bar(session=session)

    async def action_connect(self) -> None:
        session = self.session_list.selected_session
        if session is None or session.kind != "tmux":
            return
        await self._connect(session)

    async def _connect(self, session: Session) -> None:
        if self.connection is not None:
            self.connection.close()
        conn = SessionConnection(session.name, rows=self.terminal.term.rows,
                                 cols=self.terminal.term.cols)
        self.connection = conn
        conn.subscribe(self.terminal.set_content)
        await conn.connect()
        try:
            scrollback = await conn.capture_scrollback()
        except RuntimeError:
            scrollback = ""
        self.terminal.term.screen.history.clear()
        if scrollback:
            self.terminal.term.feed(scrollback)
        self.terminal.refresh_screen()
        self.status.update_bar(session=session)

    def action_interact(self) -> None:
        if self.connection is None:
            return
        self._mode = "interact"
        self.status.update_bar(mode="interact")
        self.terminal.focus()

    def action_view(self) -> None:
        self._mode = "view"
        self.status.update_bar(mode="view")
        self.session_list.focus()

    _KEY_ESCAPES: ClassVar[Mapping[str, bytes]] = {
        "up": b"\x1b[A", "down": b"\x1b[B", "right": b"\x1b[C", "left": b"\x1b[D",
        "enter": b"\r", "backspace": b"\x7f",
    }

    def on_key(self, event) -> None:
        if self._mode != "interact" or self.connection is None:
            return
        data = None
        if event.character and len(event.character) == 1:
            data = event.character.encode("utf-8")
        else:
            data = self._KEY_ESCAPES.get(event.key)
        if data is not None:
            self.connection.feed(data)
            event.stop()
            event.prevent_default()
            if event.key == "escape":
                self.action_view()

    async def action_run_command(self) -> None:
        session = self.session_list.selected_session
        if session is None or self.connection is None:
            return
        command = self.filter_input.value or "ls"
        await self.connection.run_command(command)

    async def action_kill(self) -> None:
        session = self.session_list.selected_session
        if session is None:
            return
        if session.kind == "tmux":
            await kill_session(session.name)
        elif session.pid:
            await kill_bare(session.pid)

    def action_clear_pane(self) -> None:
        self.terminal.term.screen.reset_default_margins()
        self.terminal.term.screen.history.clear()
        self.terminal.refresh_screen()
        if self.connection is not None:
            self.connection.feed(b"\x1b[H\x1b[2J")

    def action_filter(self) -> None:
        self.filter_input.disabled = False
        self.filter_input.focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        await self.session_list.set_filter(event.value)
        self.filter_input.value = event.value

    async def on_input_changed(self, event: Input.Changed) -> None:
        await self.session_list.set_filter(event.value)


def main() -> None:
    MuxterApp().run()


if __name__ == "__main__":
    main()
