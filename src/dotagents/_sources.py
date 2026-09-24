"""Overlay repos: where ``overlays add`` / ``sync`` fetch an overlay from.

A **repo** is always a *collection* of overlays, the place that answers "give
me overlay ``<name>``". Two shapes:

* a **directory of overlays** -- each subdirectory is an overlay,
  ``<dir>/<name>/`` (a checkout of the `repo` branch's ``overlays/``, any
  folder);
* a **registry** -- a JSON / TOML / YAML document mapping ``<name-or-alias>``
  to the **source** of that one overlay. Either the whole document is the
  mapping, or its ``overlays`` key is.

A **source** is *one* overlay: the directory that is its root.

Repos and sources are both written as a **spec**::

    <location>[@<ref>][#<path>]

``<location>`` is a local path, an ``http(s)://`` URL, or a git repository
(anything ``git clone`` accepts: ``https://…/x.git``, ``git@host:org/x.git``,
``ssh://…``, a local ``…/x.git``; prefix ``git+`` to force git for a URL that
does not end in ``.git``). ``<ref>`` (git only) is a branch, tag or commit --
default the remote's default branch. ``<path>`` is a path inside the location.
What the path names differs: for a **repo** it is the collection -- a
directory (of overlays) or a registry file, the checkout root when absent;
for a **source** it is the overlay's root directory, and when absent *the
repository root is the overlay*.

A ``scheme://`` location that is not git is a **pathlib_next** path. With
the ``uri`` extra installed (``dotagents-cli[uri]``; ``[http]``, ``[sftp]``,
``[s3]`` add the schemes' own clients) any scheme pathlib_next speaks works
the way a local path does: an ``http(s)://`` directory listing, an ``sftp``,
``s3``, ``dav`` or ``github`` tree is a directory of overlays (as a repo) or
one overlay (as a source), a file is a registry; so is an archive --
``zip:<archive-uri>!/<inner>`` (``tar:``, ``archive:``), the archive local or
at any URL. What is remote is
materialized into ``<user store>/.cache/overlays/uri/`` (synced once per
process) and used from there. ``file://`` is a local path, no copy. Without
the extra an ``http(s)://`` location can still be a registry file (fetched
with the standard library); anything else says which extra it needs.

A source written as a **relative path** (``./python``, ``../shared/net``,
``overlays/rust``) is relative to the registry it is in: the registry file's
directory for a local file, for a registry inside a git checkout the same
repository at the same ref with the path joined onto the registry file's
directory (``overlays/reg.toml`` saying ``./rust`` means
``<repo>@<ref>#overlays/rust``), and for a registry at a URL the URL beside
it (``https://h/cfg/reg.json`` saying ``./rust`` means ``https://h/cfg/rust``).
A relative *git* location (``../shared.git@v2``) is another repository, found
the way a relative submodule URL is: beside the registry's repository, or
beside the registry file when that is local or at a URL.

Repos are consulted in order, and **the first that offers a name wins**:
``--repo`` values as given, then ``$AGENTS_OVERLAYS_REPO_<KEY>`` sorted by
``KEY``, then ``$AGENTS_OVERLAYS_REPO`` (the default repo), then
``<project store>/dotagents.{json,toml,yaml,yml}``, then the user store's.
Repos load lazily, in that order, so a git repo late in the list is never
cloned for a name an earlier one had.

Git checkouts live under ``<user store>/.cache/overlays/<repo>-<hash>/`` (one
per repository and ref) and are refreshed (``fetch`` + checkout) once per process; a fetch that
fails against an existing checkout is a warning, and the checkout is used as
it is. The cache root carries a ``.gitignore`` of ``*``, so a store kept in git
never records a checkout. Credentials embedded in a repo URL never reach a log
line, a cache directory name or a checkout's ``.git/config``.

Failures are typed: :class:`OverlayNotFound` when every repo loaded and none
offers the name, :class:`SourceError` for everything else (a repo that cannot
be read, cloned or parsed). Both are ``SystemExit`` subclasses, so an uncaught
one still ends a command with its message; a caller that wants to tolerate a
missing name catches only :class:`OverlayNotFound`.
"""
from __future__ import annotations

