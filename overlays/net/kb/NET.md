# NET — HTTP tooling for agents

Installed by `dotagents overlays add net`. It brings two packages from PyPI
into the agents' venv (this overlay's `requirements.txt`, which dotagents
installs into the interpreter `$AGENTS_PYTHON` names):

- **syshttp** — proxy resolution with credentials and prefix gateways (with
  no proxy variable set, the system's own proxy configuration, PAC and WPAD
  included), Netscape cookie and token jars, certificates verified by the OS
  (sslcontext: the OS verifier and store client certificates), URL hooks, and
  an HTTP client on the standard library (`syshttp.fetch`,
  `syshttp.client.HTTPClient`). Reference: <https://jose-pr.github.io/syshttp/>
  and the `AGENTS.md` inside the installed package.
- **curlike** — a curl-compatible command in pure Python (`pycurl`), never
  the real curl. Reference: <https://jose-pr.github.io/curlike/> and its
  packaged `AGENTS.md`.

The overlay's `curl` launcher (`lib/curlshim`) chooses between the two: the
real curl with the agent proxy added, else curlike.

## Settings: `AGENTS_<NAME>`

Every setting has a full name (`SYSHTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`,
`CA_BUNDLE`, the launcher's `CURL`) and is read as `<PREFIX><NAME>` for each
prefix of `SYSHTTP_PREFIX`. This overlay's `env.py` sets
`SYSHTTP_PREFIX=AGENTS_` (unless a prefix is already set), and the launcher
does the same when nothing set one, so agents read `AGENTS_SYSHTTP_PROXY`,
`AGENTS_HTTPS_PROXY`, `AGENTS_NO_PROXY`, `AGENTS_CA_BUNDLE`, ... and **nothing
unprefixed**: the machine's `HTTPS_PROXY` or `NO_PROXY` are not the agents'.
The real curl still reads its own variables as curl names them, and so does
curlike for curl's own `CURL_CA_BUNDLE`.

`dotagents env` puts `bin/` on `PATH` and `lib/` (the certifi shim) on
`PYTHONPATH`. The curl launcher also puts the overlay libs on `PYTHONPATH`
for what it starts, so the shim works from any shell.

## curl — `$NET_OVERLAY_ROOT/bin/curl.py`

dotagents makes `curl` from `bin/curl.py`, as it does for every top-level
`bin/*.py` of an overlay: a launcher in the agents' venv,
`<venv>/overlays/curl` (`curl.exe` on Windows), made whenever the venv is
synced and put first on `PATH` by `dotagents env`, ahead of the system's
curl. On Windows it is a native program, never a `.cmd`: cmd.exe parses a
batch file's command line before it runs, so a URL's `&`, `|`, `>` or
`%VAR%` would act. The launcher runs `bin/curl.py` under the venv's
interpreter, found through `$NET_OVERLAY_ROOT` when it runs, so editing the
script needs no new launcher, and exits 2 when that variable is unset.
Windows PowerShell 5.1 aliases `curl` to `Invoke-WebRequest`; there, type
`curl.exe`.

`bin/curl.py` calls `lib/curlshim`'s `main` with the launcher as its entry:

1. an `AGENTS_SYSHTTP_HOOKS_<KEY>_CURL` wrapper whose pattern matches the URL
   runs in curl's place;
2. else the **real curl** on `PATH` (never this launcher, never a `.cmd` /
   `.bat`, never the current directory) runs with your argv, plus `--proxy`
   in every `--next` group when the agents' settings name a proxy for the
   group's URL (`AGENTS_SYSHTTP_PROXY`, an `AGENTS_*_PROXY` variable, else,
   when curl's own proxy variables are unset too, the system's configuration)
   and the group does not choose its own (`-x`, `--noproxy`, `--proxy1.0`,
   `--preproxy`, `--socks*`, `-K`). A host in `AGENTS_NO_PROXY` gets nothing
   added. The credential goes in a private `-K` config file, removed
   afterwards: never on the command line, which other accounts can read. The
   real curl runs with `AGENTS_CURL_SHIM=1`, so a "curl" that calls this
   launcher again is served by curlike instead of looping;
3. else, or under a `prefix` gateway, **curlike** serves it, with
   curl's exit codes and messages. An option it lacks is
   `curl: (2) Unsupported options: …`, never silently ignored.

Without curlike installed (the venv not synced yet), the launcher runs the
real curl as typed, and is exit 2 when there is none.

    curl https://example.com
    curl -s -o out.json -H 'Accept: application/json' https://api.example.com/x

