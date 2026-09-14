"""pyte-backed terminal screen: history + live view over a byte stream.

A pyte HistoryScreen consumes the raw byte stream of a tmux client PTY. The
Textual widget renders history-then-live with colour.
"""

from __future__ import annotations

import pyte
from rich.text import Text
from textual.widgets import Static


class TerminalScreen:
    """A pyte HistoryScreen fed by raw bytes, with resize support."""

    def __init__(self, cols: int = 80, rows: int = 24, history: int = 1000):
        # pyte's signature is HistoryScreen(columns, lines, history=...).
        self.screen = pyte.HistoryScreen(cols, rows, history=history)
        self.stream = pyte.Stream(self.screen)

    def feed(self, data: bytes | str) -> None:
        """Feed raw terminal bytes; pyte's stream parses the byte stream.

        pyte 0.8.2's Stream.feed has a byte-level UnicodeStream, but a
        :class:`HistoryScreen` is wrapped in an :class:`HistoryScreen` stream
        layer that needs text, so bytes are decoded here.
        """
        self.stream.feed(data.decode("utf-8", "ignore") if isinstance(data, bytes) else data)

    def resize(self, cols: int, rows: int) -> None:
        # pyte's signature is resize(lines, columns).
        self.screen.resize(rows, cols)

    @property
    def rows(self) -> int:
        return self.screen.lines

    @property
    def cols(self) -> int:
        return self.screen.columns

    def live_lines(self) -> list[Text]:
        """The current visible screen as Rich texts with colour preserved."""
        return [self._line_of(self.screen.buffer[row]) for row in range(self.screen.lines)]

    def history_lines(self) -> list[Text]:
        """Scrollback above the visible screen, oldest first.

        pyte's HistoryScreen keeps two deques (top: evicted from the top of
        the live screen, oldest first; bottom: pushed back when paging up,
        newest last). The whole scrollback is top + reversed(bottom).
        """
        history = getattr(self.screen, "history", None)
        if history is None:
            return []
        # top is the scrolled-off rows already pushed out (oldest first);
        # bottom holds rows currently paged back onto the screen, so the
        # scrollback proper stops where the live screen begins.
        rows = list(history.top)
        return [self._line_of(row) for row in rows]

    @staticmethod
    def _line_of(buffer_row: dict) -> Text:
        text = Text()
        # pyte buffer rows are dicts keyed by column; iterate in column order
        for col in sorted(buffer_row):
            char = buffer_row[col]
            text.append(char.data or " ", style=_char_style(char))
        return text


def _char_style(char) -> str:
    """Rich style string from a pyte Char: fg/bg colour and basic attributes."""
    parts: list[str] = []
    fg = getattr(char, "fg", "default")
    bg = getattr(char, "bg", "default")
    if fg and fg != "default":
        parts.append(str(fg))
    if bg and bg != "default":
        parts.append(f"on {bg}")
    if getattr(char, "bold", False):
        parts.append("bold")
    if getattr(char, "reverse", False):
        parts.append("reverse")
    if getattr(char, "italics", False):
        parts.append("italic")
    if getattr(char, "underscore", False):
        parts.append("underline")
    return " ".join(parts)


class TerminalPane(Static):
    """Textual widget rendering a TerminalScreen: history-then-live, with colour.

    In view mode it shows the live screen (scrollback available via
    `toggle_scrollback`); in interact mode the app routes keystrokes to the
    connection feeding this screen.
    """

    def __init__(self, *args, screen: TerminalScreen | None = None, **kwargs):
        # Textual Widget already owns a `screen` property; don't shadow it.
        kwargs["name"] = kwargs.get("name", "terminal")
        super().__init__(*args, **kwargs)
        self.term = screen or TerminalScreen()
        self._show_scrollback = False

    def on_mount(self) -> None:
        self._bind_to_size()
        self.refresh_screen()

    def on_resize(self, event) -> None:
        self._bind_to_size()

    def _bind_to_size(self) -> None:
        width = max(self.size.width, 1)
        height = max(self.size.height, 1)
        if (height, width) != (self.term.rows, self.term.cols):
            self.term.resize(width, height)

    def set_content(self, data: bytes) -> None:
        """Feed a chunk of the live byte stream and redraw."""
        self.term.feed(data)
        self.refresh_screen()

    def set_scrollback(self, text: str) -> None:
        """Seed the screen with captured scrollback (tmux capture-pane)."""
        self.term.screen.history.clear()
        self.term.screen.reset_default_margins()
        self.term.feed(text)
        self.refresh_screen()

    def toggle_scrollback(self) -> bool:
        self._show_scrollback = not self._show_scrollback
        self.refresh_screen()
        return self._show_scrollback

    def refresh_screen(self) -> None:
        if self._show_scrollback:
            lines = self.term.history_lines()[-self.term.rows:]
        else:
            lines = self.term.live_lines()
        self.update(Text("\n").join(lines) if lines else Text(""))
