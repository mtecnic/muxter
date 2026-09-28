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


def parse_tmux_clients(text: str) -> set[str]:
    """Parse `tmux list-clients -F '#{client_tty}'` into a set of pts names."""
    return {line.strip().removeprefix("/dev/") for line in text.splitlines() if line.strip()}


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

    Field 7 is tty_nr, encoded as (major << 8) | minor: major 136 (0x8800)
    for the unix98 pty pts/ptmx group (pts/N with N = tty_nr & 0xff), and the
    legacy 0x5000 group for the old BSD ptys. tty_nr 0 means no controlling
    tty. Comm can contain spaces and parens, so the fields after state are
    counted from the text after the last ')':
    state(3) ppid(4) pgrp(5) session(6) tty_nr(7), i.e. tail[1].split()[4].

    The recorded PROC_STAT fixtures are *not* in this layout -- they truncate
    each record right after field 7 and spell the device name verbatim one
    field later, so field 7 there is a placeholder. Callers pass `ps` output
    straight through instead; do not trust a fixture-shaped record here.
    """
    try:
        # read the path lazily so tests can monkeypatch PROC_STAT_DIR
        path = f"{PROC_STAT_DIR}/{pid}/stat"
        with open(path, "rb") as fh:
            data = fh.read().decode("ascii", "replace")
    except OSError:
        return None
    # the ')'-suffix trick handles parens and spaces in comm: everything
    # after the last ')' is space-separated, fixed-position fields.
    tail = data.rsplit(")", 1)
    if len(tail) != 2:
        return None
    fields = tail[1].split()
    if len(fields) < 5:
        return None
    try:
        tty_nr = int(fields[4])
    except ValueError:
        return None
    if tty_nr == 0:
        return None
    # a tty_nr outside the unix98 pts group and the legacy 0x5000 group is not
    # a pts we can name (it is a /dev/ttyN, a console, or a pty master)
    if tty_nr & ~0xFF not in (0x8800, 0x5000):
        return None
    return f"pts/{tty_nr & 0xFF}"


async def discover() -> list[Session]:
    """Discover all sessions: tmux sessions plus bare pts logins."""
    sessions_text, panes_text, clients_text, who_text, ps_text = await asyncio.gather(
        run("tmux", "list-sessions", "-F", "#{session_name}\t#{session_created_string}"),
        run("tmux", "list-panes", "-a", "-F", "#{session_name} #{pane_id} #{pane_tty}"),
        run("tmux", "list-clients", "-F", "#{client_tty}"),
        run("who"),
        run("ps", "-e", "-o", "pid,tty,comm"),
    )

    tmux_sessions = parse_tmux_sessions(sessions_text)
    panes = parse_tmux_panes(panes_text)
    client_ttys = parse_tmux_clients(clients_text)
    who = parse_who(who_text)
    ps = parse_ps_ttys(ps_text)

    sessions: list[Session] = []
    pane_ttys: set[str] = set()
    for name, sess in tmux_sessions.items():
        sess_panes = panes.get(name, [])
        # list-panes reports /dev/pts/N; who and ps say pts/N
        pane_ttys.update(p["tty"].removeprefix("/dev/") for p in sess_panes)
        # a tmux session's terminal is the tty its first pane runs on
        tty = sess_panes[0]["tty"] if sess_panes else None
        attached = sum(
            1 for p in sess_panes
            if p["tty"].removeprefix("/dev/") in who and who[p["tty"].removeprefix("/dev/")]["tmux"]
        )
        meta = {"panes": [p["pane_id"] for p in sess_panes]}
        sessions.append(replace(sess, tty=tty, attached=attached, meta=meta))

    # bare sessions: pts logins in `who` that are not a live tmux pane's tty.
    # `ps` is not a session source of its own -- every tmux pane has a shell in
    # `ps` on the pane tty (the fixture's zsh 7100 on pts/22 is
    # clusterspace-pane-fd8404dc's %15 pane) -- and tmux does not write a utmp
    # entry for the shells it spawns, so a ps-only pts is a pane whose session
    # line `who` happens to carry anyway, not a login.
    # A login whose terminal is a tmux client (a window running `tmux attach`)
    # is just a view onto a session already listed, not an extra session.
    for tty, info in who.items():
        if info["tmux"] or tty in pane_ttys or tty in client_ttys:
            continue
        proc = ps.get(tty, {})
        sessions.append(
            Session(
                kind="bare",
                name=tty,
                tty=f"/dev/{tty}",
                shell=proc.get("comm"),
                pid=proc.get("pid") or None,
                created=info["created"],
                attached=1,
                meta={"user": info["user"], "from": info["from"]},
            )
        )
    return sessions
