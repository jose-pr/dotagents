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

# Ensure the push can authenticate even if the SessionStart hook didn't run.
# The helper reads the token from the environment at auth time; the value is
# never written to disk (idempotent with the start hook's identical setup).
if [ -n "${DOTAGENTS_AGENTS_TOKEN:-}" ]; then
    git config --global credential."https://github.com".helper \
        '!f() { printf "username=x-access-token\npassword=%s\n" "$DOTAGENTS_AGENTS_TOKEN"; }; f'
    git config --global credential."https://github.com".useHttpPath false
fi

dotagents_cmd sync --agents-dir "$AGENTS_DIR" --project "$PROJECT_DIR" \
    --message "sync: ${PROJECT_DIR##*/} session" \
    || echo "dotagents: sync failed"

exit 0
