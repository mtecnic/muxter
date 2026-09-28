#!/usr/bin/env bash
# Launch MuxTer from anywhere. Symlink this into ~/.local/bin to put it on PATH.
set -euo pipefail

here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

if ! command -v tmux >/dev/null 2>&1; then
    echo "muxter: tmux not found on PATH" >&2
    exit 1
fi

if command -v uv >/dev/null 2>&1; then
    exec uv run --project "$here" muxter "$@"
elif [[ -x "$here/.venv/bin/muxter" ]]; then
    exec "$here/.venv/bin/muxter" "$@"
else
    echo "muxter: need uv, or a synced venv at $here/.venv" >&2
    exit 1
fi
