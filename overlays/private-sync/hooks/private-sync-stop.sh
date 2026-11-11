#!/usr/bin/env sh
# `turn-end` hook (declared in overlay.cfg): push this session's private-agents changes back to
# the repo. For a symlinked project this just commits/pushes ~/.agents; for a copy-mode project
# it copies .agents back into the store first. Never fails the session. A hook's standard output
# is its answer, so what it has to say goes to standard error.
#
# Env: AGENTS_HOME (default $HOME/.agents),
# CLAUDE_PROJECT_DIR when not run as a hook (or PWD),
# DOTAGENTS_AGENTS_TOKEN (optional PAT; wired as a credential helper for the push).

AGENTS_DIR="${AGENTS_HOME:-$HOME/.agents}"
# The project directory is the `cwd` of the hook's payload (an AOS hook is given one JSON object
# on standard input and its event in AOS_HOOK_EVENT); run by hand it is the project the shell is in.
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
if [ -n "${AOS_HOOK_EVENT:-}" ]; then
    _dg_cwd=$(sed -n 's/.*"cwd": *"\([^"]*\)".*/\1/p' | head -n 1)
    [ -n "$_dg_cwd" ] && PROJECT_DIR="$_dg_cwd"
fi

dotagents_cmd() {
    if command -v dotagents >/dev/null 2>&1; then
        dotagents "$@"
    else
        python -m dotagents "$@"
    fi
}

# Ensure the push can authenticate even if the SessionStart hook didn't run
# (token credential helper + bypass of a github.com -> proxy rewrite). Sourced
# so its exports reach the `dotagents sync-project` subprocess below.
_DG_AUTH="$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd)/_agents-git-auth.sh"
if [ -f "$_DG_AUTH" ]; then
    . "$_DG_AUTH"
    _dotagents_git_auth
fi

dotagents_cmd sync-project --agents-dir "$AGENTS_DIR" --project "$PROJECT_DIR" \
    --message "sync: ${PROJECT_DIR##*/} session" >&2 \
    || echo "dotagents: sync failed" >&2

exit 0
