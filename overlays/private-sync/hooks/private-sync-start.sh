#!/usr/bin/env sh
# `session-start` hook (declared in overlay.cfg): make the private ~/.agents repo present, then
# link this project's .agents to its per-project store. Safe to run every session; never fails
# the session (always exits 0). A hook's standard output is its answer, so what it has to say
# goes to standard error.
#
# Env:
#   AGENTS_REMOTE            git URL of your private .agents repo. Prefer a
#                            TOKENLESS https URL (https://github.com/<you>/.agents.git)
#                            plus DOTAGENTS_AGENTS_TOKEN below; a token may be
#                            embedded here instead, but then it is persisted in
#                            .git/config on disk.
#   DOTAGENTS_AGENTS_TOKEN   (recommended) a fine-grained PAT scoped to the repo
#                            (Contents: read/write). Wired via a git credential
#                            helper that reads it from the environment at auth
#                            time, so it is never written to .git/config or disk.
#   AGENTS_HOME              where the private repo lives (default: $HOME/.agents)
#   CLAUDE_PROJECT_DIR       project checkout when not run as a hook (falls back to PWD)

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

# Wire git auth for the private repo (token credential helper; bypass a
# github.com -> in-session-proxy rewrite when present). Sourced so its exports
# reach this shell and the `dotagents` subprocesses below.
_DG_AUTH="$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd)/_agents-git-auth.sh"
if [ -f "$_DG_AUTH" ]; then
    . "$_DG_AUTH"
    _dotagents_git_auth
fi

# 1. Ensure the private repo is present and current.
if [ -d "$AGENTS_DIR/.git" ]; then
    git -C "$AGENTS_DIR" pull --rebase --autostash --quiet \
        || echo "dotagents: pull failed (offline?), using local copy" >&2
elif [ -n "${AGENTS_REMOTE:-}" ]; then
    echo "dotagents: cloning private agents repo into $AGENTS_DIR" >&2
    git clone --quiet "$AGENTS_REMOTE" "$AGENTS_DIR" \
        || { echo "dotagents: clone failed" >&2; exit 0; }
else
    echo "dotagents: no $AGENTS_DIR/.git and no AGENTS_REMOTE set; skipping link" >&2
    exit 0
fi

# 2. Link this project's .agents to its per-project store.
dotagents_cmd link-project "$PROJECT_DIR" --agents-dir "$AGENTS_DIR" >&2 \
    || echo "dotagents: link failed" >&2

exit 0
