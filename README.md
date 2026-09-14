# MuxTer

A two-pane terminal-session monitor. Left pane: a scrollable, filterable list of this
machine's terminal sessions (tmux sessions and bare pts logins). Right pane: the selected
session rendered live — a terminal-emulator screen seeded with scrollback and updated from
a live byte stream.

Modes: **view** (read-only mirror) and **interact** (keystrokes forwarded to the session;
Esc returns to view). Per-session actions: run a program (send-keys), clear, quit/kill.

## Usage

```
uv run muxter
```

Keys: `j/k` or arrows select, `Enter` connect, `i` interact / `Esc` view, `r` run command,
`K` kill (confirm), `c` clear, `/` filter, `q` quit.

## Design notes

- tmux is both discovery source and connect mechanism: connecting spawns a client PTY
  running `tmux -2 attach-session -t <name>`, so the mirror gets full screen state,
  history, and correct resize/reflow semantics. Scrollback seeds from
  `tmux capture-pane -p -S -500`.
- Sessions are asyncio actors; no locks anywhere. One supervised reader task per connected
  session feeds one UI-facing byte stream.
- The seam is the byte stream: pyte consumes raw bytes end to end, including in tests.
- The client PTY is resized with the right pane (`setwinsize`) so tmux reflows the remote
  app.
