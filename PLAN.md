## MuxTer — two-pane terminal-session monitor

The workspace holds only the plan from prior sessions (committed as `9ba0ad9`); nothing is built yet. The plan below is that plan, verified against this box and ready to execute.

### Ground truth (re-measured just now)

- tmux 3.4 is the active multiplexer (server pid 3102, ~8 live sessions); `tmux list-panes -a -F '#{session_name} #{pane_id} #{pane_tty}'` is the authoritative session→/dev/pts mapping.
- systemd-logind runs (`loginctl list-sessions`); `who` + `ps -e -o pid,tty,comm` + `/proc/<pid>/stat` field 7 (tty_nr, parsed from the *end* of the line — `comm` can contain spaces/parens) are the fallback enumerators for bare sessions.
- Environment: Python 3.12.3, uv-managed; textual 8.2.8 + rich 15.0.0 (house pins); house packaging: hatchling, `[project.scripts]`, ruff line-length 100.

### Product

Textual app with a **20/80 horizontal split**. Left: scrollable, filterable list of this machine's terminal sessions. Right: the selected session live — a pyte terminal emulator seeded with scrollback, fed by a live byte stream, interactive in tmux style (view mode = read-only mirror; interact mode = keystrokes forwarded; Esc returns to view). Actions: run a program (send-keys), clear, quit/kill.

### Architecture decisions (each tied to a measured fact)

1. **tmux is both discovery source and connect mechanism.** Connect = spawn a client PTY running `tmux -2 attach-session -t <name>`: full screen state, reflow on resize. Scrollback seeds from `tmux capture-pane -p -S -500`. Bare non-tmux pts sessions list as read-only entries (shell/PID/login-time from who + ps + /proc).
2. **Sessions are asyncio actors; no locks** (house invariant). One supervised reader task per connected session feeding one UI-facing byte stream.
3. **The seam is the byte stream** (house invariant): pyte `Screen`/`HistoryScreen` consumes recorded raw-byte fixtures through the real pipeline in tests; nothing mocked above the parser.
4. Client PTY resized with the right pane (ptyprocess `setwinsize`) so tmux reflows; every subprocess spawns with `start_new_session=True`.
5. Packaging per house style: hatchling pyproject, `[project.scripts] muxter`.

### Steps

- [ ] Scaffold uv project: `pyproject.toml` (hatchling; script `muxter = "muxter.app:main"`; deps textual==8.2.8, rich==15.0.0, pyte, ptyprocess; dev: pytest, pytest-asyncio, ruff), package `muxter/`, `.python-version` (3.12), README.
- [ ] `muxter/model.py`: `Session` dataclass (kind tmux|bare, name, tty, shell, pid, created, attached, meta) + session-store actor with add/remove diffing and periodic refresh.
- [ ] `muxter/discovery.py`: tmux discovery (`list-sessions`, `list-panes -a`) + bare-session discovery (who, `ps -e -o pid,tty,comm`, /proc/<pid>/stat field 7 parsed from the end).
- [ ] `muxter/session_io.py`: client-PTY bridge — PtyProcess spawning `tmux -2 attach-session`, non-blocking asyncio reader emitting bytes to subscribers, winsize tracking; `run_command` (send-keys), `kill` (kill-session / SIGTERM leader), `capture-pane` scrollback.
- [ ] `muxter/terminal_screen.py`: pyte Screen + HistoryScreen wrapper consuming the byte stream; Textual widget rendering history-then-live with color.
- [ ] `muxter/app.py` + `muxter/widgets.py`: Horizontal split 1:4 (`Size.from_fraction`); left SessionListView with filter Input and per-kind badge; right terminal pane; keymap: j/k or arrows select, Enter connect, i interact-mode passthrough, Esc view mode, r run-command, K kill (confirm), c clear, / filter, q quit; status bar (mode, tty, shell, refresh age).
- [ ] `tests/`: recorded-fixture discovery tests (real tmux/who/ps outputs captured from this box); byte-stream→pyte tests (colors, cursor motion, resize, scrollback); at least one Textual `run_test()` render test asserting both panes and selection via screenshot text.
- [ ] Verify: `uv run pytest -q` green; `uv run ruff check muxter/ tests/` clean; live smoke: `uv run muxter` lists the 8 real tmux sessions and attaches to one.

### Features beyond the ask (included)

- `/` filter box + tmux-vs-bare badge in the session list.
- Per-session metadata: shell, foreground process, login time, attached count.
- Scrollback on connect so the right pane shows history, not a blank screen.
- Auto-refresh: new sessions appear without restart.
- Kill confirmation so K never nukes a session silently.

### Defaults I'll take unless you redirect

1. "Interact" = full tmux-frontend passthrough; Esc returns to view mode.
2. "Run programs" = command sent into the session via tmux send-keys + Enter.
3. Bare (non-tmux) sessions: listed read-only (kill works; interact needs tmux). Raw-pts mirroring is a v2 candidate.
