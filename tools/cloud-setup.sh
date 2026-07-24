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
# rewrite when present, (2) clones (with retry/backoff) or pulls the private repo
# into ~/.agents, (3) ensures the dotagents CLI is installed, (4) links the current
# project's .agents into the repo, (5) wires the private-sync hooks into
# ~/.claude/settings.json so per-session pull/sync-back actually runs.
# Safe to run at every container start; never fails hard.
#
# Self-heal: the container-start clone often loses a race with egress/proxy
# readiness. Two defenses so a single early failure can't permanently disable the
# environment: (a) the clone in step 2 retries with backoff, and (b) if it still
# fails, we persist a copy of THIS script and wire a SessionStart *recovery* hook
# that re-runs it next session -- when egress is up, the clone (and steps 3-5)
# succeed, and that same run removes the recovery hook. Without (b) the private-sync
# hooks -- which can themselves re-clone -- would never get registered, since step 5
# is what registers them. (b) also fires when AGENTS_REMOTE is unset at
# setup time: hosted runners often expose secrets to the session but not to the
# setup-script phase, so the very first bootstrap has no remote to clone -- the
# recovery hook retries next session, where the secret is present, and heals it.
#
# Env (set as secrets/vars in the web UI):
#   AGENTS_REMOTE            tokenless https URL of your private repo, e.g.
#                            https://github.com/<you>/.agents.git  (needed to clone)
#   AGENTS_HOME              where the repo lives (default: $HOME/.agents)
#   DOTAGENTS_AGENTS_TOKEN   fine-grained PAT, Contents: read/write, scoped to it (SECRET)
#   DOTAGENTS_CLI_INSTALL    pip spec for the CLI if not already installed
#                            (default: "dotagents"; e.g. a git URL if unpublished)
#   CLAUDE_PROJECT_DIR       project to link (default: current directory)
#
# back-compat: the old names DOTAGENTS_AGENTS_REMOTE / DOTAGENTS_AGENTS_DIR are
# still honored this release (removable next); the AGENTS_* names win when both set.

AGENTS_DIR="${AGENTS_HOME:-${DOTAGENTS_AGENTS_DIR:-$HOME/.agents}}"
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

# --- Self-heal helpers (recovery hook + settings.json editor). ----------------
# The recovery hook is a persisted copy of this script wired into SessionStart, so
# a clone that loses the container-start egress race is retried next session.
_DG_RECOVERY_DIR="$HOME/.dotagents"
_DG_RECOVERY_SCRIPT="$_DG_RECOVERY_DIR/cloud-setup.sh"
# Stored literally (unexpanded) so the harness expands $HOME at hook-run time,
# matching how the private-sync hooks reference "$HOME/.agents/...".
_DG_RECOVERY_CMD='sh "$HOME/.dotagents/cloud-setup.sh"'

# Edit ~/.claude/settings.json: optionally merge a private-sync snippet, and add or
# remove the recovery SessionStart hook. Idempotent; preserves unrelated settings.
#   _dg_settings <recovery: present|absent> [snippet_path]
_dg_settings() {
    _dg_state="$1"; _dg_snip="${2:-}"
    _dg_py=$(command -v python || command -v python3)
    [ -n "$_dg_py" ] || return 1
    DG_RECOVERY_CMD="$_DG_RECOVERY_CMD" "$_dg_py" - \
        "$HOME/.claude/settings.json" "$_dg_state" "$_dg_snip" <<'PYEOF'
import json, os, sys
dst_path, state, snip_path = sys.argv[1], sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else "")
recovery_cmd = os.environ.get("DG_RECOVERY_CMD", "")
try:
    with open(dst_path) as f:
        settings = json.load(f)
except (FileNotFoundError, ValueError):
    settings = {}
hooks = settings.setdefault("hooks", {})
changed = False

def cmds(entry):
    return [h.get("command") for h in entry.get("hooks", [])]

# Merge the private-sync snippet when one was given and exists (i.e. after a
# successful clone made $AGENTS_DIR/hooks/settings.snippet.json available).
if snip_path and os.path.isfile(snip_path):
    with open(snip_path) as f:
        snip_hooks = json.load(f).get("hooks", {})
    for event, entries in snip_hooks.items():
        have = {json.dumps(e, sort_keys=True) for e in hooks.get(event, [])}
        for entry in entries:
            if json.dumps(entry, sort_keys=True) not in have:
                hooks.setdefault(event, []).append(entry)
                changed = True

