"""File-backed cookie/token jars, keyed by host.

Jars live under the **configured store** (D58), not a hardcoded ``~/.agents``:
``store_root()`` resolves ``$DOTAGENTS_AGENTS_DIR`` -> ``$DOTAGENTS_STORE_DIR`` ->
``~/.agents``. Cookies go under ``<store>/cookies/<host>.txt`` (Netscape format),
tokens under ``<store>/tokens/<host>.token``.

Token values are secrets: this module never logs or prints them (Leakage rule).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Protocol

from .cookies import CookieSpec, load_netscape, save_netscape


def store_root() -> Path:
    """Resolve the dotagents store directory (D58 configurable store).

    ``$DOTAGENTS_AGENTS_DIR`` (set by the overlay setup runner and the CLI) ->
    ``$DOTAGENTS_STORE_DIR`` -> ``~/.agents``. Never hardcodes ``~/.agents``
    unconditionally so a relocated store still finds its jars."""
    for var in ("DOTAGENTS_AGENTS_DIR", "DOTAGENTS_STORE_DIR"):
        val = os.environ.get(var)
        if val:
            return Path(val)
    return Path.home() / ".agents"


class CookieJar(Protocol):
    def __getitem__(self, key: str) -> Iterable[CookieSpec]: ...
    def __setitem__(self, key: str, value: Iterable[CookieSpec]) -> None: ...
    def get(self, key: str, default=None): ...


class TokenJar(Protocol):
    def __getitem__(self, key: str) -> str: ...
    def __setitem__(self, key: str, value: str) -> None: ...
    def get(self, key: str, default=None): ...


class FileCookieJar:
    def __init__(self, root: "Path | None" = None):
        self.root = Path(root) if root is not None else store_root()
        self.dir = self.root / "cookies"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = key.replace("/", "_")
        return self.dir / ("%s.txt" % safe)

    def __getitem__(self, key: str) -> Iterable[CookieSpec]:
        return load_netscape(self._path(key))

    def __setitem__(self, key: str, value: Iterable[CookieSpec]) -> None:
        path = self._path(key)

        class _C:
            def __init__(self, c: CookieSpec):
                self.domain = c.domain
                self.path = c.path
                self.secure = c.secure
                self.expires = c.expires
                self.name = c.name
                self.value = c.value

        save_netscape([_C(c) for c in value], path)

    def get(self, key: str, default=None):
        try:
            return self[key]
        except Exception:
            return default


class FileTokenJar:
    def __init__(self, root: "Path | None" = None):
        self.root = Path(root) if root is not None else store_root()
        self.dir = self.root / "tokens"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = key.replace("/", "_")
        return self.dir / ("%s.token" % safe)

    def __getitem__(self, key: str) -> str:
        # Never logs the value read (secret).
        return self._path(key).read_text(encoding="utf-8").strip()

    def __setitem__(self, key: str, value: str) -> None:
        self._path(key).write_text(value.strip() + "\n", encoding="utf-8")

    def get(self, key: str, default=None):
        try:
            v = self[key]
            return v if v else default
        except Exception:
            return default


class MemoryCookieJar(dict):
    def __getitem__(self, key: str) -> Iterable[CookieSpec]:
        return super().get(key, [])


class MemoryTokenJar(dict):
    def __getitem__(self, key: str) -> str:
        return super().__getitem__(key)
