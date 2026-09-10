"""Overlay repos: where ``overlays add`` / ``sync`` fetch an overlay from.

A **repo** is any place that can answer "give me overlay ``<name>``". Two shapes:

* a **directory of overlays** -- ``<dir>/<name>/`` is the overlay (the ``.pyz``'s
  bundled ``overlays/``, a checkout of the overlays branch, any folder);
* a **registry** -- a JSON / TOML / YAML document mapping ``<name-or-alias>`` to
  a *spec* for that one overlay. Either the whole document is the mapping, or
  its ``overlays`` key is.

Every repo is named by a **spec**, and so is every registry entry::

    <location>[@<ref>][#<path>]

``<location>`` is a local path, an ``http(s)://`` URL, or a git repository
(anything ``git clone`` accepts: ``https://…/x.git``, ``git@host:org/x.git``,
``ssh://…``, a local ``…/x.git``; prefix ``git+`` to force git for a URL that
does not end in ``.git``). ``<ref>`` (git only) is a branch, tag or commit --
default the remote's default branch. ``<path>`` is a path inside the location.
What the spec resolves to decides what it is: a **file** is a registry, a
**directory** is a directory of overlays (for a repo) or the overlay itself
(for a registry entry -- so with no path, *the whole git repository is the
overlay*). An ``http(s)://`` location can only be a file, i.e. a registry.

Repos are consulted in order, and **the first that offers a name wins**:
``--repo`` values as given, then ``$AGENTS_OVERLAYS_REPO_<KEY>`` sorted by
``KEY``, then ``$AGENTS_OVERLAYS_REPO`` (the default repo), then
``<project store>/dotagents.{json,toml,yaml,yml}``, then the user store's, then
the bundled ``overlays/`` of this build. Repos load lazily, in that order, so a
git repo late in the list is never cloned for a name an earlier one had.

Git checkouts live under ``<user store>/.cache/overlays/<repo>-<hash>/`` (one
per repository and ref) and are refreshed (``fetch`` + checkout) once per process; a fetch that
fails against an existing checkout is a warning, and the checkout is used as
it is. Credentials embedded in a repo URL never reach a log line.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

from dotagents._overlays import Overlay

#: The default repo, and one repo per ``AGENTS_OVERLAYS_REPO_<KEY>`` variable
#: (ordered by ``KEY``, consulted before the default).
REPO_ENV_DEFAULT = "AGENTS_OVERLAYS_REPO"
REPO_ENV_PREFIX = "AGENTS_OVERLAYS_REPO_"
#: ``<store>/dotagents.<suffix>`` registry files; the first present suffix wins.
REGISTRY_FILE_SUFFIXES = (".json", ".toml", ".yaml", ".yml")
REGISTRY_FILE_STEM = "dotagents"

_GIT_PREFIXES = ("git@", "ssh://", "git://")


class Spec(object):
    """A parsed spec. ``kind`` is ``"git"``, ``"url"`` or ``"dir"``."""

    __slots__ = ("kind", "location", "ref", "path")

    def __init__(self, kind: str, location: str, ref: "Optional[str]" = None, path: "Optional[str]" = None):
        self.kind = kind
        self.location = location
        self.ref = ref
        self.path = path

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Spec) and (self.kind, self.location, self.ref, self.path) == (
            other.kind, other.location, other.ref, other.path)

    def __repr__(self) -> str:
        return "Spec(%s, %s, ref=%r, path=%r)" % (self.kind, self.display(), self.ref, self.path)

    def display(self) -> str:
        """The spec, userinfo dropped from the location -- safe for messages."""
        out = redact(self.location)
        if self.ref:
            out += "@" + self.ref
        if self.path:
            out += "#" + self.path
        return out


def redact(text: str) -> str:
    """``text`` with the userinfo of every ``scheme://user:pass@host`` removed."""
    return re.sub(r"(://)[^/@\s]+@", r"\1", text)


def parse_spec(text: str) -> Spec:
    """Parse ``<location>[@<ref>][#<path>]``. See the module docstring."""
    raw = text.strip()
    if not raw:
        raise ValueError("empty overlay source spec")
    explicit_git = raw.startswith("git+")
    if explicit_git:
        raw = raw[len("git+"):]
    body, hash_sep, path = raw.partition("#")
    path = path.strip().strip("/") or None if hash_sep else None
    location, ref = body.strip(), None
    head, at, tail = location.rpartition("@")
    # `repo.git@ref` -- but never the `@` of `git@host:…` or `https://user@host/…`:
    # a ref has no path or host separators and does not follow a bare scheme.
    if at and head and tail and not re.search(r"[/:\\]", tail) and not head.endswith(("://", ":")):
        location, ref = head, tail
    if explicit_git or location.endswith(".git") or location.startswith(_GIT_PREFIXES):
        return Spec("git", location, ref, path)
    if "://" in location:
        return Spec("url", location, None, path)
    return Spec("dir", location, None, path)


