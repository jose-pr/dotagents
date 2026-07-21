#!/usr/bin/env sh
# Stop hook: push this session's private-agents changes back to the repo.
# For a symlinked project this just commits/pushes ~/.agents; for a copy-mode
# project it copies .agents back into the store first. Never fails the session.
#
# Env: DOTAGENTS_AGENTS_DIR (default $HOME/.agents), CLAUDE_PROJECT_DIR (or PWD),
# DOTAGENTS_AGENTS_TOKEN (optional PAT; wired as a credential helper for the push).

AGENTS_DIR="${DOTAGENTS_AGENTS_DIR:-$HOME/.agents}"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"

dotagents_cmd() {
    if command -v dotagents >/dev/null 2>&1; then
        dotagents "$@"
    else
        python -m dotagents "$@"
    fi
}

# Ensure the push can authenticate even if the SessionStart hook didn't run
# (token credential helper + bypass of a github.com -> proxy rewrite). Sourced
# so its exports reach the `dotagents sync` subprocess below.
_DG_AUTH="$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd)/_agents-git-auth.sh"
if [ -f "$_DG_AUTH" ]; then
    . "$_DG_AUTH"
    _dotagents_git_auth
fi

dotagents_cmd sync --agents-dir "$AGENTS_DIR" --project "$PROJECT_DIR" \
    --message "sync: ${PROJECT_DIR##*/} session" \
    || echo "dotagents: sync failed"

exit 0
