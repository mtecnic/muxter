"""Tests for the pyte-backed terminal screen: byte stream -> screen state."""

from __future__ import annotations

from muxter.terminal_screen import TerminalScreen


def screen(rows=5, cols=20, history=100):
    return TerminalScreen(cols=cols, rows=rows, history=history)


def test_feed_plain_text():
    ts = screen()
    ts.feed(b"hello world")
    lines = ts.live_lines()
    assert lines[0].plain == "hello world"
    assert lines[1].plain == ""


def test_feed_color_and_attributes():
    ts = screen()
    ts.feed(b"\x1b[31mRED\x1b[0m \x1b[1;4mboldunderline\x1b[0m")
    text = ts.live_lines()[0]
    styled = {span.style for span in text.spans}
    assert "red" in styled
    assert "bold" in " ".join(span.style for span in text.spans)
    assert "underline" in " ".join(span.style for span in text.spans)
    assert text.plain.startswith("RED boldunderline")


def test_cursor_motion_overwrites():
    ts = screen()
    ts.feed(b"AAAAAAAAAA\nBBBBBBBBBB")
    ts.feed(b"\x1b[1;1HXY")
    first = ts.live_lines()[0].plain
    assert first.startswith("XY")
    assert first[2:] == "AAAAAAAAAA"[2:]


def test_line_wrap_wraps_onto_next_row():
    ts = screen(rows=2, cols=5)
    ts.feed(b"123456789")
    lines = ts.live_lines()
    assert lines[0].plain == "12345"
    assert lines[1].plain == "6789"


def test_resize_reflows_instead_of_clipping():
    ts = screen(rows=2, cols=10)
    ts.feed(b"aaaabbbbccccdddd")
    ts.resize(5, 4)
    lines = [ln.plain for ln in ts.live_lines()]
    assert ts.rows == 4 and ts.cols == 5
    assert len(lines) == 4
    # Content is replayed at the new width: each rendered row at the old width
    # hard-wraps into the new one -- "aaaabbbbcc" -> two 5-col rows, "ccdddd"
    # -> "ccddd"+"d" -- and the last row scrolls off the 4-row screen.
    assert lines == ["aaaab", "bbbcc", "ccddd", "d"]


def test_scrollback_keeps_scrolled_off_lines():
    ts = screen(rows=2, cols=10, history=50)
    ts.feed(b"old1\nold2\nnew1\nnew2")
    live = [ln.plain for ln in ts.live_lines()]
    assert live == ["new1", "new2"]
    history = [ln.plain for ln in ts.history_lines()]
    assert history == ["old1", "old2"]


def test_erase_display_clears_screen():
    ts = screen()
    ts.feed(b"gone")
    ts.feed(b"\x1b[H\x1b[2J")
    # ED2 clears below the cursor; the test screen has nothing above the cursor
    assert all(ln.plain == "" for ln in ts.live_lines())
    # ED2 alone does not clear scrollback — ED3 ("reset") does
    ts.feed(b"gone")
    ts.feed(b"\x1b[3J")
    assert ts.history_lines() == []


def test_feed_in_small_chunks():
    """A byte stream split into 1-byte chunks must render identically."""
    ts = screen()
    data = b"hello \x1b[32mworld\x1b[0m"
    for i in range(len(data)):
        ts.feed(data[i : i + 1])
    assert ts.live_lines()[0].plain == "hello world"
