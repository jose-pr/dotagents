# NET — dependency-free HTTP tooling

Installed by `dotagents overlays add net`. `dotagents env` wires it in on its
own — every installed overlay's `bin/` goes on `PATH`, its `lib/` on
`PYTHONPATH`, and `NET_OVERLAY_ROOT` points at the installed overlay dir — so
after `add` these are live in an env-applied shell. Nothing else to set up:
the overlay ships no setup script and no env file.

## curl shim — `$NET_OVERLAY_ROOT/bin/curl` (POSIX sh) / `bin/curl.cmd` (Windows)

A drop-in `curl`: two thin entry scripts, one `curl.py`. It **runs the real
system `curl` first** (walking `PATH` past its own directory, so it never
re-executes itself) and only falls back to a pure-stdlib (`urllib`)
implementation when no other `curl` exists — so on a normal box you get real
curl, and on a locked-down/minimal host you still get a working `curl` with
**zero dependencies**. Both entries run `curl.py` with **`$AGENTS_PYTHON`** —
the interpreter `dotagents` itself runs under, which `dotagents env` exports —
before trying `python3`/`python` on `PATH`, so a Store stub or an emulated
build on `PATH` cannot break the shim.

    curl https://example.com
    curl -s -o out.json -H 'Accept: application/json' https://api.example.com/x
    curl -X POST -d '{"k":1}' -H 'Content-Type: application/json' https://h/api

