"""Tests for the session-store actor: diffing refreshes, subscribe/notify."""

from __future__ import annotations

import asyncio

import pytest

from muxter.model import Session, SessionStore


def sess(name: str, kind: str = "tmux", **kw) -> Session:
    return Session(kind=kind, name=name, **kw)


async def test_store_emits_added_and_removed():
    current = [sess("a"), sess("b")]

    async def refresh():
        return list(current)

    store = SessionStore(refresh, interval=0.01)
    events: list[tuple[list, list]] = []
    store.subscribe(lambda added, removed: events.append((added, removed)))

    added, removed = await store.refresh_now()
    assert [s.name for s in added] == ["a", "b"]
    assert removed == []

    current.pop()  # "b" goes away
    added, removed = await store.refresh_now()
    assert added == []
    assert [s.name for s in removed] == ["b"]

    assert [s.key for s in store.sessions] == ["tmux:a"]
    await store.stop()


async def test_store_loop_runs_periodically():
    calls = 0

    async def refresh():
        nonlocal calls
        calls += 1
        return []

    store = SessionStore(refresh, interval=0.01)
    store.start()
    await asyncio.sleep(0.05)
    await store.stop()
    assert calls >= 2


async def test_store_survives_refresh_errors():
    calls = 0

    async def refresh():
        nonlocal calls
        calls += 1
        raise RuntimeError("tmux died")

    store = SessionStore(refresh, interval=0.01)
    store.start()
    await asyncio.sleep(0.05)
    assert store._task is not None and not store._task.done()
    await store.stop()
    assert calls >= 1


async def test_store_unsubscribe():
    async def refresh():
        return [sess("a")]

    store = SessionStore(refresh, interval=10)
    seen = []
    cb = store.subscribe(lambda a, r: seen.append((a, r)))
    store.unsubscribe(cb)
    await store.refresh_now()
    assert seen == []


def test_session_key_is_kind_prefixed():
    assert sess("dev-ai").key == "tmux:dev-ai"
    assert sess("pts/0", kind="bare").key == "bare:pts/0"


@pytest.mark.parametrize("kind", ["tmux", "bare"])
def test_session_fields(kind):
    s = Session(kind=kind, name="x", tty="/dev/pts/1", shell="bash", pid=5,
                created="now", attached=2, meta={"k": "v"})
    assert s.attached == 2
    assert s.meta["k"] == "v"
    assert s.key == f"{kind}:x"
