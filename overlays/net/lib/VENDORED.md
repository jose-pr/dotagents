# Vendored code in `overlays/net/lib/`

**Nothing third-party is vendored here.** This overlay deliberately does *not*
bundle `requests` / `urllib3` / `idna` / `charset_normalizer`.

## Why (the ADAPT decision — see D67)

The precursor vendored the whole `requests` stack so agents got HTTPS with zero
`pip install`. dotagents keeps the *zero-dependency* promise for the pieces that
matter most, without shipping hundreds of KB of third-party code:

- **`bin/curl.py`** — dependency-free. Tries the real system `curl` first; its
  fallback is pure stdlib (`urllib.request` + `ssl`), verifying TLS through the
  OS trust store via the `certifi` shim below. No `requests` needed.
- **`lib/certifi/`** — an OS-trust-store shim, *not* the real cert bundle. Pure
  stdlib (`os` + `ssl`). ~2 KB, ships no certificates.
- **`lib/httplib/`** — the session toolkit. `proxy.py` and `jar.py` are pure
  stdlib. `session.py` / `fetch.py` import `requests` (+ `urllib3`) **lazily**
  and only when a caller builds a `Session`; absence raises a clear, actionable
  `ImportError` pointing at `pip install requests` or the curl shim.

So `requests`/`urllib3` are a **documented optional dependency** of the httplib
session toolkit — not vendored, not required for the curl shim or certifi shim.

## If a future change *does* vendor a package

Add it here with: package name, exact version, upstream URL, and SPDX license,
and confirm the license permits redistribution. `requests` (Apache-2.0),
`urllib3` (MIT), `idna` (BSD-3-Clause), and `charset_normalizer` (MIT) are all
permissive and would be license-clean to vendor — the choice above is about
payload size and maintenance, not licensing.