import hashlib
import json
import posixpath
from urllib.parse import urljoin, urlsplit, urlunsplit, uses_relative
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional, Union

from dotagents._overlays import Overlay


class SourceError(SystemExit):
    """A repo or source could not be read, fetched, cloned or parsed.

    A ``SystemExit`` (its message is the exit message), so a caller that does
    not handle it still ends with a one-line error rather than a traceback."""


class OverlayNotFound(SourceError):
    """Every repo consulted loaded fine and none offers the overlay asked for --
    the one failure a caller may treat as "absent"."""

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
    """``text`` with the userinfo of every ``scheme://user:pass@host`` removed
    (up to the LAST ``@`` of the authority, so a raw ``@`` in a password goes
    too) and every URL query value masked (``?access_token=***``)."""
    text = re.sub(r"(://)[^/\s?#]*@", r"\1", text)
    return re.sub(r"([?&][^=&#\s]+=)[^&#\s]+", r"\1***", text)


def _is_git_location(location: str) -> bool:
    return location.endswith(".git") or location.startswith(_GIT_PREFIXES)


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
    # Only a GIT location has a ref: for a directory or a non-git URL the `@`
    # is part of the location (`/srv/overlays@v2`, `sftp://user@host`).
    if (
        at and head and tail and not re.search(r"[/:\\]", tail)
        and not head.endswith(("://", ":"))
        and (explicit_git or _is_git_location(head))
    ):
        location, ref = head, tail
    if explicit_git or _is_git_location(location):
        # Passed to git as arguments: a leading `-` would be read as an option
        # (`@--upload-pack=<cmd>` ran <cmd>).
        for part, what in ((location, "location"), (ref, "ref")):
            if part and part.startswith("-"):
                raise SourceError(
                    "error: overlay source %r: the git %s must not start with '-'" % (text, what)
                )
        return Spec("git", location, ref, path)
    if "://" in location:
        return Spec("url", location, None, path)
    return Spec("dir", location, None, path)


# --------------------------------------------------------------------------
# Git checkouts
# --------------------------------------------------------------------------

def uri_path_class() -> "Any":
    """``pathlib_next.uri.UriPath`` when the ``uri`` extra is installed, else
    ``None`` -- the switch between "any scheme pathlib_next speaks" and the
    standard-library http(s) registry fetch."""
    try:
        from pathlib_next.uri import UriPath
    except ImportError:
        return None
    return UriPath


def _local_from_file_url(url: str) -> Path:
    """The local path a ``file://`` URL names."""
    from urllib.parse import urlsplit
    from urllib.request import url2pathname

    parts = urlsplit(url)
    path = url2pathname(parts.path)
    if parts.netloc and parts.netloc not in ("", "localhost"):
        # file://host/share/x -> a UNC path on Windows, a host-qualified one elsewhere.
        path = "//%s%s" % (parts.netloc, path.replace("\\", "/"))
    return Path(path)


def url_scheme(location: str) -> str:
    return location.split("://", 1)[0].lower()


