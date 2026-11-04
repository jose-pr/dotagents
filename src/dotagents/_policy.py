"""The consumer's own configuration for overlays: pins, grants, named
secrets and the env-script opt-in.

It lives in each store's registry document, ``<store>/dotagents.{toml,json,
yaml,yml}`` -- a local file the user writes, never an overlay and never a
remote registry -- in its ``overlays`` form, where an entry may be a table::

    secrets_dir = "~/.agent-secrets"   # where the ``file`` provider reads
    env_scripts = false                # run no env script of this store's overlays (default: true)

    [overlays.docs-tools]
    source = "https://example.org/agents.git@<commit>#overlays/docs-tools"
    pin = "sha256:<64 hex>"            # its tree digest; anything else is refused
    env_scripts = true                 # or false: its env scripts, declared or default
    grants = { env = ["DOCS_TOKEN"], secret = ["release-signing"], file = ["docs/token"] }

    [secrets.release-signing]          # a named secret: a provider and its key
    provider = "env"
    key = "RELEASE_SIGNING_KEY"

An overlay's policy is that of the store it is installed in. Nothing is
granted by default; env scripts run unless the store turns them off (each
run reported); an overlay with no pin is activated unpinned (and reported so).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from dotagents import _aos


class PolicyError(SystemExit):
    """A store's overlay configuration is invalid."""


_CACHE: "dict[str, tuple[tuple[int, int], dict[str, Any]]]" = {}


def _document(path: Path) -> "dict[str, Any]":
    """A registry file's whole document ({} when absent). Raises PolicyError."""
    try:
        st = os.stat(str(path))
    except OSError:
        return {}
    sig = (st.st_mtime_ns, st.st_size)
    cached = _CACHE.get(str(path))
    if cached is not None and cached[0] == sig:
        return cached[1]
    from dotagents._sources import SourceError, load_document

    try:
        doc = load_document(path)
    except SourceError as exc:
        raise PolicyError(exc.code)
    _CACHE[str(path)] = (sig, doc)
    return doc


class StorePolicy(object):
    """One store's overlay configuration."""

    def __init__(self, store: "Optional[Path]"):
        self.store = Path(store) if store is not None else None
        self.origin = ""
        doc: "dict[str, Any]" = {}
        if self.store is not None:
            from dotagents._sources import registry_files

            found = registry_files(self.store)
            if found:
                self.origin = str(found[0])
                doc = _document(found[0])
        self.form = isinstance(doc.get("overlays"), dict)
        top = doc if self.form else {}
        self.entries: "dict[str, dict[str, Any]]" = {}
        if self.form:
            for name, entry in doc["overlays"].items():
                if isinstance(entry, dict):
                    self.entries[_aos.normalize_name(str(name))] = entry
        self.env_scripts_default = self._bool(top.get("env_scripts", True), "env_scripts")
        self.secrets_dir = None
        raw_dir = top.get("secrets_dir")
        if raw_dir is not None:
            if not isinstance(raw_dir, str) or not raw_dir.strip():
                raise PolicyError("error: secrets_dir in %s must be a path" % self.origin)
            location = Path(os.path.expanduser(raw_dir.strip()))
            self.secrets_dir = location if location.is_absolute() else self.store / location
        self.secrets: "dict[str, dict[str, Any]]" = {}
        raw_secrets = top.get("secrets", {})
        if not isinstance(raw_secrets, dict):
            raise PolicyError("error: secrets in %s must be a table of named secrets" % self.origin)
        for name, ref in raw_secrets.items():
            if not (isinstance(ref, dict) and isinstance(ref.get("provider"), str)
                    and isinstance(ref.get("key"), str) and "secret" not in ref):
                raise PolicyError("error: secret %r in %s needs a provider and a key" % (name, self.origin))
            self.secrets[str(name)] = ref

    def _bool(self, value: Any, what: str) -> bool:
        if not isinstance(value, bool):
            raise PolicyError("error: %s in %s must be true or false" % (what, self.origin))
        return value

    def entry(self, identity: str) -> "dict[str, Any]":
        return self.entries.get(_aos.normalize_name(identity), {})

    def pin(self, identity: str) -> "Optional[str]":
        """The overlay's pin, or None. A pin that is not a digest is an error."""
        pin = self.entry(identity).get("pin")
        if pin is None:
            return None
        if not (isinstance(pin, str) and _aos.DIGEST_FORM.match(pin)):
            raise PolicyError("error: pin of overlay %s in %s is not sha256:<64 hex digits>" % (identity, self.origin))
        return pin.lower()

    def env_scripts(self, identity: str) -> bool:
        value = self.entry(identity).get("env_scripts")
        return self.env_scripts_default if value is None else self._bool(value, "env_scripts of " + identity)

    def grants(self, identity: str) -> "dict[str, list[str]]":
        raw = self.entry(identity).get("grants", {})
        if not isinstance(raw, dict) or any(
                not isinstance(v, list) or any(not isinstance(i, str) for i in v) for v in raw.values()):
            raise PolicyError("error: grants of overlay %s in %s must map a provider to a list of keys"
                              % (identity, self.origin))
        return {str(k): list(v) for k, v in raw.items()}

    def named_secret(self, name: str, host_env: "dict[str, str]") -> "Optional[str]":
        """A named secret's value, read through its own provider."""
        ref = self.secrets.get(name)
        if ref is None:
            return None
        if ref["provider"] == "env":
            return _aos.Environment(host_env, os.name == "nt").get(ref["key"])
        if ref["provider"] == "file":
            return self.read_secret_file(ref["key"])
        return None

    def read_secret_file(self, key: str) -> "Optional[str]":
        """A ``file`` secret: ``key`` under the secrets directory."""
        if self.secrets_dir is None or not _aos.valid_relpath(key):
            return None
        try:
            with open(str(self.secrets_dir.joinpath(*key.split("/"))), encoding="utf-8", newline="") as fh:
                return fh.read()
        except (OSError, UnicodeDecodeError):
            return None

    def secrets_for(self, identity: str, host_env: "Optional[dict[str, str]]" = None) -> _aos.Secrets:
        """The :class:`dotagents._aos.Secrets` an overlay installed here resolves through."""
        host = dict(os.environ if host_env is None else host_env)
        return _aos.Secrets(
            grants=self.grants(identity), host_env=host,
            named=lambda name: self.named_secret(name, host),
            read_file=self.read_secret_file, windows=os.name == "nt",
        )


_STORES: "dict[str, tuple[Any, StorePolicy]]" = {}


def for_store(store: "Optional[Path]") -> StorePolicy:
    """The policy of ``store``, re-read when its registry file changes."""
    from dotagents._sources import registry_files

    found = registry_files(store) if store is not None else []
    key = str(store)
    sig: Any = None
    if found:
        try:
            st = os.stat(str(found[0]))
            sig = (str(found[0]), st.st_mtime_ns, st.st_size)
        except OSError:
            sig = None
    cached = _STORES.get(key)
    if cached is not None and cached[0] == sig:
        return cached[1]
    policy = StorePolicy(store)
    _STORES[key] = (sig, policy)
    return policy


def for_overlay(overlay: Any) -> StorePolicy:
    """The policy of the store ``overlay`` is installed in (its parent's
    parent when it carries no store)."""
    store = overlay.store if overlay.store is not None else overlay.path.parent.parent
    return for_store(store)
