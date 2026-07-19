#!/usr/bin/env sh
# Self-contained cloud bootstrap for the private agents repo. Lives in the PUBLIC
# dotagents repo (tools/cloud-setup.sh) so a fresh container can fetch and run it
# at start, staying current without re-pasting. Put this ONE line in your Claude
# Code web environment's SETUP SCRIPT field:
#
#   curl -fsSL https://raw.githubusercontent.com/jose-pr/dotagents/main/tools/cloud-setup.sh -o /tmp/dg-cloud-setup.sh && sh /tmp/dg-cloud-setup.sh
#
# Use `curl … -o file && sh file`, NOT `curl … | sh`: a pipe makes the setup
# script's exit code that of `sh` (which exits 0 on empty stdin), so a failed
# fetch -- blocked egress, 404, proxy error at container start -- is silently
# reported as SUCCESS. `&&` propagates curl's failure so the setup log shows it.
# (Pin to a tag for reproducibility, e.g. .../dotagents/v0.2.0/tools/cloud-setup.sh.)
# It runs at container start, BEFORE ~/.agents exists -- so unlike the SessionStart
# hook it inlines its own auth and performs the very first clone.
#
# It: (1) authenticates with a token and bypasses a github.com -> in-session-proxy
# rewrite when present, (2) clones or pulls the private repo into ~/.agents,
# (3) ensures the dotagents CLI is installed, (4) links the current project's
# .agents into the repo, (5) wires the private-sync hooks into
# ~/.claude/settings.json so per-session pull/sync-back actually runs.
# Safe to run at every container start; never fails hard.
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

# Banner so the environment's setup-script log unambiguously shows this ran (a
# blank log means the setup-script field never invoked it -- a config issue, not
# a script one).
echo "dotagents cloud-setup: starting (repo -> $AGENTS_DIR, project -> $PROJECT_DIR)"

# Some setup-script contexts start before HOME exists; git config --global
# (used below) can't write ~/.gitconfig without it.
[ -n "${HOME:-}" ] && [ ! -d "$HOME" ] && mkdir -p "$HOME" 2>/dev/null

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

# --- 5. Wire the private-sync hooks into ~/.claude/settings.json. --------------
# The hooks (SessionStart pull/link, Stop sync-back) live in the private repo and
# do nothing until registered in the USER-level settings file. A fresh container
# has no ~/.claude/settings.json, and nothing else creates one -- without this
# step the clone above would go stale and session changes would never push back.
# Idempotent: each hook entry is appended only if not already present, and any
# existing settings (other hooks included) are preserved.
_snippet="$AGENTS_DIR/hooks/settings.snippet.json"
_py=$(command -v python || command -v python3)
if [ -f "$_snippet" ] && [ -n "$_py" ]; then
    "$_py" - "$_snippet" "$HOME/.claude/settings.json" <<'PYEOF' \
        || echo "dotagents: hook wiring failed; register hooks/settings.snippet.json manually"
import json, os, sys
snip_path, dst_path = sys.argv[1], sys.argv[2]
with open(snip_path) as f:
    snip_hooks = json.load(f).get("hooks", {})
try:
    with open(dst_path) as f:
        settings = json.load(f)
except (FileNotFoundError, ValueError):
    settings = {}
dst_hooks = settings.setdefault("hooks", {})
changed = False
for event, entries in snip_hooks.items():
    have = {json.dumps(e, sort_keys=True) for e in dst_hooks.get(event, [])}
    for entry in entries:
        if json.dumps(entry, sort_keys=True) not in have:
            dst_hooks.setdefault(event, []).append(entry)
            changed = True
if changed:
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    with open(dst_path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print("dotagents: wired private-sync hooks into %s" % dst_path)
PYEOF
elif [ ! -f "$_snippet" ]; then
    echo "dotagents: no hooks/settings.snippet.json in the repo; skipping hook wiring"
fi

echo "dotagents cloud-setup: done"
exit 0
