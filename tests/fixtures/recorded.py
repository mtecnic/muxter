"""Recorded tmux outputs, captured live from this box (tmux 3.4).

Regenerate with: tmux list-sessions; tmux list-panes -a -F '...'
"""

# captured with: tmux list-sessions -F '#{session_name}\t#{session_created_string}'
# and:           tmux list-panes -a -F '#{session_name} #{pane_id} #{pane_tty}'
LIST_SESSIONS = """\
clusterspace-pane-d73d00ce\tWed Sep  9 03:10:35 2026
clusterspace-pane-fd8404dc\tWed Sep  9 03:11:49 2026
clusterspace-pane-ffe6467b\tWed Sep  9 03:11:32 2026
testuser\tTue Sep  8 04:47:49 2026
tab-4\tThu Sep  3 02:32:00 2026
tab-5\tFri Sep 11 00:30:37 2026
tab-7\tSun Sep  6 15:39:33 2026
tempmon\tWed Sep  2 15:05:20 2026
"""

LIST_PANES = """\
clusterspace-pane-d73d00ce %13 /dev/pts/18
clusterspace-pane-fd8404dc %15 /dev/pts/22
clusterspace-pane-ffe6467b %14 /dev/pts/20
testuser %12 /dev/pts/4
tab-4 %6 /dev/pts/3
tab-5 %16 /dev/pts/12
tab-7 %8 /dev/pts/15
tempmon %3 /dev/pts/8
"""

# NOT recorded: written to match the logins above. Seven of the nine plain
# ssh logins are windows running `tmux attach`; pts/0 (bash + htop) and
# pts/11 are real shells.
# shape of:      tmux list-clients -F '#{client_tty}'
LIST_CLIENTS = """\
/dev/pts/1
/dev/pts/2
/dev/pts/5
/dev/pts/6
/dev/pts/7
/dev/pts/9
/dev/pts/10
"""

WHO = """\
testuser   pts/0        2026-09-11 00:15 (192.168.1.44)
testuser   pts/1        2026-09-11 00:15 (192.168.1.44)
testuser   pts/2        2026-09-11 00:15 (192.168.1.44)
testuser   pts/3        2026-09-03 02:32 (tmux(3102).%6)
testuser   pts/4        2026-09-08 04:47 (tmux(3102).%12)
testuser   pts/7        2026-09-11 00:15 (192.168.1.44)
testuser   pts/8        2026-09-02 15:05 (tmux(3102).%3)
testuser   pts/9        2026-09-11 00:15 (192.168.1.44)
testuser   pts/10       2026-09-11 00:15 (192.168.1.44)
testuser   pts/11       2026-09-11 00:30 (192.168.1.44)
testuser   pts/12       2026-09-11 00:30 (tmux(3102).%16)
testuser   pts/5        2026-09-11 00:15 (192.168.1.44)
testuser   pts/15       2026-09-06 15:39 (tmux(3102).%8)
testuser   pts/18       2026-09-09 03:10 (tmux(3102).%13)
testuser   pts/20       2026-09-09 03:11 (tmux(3102).%14)
testuser   pts/22       2026-09-09 03:11 (tmux(3102).%15)
testuser   pts/6        2026-09-11 00:15 (192.168.1.44)
"""

PS = """\
    PID TTY      COMMAND
   3102 ?        tmux: server
   4501 pts/4    bash
   4510 pts/4    vim
   5100 pts/0    bash
   5200 pts/0    htop
   7100 pts/22   zsh
"""

# /proc/<pid>/stat samples: field 7 is tty_nr; comm in parens may contain spaces.
PROC_STAT = {
    4510: "4510 (vi m) S 4501 4510 3102 7 pts/4 ...",
    5200: "5200 (htop) S 5100 5200 3102 7 pts/0 ...",
    7100: "7100 (zsh) S 1 7100 3102 7 pts/22 ...",
}
