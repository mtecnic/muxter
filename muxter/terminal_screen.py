"""pyte-backed terminal screen: history + live view over a byte stream.

A pyte HistoryScreen consumes the raw byte stream of a tmux client PTY. The
Textual widget renders history-then-live with colour.
"""

from __future__ import annotations

import codecs
import collections
import dataclasses

import pyte
from rich.text import Text
from textual.widgets import Static


@dataclasses.dataclass(frozen=True)
class Char:
    """A single cell: the character and its SGR presentation."""

    data: str = " "
    fg: str = "default"
    bg: str = "default"
    bold: bool = False
    italics: bool = False
    underscore: bool = False
    strikethrough: bool = False
    reverse: bool = False
    blink: bool = False


#: The cell every untouched buffer position holds.
DEFAULT_CHAR = Char()

def init_screen(screen: pyte.HistoryScreen) -> None:
    """Make a HistoryScreen behave like a terminal, in two ways pyte lacks.

    1. It enables DECAWM (modes are a public set, so just add it): pyte's
       default mode set omits autowrap, so a line longer than the width is
       silently truncated instead of continuing on the next row -- nothing
       would scroll either, since pyte only scrolls on an explicit newline.
       DECOM (origin mode) is enabled for the same reason: the cursor should
       respect the scroll region's top margin. DECTCEM makes the cursor visible
       when it is at the bottom of the history.
    2. It replaces the history deques with ones that *snapshot* each row as it
       scrolls off. pyte appends the live buffer row -- a sparse dict keyed by
       column -- by reference, and then reuses that very dict as the new bottom
       buffer row, so later writes splice in at whatever column the cursor
       lands on and a row captured at 80 cols resurfaces carrying its text at
       whatever geometry the reused row accumulated. Materialising every column
       0..cols into a fresh dict freezes each row's geometry at scroll-off.
    """
    # DECAWM and DECOM: pyte's default mode set omits autowrap and (in
    # HistoryScreen) origin mode, so a long line would truncate instead of wrap
    # and the cursor would ignore the scroll region's top margin.
    screen.mode.update({pyte.modes.LNM, pyte.modes.DECAWM, pyte.modes.DECOM, pyte.modes.DECTCEM})
    top = FullWidthRows(maxlen=screen.history.size, cols=screen.columns)
    top.extend(screen.history.top)
    # bottom is newest-first; keep that order when swapping the deques.
    bottom = FullWidthRows(maxlen=screen.history.size, cols=screen.columns)
    bottom.extend(reversed(list(screen.history.bottom)))
    screen.history = screen.history._replace(top=top, bottom=bottom)
    # A stream dispatches to the screen object captured at construction, so it
    # must be rebound to the screen whose history we just swapped in.
    screen.stream = pyte.Stream(screen)


def _install_reset_hook(screen: pyte.HistoryScreen) -> None:
    """Wrap the screen's *bound* reset so our setup survives every reset.

    HistoryScreen.reset() installs a plain History and rebinds the stream, which
    would undo init_screen(). HistoryScreen wraps its event methods (including
    reset) via ``__getattribute__``, so ``screen.reset`` is a freshly-built
    wrapper on every access; binding it to a local first captures the *original*
    wrapped reset before we overwrite the instance attribute, so the wrapper we
    install calls the real reset and then re-runs our init — and never itself.
    """
    bound_reset = screen.reset

    def _reset() -> None:
        bound_reset()
        init_screen(screen)

    screen.reset = _reset


class FullWidthRows(collections.deque):
    """A history deque whose rows arrive snapshotted to the full width."""

    def __init__(self, maxlen: int | None = None, cols: int = 80):
        super().__init__(maxlen=maxlen)
        self.cols = cols

    def append(self, row: dict) -> None:
        super().append(_materialise(row, self.cols))

    def appendleft(self, row: dict) -> None:
        super().appendleft(_materialise(row, self.cols))


def _materialise(row: dict, cols: int) -> dict:
    """Copy a sparse buffer row with every column 0..cols materialised.

    Copied, not mutated in place: the live buffer row this snapshot is taken
    from goes straight back into the buffer, where it keeps being written; the
    frozen history copy must not share it.
    """
    return {col: row.get(col, DEFAULT_CHAR) for col in range(cols)}