# Add or remove the recovery SessionStart hook.
ss = hooks.setdefault("SessionStart", [])
has_recovery = any(recovery_cmd in cmds(e) for e in ss)
if state == "present" and not has_recovery:
    ss.append({"hooks": [{"type": "command", "command": recovery_cmd}]})
    changed = True
elif state == "absent" and has_recovery:
    hooks["SessionStart"] = [e for e in ss if recovery_cmd not in cmds(e)]
    changed = True

if changed:
    os.makedirs(os.path.dirname(dst_path) or ".", exist_ok=True)
    with open(dst_path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print("dotagents: updated %s" % dst_path)
PYEOF
}

# Persist a copy of this script and register the recovery hook, so the next
# session (egress ready) retries the whole bootstrap.
_dg_install_recovery_hook() {
    [ -n "${HOME:-}" ] || return 0
    mkdir -p "$_DG_RECOVERY_DIR" 2>/dev/null || return 0
    # Copy ourselves outside ~/.agents (which doesn't exist yet on a failed clone).
    # $0 is a real file under `sh <file>`; skip the copy for `curl | sh` ($0="sh").
    if [ -f "$0" ] && [ "$0" != "$_DG_RECOVERY_SCRIPT" ]; then
        cp "$0" "$_DG_RECOVERY_SCRIPT" 2>/dev/null || return 0
    fi
    [ -f "$_DG_RECOVERY_SCRIPT" ] || return 0
    _dg_settings present \
        && echo "dotagents: wired a SessionStart recovery hook; retrying the clone next session"
}

# --- 2. Clone (with retry/backoff) or pull the private repo. ------------------
# back-compat: DOTAGENTS_AGENTS_REMOTE is deprecated, removable next release.
AGENTS_REMOTE="${AGENTS_REMOTE:-${DOTAGENTS_AGENTS_REMOTE:-}}"
if [ -d "$AGENTS_DIR/.git" ]; then
    dg_git -C "$AGENTS_DIR" pull --rebase --autostash --quiet \
        || echo "dotagents: pull failed, using local copy"
elif [ -n "${AGENTS_REMOTE:-}" ]; then
    echo "dotagents: cloning private agents repo into $AGENTS_DIR"
    _dg_tries=0
    until dg_git clone --quiet "$AGENTS_REMOTE" "$AGENTS_DIR"; do
        _dg_tries=$((_dg_tries + 1))
        if [ "$_dg_tries" -ge 5 ]; then
            echo "dotagents: clone failed after $_dg_tries attempts (egress not ready at container start?)"
            _dg_install_recovery_hook
            exit 0
        fi
        # A failed clone can leave a partial target that would block a retry.
        [ -d "$AGENTS_DIR" ] && [ ! -d "$AGENTS_DIR/.git" ] && rm -rf "$AGENTS_DIR"
        _dg_wait=$((_dg_tries * _dg_tries))
        echo "dotagents: clone attempt $_dg_tries failed, retrying in ${_dg_wait}s"
        sleep "$_dg_wait"
    done
else
    # No remote at setup time. On a hosted runner this is usually not "never
    # configured" but the secret not being injected into the setup-script phase
    # (it is present in-session) -- so treat it like an exhausted clone: wire the
    # recovery hook and retry next session, when the secret is available. A run
    # that genuinely has no remote just re-skips next session (~100ms, idempotent);
    # the first session that sees it clones and drops the hook in step 5.
    echo "dotagents: AGENTS_REMOTE unset at setup time (secret not injected into the setup phase?)"
    _dg_install_recovery_hook
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
# Reaching this point means the clone succeeded, so we also drop any recovery hook
# a prior failed run left behind. Idempotent; unrelated settings are preserved.
_snippet="$AGENTS_DIR/hooks/settings.snippet.json"
if [ -f "$_snippet" ]; then
    _dg_settings absent "$_snippet" \
        || echo "dotagents: hook wiring failed; register hooks/settings.snippet.json manually"
else
    echo "dotagents: no hooks/settings.snippet.json in the repo; skipping hook wiring"
fi

echo "dotagents cloud-setup: done"
exit 0
