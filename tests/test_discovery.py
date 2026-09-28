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
    # comm "(vi m)" contains a space; the ')'-suffix trick still finds field 7.
    # A *real* /proc/<pid>/stat field 7 carries the numeric encoding
    # (major << 8) | minor -- pts/4 is 0x5000 | 4 there, pts/228 is 0x8800 | 228.
    proc = tmp_path / "4510"
    proc.mkdir()
    (proc / "stat").write_text("4510 (vi m) S 4501 4510 3102 20484 ...")
    monkeypatch.setattr(discovery, "PROC_STAT_DIR", str(tmp_path))
    assert tty_of_stat_field7(4510) == "pts/4"
    assert (0x5000 | 4) == 20484  # what a real field 7 holds for pts/4


def test_tty_of_stat_field7_returns_none_without_a_controlling_tty(tmp_path, monkeypatch):
    proc = tmp_path / "3102"
    proc.mkdir()
    # tty_nr 0: a daemon with no controlling terminal, e.g. the tmux server
    (proc / "stat").write_text("3102 (tmux: server) S 1 3102 3102 0 ...")
    monkeypatch.setattr(discovery, "PROC_STAT_DIR", str(tmp_path))
    assert tty_of_stat_field7(3102) is None
    # the recorded PROC_STAT fixtures are truncated after field 7 and spell the
    # device *name* verbatim one field later, so field 7 there is a placeholder
    # number, not a tty_nr -- a fixture-shaped record resolves to nothing.
    (proc / "stat").write_text(recorded.PROC_STAT[7100])
    assert tty_of_stat_field7(3102) is None


async def test_discover_merges_tmux_and_bare(replay):
    sessions = await discovery.discover()
    by_key = {s.key: s for s in sessions}

    dev = by_key["tmux:dev-ai"]
    assert dev.tty == "/dev/pts/4"
    assert dev.attached == 1
    assert dev.meta["panes"] == ["%12"]

    bare = by_key["bare:pts/0"]
    assert bare.kind == "bare"
    assert bare.shell == "bash"
    assert bare.attached == 1
    assert bare.meta["from"] == "192.168.86.44"
    # pts/22 is clusterspace-pane-fd8404dc's %15 pane, so it is a tmux session,
    # never a bare login -- even though its zsh (pid 7100) is in `ps`, which is
    # true of every pane shell: tmux does not write utmp for the shells it
    # spawns, so `ps` is not a session source of its own.
    pane = by_key["tmux:clusterspace-pane-fd8404dc"]
    assert pane.tty == "/dev/pts/22"
    assert "bare:pts/22" not in by_key

    # 17 who logins, 8 of which carry a (tmux) marker -- exactly the 8 pane
    # ttys from list-panes -- leaving 9 bare login shells.
    assert len(sessions) == 8 + 9
    assert sum(1 for s in sessions if s.kind == "bare") == 9
    # every tmux pane tty from list-panes is NOT also a bare session, even
    # when `who` lists a plain login on it (dev-ai's pane lives on pts/4)
    assert "bare:pts/4" not in by_key
    # a pane tty `who` marks (tmux) stays tmux's alone: pts/12 is tab-5's %16
    assert "tmux:tab-5" in by_key
    assert "bare:pts/12" not in by_key
