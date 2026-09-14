"""Tests for the client-PTY bridge: ptyprocess spawn, pump, feed, resize, kill."""

from __future__ import annotations

import asyncio

import pytest

from muxter import session_io
from muxter.session_io import SessionConnection, kill_bare, kill_session


class FakeProc:
    """Minimal stand-in for ptyprocess.PtyProcess."""

    def __init__(self, fd=7):
        self.fd = fd
        self.written: list[bytes] = []
        self.terminated = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def terminate(self, force=1.0) -> None:
        self.terminated = True


@pytest.fixture
def spawned(monkeypatch):
    """Replace PtyProcess.spawn and the fdopen the reader uses."""
    procs: list[FakeProc] = []

    class FakeMaster:
        """Stand-in for the dup'd master file: a StreamReader in file clothes."""

        def __init__(self):
            self.reader = asyncio.StreamReader()
            self.reader.feed_data(b"hello from tmux\n")
            self.reader.feed_eof()

        def fileno(self):
            return 7

        def close(self):
            pass

        async def readline(self):
            return await self.reader.readline()

    def fake_spawn(cmd, dimensions=None, env=None):
        proc = FakeProc()
        proc.spawn_cmd = cmd
        proc.dimensions = dimensions
        procs.append(proc)
        return proc

    monkeypatch.setattr(session_io.ptyprocess.PtyProcess, "spawn", staticmethod(fake_spawn))
    monkeypatch.setattr(session_io.os, "set_blocking", lambda fd, mode: None)
    monkeypatch.setattr(session_io.os, "dup", lambda fd: 7)
    monkeypatch.setattr(session_io.os, "fdopen", lambda fd, mode, buffering: FakeMaster())
    return procs


async def test_connect_spawns_tmux_attach_and_pumps(spawned):
    conn = SessionConnection("dev-ai", rows=30, cols=120)
    received: list[bytes] = []
    conn.subscribe(received.append)
    await conn.connect()
    assert spawned[0].spawn_cmd == ["tmux", "-2", "attach-session", "-t", "dev-ai"]
    assert spawned[0].dimensions == (30, 120)
    await asyncio.sleep(0.05)
    assert received == [b"hello from tmux\n"]
    conn.close()


async def test_feed_writes_to_proc(spawned):
    conn = SessionConnection("x")
    await conn.connect()
    conn.feed(b"ls\r")
    assert spawned[0].written == [b"ls\r"]
    conn.close()


async def test_close_marks_closed_and_terminates(spawned):
    conn = SessionConnection("x")
    await conn.connect()
    conn.close()
    assert conn.closed is True
    assert spawned[0].terminated is True
    conn.feed(b"ignored")
    assert spawned[0].written == []  # closed -> writes are dropped


async def test_unsubscribe_stops_delivery(spawned):
    conn = SessionConnection("x")
    got = []
    cb = conn.subscribe(got.append)
    conn.unsubscribe(cb)
    await conn.connect()
    await asyncio.sleep(0.05)
    assert got == []
    conn.close()


async def test_run_command_sends_keys(monkeypatch):
    calls = []

    async def fake_tmux(*cmd):
        calls.append(cmd)
        return ""

    monkeypatch.setattr(session_io, "_tmux", fake_tmux)
    conn = SessionConnection("dev-ai")
    await conn.run_command("ls -la")
    assert calls == [("tmux", "send-keys", "-t", "dev-ai", "-l", "ls -la", "Enter")]


async def test_capture_scrollback_uses_capture_pane(monkeypatch):
    calls = []

    async def fake_tmux(*cmd):
        calls.append(cmd)
        return "old output\n"

    monkeypatch.setattr(session_io, "_tmux", fake_tmux)
    conn = SessionConnection("dev-ai")
    out = await conn.capture_scrollback(lines=100)
    assert out == "old output\n"
    assert calls == [("tmux", "capture-pane", "-p", "-S", "-100", "-t", "dev-ai")]


async def test_tmux_nonzero_raises(monkeypatch):
    class Proc:
        returncode = 1

        async def communicate(self):
            return b"", b"no such session\n"

    async def fake_exec(*cmd, **kw):
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(RuntimeError, match="no such session"):
        await kill_session("ghost")


async def test_kill_bare_sends_sigterm(monkeypatch):
    import signal

    sent = []
    monkeypatch.setattr(session_io.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    await kill_bare(4501)
    assert sent == [(4501, signal.SIGTERM)]
