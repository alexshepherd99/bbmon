#!/usr/bin/env bash
# Launch Claude Code with the shared `agentic` repo mounted, so its
# skills and agents load. Relative paths only — nothing
# machine-specific is committed.
set -euo pipefail

cd "$(dirname "$0")"

agentic_dir="../agentic"

if [ ! -d "$agentic_dir" ]; then
    echo "error: no agentic repo at $agentic_dir (expected beside this repo)" >&2
    exit 1
fi

exec claude --add-dir "$agentic_dir" "$@"
