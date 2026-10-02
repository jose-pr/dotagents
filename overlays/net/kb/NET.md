# NET — HTTP tooling with no installs

Installed by `dotagents overlays add net`. `dotagents env` wires it in on its
own — every installed overlay's `bin/` goes on `PATH`, its `lib/` on `PYTHONPATH`
(and in `AGENTS_PYTHONPATH`), and `NET_OVERLAY_ROOT` points at the installed
overlay dir — so after `add` the curl shim, `certifi` and `httplib` are live in an
env-applied shell. The `bin/` launchers also set `PYTHONPATH` themselves (this
`lib/`, then `$AGENTS_PYTHONPATH`, then the caller's), so the shim and anything it
starts work from any shell — sh, cmd.exe, PowerShell — env applied or not. The
overlay ships no setup script and no env file.

## curl shim — `$NET_OVERLAY_ROOT/bin/curl` (POSIX sh) / `bin/curl.cmd` (Windows)

A drop-in `curl`: two thin entry scripts, one `curl.py`. It **runs the real
system `curl` when there is one** (walking `PATH` past its own directory, so it
never re-executes itself) and otherwise **`httplib.cli`** — `python -m httplib`,
a curl-compatible command over `urllib` — so on a normal box you get real curl,
and on a locked-down host without one you still get a working `curl`. Both
entries run `curl.py` with **`$AGENTS_PYTHON`** — the interpreter `dotagents`
itself runs under, which `dotagents env` exports — before trying
`python3`/`python` on `PATH`, so a Store stub or an emulated build on `PATH`
cannot break the shim.

`httplib.cli` is built on **duho** (the argument library dotagents itself uses),
taken from the interpreter when it has it (an installed dotagents: that is
`$AGENTS_PYTHON`) and otherwise from **`$AGENTS_PYLIB`**, the `.pyz` a
`.pyz`-run dotagents names in its env. Outside a dotagents session with neither,
the fallback exits 2 with one line saying so (`pip install duho` also does);
real curl never needs it. Its options are mixin groups, one module per group —
`request`, `body`, `output`, `writeout`, `tls`, `connection`, `cookies` —
composed in `options.CurlCmd`; the refused flags live in `unsupported`.

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
- **`-q` / `--disable`** must be curl's FIRST argument to skip `.curlrc`, so
  the shim keeps a leading one first when it adds the proxy options.
- Supported (`curl -h` lists them): request `-X -G --url --url-query -H -A -e
  -r --compressed --request-target`; auth `-u --basic --digest --anyauth --oauth2-bearer -n --netrc-file
  --netrc-optional` and `user:pw@` in the URL; body `-d --data-ascii --data-raw
  --data-binary --data-urlencode --json -F --form-string -T`; output `-o -O
  --remote-name-all --output-dir --create-dirs -D -i --show-headers -I -s -S -v
  -f --fail-with-body -w --stderr`; downloads `-C -J -R -z --etag-save
  --etag-compare --no-clobber --skip-existing --remove-on-error --max-filesize`;
  TLS `-k --cacert --capath -E/--cert --cert-type --key --key-type --pass
  -1/--tlsv1 --tlsv1.0 --tlsv1.1 --tlsv1.2 --tlsv1.3 --tls-max --ciphers --crlfile
  --pinnedpubkey`; connection `-x -U -p --noproxy -L --max-redirs --location-trusted
  --post301 --post302 --post303 -m --connect-timeout --timeout -4 -6 --resolve
  --connect-to --unix-socket --interface --local-port --limit-rate -Y/--speed-limit
  -y/--speed-time --ignore-content-length --retry --retry-delay --retry-max-time
  --retry-connrefused --retry-all-errors`; cookies `-b -c`; and the no-ops `-q -g
  --http1.1 -N -# --no-progress-meter --styled-output --no-keepalive
  --keepalive-time --tcp-nodelay --ssl-no-revoke --ssl-revoke-best-effort
  --ca-native --proxy-ca-native --no-alpn --no-npn --no-sessionid`, each true of
  this client (no config file read, no progress shown, no keepalive, session
  reuse or ALPN, and no revocation check but `--crlfile`'s). `-g` sends a URL's `{}` / `[]` as typed;
  without it a glob pattern (one transfer per expansion in curl) is refused,
  exit 2, and a broken one is curl's exit 3. A URL with no scheme is
  `http://`, as in curl; an option value may start with `-` (`-z -DATE`, `-d
  -1`), as in curl. With curl's meaning where it matters:
  - **`-L` follows as curl does**: credentials (`Authorization` from `-u`,
    `--oauth2-bearer` or `-H`; `Cookie` from `-H` or a `-b` string) go to the
    next hop only while scheme, host and port stay the same, unless
    `--location-trusted`; a `-b` file's cookies are re-chosen per hop; 301/302
    turn a POST into a GET (`--post301`/`--post302` keep it), 303 anything but
    HEAD, 307/308 keep method and body, and `-X` stays forced.
  - **Credentials**: `-u` wins, then `user:pw@` in the URL, then the netrc entry
    for the host (`default` if none matches) with `-n`/`--netrc-optional`;
    `--digest` answers the server's challenge. `-n` without a netrc is exit 26.
  - **Downloads**: `-C -` resumes from the file's size (a server ignoring the
    range is exit 33, a 416 means already complete); `-J` takes the
    `Content-Disposition` name without its directories and never overwrites
    (exit 23); `-R` sets the mtime from `Last-Modified`; `-z DATE|FILE` (`-z
    -DATE`: unmodified since) and `--etag-compare` make the request conditional,
    and an unmet condition or a 304 writes no file; `--no-clobber` writes
    `name.1`..`name.100`; `--max-filesize` over the limit is exit 63 with nothing
    written.
  - **Shaping a connection**: `--interface` takes an IP address, a host
    (`host!NAME`) or an interface (`if!NAME`, or a bare name; by name only with
    netimps installed), `--local-port N[-M]` the first port of the range that
    binds (none: exit 45). `-p` tunnels an `http://` URL through the proxy with
    CONNECT too. `--limit-rate RATE[k|m|g]` paces the body both ways;
    `--speed-limit`/`--speed-time` (30 s, 1 B/s when only one is given) end a
    slower transfer with exit 28. `--anyauth` sends no credentials until the
    server's 401 says which (Digest, else Basic). `--request-target` replaces
    the first request's target (`*` for `OPTIONS`). `--crlfile` checks the
    server's chain against a PEM list (revoked: 60); `--pinnedpubkey
    sha256//BASE64[;...]` or a public-key file requires the server's key (exit
    90), checked even with `-k`.
  - **Connections**: `-4`/`-6`, `--resolve HOST:PORT:ADDR` and `--connect-to
    HOST1:PORT1:HOST2:PORT2` steer only name resolution, so the `Host` header,
    TLS name and certificate check stay the URL's; `--unix-socket PATH` needs a
    Python with `AF_UNIX` (not Windows CPython), else it is refused.
  - **Exit codes are curl's**, checked against real curl by
    `tests/test_curl_parity.py` (same command, same fake servers; skipped
    without a curl). An HTTP status is a response, printed and exit 0 (a 404, a
    302 without `-L`, a plain-http proxy's 407); `-f` makes a 4xx/5xx exit 22
    with no body, `--fail-with-body` the same exit with the body.
    Before anything is sent: 1 a scheme curl does not speak, 3 a URL it cannot
    parse; 2 a `--cacert` / `--netrc-file` naming no file (from curl 8.18 on;
    before, 77 at the handshake / 26), 26 a `-d @` / `-H @`
    / `-w @` / `-T` file that cannot be read (these print `curl: message`
    with no `(N)` and no `-w`, as curl does). Connecting: 5 the proxy's name
    or URL is unusable, 6 the host's name, 7 could not connect, 28 timed out,
    35 a failed TLS handshake, 60 a certificate that does not verify, 58 a
    client certificate and 77 a CA bundle/path that cannot be loaded (at the
    handshake, so an `http://` transfer never fails on them), 59 an unusable
    `--ciphers` list. A proxy refusing the https CONNECT is 56, or 7 from curl
    8.20 on (`NET_CURL_COMPAT`; 22 under `-f`), dropping it 56. The reply: 52
    nothing came back, 1 not HTTP at all, 56 the connection reset, 18 a body
    shorter than its `Content-Length` or chunks cut short (what arrived is
    written first), 56 a malformed chunk; 23 when `-o`/`-D`/`-O` cannot be
    written, 26 when a `-F` file cannot be read. An unsupported flag, a
    conflicting pair (`-d` with `-F`, `-f` with `--fail-with-body`) or a bad
    proxy configuration is exit 2 (`curl: (2) …`, one line, never a
    traceback). `-o /dev/null` is the null device on Windows too.
  - **`--retry N`** retries what curl calls transient — a timeout and HTTP
    408/429/500/502/503/504 (`httplib.retry.TRANSIENT_STATUSES`, the list the
    requests session retries too) — after 1s, doubling (`--retry-delay` fixes
    it, a `Retry-After` wins), within `--retry-max-time`; `--retry-connrefused`
    adds a refused connection, `--retry-all-errors` any error (with `-f`, an
    HTTP error status too). Each retry warns on stderr unless `-s`.
  - `--json` sends `Content-Type` and `Accept: application/json` (repeats
    concatenate); `--data-urlencode` takes curl's `content`, `=content`,
    `name=content`, `@file`, `name@file`; `-G` moves the data into the query;
    `--url-query` adds to it (`+` as it stands). `-F name=value`,
    `name=@file` (an upload: filename, type by extension), `name=<file` (its
    content), with `;type=` / `;filename=`. `-T file` PUTs it (a URL ending in
    `/` gets its name). `-r` is `Range: bytes=…`, `--oauth2-bearer` a Bearer
    `Authorization`.
  - **`-w` / `--write-out`** (`@file`, `@-` read the format) writes curl's
    output for `http_code response_code http_version method scheme url
    url_effective redirect_url num_redirects content_type num_headers
    header_json size_download size_upload time_total time_starttransfer
    exitcode errormsg filename_effective urlnum`, plus `%header{name}`,
    `%{stdout}` / `%{stderr}` / `%{onerror}`, `%%` and `\n \r \t` — after
    failures too (`000` and exit 7 when nothing answered, the status after
    `-f`'s 22). Any other variable (`time_connect`, `remote_ip`, `json`, …) is
    refused before the request is sent. `-q` is a no-op (no `.curlrc` is read).
  - `-d` repeats and joins with `&`; `@file` reads a file (CR/LF stripped, as
    curl does), `@-` stdin; `--data-binary` keeps bytes as they are; a body
    without a `Content-Type` gets `application/x-www-form-urlencoded`.
  - `-H 'Name: v'` sets, `-H 'Name:'` removes the header (urllib's own defaults
    included), `-H 'Name;'` sends it empty, `-H @file` one header per line. `-u user:pass` is Basic, `-e` the
    Referer, `--compressed` asks for gzip/deflate and decodes the body.
  - `-b <file>` sends only the rows that apply to the URL — domain (the leading
    dot or the `TRUE` flag covers subdomains), path, `Secure` only over https,
    not expired, `#HttpOnly_` rows included — so a jar with several hosts never
    leaks one host's cookies to another. `-c` writes the origin's cookies with
    their expiry and `HttpOnly`, on top of what `-b` read.
  - `-I` prints the headers by itself; `-i` adds them to a body.
- **Unsupported flags fail loud** (`curl: (2) Unsupported options: --http2`, exit
  2) rather than silently do the wrong thing — that guard is deliberate. If you
  hit one, call real `curl`.
- **`NET_CURL_COMPAT`** — the curl version the fallback answers as where curl
  versions disagree (`8.19`, `8.19.0`), or `auto` (default): the real curl on
  PATH, else the newest measured (8.22). It changes only the answers listed in
  `httplib/cli/compat.py`, each measured against curl's own builds; an
  unreadable value is exit 2.
- **`NET_CURL=0`** (or `n`/`no`/`false`/`off`) runs the real curl exactly as
  typed — no agent proxy, no hooks, no fallback; exit 2 if there is none. Unset
  or `1`/`y` is the shim.
- Fallback TLS verifies against the **OS trust store** — `httplib.tls`, the
  same store the requests session uses — unless `--cacert` / `--capath` or
  `$CURL_CA_BUNDLE` name one, which replaces it as in curl; `-k/--insecure`
  disables verification. `--cert file[:password]` (the file may hold the key),
  `--key`, `--pass` present a client certificate, PEM only.
- `-v` prints the proxy with the password redacted; nothing here logs a credential.

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

## httplib — `$NET_OVERLAY_ROOT/lib/httplib` (on `PYTHONPATH`)

TLS: a session with `verify=True` (the default) verifies through an
`ssl.create_default_context()` — the OS trust store itself (the certificate store on
Windows), shared for a minute and then rebuilt — and names no CA file, so it needs
neither `certifi` nor its shim. `verify=<bundle>` and `$REQUESTS_CA_BUNDLE` /
`$CURL_CA_BUNDLE` still win; `verify=False` never touches the shared context.

A small session toolkit. `proxy`, `jar`, `cookies`, `tls`, `retry` and `hooks`
are pure stdlib — and the curl fallback (`httplib.cli`) runs on them too, so
the two agree: one proxy plan (`proxy.plan()`), one Netscape cookie reader,
writer and matching rule (`cookies`), one OS trust-store context (`tls`), one
transient-status list (`retry`). `session`/`fetch` import `requests`
(+`urllib3`) **lazily** — that is the overlay's one *optional* dependency
(nothing is vendored; see `lib/VENDORED.md`). The curl shim needs it not at all.

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
  `Basic`. **`AGENTS_PROXY_AUTH_HEADER`** names the header the value rides in,
  default `Proxy-Authorization`, for a gateway that wants it under another name
  (`X-Proxy-Token`); the session, the fallback and real curl's `--proxy-header`
  all use it, and all send it to the proxy only — on the https `CONNECT`, never
  through the tunnel (urllib by itself moves only a header named
  `Proxy-Authorization` there). `new_session()` delivers it through its HTTP adapter — on the
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
project's `.agents/local.env` can carry it, and matched on **the URL you ask for** —
the origin's, never the proxy's or a prefix gateway's rewrite:

    NET_HOOKS_<KEY>=<regex>                 re.search against the requested URL
    NET_HOOKS_<KEY>_CURL=<command>          the curl shim runs this in its place
    NET_HOOKS_<KEY>_PY=<module:callable>    httplib calls it before the request

- **httplib**: `callable(session, method, url, kwargs)` runs inside
  `session.request` (so `session.get`, the `fetch` helpers, everything). It may
  log in through the session, put headers in `kwargs["headers"]`, refresh the
  token jar — and return `None` to let the request go, or a response of its own
  to answer instead. Requests the hook makes through the session do not run
  hooks again. `<module:callable>` is importable from `PYTHONPATH` (every
  overlay's `lib/` is) or a `<file>.py:callable`.
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
