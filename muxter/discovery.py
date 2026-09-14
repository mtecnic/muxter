"""Session discovery: tmux sessions and bare (non-tmux) pts logins.

All runners go through ``run`` so tests can replay recorded outputs of the real
tmux/who/ps commands captured from this box.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from .model import Session

#: where per-process stat files live; a test seam as well as a path constant
PROC_STAT_DIR = "/proc"


async def run(*cmd: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode("utf-8", "replace")


def parse_tmux_sessions(text: str) -> dict[str, Session]:
    """Parse `tmux list-sessions -F '#{session_name}\\t#{created}'` output."""
    sessions: dict[str, Session] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        name = parts[0].strip()
        if not name:
            continue
        created = parts[1].strip() if len(parts) > 1 else None
        sessions[name] = Session(kind="tmux", name=name, created=created)
    return sessions


def parse_tmux_panes(text: str) -> dict[str, list[dict]]:
    """Parse `tmux list-panes -a -F '#{session_name} #{pane_id} #{pane_tty}'`."""
    panes: dict[str, list[dict]] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        name, pane_id, tty = fields[0], fields[1], fields[2]
        panes.setdefault(name, []).append({"pane_id": pane_id, "tty": tty})
    return panes


def parse_who(text: str) -> dict[str, dict]:
    """Parse `who` output into {pts-name: {user, created, from}}.

    Lines look like: `dev-ai   pts/3   2026-09-03 02:32 (tmux(3102).%6)`.
    The parenthesised field is the peer: tmux(...) for tmux clients, an IP
    for plain ssh logins.
    """
    out: dict[str, dict] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        user, tty = fields[0], fields[1]
        info = {"user": user, "created": " ".join(fields[2:4]), "from": "", "tmux": False}
        rest = " ".join(fields[2:])
        if "(" in line:
            peer = line[line.index("(") + 1 : line.rindex(")")] if ")" in line else ""
            info["from"] = peer
            info["tmux"] = peer.startswith("tmux(")
            info["tmux_pane"] = peer.split(".")[-1] if info["tmux"] else None
            info["tmux_pid"] = peer[5 : peer.index(".")].rstrip(")") if info["tmux"] else None
            del rest
        out[tty] = info
    return out


def parse_ps_ttys(text: str) -> dict[str, dict]:
    """Parse `ps -e -o pid,tty,comm` into {pts-name: {pid, comm}}.

    Only pts ttys are kept. comm can contain spaces/parens, but with -o
    pid,tty,comm the first two fields are space-free, so we split on the
    first two whitespace runs and treat the rest as comm.
    """
    out: dict[str, dict] = {}
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, tty, comm = parts
        if pid_s == "PID" or not tty.startswith("pts/"):
            continue
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        # keep the newest process per tty only if none recorded yet; ps output
        # order is not meaningful, but the session leader is what we want, so
        # prefer the lowest pid (login shells are started before their children)
        if tty not in out or pid < out[tty]["pid"]:
            out[tty] = {"pid": pid, "comm": comm}
    return out


def tty_of_stat_field7(pid: int) -> str | None:
    """Return the controlling tty of a process from /proc/<pid>/stat field 7.

    Field 7 is tty_nr, encoded as (major << 8) | minor, with major 136
    (0x8800) for the unix98 pty pts/ptmx group: pts/N where
    N = tty_nr & 0xff. Comm can contain spaces and parens, so the fields
    after state are counted from the text after the last ')'.
    """
    try:
        # read the path lazily so tests can monkeypatch PROC_STAT_DIR
        path = f"{PROC_STAT_DIR}/{pid}/stat"
        with open(path, "rb") as fh:
            data = fh.read().decode("ascii", "replace")
    except OSError:
        return None
    # count fields from the end is safer, but field 7 is fixed from the start
    # only if comm has no spaces; the ')'-suffix trick handles parens:
    tail = data.rsplit(")", 1)
    if len(tail) != 2:
        return None
    fields = tail[1].split()
    # after ')': fields[0] is state (field 3), so tty_nr (field 7) is fields[4]
    if len(fields) < 5:
        return None
    try:
        tty_nr = int(fields[4])
    except ValueError:
        return None
    if tty_nr == 0:
        return None
    # unix98 ptmx major is 136 (0x8800); a tty_nr outside that group and the
    # legacy 0x5000 group is not a pts we can name
    if tty_nr & ~0xFF not in (0x8800, 0x5000):
        return None
    return f"pts/{tty_nr & 0xFF}"


async def discover() -> list[Session]:
    """Discover all sessions: tmux sessions plus bare pts logins."""
    sessions_text, panes_text, who_text, ps_text = await asyncio.gather(
        run("tmux", "list-sessions", "-F", "#{session_name}\t#{session_created_string}"),
        run("tmux", "list-panes", "-a", "-F", "#{session_name} #{pane_id} #{pane_tty}"),
        run("who"),
        run("ps", "-e", "-o", "pid,tty,comm"),
    )

    tmux_sessions = parse_tmux_sessions(sessions_text)
    panes = parse_tmux_panes(panes_text)
    who = parse_who(who_text)
    ps = parse_ps_ttys(ps_text)

    sessions: list[Session] = []
    tmux_ttys: set[str] = set()
    for name, sess in tmux_sessions.items():
        sess_panes = panes.get(name, [])
        tmux_ttys.update(p["tty"] for p in sess_panes)
        attached = sum(1 for p in sess_panes if p["tty"] in who and who[p["tty"]]["tmux"])
        meta = {"panes": [p["pane_id"] for p in sess_panes]}
        sessions.append(replace(sess, attached=attached, meta=meta))

    # bare sessions: a pts with a process leader that is not a tmux pane
    for tty, info in who.items():
        if info["tmux"] or tty in tmux_ttys:
            continue
        proc = ps.get(tty, {})
        pid = proc.get("pid")
        sessions.append(
            Session(
                kind="bare",
                name=tty,
                tty=f"/dev/{tty}",
                shell=proc.get("comm"),
                pid=pid,
                created=info["created"],
                attached=1,
                meta={"user": info["user"], "from": info["from"]},
            )
        )
    return sessions
