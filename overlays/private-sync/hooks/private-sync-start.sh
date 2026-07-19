#!/usr/bin/env sh
# SessionStart hook: make the private ~/.agents repo present, then link this
# project's .agents to its per-project store. Safe to run every session; never
# fails the session (always exits 0).
#
# Env:
#   DOTAGENTS_AGENTS_REMOTE  git URL of your private .agents repo (with auth, e.g.
#                            https://<token>@github.com/<you>/.agents.git). Needed
#                            only for the initial clone in a fresh container.
#   DOTAGENTS_AGENTS_DIR     where the private repo lives (default: $HOME/.agents)
#   CLAUDE_PROJECT_DIR       project checkout (set by the harness; falls back to PWD)

AGENTS_DIR="${DOTAGENTS_AGENTS_DIR:-$HOME/.agents}"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"

dotagents_cmd() {
    if command -v dotagents >/dev/null 2>&1; then
        dotagents "$@"
    else
        python -m dotagents "$@"
    fi
}

# 1. Ensure the private repo is present and current.
if [ -d "$AGENTS_DIR/.git" ]; then
    git -C "$AGENTS_DIR" pull --rebase --autostash --quiet \
        || echo "dotagents: pull failed (offline?), using local copy"
elif [ -n "${DOTAGENTS_AGENTS_REMOTE:-}" ]; then
    echo "dotagents: cloning private agents repo into $AGENTS_DIR"
    git clone --quiet "$DOTAGENTS_AGENTS_REMOTE" "$AGENTS_DIR" \
        || { echo "dotagents: clone failed"; exit 0; }
else
    echo "dotagents: no $AGENTS_DIR/.git and no DOTAGENTS_AGENTS_REMOTE set; skipping link"
    exit 0
fi

# 2. Link this project's .agents to its per-project store.
dotagents_cmd link "$PROJECT_DIR" --agents-dir "$AGENTS_DIR" \
    || echo "dotagents: link failed"

exit 0
