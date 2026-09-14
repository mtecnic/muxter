"""Textual render test: the app boots, composes both panes, and its keymap works."""

from __future__ import annotations

import pytest
from textual.widgets import Input

from muxter.app import MuxterApp, StatusBar
from muxter.model import Session, SessionStore
from muxter.widgets import SessionList


def three_sessions() -> list[Session]:
    return [
        Session(kind="tmux", name="dev-ai", tty="/dev/pts/4", attached=1,
                created="Tue Sep  8"),
        Session(kind="bare", name="pts/0", tty="/dev/pts/0", shell="bash",
                attached=1, created="Thu Sep 11"),
        Session(kind="tmux", name="tempmon", tty="/dev/pts/8", attached=1),
    ]


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
        assert [s.name for s in lst.sessions] == ["dev-ai", "pts/0", "tempmon"]


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
        assert "tmux  dev-ai" in app.query_one("#status-bar", StatusBar).render().plain


async def test_i_enters_interact_and_escape_returns(app):
    async with app.run_test() as pilot:
        app.connection = object()  # interact needs a live connection
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
