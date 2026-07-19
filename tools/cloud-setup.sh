#!/usr/bin/env sh
# Self-contained cloud bootstrap for the private agents repo. Lives in the PUBLIC
# dotagents repo (tools/cloud-setup.sh) so a fresh container can fetch and run it
# at start, staying current without re-pasting. Put this ONE line in your Claude
# Code web environment's SETUP SCRIPT field:
#
#   curl -fsSL https://raw.githubusercontent.com/jose-pr/dotagents/main/tools/cloud-setup.sh | sh
#
# (Pin to a tag for reproducibility, e.g. .../dotagents/v0.2.0/tools/cloud-setup.sh.)
# It runs at container start, BEFORE ~/.agents exists -- so unlike the SessionStart
# hook it inlines its own auth and performs the very first clone.
#
# It: (1) authenticates with a token and bypasses a github.com -> in-session-proxy
# rewrite when present, (2) clones or pulls the private repo into ~/.agents,
# (3) ensures the dotagents CLI is installed, (4) links the current project's
# .agents into the repo. Safe to run at every container start; never fails hard.
#
# Env (set as secrets/vars in the web UI):
#   DOTAGENTS_AGENTS_REMOTE  tokenless https URL of your private repo, e.g.
#                            https://github.com/<you>/.agents.git  (needed to clone)
#   DOTAGENTS_AGENTS_TOKEN   fine-grained PAT, Contents: read/write, scoped to it
#   DOTAGENTS_AGENTS_DIR     where the repo lives (default: $HOME/.agents)
#   DOTAGENTS_CLI_INSTALL    pip spec for the CLI if not already installed
#                            (default: "dotagents"; e.g. a git URL if unpublished)
#   CLAUDE_PROJECT_DIR       project to link (default: current directory)

AGENTS_DIR="${DOTAGENTS_AGENTS_DIR:-$HOME/.agents}"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"

# --- 1. Token auth + github.com -> proxy rewrite bypass (applied per git call
#        below via dg_git, never exported into the session environment). --------
_DG_CFG=""
if [ -n "${DOTAGENTS_AGENTS_TOKEN:-}" ]; then
    _helper='!f() { printf "username=x-access-token\npassword=%s\n" "$DOTAGENTS_AGENTS_TOKEN"; }; f'
    _rewrite=$(git config --get-regexp '^url\..*\.insteadof$' 2>/dev/null \
        | awk 'tolower($2) ~ /^https:\/\/github\.com/ {print; exit}')
    if [ -n "$_rewrite" ]; then
        _DG_CFG=$(mktemp)
        git config --file "$_DG_CFG" credential."https://github.com".helper "$_helper"
        git config --file "$_DG_CFG" credential."https://github.com".useHttpPath false
        git config --file "$_DG_CFG" init.defaultBranch main
        _n=$(git config user.name 2>/dev/null || true);  [ -n "$_n" ] || _n="dotagents"
        _e=$(git config user.email 2>/dev/null || true); [ -n "$_e" ] || _e="dotagents@localhost"
        git config --file "$_DG_CFG" user.name "$_n"
        git config --file "$_DG_CFG" user.email "$_e"
        for _ca in "${GIT_SSL_CAINFO:-}" "${SSL_CERT_FILE:-}" "${CURL_CA_BUNDLE:-}" \
                   "${REQUESTS_CA_BUNDLE:-}" /root/.ccr/ca-bundle.crt "${HOME:-}/.ccr/ca-bundle.crt"; do
            [ -n "$_ca" ] && [ -f "$_ca" ] && { git config --file "$_DG_CFG" http.sslCAInfo "$_ca"; break; }
        done
        echo "dotagents: bypassing github.com->proxy rewrite for token auth"
    else
        git config --global credential."https://github.com".helper "$_helper"
        git config --global credential."https://github.com".useHttpPath false
    fi
fi

# git with the isolated config when a bypass was built; else plain git.
dg_git() {
    if [ -n "$_DG_CFG" ]; then
        GIT_CONFIG_GLOBAL="$_DG_CFG" GIT_CONFIG_SYSTEM=/dev/null git "$@"
    else
        git "$@"
    fi
}

# --- 2. Clone or pull the private repo. ---------------------------------------
if [ -d "$AGENTS_DIR/.git" ]; then
    dg_git -C "$AGENTS_DIR" pull --rebase --autostash --quiet \
        || echo "dotagents: pull failed, using local copy"
elif [ -n "${DOTAGENTS_AGENTS_REMOTE:-}" ]; then
    echo "dotagents: cloning private agents repo into $AGENTS_DIR"
    dg_git clone --quiet "$DOTAGENTS_AGENTS_REMOTE" "$AGENTS_DIR" \
        || { echo "dotagents: clone failed"; exit 0; }
else
    echo "dotagents: set DOTAGENTS_AGENTS_REMOTE to clone the private repo; skipping"
    exit 0
fi

# --- 3. Ensure the dotagents CLI is available. --------------------------------
if ! command -v dotagents >/dev/null 2>&1 && ! python -m dotagents --version >/dev/null 2>&1; then
    _spec="${DOTAGENTS_CLI_INSTALL:-dotagents}"
    echo "dotagents: installing the CLI ($_spec)"
    pip install --quiet "$_spec" 2>/dev/null \
        || python -m pip install --quiet "$_spec" 2>/dev/null \
        || echo "dotagents: could not install the CLI; set DOTAGENTS_CLI_INSTALL or install it manually"
fi
dg_cli() { if command -v dotagents >/dev/null 2>&1; then dotagents "$@"; else python -m dotagents "$@"; fi; }

# --- 4. Link the current project's .agents into the repo. ---------------------
if [ -d "$PROJECT_DIR" ]; then
    dg_cli link "$PROJECT_DIR" --agents-dir "$AGENTS_DIR" || echo "dotagents: link failed"
fi

exit 0
