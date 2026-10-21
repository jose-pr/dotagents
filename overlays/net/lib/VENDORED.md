# Vendored code in `overlays/net/lib/`

**Nothing third-party is vendored here.** `lib/` holds one module of this
overlay's own:

- **`lib/certifi/`** — an OS-trust-store shim, *not* the real cert bundle. Pure
  stdlib (`os` + `ssl`), ships no certificates.

The HTTP code is not vendored either. `syshttp` and `curlike` are packages of
their own, installed into the agents' venv from this overlay's
`requirements.txt`, from PyPI. Neither needs `requests`, and the overlay does
not install it.

## If a future change *does* vendor a package

Add it here with: package name, exact version, upstream URL, and SPDX license,
and confirm the license permits redistribution.
