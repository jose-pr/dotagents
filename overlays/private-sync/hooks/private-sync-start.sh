#!/usr/bin/env sh
# SessionStart hook: make the private ~/.agents repo present, then link this
# project's .agents to its per-project store. Safe to run every session; never
# fails the session (always exits 0).
#
# Env:
#   DOTAGENTS_AGENTS_REMOTE  git URL of your private .agents repo. Prefer a
#                            TOKENLESS https URL (https://github.com/<you>/.agents.git)
#                            plus DOTAGENTS_AGENTS_TOKEN below; a token may be
#                            embedded here instead, but then it is persisted in
#                            .git/config on disk.
#   DOTAGENTS_AGENTS_TOKEN   (recommended) a fine-grained PAT scoped to the repo
#                            (Contents: read/write). Wired via a git credential
#                            helper that reads it from the environment at auth
#                            time, so it is never written to .git/config or disk.
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

# If a token is provided, wire a credential helper that supplies it at auth
# time from the environment. The helper string stored in ~/.gitconfig only
# NAMES the env var -- the secret value never lands on disk. Tokenless remote
# URLs (https://github.com/...) then authenticate without embedding the PAT.
if [ -n "${DOTAGENTS_AGENTS_TOKEN:-}" ]; then
    git config --global credential."https://github.com".helper \
        '!f() { printf "username=x-access-token\npassword=%s\n" "$DOTAGENTS_AGENTS_TOKEN"; }; f'
    git config --global credential."https://github.com".useHttpPath false
fi

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