# --------------------------------------------------------------------------
# Git checkouts
# --------------------------------------------------------------------------

class GitCache(object):
    """Cached clones under ``root``, one per repository location AND ref: two
    registry entries naming the same repository at different refs (``@main``
    for one overlay, ``@dev`` for another) must each see their own tree, since
    the commands resolve every source before copying any."""

    def __init__(self, root: Path, logger: Any = None):
        self.root = Path(root)
        self.logger = logger
        self._fresh: "set[str]" = set()  # locations fetched during this process

    def _git(self, args: "list[str]", cwd: "Optional[Path]", *, check: bool = True) -> "subprocess.CompletedProcess":
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"  # never hang on a credential prompt
        try:
            proc = subprocess.run(
                ["git", *args], cwd=str(cwd) if cwd else None,
                capture_output=True, text=True, env=env,
            )
        except OSError as exc:
            raise SystemExit("error: git is needed for a git overlay source: %s" % exc)
        if check and proc.returncode != 0:
            raise SystemExit(
                "error: git %s failed (exit %d): %s"
                % (args[0], proc.returncode, redact(proc.stderr.strip() or proc.stdout.strip()))
            )
        return proc

    def repo_dir(self, location: str, ref: "Optional[str]" = None) -> Path:
        stem = Path(location.rstrip("/").replace("\\", "/")).name
        if stem.endswith(".git"):
            stem = stem[:-4]
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.") or "repo"
        digest = hashlib.sha1(("%s@%s" % (location, ref or "")).encode("utf-8")).hexdigest()[:12]
        return self.root / ("%s-%s" % (slug, digest))

    def checkout(self, spec: Spec) -> Path:
        """The working tree for ``spec`` at its ref (the repository root;
        ``spec.path`` is the caller's to join), cloned or refreshed as needed."""
        if spec.kind != "git":
            raise ValueError("not a git spec: %r" % (spec,))
        dest = self.repo_dir(spec.location, spec.ref)
        key = str(dest)
        if not (dest / ".git").exists():
            self.root.mkdir(parents=True, exist_ok=True)
            if self.logger:
                self.logger.info("cloning %s", redact(spec.location))
            self._git(["clone", "--quiet", spec.location, str(dest)], None)
            self._fresh.add(key)
        elif key not in self._fresh:
            proc = self._git(["fetch", "--quiet", "--tags", "--prune", "origin"], dest, check=False)
            if proc.returncode != 0 and self.logger:
                self.logger.warning(
                    "fetch of %s failed (using the cached checkout as is): %s",
                    redact(spec.location), redact(proc.stderr.strip()),
                )
            self._fresh.add(key)
        self._checkout_ref(dest, spec)
        return dest

    def _checkout_ref(self, dest: Path, spec: Spec) -> None:
        if spec.ref is None:
            candidates = ["origin/HEAD"]
        else:
            # A branch (its remote-tracking tip, so a fresh fetch is what gets
            # used), else a tag, else a commit.
            candidates = ["origin/" + spec.ref, spec.ref]
        for candidate in candidates:
            probe = self._git(["rev-parse", "--verify", "--quiet", candidate + "^{commit}"], dest, check=False)
            if probe.returncode == 0:
                self._git(["checkout", "--quiet", "--detach", candidate], dest)
                return
        if spec.ref is not None:
            # Not among the fetched refs: ask the remote for it by name. Covers
            # a commit the default refspec did not bring over (a server that
            # allows reachable SHAs in want) and a ref the clone did not track.
            fetched = self._git(["fetch", "--quiet", "origin", spec.ref], dest, check=False)
            if fetched.returncode == 0:
                self._git(["checkout", "--quiet", "--detach", "FETCH_HEAD"], dest)
                return
        raise SystemExit(
            "error: %s has no branch, tag or commit %r" % (redact(spec.location), spec.ref or "HEAD")
        )


# --------------------------------------------------------------------------
# Registry documents
# --------------------------------------------------------------------------

