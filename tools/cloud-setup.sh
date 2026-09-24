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
# into ~/.agents, (3) ensures the dotagents CLI is installed, (4) installs the
# private-sync OVERLAY (which supplies `link-project`/`sync-project` -- they are not
# dotagents commands), (5) links the current project's .agents into the repo,
# (6) wires the private-sync hooks into ~/.claude/settings.json so per-session
# pull/sync-back actually runs.
# Safe to run at every container start; never fails hard. It never deletes a
# store: a pre-existing ~/.agents that is not a git checkout is moved aside
# (to ~/.agents.pre-clone.<time>) before the first clone. The token is offered
# only to AGENTS_REMOTE, only by this script's own git calls; ~/.gitconfig is
# never changed.
#
# Self-heal: the container-start clone often loses a race with egress/proxy
# readiness. Two defenses so a single early failure can't permanently disable the
# environment: (a) the clone in step 2 retries with backoff, and (b) if it still
# fails, we persist a copy of THIS script and wire a SessionStart *recovery* hook
# that re-runs it next session -- when egress is up, the clone (and steps 3-5)
# succeed, and that same run removes the recovery hook. Without (b) the private-sync
# hooks -- which can themselves re-clone -- would never get registered, since step 6
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
#                            (default: "dotagents-cli"; e.g. a git URL for an
#                            unreleased build)
#   CLAUDE_PROJECT_DIR       project to link (default: current directory)
#   AGENTS_OVERLAYS_REPO     an overlay repo (a directory of overlays, a registry
#                            file, or a git <repo>[@ref][#path]); set it to skip
#                            the `repo` branch fetch in step 4 entirely
#   DOTAGENTS_OVERLAYS_REMOTE / DOTAGENTS_OVERLAYS_REF
#                            where to fetch the private-sync overlay from
#                            (default: this repo, branch `repo`)

AGENTS_DIR="${AGENTS_HOME:-$HOME/.agents}"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
AGENTS_REMOTE="${AGENTS_REMOTE:-}"
# One interpreter for every Python step below.
_DG_PY=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)

# Banner so the environment's setup-script log unambiguously shows this ran (a
# blank log means the setup-script field never invoked it -- a config issue, not
# a script one).
echo "dotagents cloud-setup: starting (repo -> $AGENTS_DIR, project -> $PROJECT_DIR)"

# Some setup-script contexts start before HOME exists; the store, the recovery
# copy and ~/.claude/settings.json all live under it.
[ -n "${HOME:-}" ] && [ ! -d "$HOME" ] && mkdir -p "$HOME" 2>/dev/null

# --- 1. Token auth + github.com -> proxy rewrite bypass. Applied per git call
#        below via dg_git: never written to ~/.gitconfig, never exported into the
#        session environment, and scoped to AGENTS_REMOTE, so no other github.com
#        repository is ever offered this PAT. ------------------------------------
_DG_CFG=""
_DG_HELPER=""
if [ -n "${DOTAGENTS_AGENTS_TOKEN:-}" ] && [ -n "$AGENTS_REMOTE" ]; then
    _DG_HELPER='!f() { printf "username=x-access-token\npassword=%s\n" "$DOTAGENTS_AGENTS_TOKEN"; }; f'
    _rewrite=$(git config --get-regexp '^url\..*\.insteadof$' 2>/dev/null \
        | awk 'tolower($2) ~ /^https:\/\/github\.com/ {print; exit}')
    if [ -n "$_rewrite" ]; then
        _DG_CFG=$(mktemp)
        git config --file "$_DG_CFG" credential."$AGENTS_REMOTE".helper "$_DG_HELPER"
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
    fi
fi

