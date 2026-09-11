# MuxTer — two-pane terminal-session monitor

# MuxTer — two-pane terminal-session monitor

## Context

Greenfield build; the workspace is empty (prior planning sessions wrote nothing to disk). Recon on this box established the facts the design rests on:

- **tmux 3.4 is the active multiplexer** (server pid 3102, 8 live sessions; nearly every pts is a tmux pane or tmux client). `tmux list-panes -a -F '#{session_name} #{pane_id} #{pane_tty}'` is the authoritative session→/dev/pts mapping.
- **systemd-logind runs** (`/usr/bin/loginctl`; ~10 sessions under /run/systemd/sessions/); `who` and `ps -e -o pid,tty,comm` + `/proc/<pid>/stat` field 7 (tty_nr) are the offline fallback enumerators. When parsing /proc stat, count fields from the end — `comm` can contain spaces/parens (`tmux: client`).
- **Environment**: Python 3.12.3, uv-managed; textual 8.2.8 + rich 15.0.0 (house pins). House project style: hatchling, `[project.scripts]`, ruff line-length 100, py312.

## The product

A Textual app with a **20/80 horizontal split**. Left: scrollable, filterable list of this machine's terminal sessions. Right: the selected session rendered live — a terminal-emulator screen seeded with scrollback and updated from a live byte stream. Modes: **view** (read-only mirror) and **interact** (keystrokes forwarded to the session; a tmux-style escape key returns to view). Per-session actions: run a program (send-keys), clear, quit/kill.

## Architecture decisions (each tied to a measured fact)

1. **tmux is both discovery source and connect mechanism.** Connect = spawn a client PTY running `tmux -2 attach-session -t <name>`: full screen state, history, correct resize/reflow semantics — everything raw /dev/pts sniffing cannot give. Scrollback seeds from `tmux capture-pane -p -S -500`. Bare non-tmux pts sessions (plain ssh logins) are listed as read-only entries with shell/PID/login-time from who + ps + proc.
2. **Sessions are asyncio actors; no locks anywhere** (house invariant). One supervised reader task per connected session feeds one UI-facing byte stream.
3. **The seam is the byte stream** (house invariant): pyte (Screen + HistoryScreen) consumes recorded raw-byte fixtures through the real pipeline in tests; nothing is mocked above the parser.
4. Client PTY is resized with the right pane (ptyprocess `setwinsize`) so tmux reflows the remote app; every subprocess spawns with `start_new_session=True` (house invariant).
5. Packaging follows house style: hatchling pyproject, `[project.scripts] muxter`.

## Steps

- [ ] Scaffold uv project: `pyproject.toml` (hatchling; script `muxter = "muxter.app:main"`; deps textual==8.2.8, rich==15.0.0, pyte, ptyprocess; dev: pytest, pytest-asyncio, ruff), package `muxter/`, `.python-version` (3.12), README.
- [ ] `muxter/model.py`: `Session` dataclass (kind tmux|bare, name, tty, shell, pid, created, attached, meta) + session-store actor with add/remove diffing and periodic refresh.
- [ ] `muxter/discovery.py`: tmux discovery (`list-sessions`, `list-panes -a`) + bare-session discovery (who, `ps -e -o pid,tty,comm`, /proc/<pid>/stat field 7 parsed from the end).
- [ ] `muxter/session_io.py`: client-PTY bridge — ptyprocess.PtyProcess spawning `tmux -2 attach-session`, non-blocking asyncio reader emitting bytes to subscribers, winsize tracking; `run_command` (send-keys), `kill` (kill-session / SIGTERM leader), `capture-pane` scrollback.
- [ ] `muxter/terminal_screen.py`: pyte Screen + HistoryScreen wrapper consuming the byte stream; Textual widget rendering history-then-live with color.
- [ ] `muxter/app.py` + `muxter/widgets.py`: Horizontal split 1:4 (`Size.from_fraction`); left SessionListView with filter Input and per-kind badge; right terminal pane; keymap: j/k or arrows select, Enter connect, i/Enter interact-mode passthrough, Esc view-mode, r run-command, K kill (confirm), c clear, / filter, q quit; status bar (mode, tty, shell, refresh age).
- [ ] `tests/`: recorded-fixture discovery tests (real tmux/who/ps outputs captured from this box); byte-stream→pyte tests (colors, cursor motion, resize, scrollback); at least one Textual `run_test()` render test asserting both panes and selection via screenshot text.
- [ ] Verify: `uv run pytest -q` green; `uv run ruff check muxter/ tests/` clean; live smoke: `uv run muxter` lists the 8 real tmux sessions and attaches to one.

## Features beyond the ask (included in the steps above)

- `/` filter box + tmux-vs-bare badge in the session list.
- Per-session metadata: shell, foreground process, login time, attached count.
- Scrollback on connect (capture-pane) so the right pane shows history, not a blank screen.
- Auto-refresh: new ssh logins / tmux sessions appear without restart.
- Kill confirmation so K never nukes a session silently.

## Defaults I'll take unless you redirect

1. "Interact" = full tmux-frontend passthrough; Esc returns to view mode.
2. "Run programs" = command sent into the session via tmux send-keys + Enter.
3. Bare (non-tmux) sessions: listed read-only (kill works; interact needs tmux). True raw-pts mirroring is a v2 candidate.