- **It uses the agent proxy either way.** Real curl reads only the global
  `http_proxy` vars, never `AGENTS_PROXY`, so when `AGENTS_PROXY` is set and you
  did not name a proxy (`-x`) or a bypass list (`--noproxy`), the shim prepends
  `--proxy` plus `--proxy-header 'Proxy-Authorization: …'` (or `--proxy-user`
  for URL userinfo). Your own `-U user:pass` keeps the agent proxy and replaces
  its credential. A **`prefix` gateway is served by the fallback**, not real
  curl: curl cannot express `<proxy><endpoint><url>` per hop (multi-URL, `-L`,
  config files), so the shim speaks the gateway itself and flags it lacks fail
  loud. The fallback resolves the proxy exactly like `httplib` (`AGENTS_PROXY`,
  then the global vars; credential and type as below), re-decides **every
  redirect hop** (proxied again, or direct without the credential for a
  `NO_PROXY` host) and honours `NO_PROXY`. `-x` names a proxy of your own
  (always `connect`, never given the agent proxy's credential); `--noproxy`
  (`*` or a host list) replaces `NO_PROXY`, as in curl. A malformed
  `AGENTS_PROXY`/`AGENTS_PROXY_TYPE` never blocks real curl (one warning, argv
  as typed); the fallback, which would have to use it, exits 2.
- Supported: `-X -d --data-raw -H -o -s -S -v -i -I -D -b -c -x -U --noproxy -k -A
  -L --timeout`.
- **Unsupported flags fail loud** (`NotImplementedError`) rather than silently do
  the wrong thing — that guard is deliberate. If you hit one, call real `curl`.
- Fallback TLS verifies against the **OS trust store** (via the `certifi` shim);
  `-k/--insecure` disables verification.
- `-v` prints the proxy with the password redacted; nothing here logs a credential.

## certifi shim — `$NET_OVERLAY_ROOT/lib/certifi`

Not the real cert bundle: `certifi.where()` resolves a CA bundle from
`$SSL_CERT_FILE` → `ssl.get_default_verify_paths()` → well-known OS paths. With
`$NET_OVERLAY_ROOT/lib` on `PYTHONPATH` (`dotagents env` puts every installed overlay's
`lib/` there), any library that `import certifi` verifies TLS through the OS
trust store with no shipped certs.

    python -m certifi        # prints the resolved OS CA bundle path

## httplib — `$NET_OVERLAY_ROOT/lib/httplib` (on PYTHONPATH)

A small session toolkit. `proxy`/`jar` are pure stdlib; `session`/`fetch` import
`requests` (+`urllib3`) **lazily** — that is the overlay's one *optional*
dependency (nothing is vendored; see `lib/VENDORED.md`). The curl shim needs it
not at all.

    from httplib.session import new_session
    from httplib.fetch import request_json
    s = new_session(cookies=True, tokens=True)   # file-backed jars, retry/backoff
    status, obj = request_json(s, "GET", "https://api.example.com/thing")

- **Proxy = `AGENTS_PROXY`** (the *agent* proxy, distinct from the machine's
  `HTTP_PROXY`). `httplib` reads `AGENTS_PROXY` first, then falls back to
  `HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY` (either case). It never fans back out to
  the global proxy vars.
- **Proxy credentials are used, not just carried.** The credential is the value
  of the `Proxy-Authorization` header, sent verbatim, so any scheme works:
  **`AGENTS_PROXY_AUTH`** first, then **`HTTP_PROXY_AUTH`**, e.g.
  `AGENTS_PROXY_AUTH="Basic $(printf 'user:pass' | base64)"` or `Bearer <token>`.
  Without either, userinfo in the URL (`http://user:pass@host:3128`) is sent as
  `Basic`. `new_session()` delivers it through its HTTP adapter — on the
  plain-http request and on the HTTPS `CONNECT`, for the agent proxy only — so
  it never reaches an origin or another proxy (a `proxies=` you pass yourself is
  not given it unless it is the agent proxy), and the session gets past a 407 on
  its own. The adapter also passes the proxy explicitly per request, so the
  machine's `HTTP_PROXY` cannot win over `AGENTS_PROXY` through `trust_env`, and
  every redirect hop is decided afresh. `proxy.resolve()` gives the two halves
  (clean URL, header value); print a URL only through `proxy.redact()`.
- **`AGENTS_PROXY_TYPE`** — how the proxy is spoken to. `connect` (default) is a
  standard HTTP proxy. **`prefix[:/endpoint]`** is a URL-prefix gateway: every
  request goes *directly* to `<proxy><endpoint><url>` (endpoint defaults to `/`)
  with the `Proxy-Authorization` value as a header of that request —
  `AGENTS_PROXY=https://gw.example AGENTS_PROXY_TYPE=prefix:/fetch/` turns
  `GET https://api.example.com/x` into
  `GET https://gw.example/fetch/https://api.example.com/x`. The session does this
  in its HTTP adapter, so redirects, cookies and `response.url` still speak the
  caller's URL; the curl shim rewrites the URL the same way.
- **`NO_PROXY`/`no_proxy`** (comma list of `host[:port]` suffixes, or `*`) is
  honoured for every request, including hosts you set as `session.proxies` —
  `requests` alone applies it only to proxies it found in the environment, so a
  loopback test server would otherwise be sent to the proxy.
- **Jars live under the store**, resolved from `$AGENTS_HOME` (→ `~/.agents`),
  never a hardcoded path:
  cookies at `<store>/cookies/<host>.txt` (Netscape), tokens at
  `<store>/tokens/<host>.token`. **Token values are secrets — never log them.**
- **Cookies and tokens are picked by the origin**, the host of the URL you
  ask for — never the proxy's or the gateway's, under either `AGENTS_PROXY_TYPE`.
  `cookies=True` loads `<host>.txt` before the request and saves what came back
  under the same host; `tokens=True` sends `<host>.token` as a per-request
  `Authorization` header (`Bearer <value>`, or the value verbatim when it
  already names a scheme such as `Basic …` / `token …`) — never a session
  header, so it cannot reach another host, and an explicit `Authorization` or
  `auth=` wins. A string instead of `True` names one jar file for every host.
  Under a prefix gateway the response still speaks the origin (`response.url`,
  `response.request`, `response.cookies`), so redirects keep the origin's token
  and a `Set-Cookie` is filed under the origin, not the gateway. The curl shim's
  `-b` sends what you give it and `-c` writes the origin's cookies the same way.

## URL hooks — `NET_HOOKS_<KEY>`

Something special for some URLs (a login dance, a signed header, a token
refresh, a stub), declared in the environment so an overlay's `env` file or a
project's `local.env` can carry it, and matched on **the URL you ask for** —
the origin's, never the proxy's or a prefix gateway's rewrite:

    NET_HOOKS_<KEY>=<regex>                 re.search against the requested URL
    NET_HOOKS_<KEY>_CURL=<command>          the curl shim runs this in its place
    NET_HOOKS_<KEY>_PY=<module:callable>    httplib calls it before the request

- **httplib**: `callable(session, method, url, kwargs)` runs inside
  `session.request` (so `session.get`, the `fetch` helpers, everything). It may
  log in through the session, put headers in `kwargs["headers"]`, refresh the
  token jar — and return `None` to let the request go, or a response of its own
  to answer instead. Requests the hook makes through the session do not run
  hooks again. `<module:callable>` is importable from `PYTHONPATH` (an overlay's
  `lib/` is, after `dotagents env`) or a `<file>.py:callable`.
- **curl shim**: the wrapper is a shell-quoted command line — a program and its
  own arguments — that the shim's whole argv is appended to:
  `NET_HOOKS_X_CURL='cmd fetch --'` runs `cmd fetch -- "$@"`. Quote what
  has spaces; a backslash is literal on every platform (a Windows path needs no
  doubling); a first word that is a `.py` file runs under `$AGENTS_PYTHON`. The
  wrapper's environment carries **`NET_HOOK_URL`** (the requested URL, no argv
  parsing needed), `NET_HOOK_KEY` (which hook matched), `NET_HOOK_CURL` (the shim,
  to call curl back with after its own work) and `NET_HOOK_SKIP` holding the KEY
  so that call does not run the wrapper again (`NET_HOOKS_<KEY>`, plural,
  declares a hook; `NET_HOOK_<NAME>`, singular, is what one hook run exports).
  Its exit code is the shim's. It runs before the real-curl passthrough and the
  fallback alike.
- Several hooks may match: httplib runs each in KEY order and stops at the first
  that answers; the shim runs the first `_CURL` in that order. A `_PY` without a
  `_CURL` leaves the shim alone and vice versa.

```sh
# a signed-request helper for one API, whichever tool an agent picks
NET_HOOKS_INTERNAL='^https://api\.internal\.example/'
NET_HOOKS_INTERNAL_PY='mytools.net:sign_request'      # (session, method, url, kwargs)
NET_HOOKS_INTERNAL_CURL="$MYTOOLS_OVERLAY_ROOT/bin/curl-signed.py"
```

## Referencing the lib from a skill

Use `$NET_OVERLAY_ROOT/lib` — `dotagents env` exports one `<NAME>_OVERLAY_ROOT`
per installed overlay, so this needs no setup. Never hardcode the store path.