def parse_document(text: str, suffix: str, origin: str) -> "dict[str, str]":
    """The ``name -> spec`` mapping of a registry document (the whole document,
    or its ``overlays`` key)."""
    suffix = suffix.lower()
    if suffix == ".json":
        doc = json.loads(text)
    elif suffix == ".toml":
        try:
            import tomllib  # type: ignore[import-not-found]
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore[no-redef]
            except ImportError:
                raise SystemExit(
                    "error: registry %s is TOML, which needs Python 3.11+ or `pip install tomli`" % origin
                )
        doc = tomllib.loads(text)
    elif suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            raise SystemExit("error: registry %s is YAML, which needs `pip install pyyaml`" % origin)
        doc = yaml.safe_load(text)
    else:
        raise SystemExit(
            "error: registry %s: unknown format %r (use .json, .toml, .yaml or .yml)" % (origin, suffix)
        )
    if isinstance(doc, dict) and isinstance(doc.get("overlays"), dict):
        doc = doc["overlays"]
    if not isinstance(doc, dict):
        raise SystemExit("error: registry %s must be a mapping of overlay name -> source" % origin)
    entries: "dict[str, str]" = {}
    for key, value in doc.items():
        if not isinstance(value, str) or not value.strip():
            raise SystemExit("error: registry %s: entry %r must be a source string" % (origin, key))
        entries[str(key)] = value.strip()
    return entries


def _read_url(url: str) -> str:
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 -- the user named it
            return resp.read().decode("utf-8")
    except OSError as exc:
        raise SystemExit("error: cannot read registry %s: %s" % (redact(url), exc))


# --------------------------------------------------------------------------
# Repos
# --------------------------------------------------------------------------

class DirRepo(object):
    """A directory of overlays: ``<root>/<name>/`` is the overlay."""

    def __init__(self, root: Path, origin: "Optional[str]" = None):
        self.root = Path(root)
        self.origin = origin or str(self.root)

    def available(self) -> "list[str]":
        """Overlay names the directory offers -- the ONE discovery rule
        (:meth:`Overlay.discover`), so ``__pycache__`` / dotdirs are never listed."""
        return [overlay.name for overlay in Overlay.discover(self.root)]

    def _lookup(self, name: str) -> "Optional[Path]":
        wanted = Overlay.normalize_name(name)
        for candidate in (self.root / name, self.root / wanted):
            if candidate.is_dir():
                return candidate
        for overlay in Overlay.discover(self.root):
            if overlay.normalized_name == wanted:
                return overlay.path
        return None

    def has(self, name: str) -> bool:
        return self._lookup(name) is not None

    def overlay_dir(self, name: str) -> Path:
        """The source dir for ``name``: the literal dir, its normalized form, or
        any available overlay whose NORMALIZED name matches -- so a source dir
        named ``my_overlay`` resolves for ``add my_overlay`` / ``add my-overlay``
        alike."""
        found = self._lookup(name)
        if found is None:
            raise SystemExit(
                "error: overlay %r not found in source %s (available: %s)"
                % (name, self.origin, ", ".join(self.available()) or "none")
            )
        return found

    def __repr__(self) -> str:
        return "DirRepo(%s)" % self.origin


class RegistryRepo(object):
    """A registry: ``entries`` map names (or aliases) to specs, materialized on
    demand through ``cache``."""

    def __init__(self, origin: str, entries: "dict[str, str]", cache: GitCache):
        self.origin = origin
        self.entries = entries
        self.cache = cache
        self._by_normalized = {Overlay.normalize_name(k): k for k in entries}

    @property
    def root(self) -> str:
        return self.origin

    def key_for(self, name: str) -> "Optional[str]":
        if name in self.entries:
            return name
        return self._by_normalized.get(Overlay.normalize_name(name))

    def available(self) -> "list[str]":
        return list(self.entries)

    def has(self, name: str) -> bool:
        return self.key_for(name) is not None

    def overlay_dir(self, name: str) -> Path:
        key = self.key_for(name)
        if key is None:
            raise SystemExit("error: overlay %r not in registry %s" % (name, self.origin))
        spec = parse_spec(self.entries[key])
        if spec.kind == "url":
            raise SystemExit(
                "error: registry %s: %r is an http(s) URL, which can only be a registry; "
                "an overlay needs a directory or a git spec" % (self.origin, key)
            )
        target = locate(spec, self.cache)
        if not target.is_dir():
            raise SystemExit("error: registry %s: %r resolves to %s, not a directory" % (self.origin, key, spec.display()))
        if not (target / "overlay.toml").is_file():
            # A directory of overlays named as an entry: the one called `name`.
            for candidate in (target / name, target / Overlay.normalize_name(name)):
                if candidate.is_dir():
                    return candidate
        return target

    def __repr__(self) -> str:
        return "RegistryRepo(%s)" % self.origin


