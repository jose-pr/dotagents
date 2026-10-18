# Vendored code in `overlays/net/lib/`

**Nothing third-party is vendored here.** `lib/` holds one module of this
overlay's own:

- **`lib/certifi/`** — an OS-trust-store shim, *not* the real cert bundle. Pure
  stdlib (`os` + `ssl`), ships no certificates.

The HTTP code is not vendored either. `httplib` and `curlike` are packages of
their own, installed into the agents' venv from this overlay's
`requirements.txt` (pinned to a commit while neither is on PyPI). `requests` is
an optional dependency of `httplib.session` and is not installed by the
overlay.

## If a future change *does* vendor a package

Add it here with: package name, exact version, upstream URL, and SPDX license,
and confirm the license permits redistribution.
