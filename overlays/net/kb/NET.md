# NET — dependency-free HTTP tooling

Installed by `dotagents overlays add net`. `dotagents env` wires it in on its
own — every installed overlay's `bin/` goes on `PATH`, its `lib/` on
`PYTHONPATH`, and `NET_OVERLAY_ROOT` points at the installed overlay dir — so
after `add` these are live in an env-applied shell. Nothing else to set up:
the overlay ships no setup script and no env file.

## curl shim — `$NET_OVERLAY_ROOT/bin/curl` (`curl.cmd` on Windows)

A drop-in `curl`. It **runs the real system `curl` first** and only falls back to
a pure-stdlib (`urllib`) implementation when `curl` is absent from PATH — so on a
normal box you get real curl, and on a locked-down/minimal host you still get a
working `curl` with **zero dependencies**.

    curl https://example.com
    curl -s -o out.json -H 'Accept: application/json' https://api.example.com/x
    curl -X POST -d '{"k":1}' -H 'Content-Type: application/json' https://h/api

- Supported: `-X -d --data-raw -H -o -s -S -v -i -I -D -b -c -x -k -A -L --timeout`.
- **Unsupported flags fail loud** (`NotImplementedError`) rather than silently do
  the wrong thing — that guard is deliberate. If you hit one, call real `curl`.
- Fallback TLS verifies against the **OS trust store** (via the `certifi` shim);
  `-k/--insecure` disables verification.

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
- **Jars live under the store**, resolved from `$AGENTS_HOME`
  (→ the legacy `$DOTAGENTS_AGENTS_DIR` → `~/.agents`), never a hardcoded path:
  cookies at `<store>/cookies/<host>.txt` (Netscape), tokens at
  `<store>/tokens/<host>.token`. **Token values are secrets — never log them.**

## Referencing the lib from a skill

Use `$NET_OVERLAY_ROOT/lib` — `dotagents env` exports one `<NAME>_OVERLAY_ROOT`
per installed overlay, so this needs no setup. Never hardcode the store path.
