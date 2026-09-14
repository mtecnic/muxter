"""Session model and the session-store actor.

The store owns the authoritative list of sessions. A refresh function (discovery)
returns a list of Session objects; the store diffs them against what it holds and
emits add/remove events to subscribers. No locks: a single actor task owns state.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

log = logging.getLogger("muxter.model")


@dataclass(frozen=True)
class Session:
    """One terminal session on this machine."""

    kind: str  # "tmux" or "bare"
    name: str  # tmux session name, or the pts device for bare sessions
    tty: str | None = None  # e.g. /dev/pts/4
    shell: str | None = None
    pid: int | None = None  # leader/login process pid where known
    created: str | None = None  # creation/login time, human string
    attached: int = 0
    meta: dict = field(default_factory=dict, hash=False, compare=False)

    @property
    def key(self) -> str:
        """Stable identity: kind + name."""
        return f"{self.kind}:{self.name}"


RefreshFn = Callable[[], Awaitable[list[Session]]]


class SessionStore:
    """Actor holding the session list, refreshed periodically.

    Subscribers receive (added, removed) lists on every change. The refresh
    function is injected so tests can replay recorded tmux/who/ps fixtures.
    """

    def __init__(self, refresh: RefreshFn, interval: float = 5.0):
        self._refresh = refresh
        self._interval = interval
        self._sessions: dict[str, Session] = {}
        self._task: asyncio.Task | None = None
        self._subscribers: list[
            Callable[[list[Session], list[Session]], Awaitable[None] | None]
        ] = []

    @property
    def sessions(self) -> list[Session]:
        return sorted(self._sessions.values(), key=lambda s: s.name)

    def subscribe(
        self, cb: Callable[[list[Session], list[Session]], None]
    ) -> Callable[[list[Session], list[Session]], None]:
        self._subscribers.append(cb)
        return cb

    def unsubscribe(self, cb: Callable[[list[Session], list[Session]], None]) -> None:
        self._subscribers.remove(cb)

    async def refresh_now(self) -> tuple[list[Session], list[Session]]:
        """Run one refresh; return (added, removed)."""
        fresh = await self._refresh()
        new: dict[str, Session] = {s.key: s for s in fresh}
        added = [s for k, s in new.items() if k not in self._sessions]
        removed = [s for k, s in self._sessions.items() if k not in new]
        self._sessions = new
        if added or removed:
            for cb in self._subscribers:
                result = cb(added, removed)
                if inspect.isawaitable(result):
                    await result
        return added, removed

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.refresh_now()
            except Exception:
                # a transient tmux failure must not kill the loop
                log.exception("session refresh failed")
            await asyncio.sleep(self._interval)
