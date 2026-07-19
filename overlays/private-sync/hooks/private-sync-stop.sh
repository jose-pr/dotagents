#!/usr/bin/env sh
# Stop hook: push this session's private-agents changes back to the repo.
# For a symlinked project this just commits/pushes ~/.agents; for a copy-mode
# project it copies .agents back into the store first. Never fails the session.
#
# Env: DOTAGENTS_AGENTS_DIR (default $HOME/.agents), CLAUDE_PROJECT_DIR (or PWD).

AGENTS_DIR="${DOTAGENTS_AGENTS_DIR:-$HOME/.agents}"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"

dotagents_cmd() {
    if command -v dotagents >/dev/null 2>&1; then
        dotagents "$@"
    else
        python -m dotagents "$@"
    fi
}

dotagents_cmd sync --agents-dir "$AGENTS_DIR" --project "$PROJECT_DIR" \
    --message "sync: ${PROJECT_DIR##*/} session" \
    || echo "dotagents: sync failed"

exit 0
