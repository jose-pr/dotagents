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
**zero dependencies**.

    curl https://example.com
    curl -s -o out.json -H 'Accept: application/json' https://api.example.com/x
    curl -X POST -d '{"k":1}' -H 'Content-Type: application/json' https://h/api

- **It uses the agent proxy either way.** Real curl reads only the global
  `http_proxy` vars, never `AGENTS_PROXY`, so when `AGENTS_PROXY` is set and you
  did not pass `-x`/`--proxy*`/`--noproxy`/`-U`, the shim hands it over: for a
  `connect` proxy it prepends `--proxy` plus `--proxy-header
  'Proxy-Authorization: …'` (or `--proxy-user` for URL userinfo); for a `prefix`
  gateway it rewrites the URL argument to `<proxy><endpoint><url>` and adds the
  header with `-H`. The fallback resolves the proxy exactly like `httplib`
  (`AGENTS_PROXY`, then the global vars; credential and type as below) and
  honours `NO_PROXY`. `-x` names a proxy of your own (always `connect`, never
  given the agent proxy's credential); `-U user:pass` its credentials;
  `--noproxy` (`*` or a host list) replaces `NO_PROXY`, as in curl.
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
  `Basic`. `new_session()` delivers it through the adapter's proxy headers — on
  the plain-http request and on the HTTPS `CONNECT` — so it never reaches the
  origin and the session gets past a 407 on its own. `proxy.resolve()` gives the
  two halves (clean URL, header value); print a URL only through `proxy.redact()`.
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
- **Jars live under the store**, resolved from `$AGENTS_HOME`
  (→ the legacy `$DOTAGENTS_AGENTS_DIR` → `~/.agents`), never a hardcoded path:
  cookies at `<store>/cookies/<host>.txt` (Netscape), tokens at
  `<store>/tokens/<host>.token`. **Token values are secrets — never log them.**

## Referencing the lib from a skill

Use `$NET_OVERLAY_ROOT/lib` — `dotagents env` exports one `<NAME>_OVERLAY_ROOT`
per installed overlay, so this needs no setup. Never hardcode the store path.
