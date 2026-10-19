# NET — HTTP tooling for agents

Installed by `dotagents overlays add net`. It brings two packages into the
agents' venv (this overlay's `requirements.txt`, which dotagents installs into
the interpreter `$AGENTS_PYTHON` names):

- **syshttp** — proxy resolution with credentials and prefix gateways (with
  no proxy variable set, the system's own proxy configuration, PAC and WPAD
  included), Netscape cookie and token jars, certificates verified by the OS
  (sslcontext: the OS verifier and store client certificates), URL hooks, and a configured `requests` session. Reference: <https://jose-pr.github.io/syshttp/>
  and the `AGENTS.md` inside the installed package.
- **curlike** — a curl-compatible command in pure Python (`pycurl`), never
  the real curl. Reference: <https://jose-pr.github.io/curlike/> and its
  packaged `AGENTS.md`.

The overlay's `curl` launcher (`lib/curlshim`) chooses between the two: the
real curl with the agent proxy added, else curlike.

Both read their settings as `<PREFIX>_<NAME>`. This overlay's `env.py` sets
`SYSHTTP_PREFIX=AGENTS` (unless a prefix is already set), so every setting is
an `AGENTS_*` variable. `dotagents env` puts `bin/` on `PATH` and `lib/` (the
certifi shim) on `PYTHONPATH`. The `bin/` launchers also set `PYTHONPATH`
themselves, so the shim works from any shell.

## curl — `$NET_OVERLAY_ROOT/bin/curl` (POSIX sh) / `bin/curl.cmd` (Windows)

Both entries run `bin/curl.py` under `$AGENTS_PYTHON` (then `python3` /
`python`), which calls `lib/curlshim`'s `main` with the launcher as its entry:

1. an `AGENTS_HOOKS_<KEY>_CURL` wrapper whose pattern matches the URL runs in
   curl's place;
2. else the **real curl** on `PATH` (never this launcher) runs with your argv,
   plus `--proxy` and its credential when `AGENTS_PROXY` is set and you named
   no proxy (`-x`) or bypass list (`--noproxy`);
3. else, or under a `prefix` gateway, **curlike** serves it, with
   curl's exit codes and messages. An option it lacks is
   `curl: (2) Unsupported options: …`, never silently ignored.

Without curlike installed (the venv not synced yet), the launcher runs the
real curl as typed, and is exit 2 when there is none.

    curl https://example.com
    curl -s -o out.json -H 'Accept: application/json' https://api.example.com/x

| Variable | Sets |
| --- | --- |
| `AGENTS_PROXY` | the agent proxy (else the request scheme's `*_PROXY` variable or `ALL_PROXY`, else the system's proxy configuration for the URL) |
| `AGENTS_SYSTEM_PROXY` | `0` to stop consulting the system's proxy configuration |
| `AGENTS_PROXY_AUTH` | its credential, the `Proxy-Authorization` value sent verbatim to it only (else URL userinfo as Basic) |
| `HTTP_PROXY_AUTH` | the credential of the machine's proxy (a `*_PROXY` variable's or the system's) |
| `AGENTS_PROXY_AUTH_HEADER` | the header it rides in (default `Proxy-Authorization`) |
| `AGENTS_PROXY_TYPE` | `connect` (default) or `prefix[:/endpoint]` |
| `AGENTS_CURL` | `0`/`no`/`off`: run the real curl exactly as typed |
| `AGENTS_CURL_COMPAT` | the curl version curlike answers as (default: the real curl's) |
| `AGENTS_CURL_SSL_REVOKE` | `best-effort` (default), `true`, `false`: revocation for a Schannel curl |
| `AGENTS_PYTHON` | the interpreter a `.py` hook wrapper runs under |
| `NO_PROXY` | hosts that go direct |

## certifi shim — `$NET_OVERLAY_ROOT/lib/certifi`

Not the real cert bundle, and never falls back to one: `certifi.where()` names a CA
bundle FILE from the OS trust store —

1. an explicit override: `$SSL_CERT_FILE`, `$REQUESTS_CA_BUNDLE`, `$CURL_CA_BUNDLE`;
2. the system's bundle file: OpenSSL's default, then the well-known OS paths;
3. otherwise a bundle **built from the system's certificates** — the Windows ROOT and
   CA stores (Windows keeps its roots there, not in a file), a hashed certs directory
   (`$SSL_CERT_DIR`, OpenSSL's default, `/etc/ssl/certs`), whatever OpenSSL loads by
   default — written to a per-user file (`%TEMP%\dotagents-net\cacert.pem`;
   `$XDG_RUNTIME_DIR` or `~/.cache` on POSIX, never a shared `/tmp`), a cache rebuilt
   when it is older than a minute;
4. nothing: `None`, and a one-time note on stderr naming the variables to set (a bare
   container with no trust store).

With `$NET_OVERLAY_ROOT/lib` on `PYTHONPATH` ahead of any real `certifi` (every
session has it), any library that `import certifi` verifies TLS through the OS trust
store with no shipped certs.

    python -m certifi        # prints the resolved OS CA bundle path

## syshttp — `import syshttp` in the agents' venv

    from syshttp.session import new_session
    from syshttp.fetch import request_json
    s = new_session(cookies=True, tokens=True)   # jars under $AGENTS_HOME
    status, obj = request_json(s, "GET", "https://api.example.com/thing")

- The proxy, its credential, its type and `NO_PROXY` are the same
  `AGENTS_*` settings as the curl launcher's: `syshttp.proxy.proxy_map()`,
  a proxylib proxy map asked again for every redirect hop; a list from the
  system is tried in order.
- Jars live under `$AGENTS_HOME` (`AGENTS_HOME` is syshttp's `<PREFIX>_HOME`):
  `cookies/<host>.txt` and `tokens/<host>.token`, picked by the origin's
  host. Token values are secrets; never log them.
- `AGENTS_SYSHTTP_IMPORTPATCH=1` (or `requests:urllib:httpx:aiohttp:pip`) patches those
  libraries as they are imported in any program of the agents' venv, so they
  route through the agent proxy, the system's proxy and `NO_PROXY`, and verify
  with the system's TLS context (`syshttp.autopatch`; what a caller sets wins).
- `session` and `fetch` need `requests`, which the overlay does not install:
  `pip install 'syshttp[session]'` into the venv that uses them.

## URL hooks — `AGENTS_HOOKS_<KEY>`

Something special for some URLs (a login, a signed header, a stub), declared in
the environment and matched on the URL you ask for:

    AGENTS_HOOKS_<KEY>=<regex>                 re.search against the requested URL
    AGENTS_HOOKS_<KEY>_CURL=<command>          the curl launcher runs this in its place
    AGENTS_HOOKS_<KEY>_PY=<module:callable>    syshttp calls it before the request

A `_CURL` wrapper gets `AGENTS_HOOK_URL`, `AGENTS_HOOK_KEY`, `AGENTS_HOOK_CURL`
(the launcher, to call curl back) and `AGENTS_HOOK_SKIP`. The full contract is in
syshttp's reference.

```sh
AGENTS_HOOKS_INTERNAL='^https://api\.internal\.example/'
AGENTS_HOOKS_INTERNAL_PY='mytools.net:sign_request'
AGENTS_HOOKS_INTERNAL_CURL="$MYTOOLS_OVERLAY_ROOT/bin/curl-signed.py"
```

## Referencing the lib from a skill

Use `$NET_OVERLAY_ROOT/lib` for the certifi shim; `syshttp` and `curlike` are
importable from `$AGENTS_PYTHON`. Never hardcode the store path.