def locate(spec: Spec, cache: GitCache) -> Path:
    """The local path a spec resolves to: a git checkout (plus ``path``), or a
    local path (plus ``path``). Raises when it does not exist."""
    if spec.kind == "git":
        root = cache.checkout(spec)
        target = root / spec.path if spec.path else root
        if not target.exists():
            raise SystemExit("error: %s has no %r" % (redact(spec.location), spec.path))
        return target
    if spec.kind == "url":
        raise ValueError("an http(s) URL has no local path")
    target = Path(spec.location).expanduser()
    if spec.path:
        target = target / spec.path
    if not target.exists():
        raise SystemExit("error: overlay source does not exist: %s" % target)
    return target


def load_repo(spec_text: str, cache: GitCache) -> Any:
    """A :class:`DirRepo` or :class:`RegistryRepo` for a spec: what the spec
    resolves to decides -- a directory is a directory of overlays, a file is a
    registry, an ``http(s)://`` location is fetched as a registry."""
    spec = parse_spec(spec_text)
    origin = spec.display()
    if spec.kind == "url":
        from urllib.parse import urlsplit

        suffix = Path(urlsplit(spec.location).path).suffix
        return RegistryRepo(origin, parse_document(_read_url(spec.location), suffix, origin), cache)
    target = locate(spec, cache)
    if target.is_dir():
        return DirRepo(target, origin)
    return RegistryRepo(origin, parse_document(target.read_text(encoding="utf-8"), target.suffix, origin), cache)


def registry_files(*stores: "Optional[Path]") -> "list[Path]":
    """``<store>/dotagents.<suffix>`` for each store that has one (first suffix
    present per store), in the order given."""
    found = []
    for store in stores:
        if store is None:
            continue
        for suffix in REGISTRY_FILE_SUFFIXES:
            candidate = Path(store) / (REGISTRY_FILE_STEM + suffix)
            if candidate.is_file():
                found.append(candidate)
                break
    return found


def env_repos(environ: "Optional[dict]" = None) -> "list[str]":
    """The ``AGENTS_OVERLAYS_REPO_<KEY>`` values ordered by ``KEY``, then the
    default ``AGENTS_OVERLAYS_REPO`` if set."""
    environ = os.environ if environ is None else environ
    keyed = [(k[len(REPO_ENV_PREFIX):], v) for k, v in environ.items()
             if k.startswith(REPO_ENV_PREFIX) and v.strip()]
    repos = [v.strip() for _k, v in sorted(keyed)]
    default = (environ.get(REPO_ENV_DEFAULT) or "").strip()
    if default:
        repos.append(default)
    return repos


class CompositeSource(object):
    """The repos in precedence order, loaded lazily; the first offering a name
    wins. This is what the overlay commands talk to (``available`` /
    ``overlay_dir`` / ``root``)."""

    def __init__(self, specs: "list[str]", cache: GitCache):
        self.specs = list(specs)
        self.cache = cache
        self._loaded: "dict[int, Any]" = {}

    @property
    def root(self) -> str:
        return " > ".join(redact(s) for s in self.specs)

    def _repo(self, index: int) -> Any:
        if index not in self._loaded:
            self._loaded[index] = load_repo(self.specs[index], self.cache)
        return self._loaded[index]

    def repos(self) -> "list[Any]":
        return [self._repo(i) for i in range(len(self.specs))]

    def available(self) -> "list[str]":
        seen: "list[str]" = []
        for repo in self.repos():
            for name in repo.available():
                if name not in seen:
                    seen.append(name)
        return seen

    def overlay_dir(self, name: str) -> Path:
        for index in range(len(self.specs)):
            repo = self._repo(index)
            if repo.has(name):
                return repo.overlay_dir(name)
        raise SystemExit(
            "error: overlay %r not found in source %s (available: %s)"
            % (name, self.root, ", ".join(self.available()) or "none")
        )

    def __repr__(self) -> str:
        return "CompositeSource(%s)" % self.root


def resolve(
    specs: "list[str]",
    *,
    cache_root: Path,
    stores: "list[Optional[Path]]",
    bundled: "Optional[Path]",
    environ: "Optional[dict]" = None,
    logger: Any = None,
) -> CompositeSource:
    """The source the commands use: ``specs`` (``--repo`` values), the env
    repos, the stores' registry files, then the bundled directory. Raises when
    there is nothing at all."""
    environ = os.environ if environ is None else environ
    ordered = list(specs or []) + env_repos(environ)
    ordered += [str(p) for p in registry_files(*stores)]
    if bundled is not None:
        ordered.append(str(bundled))
    if not ordered:
        raise SystemExit(
            "error: no overlay source. This build bundles no overlays; pass --repo <repo>, "
            "set AGENTS_OVERLAYS_REPO, or add a dotagents.{json,toml,yaml} registry to the store."
        )
    return CompositeSource(ordered, GitCache(cache_root, logger))