class SourceCache(object):
    """What the commands fetch, under ``root``: git clones, one per repository
    location AND ref (two registry entries naming the same repository at
    different refs -- ``@main`` for one overlay, ``@dev`` for another -- must
    each see their own tree, since the commands resolve every source before
    copying any), and materializations of pathlib_next paths under ``uri/``
    (a remote directory synced down, a remote file copied), each refreshed
    once per process."""

    def __init__(self, root: Path, logger: Any = None):
        self.root = Path(root)
        self.logger = logger
        self._fresh: "set[str]" = set()  # locations fetched during this process

    # --- pathlib_next paths ----------------------------------------------

    def materialize(self, spec: Spec) -> Path:
        """The local path a ``url`` spec resolves to: the path itself for
        ``file://``; otherwise the remote (``location`` joined with ``path``)
        brought into the cache -- a directory synced (deletions mirrored), a
        file copied -- once per process. Needs the ``uri`` extra for anything
        but ``file://``; a missing remote is an error naming it (redacted)."""
        if spec.kind != "url":
            raise ValueError("not a url spec: %r" % (spec,))
        if url_scheme(spec.location) == "file":
            target = _local_from_file_url(spec.location)
            if spec.path:
                target = target / spec.path
            if not target.exists():
                raise SourceError("error: overlay source does not exist: %s" % target)
            return target
        UriPath = uri_path_class()
        if UriPath is None:
            raise SourceError(
                "error: %s: a %s:// source needs the pathlib_next uri extra "
                "(pip install 'dotagents-cli[uri]', or [http] / [sftp] / [s3] for the "
                "scheme's own client)" % (redact(spec.location), url_scheme(spec.location))
            )
        from pathlib_next import LocalPath
        from pathlib_next.utils.sync import PathSyncer

        remote = UriPath(spec.location)
        if spec.path:
            remote = remote / spec.path
        shown = redact(spec.display())
        try:
            is_dir = remote.is_dir()
            if not is_dir and not remote.exists():
                raise SourceError("error: %s does not exist" % shown)
        except SystemExit:
            raise
        except Exception as exc:  # the scheme's client: 404, auth, network
            raise SourceError("error: cannot reach %s: %s" % (shown, redact(str(exc))))
        local = self.root / "uri" / self._slug(spec.location, spec.path)
        key = "uri:" + str(local)
        if key in self._fresh:
            return local / remote.name if not is_dir else local
        self._ensure_root()
        local.mkdir(parents=True, exist_ok=True)
        try:
            if is_dir:
                PathSyncer(remove_missing=True).sync(remote, LocalPath(local))
                result = local
            else:
                result = local / (remote.name or "registry")
                result.write_bytes(remote.read_bytes())
        except Exception as exc:
            raise SourceError("error: cannot fetch %s: %s" % (shown, redact(str(exc))))
        self._fresh.add(key)
        if self.logger:
            self.logger.info("fetched %s", shown)
        return result

    def _ensure_root(self) -> None:
        """Create the cache root with a ``.gitignore`` of ``*`` -- the cache
        sits inside the user store, and a store kept in git (private-sync's
        ``git add -A``) must never record a checkout (an embedded repo) or a
        materialized tree."""
        from dotagents._fs import write_text_lf

        self.root.mkdir(parents=True, exist_ok=True)
        ignore = self.root / ".gitignore"
        if not ignore.exists():
            write_text_lf(ignore, "# dotagents source cache: never tracked\n*\n")

    @staticmethod
    def _slug(location: str, path: "Optional[str]") -> str:
        # The readable part comes from the REDACTED location, so a URL with no
        # path never puts its userinfo in a directory name; the hash keeps the
        # full location apart.
        stem = posixpath.basename((redact(location).rstrip("/") + ("/" + path.strip("/") if path else "")))
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.") or "source"
        digest = hashlib.sha1(("%s#%s" % (location, path or "")).encode("utf-8")).hexdigest()[:12]
        return "%s-%s" % (slug, digest)

    # --- git ---------------------------------------------------------------

    def _git(self, args: "list[str]", cwd: "Optional[Path]", *, check: bool = True) -> "subprocess.CompletedProcess":
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"  # never hang on a credential prompt
        try:
            proc = subprocess.run(
                ["git", *args], cwd=str(cwd) if cwd else None,
                capture_output=True, text=True, env=env,
            )
        except OSError as exc:
            raise SourceError("error: git is needed for a git overlay source: %s" % exc)
        if check and proc.returncode != 0:
            raise SourceError(
                "error: git %s failed (exit %d): %s"
                % (args[0], proc.returncode, redact(proc.stderr.strip() or proc.stdout.strip()))
            )
        return proc

    def repo_dir(self, location: str, ref: "Optional[str]" = None) -> Path:
        stem = Path(redact(location).rstrip("/").replace("\\", "/")).name
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
        credentialed = redact(spec.location) != spec.location
        if not (dest / ".git").exists():
            self._ensure_root()
            if self.logger:
                self.logger.info("cloning %s", redact(spec.location))
            self._git(["clone", "--quiet", "--", spec.location, str(dest)], None)
            if credentialed:
                # Keep the credential out of the checkout's .git/config: fetch
                # passes the full URL each time instead (see _fetch_args).
                self._git(["remote", "set-url", "origin", redact(spec.location)], dest)
            self._fresh.add(key)
        elif key not in self._fresh:
            if credentialed:  # a checkout cloned before origin was redacted
                self._git(["remote", "set-url", "origin", redact(spec.location)], dest, check=False)
            proc = self._git(self._fetch_args(spec, ["--tags", "--prune"]), dest, check=False)
            if proc.returncode != 0 and self.logger:
                self.logger.warning(
                    "fetch of %s failed (using the cached checkout as is): %s",
                    redact(spec.location), redact(proc.stderr.strip()),
                )
            self._fresh.add(key)
        self._checkout_ref(dest, spec)
        return dest

    @staticmethod
    def _fetch_args(spec: Spec, options: "list[str]", refspecs: "Optional[list[str]]" = None) -> "list[str]":
        """``git fetch`` from origin -- or, when the location carries
        credentials (origin holds the redacted URL), from the full location
        into origin's remote-tracking refs."""
        if redact(spec.location) == spec.location:
            return ["fetch", "--quiet", *options, "origin", *(refspecs or [])]
        return [
            "fetch", "--quiet", *options, "--", spec.location,
            *(refspecs or ["+refs/heads/*:refs/remotes/origin/*"]),
        ]

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
            fetched = self._git(self._fetch_args(spec, [], [spec.ref]), dest, check=False)
            if fetched.returncode == 0:
                self._git(["checkout", "--quiet", "--detach", "FETCH_HEAD"], dest)
                return
        raise SourceError(
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
        loads, what, errors = json.loads, "JSON", (ValueError,)
    elif suffix == ".toml":
        try:
            import tomllib  # type: ignore[import-not-found]
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore[no-redef]
            except ImportError:
                raise SourceError(
                    "error: registry %s is TOML, which needs Python 3.11+ or `pip install tomli`" % origin
                )
        loads, what, errors = tomllib.loads, "TOML", (ValueError,)
    elif suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            raise SourceError("error: registry %s is YAML, which needs `pip install pyyaml`" % origin)
        loads, what, errors = yaml.safe_load, "YAML", (ValueError, yaml.YAMLError)
    else:
        raise SourceError(
            "error: registry %s: unknown format %r (use .json, .toml, .yaml or .yml)" % (origin, suffix)
        )
    try:
        doc = loads(text)
    except errors as exc:
        raise SourceError("error: registry %s is not valid %s: %s" % (origin, what, redact(str(exc))))
    if isinstance(doc, dict) and isinstance(doc.get("overlays"), dict):
        doc = doc["overlays"]
    if not isinstance(doc, dict):
        raise SourceError("error: registry %s must be a mapping of overlay name -> source" % origin)
    entries: "dict[str, str]" = {}
    for key, value in doc.items():
        if not isinstance(value, str) or not value.strip():
            raise SourceError("error: registry %s: entry %r must be a source string" % (origin, key))
        entries[str(key)] = value.strip()
    return entries


def _read_text(path: Path, origin: str) -> str:
    """A local registry file's text; unreadable or non-UTF-8 is a SourceError."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SourceError("error: cannot read registry %s: %s" % (origin, exc))


def _read_url(url: str) -> str:
    """Fetch an http(s) registry with the standard library. Userinfo in the URL
    (``https://user:token@host/…``) is sent as HTTP Basic auth to the URL
    without it -- ``urlopen`` would otherwise read ``token@host`` as a port --
    and never follows a redirect to another host. Every failure is a
    SourceError whose message is redacted."""
    import base64
    import http.client
    import urllib.request
    from urllib.parse import unquote

    shown = redact(url)
    parts = urlsplit(url)
    auth = None
    if "@" in parts.netloc:
        userinfo, _, hostport = parts.netloc.rpartition("@")
        user, _, password = userinfo.partition(":")
        auth = "Basic " + base64.b64encode(
            ("%s:%s" % (unquote(user), unquote(password))).encode("utf-8")
        ).decode("ascii")
        url = urlunsplit((parts.scheme, hostport, parts.path, parts.query, parts.fragment))
    try:
        request = urllib.request.Request(url)
        if auth:
            # Unredirected: a redirect never carries the credential elsewhere.
            request.add_unredirected_header("Authorization", auth)
        with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310 -- the user named it
            return resp.read().decode("utf-8")
    except (OSError, ValueError, http.client.HTTPException) as exc:  # UnicodeDecodeError is a ValueError
        raise SourceError("error: cannot read registry %s: %s" % (shown, redact(str(exc))))


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
            raise OverlayNotFound(
                "error: overlay %r not found in source %s (available: %s)"
                % (name, self.origin, ", ".join(self.available()) or "none")
            )
        return found

    def __repr__(self) -> str:
        return "DirRepo(%s)" % self.origin


class RegistryRepo(object):
    """A registry: ``entries`` map names (or aliases) to specs, materialized on
    demand through ``cache``. ``base`` is where the registry document lives
    (see :func:`resolve_relative`); ``None`` leaves a relative entry as the
    process sees it."""

    def __init__(
        self, origin: str, entries: "dict[str, str]", cache: SourceCache, base: "Optional[Spec]" = None,
    ):
        self.origin = origin
        self.entries = entries
        self.cache = cache
        self.base = base
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
            raise OverlayNotFound("error: overlay %r not in registry %s" % (name, self.origin))
        spec = parse_spec(self.entries[key])
        spec = resolve_relative(spec, self.base, origin=self.origin, key=key)
        target = locate(spec, self.cache)
        if not target.is_dir():
            raise SourceError("error: registry %s: %r resolves to %s, not a directory" % (self.origin, key, spec.display()))
        if not (target / "overlay.toml").is_file():
            # A directory of overlays named as an entry: the one called `name`.
            for candidate in (target / name, target / Overlay.normalize_name(name)):
                if candidate.is_dir():
                    return candidate
        return target

    def __repr__(self) -> str:
        return "RegistryRepo(%s)" % self.origin


def _is_relative_path(location: str) -> bool:
    """A local path that is not absolute, rooted, drive-qualified or
    ``~``-prefixed. Rooted (``/x``) is spelled out because on Windows (3.13+)
    ``os.path.isabs`` does not count a drive-less root as absolute."""
    return not (
        os.path.isabs(location)
        or location.startswith(("~", "/", "\\"))
        or re.match(r"^[A-Za-z]:", location)
    )


def is_relative(spec: Spec) -> bool:
    """A spec meaningful only against the registry it came from: a ``dir``
    spec whose location is a relative path, or a ``git`` spec whose location
    is one (``../shared.git`` -- not a URL, not scp-like ``host:path``)."""
    if spec.kind == "dir":
        return _is_relative_path(spec.location)
    if spec.kind == "git":
        location = spec.location
        if "://" in location:
            return False
        colon, slash = location.find(":"), location.find("/")
        if colon > 1 and (slash == -1 or colon < slash):
            return False  # scp-like `[user@]host:path`
        return _is_relative_path(location)
    return False


def _join_beside(base: str, rel: str) -> str:
    """``rel`` resolved against the document at URL ``base`` -- "the URL
    beside it". http(s)/ftp/file/sftp go through ``urljoin`` (RFC 3986); any
    other scheme (``s3://``, ``github://``) joins the path with posixpath, and
    an archive URL (``zip:<archive>!/<inner>``) joins inside the archive."""
    rel = rel.replace("\\", "/")

    def join_path(base_path: str) -> str:
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(base_path), rel))
        return joined + "/" if rel.endswith("/") and not joined.endswith("/") else joined

    if "!/" in base:
        head, _sep, inner = base.rpartition("!/")
        return head + "!/" + join_path("/" + inner).lstrip("/")
    parts = urlsplit(base)
    if parts.scheme in uses_relative and parts.netloc:
        return urljoin(base, rel)
    return urlunsplit((parts.scheme, parts.netloc, join_path(parts.path), "", ""))


def _beside_repository(base: Spec, rel: str) -> str:
    """A relative git location resolved the way a relative submodule URL is:
    against the repository ``base`` names (``../b.git`` beside
    ``https://h/org/a.git`` is ``https://h/org/b.git``), or against the
    registry file's directory / URL for a ``dir`` / ``url`` base."""
    rel = rel.replace("\\", "/")
    if base.kind == "dir":
        return os.path.normpath(os.path.join(base.location, rel))
    if base.kind == "url":
        return _join_beside(base.location, rel)
    location = base.location
    if "://" in location:
        return _join_beside(location.rstrip("/") + "/", rel)
    colon, slash = location.find(":"), location.find("/")
    if colon > 1 and (slash == -1 or colon < slash):
        host, _sep, path = location.partition(":")
        return host + ":" + posixpath.normpath(posixpath.join(path, rel))
    return os.path.normpath(os.path.join(location, rel))


def resolve_relative(spec: Spec, base: "Optional[Spec]", *, origin: str, key: str) -> Spec:
    """``spec`` made absolute against ``base``, the place its registry lives,
    when it is a relative path; any other spec, or no base, comes back as is.

    ``base`` is a ``dir`` spec (the registry file's directory), a ``git`` spec
    (the repository, ref and the registry file's directory inside it -- the
    entry stays in the same repository at the same ref), or a ``url`` spec
    (the registry's URL: the entry is the URL beside it)."""
    if base is None or not is_relative(spec):
        return spec
    if spec.kind == "git":
        return Spec("git", _beside_repository(base, spec.location), spec.ref, spec.path)
    if base.kind == "dir":
        location = os.path.normpath(os.path.join(base.location, spec.location))
        return Spec("dir", location, None, spec.path)
    if base.kind == "git":
        parts = [base.path or "", spec.location.replace("\\", "/")]
        if spec.path:
            parts.append(spec.path)
        rel = posixpath.normpath(posixpath.join(*parts)).strip("/")
        if rel == "..":
            rel = "../"
        if rel.startswith("../"):
            raise SourceError(
                "error: registry %s: %r (%s) leaves the repository it is in"
                % (origin, key, spec.location)
            )
        return Spec("git", base.location, base.ref, None if rel in ("", ".") else rel)
    # A URL: what a relative link in a document at that URL would mean.
    rel = spec.location.replace("\\", "/")
    if spec.path:
        rel = rel.rstrip("/") + "/" + spec.path
    return Spec("url", _join_beside(base.location, rel), None, None)


def locate(spec: Spec, cache: SourceCache) -> Path:
    """The local path a spec resolves to: a git checkout (plus ``path``), or a
    local path (plus ``path``). Raises when it does not exist."""
    if spec.kind == "git":
        root = cache.checkout(spec)
        target = root / spec.path if spec.path else root
        if not target.exists():
            raise SourceError("error: %s has no %r" % (redact(spec.location), spec.path))
        return target
    if spec.kind == "url":
        return cache.materialize(spec)
    target = Path(spec.location).expanduser()
    if spec.path:
        target = target / spec.path
    if not target.exists():
        raise SourceError("error: overlay source does not exist: %s" % target)
    return target


def load_repo(spec_text: "Union[str, Spec]", cache: SourceCache) -> Any:
    """A :class:`DirRepo` or :class:`RegistryRepo` for a spec: what the spec
    resolves to decides -- a directory is a directory of overlays, a file is a
    registry, an ``http(s)://`` location is fetched as a registry. A
    pre-parsed :class:`Spec` (a store's own registry path) is used as is, so a
    ``#`` or ``@`` in a local path is never read as spec grammar."""
    spec = parse_spec(spec_text) if isinstance(spec_text, str) else spec_text
    origin = spec.display()
    if spec.kind == "url" and url_scheme(spec.location) != "file" and uri_path_class() is None:
        # No uri extra: the standard library can still fetch an http(s)
        # registry file; anything else is out of reach.
        if url_scheme(spec.location) not in ("http", "https"):
            cache.materialize(spec)  # raises, naming the extra
        url = spec.location
        if spec.path:
            url = url.rstrip("/") + "/" + spec.path.strip("/")
        suffix = Path(urlsplit(url).path).suffix
        entries = parse_document(_read_url(url), suffix, origin)
        return RegistryRepo(origin, entries, cache, base=Spec("url", url))
    target = locate(spec, cache)
    if target.is_dir():
        return DirRepo(target, origin)
    entries = parse_document(_read_text(target, origin), target.suffix, origin)
    # Relative entries resolve against the registry FILE: its directory, or
    # for a file inside a git checkout, the same repository and ref with the
    # file's directory as the path prefix.
    if spec.kind == "git":
        base = Spec("git", spec.location, spec.ref, posixpath.dirname(spec.path or "") or None)
    elif spec.kind == "url" and url_scheme(spec.location) != "file":
        location = spec.location.rstrip("/") + ("/" + spec.path.strip("/") if spec.path else "")
        base = Spec("url", location)
    else:
        base = Spec("dir", str(target.parent))
    return RegistryRepo(origin, entries, cache, base=base)


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


NO_SOURCE_MESSAGE = (
    "error: no overlay source: pass --repo <repo>, set AGENTS_OVERLAYS_REPO, "
    "or add a dotagents.{json,toml,yaml} registry to the store."
)


class CompositeSource(object):
    """The repos in precedence order, loaded lazily; the first offering a name
    wins. This is what the overlay commands talk to (``available`` /
    ``overlay_dir`` / ``root``)."""

    def __init__(self, specs: "list[Union[str, Spec]]", cache: SourceCache):
        self.specs = list(specs)
        self.cache = cache
        self._loaded: "dict[int, Any]" = {}

    @property
    def root(self) -> str:
        return " > ".join(
            s.display() if isinstance(s, Spec) else redact(s) for s in self.specs
        )

    def _repo(self, index: int) -> Any:
        if index not in self._loaded:
            self._loaded[index] = load_repo(self.specs[index], self.cache)
        return self._loaded[index]

    def repos(self) -> "list[Any]":
        return [self._repo(i) for i in range(len(self.specs))]

    def available(self, on_error: Any = None) -> "list[str]":
        """Every name the repos offer, first-offered order. A repo that fails
        to load raises -- unless ``on_error(spec, exc)`` is given, which is
        called for it instead so the other repos are still listed."""
        seen: "list[str]" = []
        for index in range(len(self.specs)):
            try:
                repo = self._repo(index)
            except SourceError as exc:
                if on_error is None:
                    raise
                on_error(self.specs[index], exc)
                continue
            for name in repo.available():
                if name not in seen:
                    seen.append(name)
        return seen

    def overlay_dir(self, name: str) -> Path:
        """The directory of overlay ``name`` from the first repo offering it.
        :class:`OverlayNotFound` when no repo does; a repo that cannot be
        loaded is a :class:`SourceError`, never "not found"."""
        for index in range(len(self.specs)):
            repo = self._repo(index)
            if repo.has(name):
                return repo.overlay_dir(name)
        if not self.specs:
            raise OverlayNotFound(NO_SOURCE_MESSAGE)
        raise OverlayNotFound(
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
    environ: "Optional[dict]" = None,
    logger: Any = None,
    allow_empty: bool = False,
) -> CompositeSource:
    """The source the commands use: ``specs`` (``--repo`` values), the env
    repos, then the stores' registry files. Raises when
    there is nothing at all, unless ``allow_empty``: then an empty source
    answers every name with :class:`OverlayNotFound`."""
    environ = os.environ if environ is None else environ
    ordered: "list[Union[str, Spec]]" = list(specs or []) + env_repos(environ)
    # Paths the process found, not specs the user wrote: never re-parsed.
    ordered += [Spec("dir", str(p)) for p in registry_files(*stores)]
    if not ordered and not allow_empty:
        raise SourceError(NO_SOURCE_MESSAGE)
    return CompositeSource(ordered, SourceCache(cache_root, logger))
