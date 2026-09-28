"""Textual render test: the app boots, composes both panes, and its keymap works."""

from __future__ import annotations

import pytest
from textual.widgets import Input

from muxter.app import MuxterApp, StatusBar
from muxter.model import Session, SessionStore
from muxter.widgets import SessionList


def three_sessions() -> list[Session]:
    return [
        Session(kind="tmux", name="testuser", tty="/dev/pts/4", attached=1,
                created="Tue Sep  8"),
        Session(kind="bare", name="pts/0", tty="/dev/pts/0", shell="bash",
                attached=1, created="Thu Sep 11"),
        Session(kind="tmux", name="tempmon", tty="/dev/pts/8", attached=1),
    ]


class FakeConnection:
    """Records what the app writes to the session and how it is resized."""

    def __init__(self):
        self.fed: list[bytes] = []
        self.sizes: list[tuple[int, int]] = []

    def feed(self, data: bytes) -> None:
        self.fed.append(data)

    def resize(self, rows: int, cols: int) -> None:
        self.sizes.append((rows, cols))

    def close(self) -> None:
        pass


@pytest.fixture
def app():
    async def fake_discover():
        return three_sessions()

    store = SessionStore(fake_discover, interval=9999)
    return MuxterApp(store=store)


async def test_app_boots_with_both_panes(app):
    async with app.run_test():
        assert isinstance(app.query_one("#session-list"), SessionList)
        assert app.query_one("#terminal-pane") is not None
        assert app.query_one("#status-bar", StatusBar) is not None
        assert app.query_one("#filter-input") is not None


async def test_sessions_land_in_the_list(app):
    async with app.run_test():
        lst = app.query_one("#session-list", SessionList)
        assert [s.name for s in lst.sessions] == ["testuser", "pts/0", "tempmon"]


async def test_status_bar_shows_refresh_age(app):
    async with app.run_test():
        bar = app.query_one("#status-bar", StatusBar)
        assert bar.mode == "view"
        assert "refreshed 0s ago" in bar.render().plain


async def test_slash_focuses_filter_input(app):
    async with app.run_test() as pilot:
        assert app.filter_input.disabled is True
        await pilot.press("/")
        assert app.filter_input.disabled is False
        assert app.filter_input.has_focus


async def test_typing_in_filter_mode_filters_list(app):
    async with app.run_test() as pilot:
        await pilot.press("/")
        for ch in "tem":
            await pilot.press(ch)
        lst = app.query_one("#session-list", SessionList)
        assert lst.filter_text == "tem"
        assert [s.name for s in lst.sessions if lst._matches(s)] == ["tempmon"]


async def test_j_k_move_selection_and_update_status(app):
    async with app.run_test() as pilot:
        await pilot.press("j")
        assert app.session_list.index == 1
        bar = app.query_one("#status-bar", StatusBar)
        assert "bare  pts/0" in bar.render().plain
        await pilot.press("k")
        assert app.session_list.index == 0
        await pilot.press("k")  # already at top; must not go negative
        assert app.session_list.index == 0
        assert "tmux  testuser" in app.query_one("#status-bar", StatusBar).render().plain


async def test_i_enters_interact_and_escape_returns(app):
    async with app.run_test() as pilot:
        app.connection = FakeConnection()  # interact needs a live connection
        await pilot.press("i")
        assert app._mode == "interact"
        assert app.query_one("#status-bar", StatusBar).mode == "interact"
        # the Input is disabled while in view mode, so the terminal is the
        # only focusable widget in the focus chain
        assert app.query_one("#filter-input", Input).disabled is True
        assert app.terminal.can_focus
        await pilot.press("escape")
        assert app._mode == "view"
        assert app.query_one("#filter-input", Input).disabled is False
        assert app.session_list.has_focus


async def test_q_quits(app):
    async with app.run_test() as pilot:
        await pilot.press("q")
        assert app.is_running is False


async def test_enter_on_the_list_connects(app):
    # the focused ListView eats Enter, so the app binding alone never fired
    connected = []

    async def spy(session):
        connected.append(session.name)

    app._connect = spy
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert connected == ["testuser"]


async def test_interact_forwards_control_and_named_keys(app):
    async with app.run_test() as pilot:
        conn = app.connection = FakeConnection()
        await pilot.press("i", "q", "ctrl+c", "ctrl+b", "home", "pagedown", "f5", "shift+tab")
        await pilot.pause()
        assert app.is_running  # q went to the session, not to quit
        assert conn.fed == [
            b"q", b"\x03", b"\x02", b"\x1b[H", b"\x1b[6~", b"\x1b[15~", b"\x1b[Z",
        ]


async def test_kill_asks_first_and_n_spares_the_session(app, monkeypatch):
    from muxter import app as app_module

    killed = []

    async def fake_kill(name):
        killed.append(name)

    monkeypatch.setattr(app_module, "kill_session", fake_kill)
    async with app.run_test() as pilot:
        await pilot.press("K")
        await pilot.pause()
        assert "Kill tmux session testuser?" in str(app.screen.query_one("#confirm-box").render())
        await pilot.press("n")
        await pilot.pause()
        assert killed == []
        await pilot.press("K", "y")
        await pilot.pause()
        assert killed == ["testuser"]


async def test_status_bar_is_not_under_the_footer(app):
    from textual.widgets import Footer

    async with app.run_test():
        bar = app.query_one("#status-bar", StatusBar).region
        footer = app.query_one(Footer).region
        assert bar.height == 1 and not bar.overlaps(footer)


async def test_pane_resize_reaches_the_connection(app):
    async with app.run_test(size=(100, 30)) as pilot:
        conn = app.connection = FakeConnection()
        await pilot.resize_terminal(140, 40)
        await pilot.pause()
        assert conn.sizes and conn.sizes[-1] == (app.terminal.term.rows, app.terminal.term.cols)


async def test_refresh_keeps_the_selected_session(app):
    async with app.run_test() as pilot:
        await pilot.press("j", "j")
        assert app.session_list.selected_session.name == "tempmon"
        await app.session_list.set_sessions(
            three_sessions() + [Session(kind="tmux", name="aaa-new")]
        )
        assert app.session_list.selected_session.name == "tempmon"


async def test_store_refreshes_on_its_own(app):
    async with app.run_test():
        assert app.store._task is not None and not app.store._task.done()