class TerminalScreen:
    """A pyte HistoryScreen fed by raw bytes, with resize support."""

    def __init__(self, cols: int = 80, rows: int = 24, history: int = 1000):
        # pyte's signature is HistoryScreen(columns, lines, history=...).
        self.screen = pyte.HistoryScreen(cols, rows, history=history)
        # Wrap reset() *before* the first init, so every later reset re-applies
        # our setup instead of undoing it, and so the init below runs on top of
        # the screen's own constructor reset.
        _install_reset_hook(self.screen)
        # Autowrap + full-width history snapshots + a stream bound to *this*
        # screen object (a Stream dispatches to the screen captured at its
        # construction, so it must be built after the deques are swapped).
        init_screen(self.screen)
        self.stream = self.screen.stream
        # PTY reads split anywhere, including mid-character
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def feed(self, data: bytes | str) -> None:
        """Feed raw terminal bytes; pyte's stream parses the stream of text."""
        self.stream.feed(self._decoder.decode(data) if isinstance(data, bytes) else data)

    def reset(self) -> None:
        """Blank the live screen; the reset hook re-applies init_screen()."""
        self.screen.reset()
        self.stream = self.screen.stream

    def clear_history(self) -> None:
        """Drop scrollback; pyte's History is a namedtuple of two deques."""
        self.screen.history.top.clear()
        self.screen.history.bottom.clear()

    def resize(self, cols: int, rows: int) -> None:
        """Resize the screen, reflowing content rather than clipping it.

        pyte's own ``Screen.resize`` clips over-wide rows at the right edge --
        it never re-wraps them -- and silently ignores a lines-only change
        whenever the column count already matches. Instead: render history plus
        live screen as plain text at the old width, then replay it through a
        fresh screen at the new one. Each rendered row ends in an explicit
        ``\\r\\n``... except the last: a trailing newline on a full screen would
        push the last line into history. With autowrap on, any row longer than
        the new width wraps naturally, and the new cursor lands after the last
        line -- where a real terminal resumes. tmux reflows the real session
        the same way when the client PTY resizes.
        """
        # Rows are rendered rstripped, so the padding that materialising added
        # never re-flows as content at the new width.
        content = [self._row_text(row).rstrip() for row in self._history_rows()]
        content += [ln.rstrip() for ln in self.live_plain()]
        while content and not content[-1]:
            content.pop()
        fresh = pyte.HistoryScreen(cols, rows, history=self.screen.history.size)
        # Same swap as in __init__; init_screen also rebinds the stream.
        _install_reset_hook(fresh)
        init_screen(fresh)
        if content:
            fresh.stream.feed("\r\n".join(content))
        self.screen = fresh
        self.stream = fresh.stream

    @property
    def rows(self) -> int:
        return self.screen.lines

    @property
    def cols(self) -> int:
        return self.screen.columns

    def live_lines(self) -> list[Text]:
        """The current visible screen as Rich texts with colour preserved.

        Lines are right-stripped: a terminal line has no visible end, and
        trailing padding would re-flow as content if the pane were resized.
        """
        return [self._line_of(self.screen.buffer[row]) for row in range(self.screen.lines)]

    def live_plain(self) -> list[str]:
        """Plain text of the visible screen, one string per row."""
        return [self._row_text(self.screen.buffer[row]) for row in range(self.screen.lines)]

    def _history_rows(self) -> list[dict]:
        """Scrollback rows, oldest first.

        pyte's HistoryScreen keeps two deques: ``top`` holds the rows that
        scrolled off, ``bottom`` the rows paged back onto the screen, newest
        last -- so the scrollback proper is top followed by bottom reversed.
        """
        history = getattr(self.screen, "history", None)
        if history is None:
            return []
        return list(history.top) + list(reversed(history.bottom))

    def history_lines(self) -> list[Text]:
        return [self._line_of(row) for row in self._history_rows() if self._row_text(row).strip()]

    def history_plain(self) -> list[str]:
        """Plain text of the scrollback rows, oldest first, blank rows dropped."""
        return [text for text in map(self._row_text, self._history_rows()) if text.strip()]

    @staticmethod
    def _row_text(buffer_row: dict) -> str:
        """Plain text of a buffer row, gaps filled with default chars.

        Rows are materialised full-width by FullWidthRows, but a row that never
        scrolled off (a live row read here before any resize) is still sparse,
        so iterate 0..max and fill gaps rather than trusting sorted keys.

        NOT rstripped: this feeds resize/set_scrollback replay and the tests
        assert full-width content. Callers that want display text use
        live_lines()/history_lines(), which rstrip the Text.
        """
        return "".join(
            buffer_row[col].data for col in range(max(buffer_row, default=-1) + 1)
        )

    @staticmethod
    def _line_of(buffer_row: dict) -> Text:
        # pyte buffer rows are sparse dicts keyed by column; a scrolled-off
        # history row keeps only the columns that were written. Render from
        # column 0, gaps as default chars, so such a row shows its text whole.
        text = Text()
        for col in range(max(buffer_row, default=-1) + 1):
            char = buffer_row.get(col, DEFAULT_CHAR)
            # Text.append already extends the last span when its style equals
            # the running style, so a run of same-styled cells merges naturally.
            text.append(char.data or " ", style=_char_style(char))
        # A terminal line has no visible end: trim the padding past the text.
        text.rstrip()
        return text


def _rich_color(color: str) -> str:
    """pyte colour name -> rich colour name.

    pyte spells 256-colour and truecolour as bare hex ("00ff5f"), yellow as
    "brown", and the bright set as "brightred"; rich rejects all three.
    """
    if len(color) == 6 and all(c in "0123456789abcdefABCDEF" for c in color):
        return f"#{color}"
    if color.startswith("bright") and not color.startswith("bright_"):
        color = "bright_" + color[len("bright"):]
    return color.replace("brown", "yellow")


def _char_style(char) -> str:
    """Rich style string from a pyte Char: fg/bg colour and basic attributes."""
    parts: list[str] = []
    fg = getattr(char, "fg", "default")
    bg = getattr(char, "bg", "default")
    if fg and fg != "default":
        parts.append(_rich_color(str(fg)))
    if bg and bg != "default":
        parts.append(f"on {_rich_color(str(bg))}")
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

    #: the pane must be focusable: in interact mode it owns the keystrokes
    can_focus = True

    def __init__(self, *args, screen: TerminalScreen | None = None, **kwargs):
        # Textual Widget already owns a `screen` property; don't shadow it.
        kwargs.setdefault("name", "terminal")
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
        """Seed the screen with captured scrollback (tmux capture-pane).

        Rebuilds the screen at the pane's size and replays the captured text
        through it, so the scrollback reflows to the current pane width.
        """
        self.term.resize(self.term.cols, self.term.rows)
        self.term.feed(text)
        self.term.feed("\r\n")
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
