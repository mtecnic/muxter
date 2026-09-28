"""MuxTer application: 20/80 horizontal split over sessions and live terminal."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, ListView, Static

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


class ConfirmScreen(ModalScreen[bool]):
    """A y/n question over the app; dismisses with True only on `y`."""

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }
    #confirm-box {
        width: auto;
        max-width: 80%;
        padding: 1 3;
        border: thick $error;
        background: $surface;
    }
    """

    BINDINGS: ClassVar = [
        Binding("y", "answer(True)", "Yes"),
        Binding("n", "answer(False)", "No"),
        Binding("escape", "answer(False)", "", show=False),
    ]

    def __init__(self, question: str):
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        yield Static(f"{self.question}\n\n[b]y[/b] yes    [b]n[/b] no", id="confirm-box")

    def action_answer(self, answer: bool) -> None:
        self.dismiss(answer)


def _control_keys() -> dict[str, bytes]:
    """ctrl+a..ctrl+z as the C0 bytes a terminal sends for them."""
    return {f"ctrl+{c}": bytes([ord(c) - 96]) for c in "abcdefghijklmnopqrstuvwxyz"}


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
        yield Input(placeholder="filter sessions…", id="filter-input", disabled=True)
        with Horizontal():
            yield SessionList(id="session-list")
            yield TerminalPane(id="terminal-pane")
        # in the flow between the panes and the footer: docked to the bottom
        # too, it sat in the footer's row and was drawn underneath it
        yield StatusBar()
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
        self.store.start()

    async def on_unmount(self) -> None:
        await self.store.stop()
        if self.connection is not None:
            self.connection.close()

    async def _on_sessions_changed(self, added: list[Session], removed: list[Session]) -> None:
        await self.session_list.set_sessions(self.store.sessions)
        self._last_refresh = time.time()
        self._refresh_status()

    def on_terminal_pane_resized(self, event: TerminalPane.Resized) -> None:
        if self.connection is not None:
            self.connection.resize(event.rows, event.cols)

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

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        # the focused ListView consumes Enter itself (select_cursor), so the
        # app-level "enter" binding never fires while the list has focus
        event.stop()
        await self.action_connect()

    async def _connect(self, session: Session) -> None:
        if self.connection is not None:
            self.connection.close()
        conn = SessionConnection(session.name, rows=self.terminal.term.rows,
                                 cols=self.terminal.term.cols)
        # seed scrollback *before* attaching, so the live redraw lands on top
        # of it instead of the snapshot being printed over the live screen
        try:
            scrollback = await conn.capture_scrollback()
        except RuntimeError:
            scrollback = ""
        self.terminal.term.reset()
        self.terminal.term.clear_history()
        if scrollback:
            self.terminal.term.feed(scrollback)
        self.connection = conn
        conn.subscribe(self.terminal.set_content)
        await conn.connect()
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
        # back to view mode the filter input is editable again; the terminal
        # stays focusable but the list must regain focus for j/k to work
        self.filter_input.disabled = False
        self.session_list.focus()

    # xterm's encodings. Keys are matched by name first: Textual hands some
    # of these over with no character (and binds ctrl+c itself), so the
    # character alone would silently drop them.
    _KEY_ESCAPES: ClassVar[Mapping[str, bytes]] = {
        "up": b"\x1b[A", "down": b"\x1b[B", "right": b"\x1b[C", "left": b"\x1b[D",
        "enter": b"\r", "backspace": b"\x7f", "tab": b"\t", "shift+tab": b"\x1b[Z",
        "home": b"\x1b[H", "end": b"\x1b[F", "insert": b"\x1b[2~", "delete": b"\x1b[3~",
        "pageup": b"\x1b[5~", "pagedown": b"\x1b[6~",
        "f1": b"\x1bOP", "f2": b"\x1bOQ", "f3": b"\x1bOR", "f4": b"\x1bOS",
        "f5": b"\x1b[15~", "f6": b"\x1b[17~", "f7": b"\x1b[18~", "f8": b"\x1b[19~",
        "f9": b"\x1b[20~", "f10": b"\x1b[21~", "f11": b"\x1b[23~", "f12": b"\x1b[24~",
        **_control_keys(),
    }

    def on_key(self, event) -> None:
        if self._mode != "interact" or self.connection is None:
            return
        # Escape is the escape *hatch* -- it always leaves interact mode and
        # is never forwarded to the pane, even though a bare ESC is a real
        # key the pane might otherwise want.
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.action_view()
            return
        data = self._KEY_ESCAPES.get(event.key)
        if data is None and event.key.startswith("alt+") and event.character:
            data = b"\x1b" + event.character.encode("utf-8")
        elif data is None and event.character and len(event.character) == 1:
            data = event.character.encode("utf-8")
        if data is not None:
            self.connection.feed(data)
            event.stop()
            event.prevent_default()

    async def action_run_command(self) -> None:
        session = self.session_list.selected_session
        if session is None or self.connection is None:
            return
        command = self.filter_input.value or "ls"
        await self.connection.run_command(command)

    def action_kill(self) -> None:
        session = self.session_list.selected_session
        if session is None or (session.kind != "tmux" and not session.pid):
            return
        what = f"tmux session {session.name}" if session.kind == "tmux" else (
            f"{session.shell or 'the shell'} (pid {session.pid}) on {session.name}"
        )

        async def answered(yes: bool | None) -> None:
            if yes:
                await self._kill(session)

        self.push_screen(ConfirmScreen(f"Kill {what}?"), answered)

    async def _kill(self, session: Session) -> None:
        if session.kind == "tmux":
            await kill_session(session.name)
        else:
            await kill_bare(session.pid)
        await self.store.refresh_now()

    def action_clear_pane(self) -> None:
        # local only: this used to feed ESC[H ESC[2J to the session, which
        # types those bytes into its shell as if you had pressed them
        self.terminal.term.reset()
        self.terminal.term.clear_history()
        self.terminal.refresh_screen()

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