# git with the isolated config when a bypass was built; else git with the token
# helper passed on the command line for AGENTS_REMOTE only. The empty value
# first clears any helper configured elsewhere for that URL, so the store's PAT
# is the credential git presents for it.
dg_git() {
    if [ -n "$_DG_CFG" ]; then
        GIT_CONFIG_GLOBAL="$_DG_CFG" GIT_CONFIG_SYSTEM=/dev/null git "$@"
    elif [ -n "$_DG_HELPER" ]; then
        git -c "credential.$AGENTS_REMOTE.helper=" \
            -c "credential.$AGENTS_REMOTE.helper=$_DG_HELPER" "$@"
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
# matching how the private-sync hooks reference "${AGENTS_HOME:-$HOME/.agents}/...".
_DG_RECOVERY_CMD='sh "$HOME/.dotagents/cloud-setup.sh"'

# Edit ~/.claude/settings.json: optionally merge a private-sync snippet, and add or
# remove the recovery SessionStart hook. Idempotent; preserves unrelated settings.
#   _dg_settings <recovery: present|absent> [snippet_path]
# A settings.json that does not parse is left untouched (the helper fails);
# writes go through a temp file and os.replace, into a symlink's target.
_dg_settings() {
    _dg_state="$1"; _dg_snip="${2:-}"
    [ -n "$_DG_PY" ] || return 1
    DG_RECOVERY_CMD="$_DG_RECOVERY_CMD" "$_DG_PY" - \
        "$HOME/.claude/settings.json" "$_dg_state" "$_dg_snip" <<'PYEOF'
import json, os, sys, tempfile
dst_path, state, snip_path = sys.argv[1], sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else "")
recovery_cmd = os.environ.get("DG_RECOVERY_CMD", "")
try:
    with open(dst_path, encoding="utf-8-sig") as f:
        settings = json.load(f)
except FileNotFoundError:
    settings = {}
except ValueError as exc:
    sys.stderr.write("dotagents: %s is not valid JSON (%s); left untouched\n" % (dst_path, exc))
    sys.exit(1)
if not isinstance(settings, dict) or not isinstance(settings.get("hooks", {}), dict):
    sys.stderr.write("dotagents: %s has an unexpected shape; left untouched\n" % dst_path)
    sys.exit(1)
hooks = settings.setdefault("hooks", {})
changed = False

def cmds(entry):
    return [h.get("command") for h in entry.get("hooks", [])]

# Merge the private-sync snippet when one was given and exists (i.e. after the
# private-sync overlay was installed into the store, which ships it under
# $AGENTS_DIR/overlays/private-sync/hooks/settings.snippet.json).
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
    target = os.path.realpath(dst_path)
    folder = os.path.dirname(target) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(settings, f, indent=2)
            f.write("\n")
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
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

# The store is in place: a recovery hook left by an earlier failed run has done
# its job.
_dg_drop_recovery_hook() {
    [ -n "${HOME:-}" ] || return 0
    _dg_settings absent \
        || echo "dotagents: could not remove the recovery hook from ~/.claude/settings.json"
}

# --- 2. Clone (with retry/backoff) or pull the private repo. ------------------
if [ -d "$AGENTS_DIR/.git" ]; then
    if dg_git -C "$AGENTS_DIR" pull --rebase --autostash --quiet; then
        _dg_drop_recovery_hook
    else
        echo "dotagents: pull failed, using local copy"
    fi
elif [ -n "$AGENTS_REMOTE" ]; then
    # Never clone over or delete a store that is not a git checkout: move a
    # non-empty one aside first. git removes a target it created itself when a
    # clone fails, so nothing needs cleaning up between attempts.
    if [ -e "$AGENTS_DIR" ] && [ -n "$(ls -A "$AGENTS_DIR" 2>/dev/null)" ]; then
        _dg_aside="$AGENTS_DIR.pre-clone.$(date +%s)"
        if mv "$AGENTS_DIR" "$_dg_aside"; then
            echo "dotagents: $AGENTS_DIR is not a git checkout; moved it to $_dg_aside"
        else
            echo "dotagents: $AGENTS_DIR is not a git checkout and could not be moved aside; not cloning over it"
            exit 0
        fi
    fi
    echo "dotagents: cloning private agents repo into $AGENTS_DIR"
    _dg_tries=0
    until dg_git clone --quiet "$AGENTS_REMOTE" "$AGENTS_DIR"; do
        _dg_tries=$((_dg_tries + 1))
        if [ "$_dg_tries" -ge 5 ]; then
            echo "dotagents: clone failed after $_dg_tries attempts (egress not ready at container start?)"
            _dg_install_recovery_hook
            exit 0
        fi
        _dg_wait=$((_dg_tries * _dg_tries))
        echo "dotagents: clone attempt $_dg_tries failed, retrying in ${_dg_wait}s"
        sleep "$_dg_wait"
    done
    _dg_drop_recovery_hook
else
    # No remote at setup time. On a hosted runner this is usually not "never
    # configured" but the secret not being injected into the setup-script phase
    # (it is present in-session) -- so treat it like an exhausted clone: wire the
    # recovery hook and retry next session, when the secret is available. A run
    # that genuinely has no remote just re-skips next session (~100ms, idempotent);
    # the first session that sees it clones and drops the hook.
    echo "dotagents: AGENTS_REMOTE unset at setup time (secret not injected into the setup phase?)"
    _dg_install_recovery_hook
    exit 0
fi

# --- 3. Ensure the dotagents CLI is available. --------------------------------
# pip's own error output stays visible: a PEP 668 refusal or a bad spec must
# show in the setup log.
if ! command -v dotagents >/dev/null 2>&1 \
    && ! { [ -n "$_DG_PY" ] && "$_DG_PY" -m dotagents --version >/dev/null 2>&1; }; then
    _spec="${DOTAGENTS_CLI_INSTALL:-dotagents-cli}"
    if [ -z "$_DG_PY" ]; then
        echo "dotagents: no python3 or python on PATH; cannot install the CLI ($_spec)"
    else
        echo "dotagents: installing the CLI ($_spec) with $_DG_PY"
        "$_DG_PY" -m pip install --quiet "$_spec" \
            || echo "dotagents: could not install the CLI (see pip's error above); set DOTAGENTS_CLI_INSTALL or install it manually"
    fi
fi
dg_cli() {
    if command -v dotagents >/dev/null 2>&1; then
        dotagents "$@"
    elif [ -n "$_DG_PY" ]; then
        "$_DG_PY" -m dotagents "$@"
    else
        return 127
    fi
}

# --- 4. Install the private-sync overlay (it SUPPLIES link-project). ----------
# Ordering matters: `link-project` is not a dotagents command -- the private-sync
# overlay ships it (with `sync-project` and their logic). This bootstrap IS the
# private-sync bootstrap, so the overlay must be installed BEFORE step 5 links.
# Skipped when already installed (and when the store already provides the command,
# e.g. a user's own cmds module), so the common path costs nothing.
#
# The example overlays live on the dotagents repo's `repo` BRANCH, not in
# main's tree -- so fetch that branch shallowly into a temp dir and point --repo
# at its overlays/ dir. AGENTS_OVERLAYS_REPO (or a pre-populated
# <store>/overlays/private-sync) short-circuits the fetch entirely, for an
# air-gapped or pinned setup.
DOTAGENTS_OVERLAYS_REMOTE="${DOTAGENTS_OVERLAYS_REMOTE:-https://github.com/jose-pr/dotagents.git}"
DOTAGENTS_OVERLAYS_REF="${DOTAGENTS_OVERLAYS_REF:-repo}"

_dg_have_link_project() {
    dg_cli --help 2>/dev/null | grep -q -- "link-project"
}

if [ -d "$AGENTS_DIR/overlays/private-sync" ]; then
    echo "dotagents: private-sync overlay already installed"
elif [ -n "${AGENTS_OVERLAYS_REPO:-}" ]; then
    echo "dotagents: installing the private-sync overlay from AGENTS_OVERLAYS_REPO"
    dg_cli overlays add private-sync --agents-dir "$AGENTS_DIR" -g \
        || echo "dotagents: private-sync overlay install failed (AGENTS_OVERLAYS_REPO)"
else
    _dg_ovl_tmp="$(mktemp -d 2>/dev/null || echo /tmp/dg-overlays.$$)"
    echo "dotagents: fetching the example-overlays branch ($DOTAGENTS_OVERLAYS_REF) for private-sync"
    if dg_git clone --quiet --depth 1 --branch "$DOTAGENTS_OVERLAYS_REF" \
        "$DOTAGENTS_OVERLAYS_REMOTE" "$_dg_ovl_tmp/src" 2>/dev/null; then
        dg_cli overlays add private-sync --repo "$_dg_ovl_tmp/src/overlays" \
            --agents-dir "$AGENTS_DIR" -g \
            || echo "dotagents: private-sync overlay install failed"
    else
        echo "dotagents: could not fetch the example-overlays branch; private-sync commands unavailable"
    fi
    rm -rf "$_dg_ovl_tmp" 2>/dev/null
fi

# --- 5. Link the current project's .agents into the repo. ---------------------
# `link-project` comes from the overlay added in step 4. Fail with a CLEAR message if it is missing rather than letting the
# CLI's "unknown subcommand" error scroll past -- the cause is always step 4.
if [ -d "$PROJECT_DIR" ]; then
    if _dg_have_link_project; then
        dg_cli link-project "$PROJECT_DIR" --agents-dir "$AGENTS_DIR" \
            || echo "dotagents: link-project failed"
    else
        echo "dotagents: link-project unavailable -- the private-sync overlay is not installed"
        echo "dotagents: install it, then re-run: dotagents overlays add private-sync --repo <overlays-checkout>/overlays"
    fi
fi

# --- 6. Wire the private-sync hooks into ~/.claude/settings.json. --------------
# The hooks (SessionStart pull/link, Stop sync-back) live in the private repo and
# do nothing until registered in the USER-level settings file. A fresh container
# has no ~/.claude/settings.json, and nothing else creates one -- without this
# step the clone above would go stale and session changes would never push back.
# Idempotent; unrelated settings are preserved. The snippet ships inside the
# private-sync overlay, which step 4 installs.
_snippet="$AGENTS_DIR/overlays/private-sync/hooks/settings.snippet.json"
if [ -f "$_snippet" ]; then
    _dg_settings absent "$_snippet" \
        || echo "dotagents: hook wiring failed; register $_snippet manually"
else
    echo "dotagents: no private-sync settings.snippet.json in the store; skipping hook wiring"
fi

echo "dotagents cloud-setup: done"
exit 0
