"""Left-pane widgets: the filterable session list with per-kind badges."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Label, ListItem, ListView

from .model import Session

BADGES = {"tmux": "[tmux]", "bare": "[bare]"}


class SessionList(ListView):
    """Scrollable, filterable list of sessions with kind badges."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sessions: list[Session] = []
        self.filter_text: str = ""

    async def set_sessions(self, sessions: list[Session]) -> None:
        # the store refreshes on a timer: a new session must not yank the
        # cursor off the one you were looking at
        keep = self.selected_session
        self.sessions = sorted(sessions, key=lambda s: s.name)
        await self.refresh_list(keep=keep.key if keep else None)

    async def set_filter(self, text: str) -> None:
        keep = self.selected_session
        self.filter_text = text.lower()
        await self.refresh_list(keep=keep.key if keep else None)

    def visible_sessions(self) -> list[Session]:
        """What the filter currently admits, without touching the DOM.

        The list's *contents* are a question about `sessions` and `filter_text`, and
        answering it through mounted widgets is what made every caller need a running
        app in the first place.
        """
        return [session for session in self.sessions if self._matches(session)]

    def _matches(self, session: Session) -> bool:
        if not self.filter_text:
            return True
        haystack = " ".join(
            part for part in (session.name, session.tty or "", session.shell or "") if part
        ).lower()
        return self.filter_text in haystack

    async def refresh_list(self, keep: str | None = None) -> None:
        """Rebuild the items through ListView's own API.

        The first version built `ListItem`s into a fresh `NodeList` via the private
        `_append` and swapped `self._nodes`, to avoid awaiting a mount. Those widgets
        were in the DOM and never mounted, so their message pumps never ran -- and
        Textual's pilot waits for every child of the screen to drain its queue before
        it will hand control back. It never could: `App.run_test()` hung, all eight
        app tests with it, and the suite never terminated. Awaiting a mount costs a
        keyword; unmounted children in the DOM cost the whole suite.
        """
        await self.clear()
        visible = self.visible_sessions()
        items = [ListItem(Label(self._label(session)), name=session.key) for session in visible]
        if items:
            await self.extend(items)
        keys = [session.key for session in visible]
        self.index = (keys.index(keep) if keep in keys else 0) if items else None

    @staticmethod
    def _label(session: Session) -> Text:
        badge = BADGES.get(session.kind, f"[{session.kind}]")
        label = Text.assemble(
            (badge + " ", "bold cyan" if session.kind == "tmux" else "bold yellow"),
            (session.name, "bold"),
        )
        details: list[str] = []
        if session.shell:
            details.append(session.shell)
        if session.tty:
            details.append(session.tty)
        if session.attached:
            details.append(f"{session.attached} attached")
        if session.created:
            details.append(session.created)
        if details:
            label.append("  " + "  ".join(details), style="dim")
        return label

    @property
    def selected_session(self) -> Session | None:
        if self.index is None:
            return None
        matches = [s for s in self.sessions if self._matches(s)]
        if 0 <= self.index < len(matches):
            return matches[self.index]
        return None
