"""Client-PTY bridge to tmux sessions.

Connecting to a session spawns a client PTY running
`tmux -2 attach-session -t <name>`; an asyncio reader task pumps bytes from
the PTY master to subscribers. Resizing the right pane calls `setwinsize`
(via our own PTY winsize) so tmux reflows the remote app.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import struct
import termios
from collections.abc import Callable

import ptyprocess

log = logging.getLogger("muxter.session_io")

Subscriber = Callable[[bytes], None]


def _encode_winsize(rows: int, cols: int) -> bytes:
    return struct.pack("HHHH", rows, cols, 0, 0)


class SessionConnection:
    """A live connection to one tmux session via a client PTY.

    Lifecycle: create -> connect() -> subscribe / feed / send_keys / resize -> close().
    One supervised reader task emits bytes to subscribers.
    """

    def __init__(self, name: str, rows: int = 24, cols: int = 80):
        self.name = name
        self.rows = rows
        self.cols = cols
        self.closed = False
        self._proc: ptyprocess.PtyProcess | None = None
        self._reader: asyncio.Task | None = None
        self._subscribers: list[Subscriber] = []

    # -- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        """Spawn `tmux -2 attach-session -t <name>` on a new client PTY."""
        proc = await asyncio.to_thread(
            ptyprocess.PtyProcess.spawn,
            ["tmux", "-2", "attach-session", "-t", self.name],
            dimensions=(self.rows, self.cols),
            env={**os.environ, "TERM": "xterm-256color"},
        )
        # duplicate the master fd: connect_read_pipe takes one fd and closes
        # it on EOF, while proc.fd stays open for our writes and terminate()
        master = os.fdopen(os.dup(proc.fd), "rb", 0)
        # ptyprocess's PtyProcess is a subprocess.Popen subclass; our own
        # session was created (ptyprocess uses setsid internally) and this
        # dup'd fd is non-blocking for the asyncio reader.
        os.set_blocking(master.fileno(), False)
        self._proc = proc
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, master)
        self._reader = asyncio.create_task(self._pump(reader))

    async def _pump(self, reader) -> None:
        """Drain the master until EOF, delivering every line to subscribers.

        Delivery is *not* gated on ``self.closed``: a connection closed at
        EOF must still have delivered everything already read.
        """
        try:
            while True:
                data = await reader.readline()
                if not data:
                    break
                for cb in list(self._subscribers):
                    try:
                        cb(data)
                    except Exception:
                        log.exception("subscriber failed")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("reader task crashed for %s", self.name)
        finally:
            self.closed = True

    def close(self) -> None:
        self.closed = True
        if self._reader is not None:
            self._reader.cancel()
        if self._proc is not None:
            try:
                self._proc.terminate(force=2.0)
            except OSError:
                log.exception("could not terminate client-PTY child")
            self._proc = None

    # -- interaction ---------------------------------------------------------

    def subscribe(self, cb: Subscriber) -> Subscriber:
        self._subscribers.append(cb)
        return cb

    def unsubscribe(self, cb: Subscriber) -> None:
        self._subscribers.remove(cb)

    def feed(self, data: bytes) -> None:
        """Write raw input bytes to the session (interact mode)."""
        if self._proc is not None and not self.closed:
            self._proc.write(data)

    def resize(self, rows: int, cols: int) -> None:
        """Resize our client PTY so tmux reflows the remote app."""
        self.rows, self.cols = rows, cols
        if self._proc is not None and not self.closed:
            try:
                fcntl.ioctl(
                    self._proc.fd, termios.TIOCSWINSZ, _encode_winsize(rows, cols)
                )
            except OSError:
                pass

    async def run_command(self, command: str) -> None:
        """Run a program in the session: tmux send-keys <cmd> Enter."""
        await _tmux(
            "tmux", "send-keys", "-t", self.name, "-l", command, "Enter"
        )

    async def capture_scrollback(self, lines: int = 500) -> str:
        """Snapshot the session screen+scrollback as plain text."""
        return await _tmux(
            "tmux", "capture-pane", "-p", "-S", f"-{lines}", "-t", self.name
        )


async def _tmux(*cmd: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"{' '.join(cmd)!r} failed ({proc.returncode}): {stderr.decode(errors='replace').strip()}"
        )
    return stdout.decode("utf-8", "replace")


async def kill_session(name: str) -> None:
    """Kill a tmux session outright."""
    await _tmux("tmux", "kill-session", "-t", name)


async def kill_bare(pid: int) -> None:
    """SIGTERM the leader of a bare session."""
    os.kill(pid, 15)
