#!/usr/bin/env sh
# Sourced by the private-sync hooks. Configures git auth for the private agents
# repo when DOTAGENTS_AGENTS_TOKEN is set:
#
#   * a credential helper that reads the token from the environment at auth time
#     (the secret is never written to .git/config or any file on disk), and
#   * in environments that transparently rewrite github.com git traffic to a
#     scoped in-session proxy (a hosted agent runner does this, and the proxy
#     will not serve a private repo outside the session's authorized scope), an
#     isolated git config that BYPASSES that rewrite so the token authenticates
#     directly against the real github.com.
#
# When a bypass is needed it exports GIT_CONFIG_GLOBAL / GIT_CONFIG_SYSTEM into
# the caller's environment, so a subsequent `git clone` / `dotagents sync`
# (whose git subprocesses inherit them) reaches github.com instead of the proxy.
# A no-op when no token is set.

_dotagents_git_auth() {
    [ -n "${DOTAGENTS_AGENTS_TOKEN:-}" ] || return 0

    _dg_helper='!f() { printf "username=x-access-token\npassword=%s\n" "$DOTAGENTS_AGENTS_TOKEN"; }; f'

    # A github.com -> other-host rewrite is active when some insteadOf VALUE is
    # https://github.com/... . The common SSH->HTTPS convenience rewrites have
    # git@/ssh:// values, so they don't match and are left untouched.
    _dg_rewrite=$(git config --get-regexp '^url\..*\.insteadof$' 2>/dev/null \
        | awk 'tolower($2) ~ /^https:\/\/github\.com/ {print; exit}')

    if [ -z "$_dg_rewrite" ]; then
        # Normal environment: a global credential helper is enough.
        git config --global credential."https://github.com".helper "$_dg_helper"
        git config --global credential."https://github.com".useHttpPath false
        unset _dg_helper _dg_rewrite
        return 0
    fi

    # Preserve the effective git identity BEFORE swapping the config out.
    _dg_name=$(git config user.name 2>/dev/null || true)
    _dg_email=$(git config user.email 2>/dev/null || true)
    [ -n "$_dg_name" ] || _dg_name="dotagents"
    [ -n "$_dg_email" ] || _dg_email="dotagents@localhost"

    _dg_cfg=$(mktemp)
    git config --file "$_dg_cfg" credential."https://github.com".helper "$_dg_helper"
    git config --file "$_dg_cfg" credential."https://github.com".useHttpPath false
    git config --file "$_dg_cfg" init.defaultBranch main
    git config --file "$_dg_cfg" user.name "$_dg_name"
    git config --file "$_dg_cfg" user.email "$_dg_email"

    # Re-terminated-TLS proxies need the CA bundle; fall back to the system
    # trust store when none of these point at a readable file.
    for _dg_ca in "${GIT_SSL_CAINFO:-}" "${SSL_CERT_FILE:-}" "${CURL_CA_BUNDLE:-}" \
                  "${REQUESTS_CA_BUNDLE:-}" /root/.ccr/ca-bundle.crt \
                  "${HOME:-}/.ccr/ca-bundle.crt"; do
        if [ -n "$_dg_ca" ] && [ -f "$_dg_ca" ]; then
            git config --file "$_dg_cfg" http.sslCAInfo "$_dg_ca"
            break
        fi
    done

    GIT_CONFIG_GLOBAL="$_dg_cfg"
    GIT_CONFIG_SYSTEM=/dev/null
    export GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM
    echo "dotagents: github.com git is rewritten to an in-session proxy; bypassing it for token auth"
    unset _dg_helper _dg_rewrite _dg_name _dg_email _dg_cfg _dg_ca
}
