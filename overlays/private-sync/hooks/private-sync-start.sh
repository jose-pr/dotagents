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
#   CLAUDE_PROJECT_DIR       the project (AGENTS_PROJECT_ROOT likewise); by hand, the current directory

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

# Wire git auth for the private repo (token credential helper; bypass a
# github.com -> in-session-proxy rewrite when present). Sourced so its exports
# reach this shell and the subprocesses below.
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
"$_DG_PY" -I "$_DG_HERE/run_link.py" link "$PROJECT_DIR" "$AGENTS_DIR" >&2 \
    || echo "dotagents: link failed" >&2

exit 0
