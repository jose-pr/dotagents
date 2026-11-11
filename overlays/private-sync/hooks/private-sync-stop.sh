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
# The project: what the harness says it is (CLAUDE_PROJECT_DIR, AGENTS_PROJECT_ROOT), else the
# directory holding the `.agents` found walking up from the payload's `cwd` (an AOS hook is given
# one JSON object on standard input and its event in AOS_HOOK_EVENT; the payload is read by the
# user's own Python, found by path, not by a pattern over the text). Run by hand, the current
# directory. The payload's `cwd` alone is never the project: it is wherever the agent was.
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-${AGENTS_PROJECT_ROOT:-}}"
if [ -z "$PROJECT_DIR" ] && [ -n "${AOS_HOOK_EVENT:-}" ] && [ -n "${AGENTS_PYTHON:-}" ]; then
    PROJECT_DIR=$("$AGENTS_PYTHON" -c '
import json, os, sys
try:
    here = os.path.abspath(json.load(sys.stdin)["cwd"])
except Exception:
    sys.exit(0)
while True:
    found = os.path.join(here, ".agents")
    store = os.path.realpath(os.environ.get("AGENTS_HOME") or os.path.expanduser("~/.agents"))
    if os.path.exists(found) and os.path.realpath(found) != store:
        print(here)
        break
    parent = os.path.dirname(here)
    if parent == here:
        break
    here = parent
' 2>/dev/null) || PROJECT_DIR=""
fi
[ -n "$PROJECT_DIR" ] || PROJECT_DIR="$PWD"

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
