"""Tests for session discovery, replaying recorded tmux/who/ps fixtures."""

from __future__ import annotations

import pytest

from muxter import discovery
from muxter.discovery import (
    parse_ps_ttys,
    parse_tmux_panes,
    parse_tmux_sessions,
    parse_who,
    tty_of_stat_field7,
)
from tests.fixtures import recorded


@pytest.fixture
def replay(monkeypatch):
    """Route discovery.run() to the recorded fixture outputs."""

    async def fake_run(*cmd: str) -> str:
        if cmd[:2] == ("tmux", "list-sessions"):
            return recorded.LIST_SESSIONS
        if cmd[:2] == ("tmux", "list-panes"):
            return recorded.LIST_PANES
        if cmd[0] == "who":
            return recorded.WHO
        if cmd[0] == "ps":
            return recorded.PS
        raise AssertionError(f"unexpected command: {cmd!r}")

    monkeypatch.setattr(discovery, "run", fake_run)


def test_parse_tmux_sessions():
    sessions = parse_tmux_sessions(recorded.LIST_SESSIONS)
    assert len(sessions) == 8
    dev = sessions["dev-ai"]
    assert dev.kind == "tmux"
    assert dev.name == "dev-ai"
    assert dev.created == "Tue Sep  8 04:47:49 2026"


def test_parse_tmux_panes():
    panes = parse_tmux_panes(recorded.LIST_PANES)
    assert set(panes) == {
        "clusterspace-pane-d73d00ce", "clusterspace-pane-fd8404dc",
        "clusterspace-pane-ffe6467b", "dev-ai", "tab-4", "tab-5", "tab-7", "tempmon",
    }
    assert panes["dev-ai"] == [{"pane_id": "%12", "tty": "/dev/pts/4"}]


def test_parse_who_classifies_tmux_vs_bare():
    who = parse_who(recorded.WHO)
    assert who["pts/3"]["tmux"] is True
    assert who["pts/3"]["tmux_pane"] == "%6"
    assert who["pts/3"]["tmux_pid"] == "3102"
    assert who["pts/0"]["tmux"] is False
    assert who["pts/0"]["from"] == "192.168.86.44"
    assert who["pts/0"]["user"] == "dev-ai"


def test_parse_ps_ttys_keeps_lowest_pid_per_tty():
    ps = parse_ps_ttys(recorded.PS)
    assert ps["pts/4"] == {"pid": 4501, "comm": "bash"}  # lowest pid wins, not vim
    assert ps["pts/0"]["pid"] == 5100
    assert "pts/1" not in ps  # only pts ttys are kept


def test_tty_of_stat_field7_handles_spaces_in_comm(tmp_path, monkeypatch):
    # comm "(vi m)" contains a space; the ')'-suffix trick still finds field 7
    proc = tmp_path / "4510"
    proc.mkdir()
    (proc / "stat").write_text(recorded.PROC_STAT[4510])
    monkeypatch.setattr(discovery, "PROC_STAT_DIR", str(tmp_path))
    assert tty_of_stat_field7(4510) == "pts/4"
    tail = recorded.PROC_STAT[4510].rsplit(")", 1)[1].split()
    assert int(tail[4]) == 0x5000 | 4  # field 7 = 20484 -> pts/4


async def test_discover_merges_tmux_and_bare(replay):
    sessions = await discovery.discover()
    by_key = {s.key: s for s in sessions}
    assert len(sessions) == 8 + 10  # 8 tmux + 10 bare logins

    dev = by_key["tmux:dev-ai"]
    assert dev.tty == "/dev/pts/4"
    assert dev.attached == 1
    assert dev.meta["panes"] == ["%12"]

    bare = by_key["bare:pts/0"]
    assert bare.kind == "bare"
    assert bare.shell == "bash"
    assert bare.attached == 1
    assert bare.meta["from"] == "192.168.86.44"
    assert by_key["bare:pts/22"].shell == "zsh"

    # every tmux pane tty from list-panes is NOT also a bare session
    assert "bare:pts/4" not in by_key
    assert "bare:pts/22" in by_key  # zsh on pts/22 has no tmux pane
