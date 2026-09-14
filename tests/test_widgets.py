"""Tests for the widgets: filterable session list with per-kind badges.

ListView needs to be mounted for items/index to work, so these tests mount
the widget inside a host app with run_test(); the pure logic (_matches,
_label) is also exercised directly on unmounted widgets.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from muxter.model import Session
from muxter.widgets import SessionList


def sessions() -> list[Session]:
    return [
        Session(kind="tmux", name="dev-ai", tty="/dev/pts/4", attached=1,
                created="Tue Sep  8"),
        Session(kind="bare", name="pts/0", tty="/dev/pts/0", shell="bash",
                attached=1, created="Thu Sep 11"),
        Session(kind="tmux", name="tempmon", tty="/dev/pts/8", attached=1),
    ]


class _HostApp(App):
    """Minimal host so the ListView is mounted when tests touch its items."""

    def __init__(self, lst: SessionList):
        super().__init__()
        self.lst = lst

    def compose(self) -> ComposeResult:
        yield self.lst


@pytest.fixture
async def mounted_list():
    lst = SessionList()
    async with _HostApp(lst).run_test():
        await lst.set_sessions(sessions())
        yield lst


async def test_set_sessions_sorts_by_name(mounted_list):
    assert [s.name for s in mounted_list.sessions] == ["dev-ai", "pts/0", "tempmon"]
    assert len(mounted_list) == 3


async def test_items_are_mounted_with_badges(mounted_list):
    labels = [item.children[0].render().plain for item in mounted_list.children]
    assert labels[0].startswith("[tmux] dev-ai")
    assert labels[1].startswith("[bare] pts/0")


def test_labels_contain_badge_and_details():
    labels = [SessionList._label(s).plain for s in sessions()]
    assert labels[0] == "[tmux] dev-ai  /dev/pts/4  1 attached  Tue Sep  8"
    assert labels[1] == "[bare] pts/0  bash  /dev/pts/0  1 attached  Thu Sep 11"
    assert labels[2] == "[tmux] tempmon  /dev/pts/8  1 attached"


def test_filter_matches_name_tty_shell_case_insensitive():
    """No app needed: what the filter admits is a question about the data, and
    `visible_sessions` answers it without touching the DOM."""
    lst = SessionList()
    lst.sessions = sessions()
    for text, expected in [
        ("PTS/0", ["pts/0"]),
        ("bash", ["pts/0"]),
        ("ZSH", []),
        ("", ["dev-ai", "pts/0", "tempmon"]),
    ]:
        lst.filter_text = text.lower()
        assert [s.name for s in lst.visible_sessions()] == expected, text


async def test_selected_session_follows_index_and_filter(mounted_list):
    lst = mounted_list
    assert lst.index == 0  # the list self-selects its first match on fill
    assert lst.selected_session.name == "dev-ai"
    await lst.set_filter("temp")
    assert lst.index == 0
    assert lst.selected_session.name == "tempmon"
    lst.index = 5  # ListView clamps out-of-range indices to the last item
    assert lst.index == 0
    assert lst.selected_session.name == "tempmon"
