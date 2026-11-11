#!/usr/bin/env sh
# `turn-end` hook (declared in overlay.cfg): push this session's private-agents changes back to
# the repo. For a symlinked project this just commits/pushes ~/.agents; for a copy-mode project
# it copies .agents back into the store first. Never fails the session. A hook's standard output
# is its answer, so what it has to say goes to standard error.
#
# Env: AGENTS_HOME (default $HOME/.agents),
# CLAUDE_PROJECT_DIR / AGENTS_PROJECT_ROOT (by hand: PWD),
# DOTAGENTS_AGENTS_TOKEN (optional PAT; wired as a credential helper for the push).

AGENTS_DIR="${AGENTS_HOME:-$HOME/.agents}"
# The project is what the harness says it is: CLAUDE_PROJECT_DIR or AGENTS_PROJECT_ROOT. Run by
# hand (no hook), it is the directory the shell is in. As a hook with neither set, the project is
# not known, and the payload's `cwd` is no answer (it is wherever the agent was, and a walk up
# from it could pick another project's private branch): this does nothing.
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-${AGENTS_PROJECT_ROOT:-}}"
if [ -z "$PROJECT_DIR" ] && [ -z "${AOS_HOOK_EVENT:-}" ]; then
    PROJECT_DIR="$PWD"
fi
if [ -z "$PROJECT_DIR" ]; then
    echo "dotagents: private-sync: the harness did not name the project (CLAUDE_PROJECT_DIR, AGENTS_PROJECT_ROOT); doing nothing" >&2
    exit 0
fi

# `link-project` and `sync-project` are run by this overlay's own file, by path, with an isolated
# Python: not through the `dotagents` command line, which would load whatever commands and
# libraries the project's `.agents` holds.
_DG_HERE="$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd)"
_DG_PY="${AGENTS_PYTHON:-python3}"

# Ensure the push can authenticate even if the SessionStart hook didn't run
# (token credential helper + bypass of a github.com -> proxy rewrite). Sourced
# so its exports reach the sync below.
_DG_AUTH="$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd)/_agents-git-auth.sh"
if [ -f "$_DG_AUTH" ]; then
    . "$_DG_AUTH"
    _dotagents_git_auth
fi

"$_DG_PY" -I "$_DG_HERE/run_link.py" sync "$PROJECT_DIR" "$AGENTS_DIR" "sync: ${PROJECT_DIR##*/} session" >&2 \
    || echo "dotagents: sync failed" >&2

exit 0