| Variable | Sets |
| --- | --- |
| `AGENTS_SYSHTTP_PROXY` | the agent proxy (else the request scheme's `AGENTS_*_PROXY` variable or `AGENTS_ALL_PROXY`, else the system's proxy configuration for the URL) |
| `AGENTS_SYSHTTP_SYSTEM_PROXY` | `0` to stop consulting the system's proxy configuration |
| `AGENTS_SYSHTTP_PROXY_AUTH` | its credential, the `Proxy-Authorization` value sent verbatim to it only (else URL userinfo as Basic) |
| `AGENTS_HTTP_PROXY_AUTH` | the credential of a proxy an `AGENTS_*_PROXY` variable or the system names |
| `AGENTS_SYSHTTP_PROXY_AUTH_HEADER` | the header it rides in (default `Proxy-Authorization`) |
| `AGENTS_SYSHTTP_PROXY_TYPE` | `connect` (default) or `prefix[:/endpoint]` |
| `AGENTS_NO_PROXY` | hosts that go direct |
| `AGENTS_CURL` | `0`/`n`/`no`/`false`/`off`: run the real curl exactly as typed; `1`/`y`/`yes`/`true`/`on` or unset: the launcher; anything else is exit 2 |
| `AGENTS_CURL_SSL_REVOKE` | `best-effort` (default), `true`, `false`: revocation for a Schannel curl |
| `AGENTS_PYTHON` | the interpreter a `.py` hook wrapper runs under |

A hook wrapper that runs through a command interpreter is refused, exit 2:
a `.cmd` / `.bat` (trailing dots and spaces ignored, as Windows ignores
them), `cmd`, `powershell`, `pwsh` or `wsl` itself, or a program that
resolves to one. They would parse the URL appended to them again, so its `&`
or `;` would run the rest as a command. Use a `.py` or `.exe` wrapper.
Messages never show a proxy's userinfo or credential.

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

    import syshttp
    from syshttp.client import HTTPClient, raise_for_status

    obj = syshttp.fetch("https://api.example.com/thing").json()
    with HTTPClient(cookies=True, tokens=True) as client:   # jars under $AGENTS_SYSHTTP_HOME
        resp = client.request("GET", "https://api.example.com/thing")
        raise_for_status(resp)                               # a 4xx/5xx is a reply, not an error
        text = resp.text()

- `fetch(url, method="GET", *, headers=None, data=None, json=None, ...)`
  returns the response with its body read whatever the status: `.status`,
  `.headers`, `.content`, `.text()`, `.json()`. A status of 400 or more is
  the reply, not an error (`syshttp.client.raise_for_status(resp)`).
  `requests` is not needed and not installed.
- The proxy, its credential, its type and `AGENTS_NO_PROXY` are the same
  settings as the curl launcher's: `syshttp.proxy.proxy_map()`, a proxylib
  proxy map asked again for every redirect hop; a list from the system is
  tried in order.
- TLS is the system's verifier, or the bundle `AGENTS_CA_BUNDLE` names.
- Jars live under `$AGENTS_SYSHTTP_HOME`, else the per-user state directory:
  `cookies/<host>.txt` and `tokens/<host>.token`, picked by the origin's
  host. Token values are secrets; never log them.
- `AGENTS_SYSHTTP_IMPORTPATCH=1` (or `requests:urllib:httpx:aiohttp:pip`)
  patches those libraries as they are imported in any program of the agents'
  venv, so they route through the agents' proxy settings and verify with the
  system's TLS context (`syshttp.autopatch`; what a caller sets wins).

## URL hooks — `AGENTS_SYSHTTP_HOOKS_<KEY>`

Something special for some URLs (a login, a signed header, a stub), declared in
the environment and matched on the URL you ask for:

    AGENTS_SYSHTTP_HOOKS_<KEY>=<regex>                 re.search against the requested URL
    AGENTS_SYSHTTP_HOOKS_<KEY>_CURL=<command>          the curl launcher runs this in its place
    AGENTS_SYSHTTP_HOOKS_<KEY>_PY=<module:callable>    syshttp's client calls it before the request

A `_CURL` wrapper gets `AGENTS_SYSHTTP_HOOK_URL`, `AGENTS_SYSHTTP_HOOK_KEY`,
`AGENTS_SYSHTTP_HOOK_CURL` (the launcher, to call curl back) and
`AGENTS_SYSHTTP_HOOK_SKIP`. The full contract is in syshttp's reference.

```sh
AGENTS_SYSHTTP_HOOKS_INTERNAL='^https://api\.internal\.example/'
AGENTS_SYSHTTP_HOOKS_INTERNAL_PY='mytools.net:sign_request'
AGENTS_SYSHTTP_HOOKS_INTERNAL_CURL="$MYTOOLS_OVERLAY_ROOT/bin/curl-signed.py"
```

## Referencing the lib from a skill

Use `$NET_OVERLAY_ROOT/lib` for the certifi shim; `syshttp` and `curlike` are
importable from `$AGENTS_PYTHON`. Never hardcode the store path.
