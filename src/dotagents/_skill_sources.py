"""Skills an overlay names from elsewhere: planned, fetched, kept and published.

An overlay's ``skills`` lists bundled skills, names a catalog resolves, and tables naming a
source. :mod:`coqui_aos` plans them (:func:`plan_skills`) and checks a fetched directory; this module
fetches one from a Git source (:class:`GitFetcher`), keeps what the person accepted under
``<store>/overlays/.dotagents-skills/`` (:class:`FetchedSkills`) and publishes it into the shared
skills directory the way a bundled skill is (:func:`publish`).

Nothing here runs anything from a source: a skill is read out of Git's object store, never checked
out, so no hook, filter or attribute of the source applies, and it is fetched without the user's
credentials, SSH agent or prompts and without following a redirect.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from dotagents import _aos, _skills
from dotagents._fs import write_text_lf
from dotagents._vendor import coqui_aos
from dotagents._vendor.coqui_aos import (
    EXCLUDED_COMPONENTS,
    Class,
    Error,
    Fetch,
    FetchedSkill,
    OverlayTree,
    PlannedOrigin,
    PlannedSkill,
    Settings,
    SkillPlan,
    Source,
)

#: Beside ``<store>/overlays/``'s install records: the fetched skills' files and what was accepted.
STORE_DIR = ".dotagents-skills"
RECORD_FILE = "record.json"
#: The digests of the fetched skills published as copies, apart from the bundled skills' record.
PUBLISHED_FETCHED_RECORD = ".dotagents-published-fetched.json"
#: The URL schemes a skill's Git source may use; a test adds ``file``.
GIT_PROTOCOLS = "http:https:ssh:git"
#: What one skill's directory may hold: the same files and bytes canoa's app reads (1024 files, 16 MiB).
SKILL_LIMITS = Settings(tree_files=1024, tree_bytes=16 * 1024 * 1024, file_bytes=16 * 1024 * 1024)
#: The most one fetch may write into its scratch directory before it is ended: canoa's bound too.
MAX_FETCH_BYTES = 256 * 1024 * 1024
#: The most a catalog read over the web may be.
MAX_CATALOG_BYTES = 4 * 1024 * 1024
#: What the person's own environment may still pass to a fetch: where programs are, and a proxy.
_KEPT_ENV = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "LANG",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
)
#: Front matter keys that carry no grant; any other is shown and needs the person's allowance.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
INERT_KEYS = ("name", "description", "license", "compatibility", "metadata")


def _remove_tree(path: Path) -> None:
    def retry(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        for delay in (0, 0.05, 0.1, 0.2, 0.4):
            time.sleep(delay)
            try:
                func(target)
                return
            except PermissionError:
                continue
        func(target)

    if path.is_symlink():
        path.unlink()
    elif path.exists():
        if sys.version_info >= (3, 12):
            shutil.rmtree(str(path), onexc=retry)
        else:  # pragma: no cover -- the 3.9 leg
            shutil.rmtree(str(path), onerror=retry)


def valid_skill_name(name: Any) -> bool:
    """Whether ``name`` is a skill name that is also one safe path component."""
    return (
        isinstance(name, str) and _NAME.fullmatch(name) is not None and coqui_aos.valid_relative_path(name)
    )


# --------------------------------------------------------------------------- #
# The plan.
# --------------------------------------------------------------------------- #


def _read_catalog_url(url: str) -> bytes:
    """A catalog over ``http(s)``: no redirect is followed (a manifest-picked host does not pick another) and at
    most :data:`MAX_CATALOG_BYTES` are read."""
    import urllib.error
    import urllib.request

    class Refuse(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise urllib.error.HTTPError(req.full_url, code, "a redirect is not followed", headers, fp)

    opener = urllib.request.build_opener(Refuse)
    with opener.open(urllib.request.Request(url), timeout=30) as response:  # noqa: S310 -- the person named it
        data = response.read(MAX_CATALOG_BYTES + 1)
    if len(data) > MAX_CATALOG_BYTES:
        raise OSError("the catalog is larger than %d bytes" % MAX_CATALOG_BYTES)
    return data


def load_catalogs(specs: "Iterable[str]") -> "List[Tuple[str, Any]]":
    """``LABEL=LOCATION`` catalogs read from a file or an ``http(s)`` URL. Raises ``ValueError`` for a
    spec or a catalog that does not read, naming it."""
    out = []
    for spec in specs:
        label, sep, location = spec.partition("=")
        if not sep or not label or not location:
            raise ValueError("--skill-catalog %r is not LABEL=LOCATION" % spec)
        try:
            if location.lower().startswith(("http://", "https://")):
                data = _read_catalog_url(location)
            else:
                data = Path(location).read_bytes()
            catalog = coqui_aos.read_catalog(data, os.path.basename(location.split("?", 1)[0]), location)
        except (OSError, ValueError, Error, SystemExit) as exc:
            raise ValueError("catalog %s (%s) cannot be read: %s" % (label, _shown(location), exc)) from None
        out.append((label, catalog))
    try:
        coqui_aos.list_catalogs(out)
    except Error as exc:
        raise ValueError("the catalogs are not valid: %s" % exc) from None
    return out


def _shown(location: str) -> str:
    from dotagents._sources import redact

    return redact(location)


def plan_skills(overlays: "Mapping[str, Any]", catalogs: "Sequence[Tuple[str, Any]]" = ()) -> SkillPlan:
    """What installing the skills of ``overlays`` (normalized name to :class:`Overlay`) would do, before
    anything is fetched. Raises :class:`coqui_aos.Error` for a ``requires`` cycle."""
    docs = {name: overlay.document() for name, overlay in overlays.items()}
    trees = {name: OverlayTree(overlay.member_paths()) for name, overlay in overlays.items()}
    return coqui_aos.resolve_skills(docs, list(overlays), trees, list(catalogs), (), True, Settings())


#: The identity a skill the person chose is planned under; no overlay is called this.
CHOSEN = "chosen-by-you"


def plan_chosen(entry: Any, catalogs: "Sequence[Tuple[str, Any]]" = ()) -> SkillPlan:
    """The plan for one skill the person chose: a name, or a table with a ``name`` and a ``source``."""
    return coqui_aos.resolve_skills({CHOSEN: {"skills": [entry]}}, [CHOSEN], {}, list(catalogs), (), True, Settings())


def elsewhere(plan: SkillPlan) -> "List[PlannedSkill]":
    """The planned skills that do not come with their overlay."""
    return [skill for skill in plan.skills if skill.origin is not PlannedOrigin.BUNDLED]


def _pin_text(pin: "Optional[str]", configured: "Optional[str]") -> str:
    if configured and pin and configured.lower() != pin.lower():
        return "pinned %s but your configuration pins %s: it will be refused" % (pin, configured)
    if configured:
        return "pinned %s by your configuration" % configured
    return "pinned %s" % pin if pin else "UNPINNED"


def review_lines(plan: SkillPlan, pins: "Optional[Mapping[str, str]]" = None,
                 store: "Optional[FetchedSkills]" = None) -> "List[str]":
    """What a person is shown before the yes: each skill from elsewhere with the overlay naming it and its
    source, and what cannot be resolved, which is never guessed. A skill already fetched also shows the
    front matter keys beyond the inert ones and the files it has; a new one shows them once it is fetched,
    before it is delivered."""
    pins = pins or {}
    records = store.records() if store is not None else {}
    lines = []
    for skill in elsewhere(plan):
        head = "skill %s (named by overlay %s): " % (skill.name, skill.overlay)
        if skill.origin is PlannedOrigin.MISSING:
            lines.append(head + "MISSING, no catalog offers it; not installed")
        elif skill.origin is PlannedOrigin.AMBIGUOUS:
            lines.append(head + "AMBIGUOUS, catalogs %s offer it; not installed" % ", ".join(skill.catalogs or []))
        else:
            via = " (catalog %s)" % skill.catalog if skill.catalog else ""
            lines.append(head + "fetched from %s%s, %s" % (
                skill.source, via, _pin_text(skill.pin, pins.get(skill.name.lower()))))
            record = records.get(skill.name.lower())
            if record is not None and store is not None and store.tree(skill.name).is_dir():
                lines.append(head + "has %d file(s)%s" % (
                    len(store.files(skill.name)),
                    ", front matter keys %s" % ", ".join(record["keys"]) if record.get("keys") else ""))
    for ident, name, overlay in plan.non_defaults:
        lines.append("skill %s (named by overlay %s): %s" % (name, overlay, ident))
    for name, overlay in plan.shadowing:
        lines.append("skill %s (named by overlay %s): not taken, another overlay's skill keeps the name" % (name, overlay))
    return lines


# --------------------------------------------------------------------------- #
# Fetching.
# --------------------------------------------------------------------------- #


def _dir_bytes(path: Path) -> int:
    total = 0
    for base, _dirs, files in os.walk(str(path)):
        for name in files:
            try:
                total += os.lstat(os.path.join(base, name)).st_size
            except OSError:
                pass
    return total


def fold_collisions(paths: "Iterable[str]") -> "Optional[str]":
    """A path that another equals once Unicode-normalised and case-folded (one file on Windows and macOS,
    two on Linux), or None."""
    seen: "Dict[str, str]" = {}
    for path in paths:
        key = unicodedata.normalize("NFC", unicodedata.normalize("NFKC", path).casefold()).casefold()
        if key in seen and seen[key] != path:
            return path
        seen[key] = path
    return None


def _excluded(path: str) -> bool:
    parts = path.split("/")
    return any(part in EXCLUDED_COMPONENTS for part in parts) or parts[-1].endswith(".pyc")


class GitFetcher(object):
    """Reads a skill's directory out of a Git source (``coqui_aos.SkillSource``).

    ``extra_config`` are ``-c`` settings a test adds (a ``url.<base>.insteadOf`` over a made-up host) and
    ``protocols`` the schemes Git may use; neither reaches a manifest. A fetch is ended when it has written
    ``max_fetch_bytes`` into its scratch directory."""

    def __init__(self, scratch: Path, *, git: str = "git", protocols: str = GIT_PROTOCOLS,
                 extra_config: "Sequence[str]" = (), ssl_revoke: "Optional[str]" = None, timeout: int = 300,
                 settings: "Optional[Settings]" = None, max_fetch_bytes: int = MAX_FETCH_BYTES):
        self.scratch = Path(scratch)
        self.git = git
        self.protocols = protocols
        self.extra_config = list(extra_config)
        self.ssl_revoke = ssl_revoke
        self.timeout = timeout
        self.settings = settings or SKILL_LIMITS
        self.max_fetch_bytes = max_fetch_bytes

    def _empty_file(self) -> Path:
        """An empty file: ssh reads no configuration and no key from it (``NUL`` is no file to ssh on Windows)."""
        self.scratch.mkdir(parents=True, exist_ok=True)
        path = self.scratch / "empty"
        if not path.exists():
            path.write_bytes(b"")
        return path

    def environment(self) -> "Dict[str, str]":
        """The environment of every Git command: the person's own credentials, agent, askpass and Git
        configuration are not in it."""
        env = {key: value for key, value in os.environ.items() if key in _KEPT_ENV or key.upper() in _KEPT_ENV}
        empty = self._empty_file().as_posix()
        env.update({
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": empty,
            "GIT_ALLOW_PROTOCOL": self.protocols,
            "GCM_INTERACTIVE": "never",
            "GIT_SSH_COMMAND": "ssh -F '%s' -o BatchMode=yes -o IdentityAgent=none -o IdentitiesOnly=yes "
                               "-o IdentityFile='%s'" % (empty, empty),
        })
        return env

    def arguments(self, args: "Sequence[str]") -> "List[str]":
        config = ["credential.helper=", "core.askPass=", "http.followRedirects=false", "core.symlinks=false",
                  "core.autocrlf=false", "transfer.fsckObjects=true", "protocol.version=2"]
        if self.ssl_revoke:
            config.append("http.schannelCheckRevoke=%s" % self.ssl_revoke)
        out = [self.git]
        for item in config + self.extra_config:
            out += ["-c", item]
        return out + list(args)

    def _run(self, args: "Sequence[str]", cwd: Path, *, data: "Optional[bytes]" = None,
             watch: bool = False) -> "subprocess.CompletedProcess":
        try:
            proc = subprocess.Popen(
                self.arguments(args), cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=self.environment(),
            )
        except OSError as exc:
            raise Error(Class.IO, "git: %s" % type(exc).__name__) from None
        if not watch:
            try:
                out, err = proc.communicate(data, timeout=self.timeout)
            except subprocess.SubprocessError:
                proc.kill()
                proc.communicate()
                raise Error(Class.IO, "git timed out") from None
            return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
        # A fetch is watched: what it has written is counted, and it ends when that passes the bound.
        import threading

        chunks: "List[bytes]" = []
        drain = threading.Thread(target=lambda: chunks.append(proc.stderr.read()), daemon=True)  # type: ignore[union-attr]
        drain.start()
        deadline = time.monotonic() + self.timeout
        while proc.poll() is None:
            if _dir_bytes(cwd) > self.max_fetch_bytes:
                proc.kill()
                proc.wait()
                raise Error(Class.TOO_LARGE, "the fetch is larger than %d bytes" % self.max_fetch_bytes)
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait()
                raise Error(Class.IO, "git timed out")
            time.sleep(0.05)
        drain.join(5)
        return subprocess.CompletedProcess(proc.args, proc.returncode, b"", b"".join(chunks))

    def fetch(self, source: Source) -> "List[Tuple[str, bytes]]":
        """The relative paths and bytes of the directory ``source`` names, at its ref. Raises ``Error``."""
        if source.kind is not coqui_aos.SourceKind.GIT:
            raise Error(Class.IO, "only a Git source is fetched here")
        self.scratch.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="skill-", dir=str(self.scratch)))
        try:
            return self._read(source, work)
        finally:
            _remove_tree(work)

    def _read(self, source: Source, work: Path) -> "List[Tuple[str, bytes]]":
        init = self._run(["init", "--quiet", "--bare", "--template=", "."], work)
        if init.returncode != 0:
            raise Error(Class.IO, "git init")
        ref = source.ref or "HEAD"
        # One commit, shallow. A commit hash the server will not give by itself is not fetched by hash: the
        # whole history is not asked for.
        got = self._run(["fetch", "--quiet", "--no-tags", "--depth=1", "--", source.location, ref], work, watch=True)
        if got.returncode != 0:
            raise Error(Class.IO, "the source cannot be fetched at %s" % ref)
        treeish = "FETCH_HEAD" + (":" + source.path if source.path else "")
        listing = self._run(["ls-tree", "-r", "-l", "-z", treeish], work)
        if listing.returncode != 0:
            raise Error(Class.NOT_FOUND, "the directory")
        entries = self._entries(listing.stdout)
        return self._blobs(entries, work)

    def _entries(self, raw: bytes) -> "List[Tuple[str, str, int]]":
        out = []
        total = 0
        for item in raw.split(b"\0"):
            if not item:
                continue
            meta, _tab, path = item.partition(b"\t")
            mode, kind, sha, size = meta.decode("ascii", "replace").split(None, 3)
            try:
                name = path.decode("utf-8")
            except UnicodeDecodeError:
                raise Error(Class.NON_UTF8_NAME) from None
            if _excluded(name):
                continue  # the digest leaves it out, so it is no refusal and it is not written
            if mode == "120000":
                raise Error(Class.SYMLINK, "a link in the skill")
            if kind != "blob":
                raise Error(Class.SPECIAL_FILE, "a submodule in the skill")
            if not _aos.valid_relpath(name):
                raise Error(Class.INVALID_NAME, "a path in the skill")
            size_n = int(size.strip())
            total += size_n
            if not self.settings.allows("tree_files", len(out) + 1):
                raise Error(Class.TOO_LARGE, "too many files")
            if not self.settings.allows("file_bytes", size_n) or not self.settings.allows("tree_bytes", total):
                raise Error(Class.TOO_LARGE, "more bytes than the limit")
            out.append((name, sha, size_n))
        clash = fold_collisions(name for name, _sha, _size in out)
        if clash is not None:
            raise Error(Class.CASE_COLLISION, clash)
        return out

    def _blobs(self, entries: "List[Tuple[str, str, int]]", work: Path) -> "List[Tuple[str, bytes]]":
        if not entries:
            return []
        wanted = "".join(sha + "\n" for _name, sha, _size in entries).encode("ascii")
        proc = self._run(["cat-file", "--batch"], work, data=wanted)
        if proc.returncode != 0:
            raise Error(Class.IO, "git cat-file")
        data, at, out = proc.stdout, 0, []
        for name, sha, size in entries:
            end = data.index(b"\n", at)
            head = data[at:end].decode("ascii", "replace").split()
            if len(head) != 3 or head[0] != sha or head[1] != "blob" or int(head[2]) != size:
                raise Error(Class.IO, "git cat-file")
            body = data[end + 1:end + 1 + size]
            if len(body) != size:
                raise Error(Class.IO, "git cat-file")
            out.append((name, body))
            at = end + 1 + size + 1
        return out


# --------------------------------------------------------------------------- #
# What was accepted.
# --------------------------------------------------------------------------- #


def _valid_record(lower: str, record: Any) -> bool:
    """Whether a record read from disk is one this store could have written: its key is the skill's lowercase
    name, a safe single path component, and its fields have their types."""
    return (
        isinstance(record, dict) and valid_skill_name(lower) and lower == lower.lower()
        and isinstance(record.get("name"), str) and record["name"].lower() == lower
        and valid_skill_name(record["name"]) and isinstance(record.get("digest"), str)
        and isinstance(record.get("source"), str)
        and isinstance(record.get("overlays", []), list) and all(isinstance(o, str) for o in record.get("overlays", []))
        and isinstance(record.get("keys", []), list) and all(isinstance(k, str) for k in record.get("keys", []))
        and isinstance(record.get("allowed_keys", []), list)
        and all(isinstance(k, str) for k in record.get("allowed_keys", []))
    )


class FetchedSkills(object):
    """The fetched skills of a store: ``<overlay_root>/.dotagents-skills/<lowercase name>/`` holds the
    files the digest covers and ``record.json`` what the person accepted for each (source, pin, digest,
    catalog, the overlays naming it, the front matter keys they allowed, whether the person chose it
    themselves)."""

    def __init__(self, overlay_root: Path):
        self.root = Path(overlay_root) / STORE_DIR

    def tree(self, name: str) -> Path:
        return self.root / name.lower()

    def files(self, name: str) -> "List[str]":
        base = self.tree(name)
        return sorted(p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file())

    def records(self) -> "Dict[str, Dict[str, Any]]":
        try:
            data = json.loads((self.root / RECORD_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        skills = data.get("skills") if isinstance(data, dict) else None
        if not isinstance(skills, dict):
            return {}
        return {k: v for k, v in skills.items() if _valid_record(k, v)}

    def save(self, records: "Mapping[str, Mapping[str, Any]]") -> None:
        if not records:
            if (self.root / RECORD_FILE).exists():
                (self.root / RECORD_FILE).unlink()
            try:
                self.root.rmdir()
            except OSError:
                pass
            return
        write_text_lf(self.root / RECORD_FILE, json.dumps({"skills": records}, indent=1, sort_keys=True) + "\n",
                      atomic=True)

    def intact(self, name: str, record: "Mapping[str, Any]") -> bool:
        """Whether the files on disk are the ones accepted."""
        try:
            return _aos.same_digest(_aos.tree_digest(self.tree(name)), str(record.get("digest", "")))
        except (Error, ValueError, OSError):
            return False

    def write(self, name: str, files: "Mapping[str, bytes]", digest: str) -> None:
        """Puts ``files`` (exactly the digest's) in place whole, checked against ``digest`` as written."""
        if not valid_skill_name(name):
            raise Error(Class.SKILL_NAME, name)
        target = self.tree(name)
        staging = self.root / (".staging-" + name.lower())
        if staging.exists():
            _remove_tree(staging)
        staging.mkdir(parents=True)
        try:
            for path, data in files.items():
                dest = staging.joinpath(*path.split("/"))
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open(str(dest), "xb") as fh:  # exclusive: a name the file system folds fails here
                    fh.write(data)
            if not _aos.same_digest(_aos.tree_digest(staging), digest):
                raise Error(Class.PIN_MISMATCH, name)
            if target.exists():
                _remove_tree(target)
            os.replace(str(staging), str(target))
        except BaseException:
            if staging.exists():
                _remove_tree(staging)
            raise

    def forget(self, name: str) -> None:
        if valid_skill_name(name) and self.tree(name).exists():
            _remove_tree(self.tree(name))


def entry_matches(record: "Optional[Mapping[str, Any]]", fetch: Fetch, catalog: "Optional[str]",
                  configured: "Optional[str]" = None) -> bool:
    """Whether what was accepted is what the plan now asks for: the same source in full, pin and catalog, and the
    pin in the person's configuration, if any."""
    return (
        record is not None
        and record.get("source") == fetch.from_.key()
        and record.get("pin") == fetch.pin
        and record.get("catalog") == catalog
        and (configured is None or _digest_equal(configured, str(record.get("digest", ""))))
    )


def _digest_equal(a: str, b: str) -> bool:
    try:
        return _aos.same_digest(a, b)
    except ValueError:
        return False


def keys_allowed(record: "Mapping[str, Any]") -> bool:
    """Whether every front matter key beyond the inert ones was allowed by the person."""
    return set(record.get("keys", [])) <= set(record.get("allowed_keys", []))


class Delivery(object):
    """The outcome of :func:`deliver`: the skills in place, those not fetched because their entry is new or
    changed and no yes was given, and those refused, each with its reason."""

    def __init__(self) -> None:
        self.kept: "List[str]" = []
        self.fetched: "List[str]" = []
        self.waiting: "List[str]" = []
        self.refused: "List[Tuple[str, str, str]]" = []  # name, overlay, class


def deliver(store: FetchedSkills, plan: SkillPlan, fetcher: "Optional[GitFetcher]", *, accepted: bool,
            logger=None, pins: "Optional[Mapping[str, str]]" = None,
            allow_keys: "Optional[Callable[[str, List[str], List[str]], bool]]" = None) -> Delivery:
    """Puts each skill of ``plan.to_fetch`` in the store and records who names it.

    An entry as accepted before, its files intact, is kept as it is, with no fetch. Any other is fetched only
    with ``accepted`` (the person's yes for it) and checked against its pin, and the pin in the person's
    configuration (``pins``, by lowercase name), first. The files and the front matter keys beyond the inert
    ones are shown, and a skill with such a key is delivered only when ``allow_keys(name, keys, files)`` says the
    person allows it. A refusal of one skill is that skill's: the others still go in."""
    out = Delivery()
    pins = pins or {}
    records = store.records()
    catalog_of = {(s.name, s.overlay): s.catalog for s in plan.skills}
    namers: "Dict[str, set]" = {}

    def refuse(fetch: Fetch, why: str, detail: Any = "") -> None:
        out.refused.append((fetch.name, fetch.overlay, why))
        namers.pop(fetch.name.lower(), None)
        if logger is not None:
            logger.error("skill %s (named by overlay %s) is not delivered: %s%s", fetch.name, fetch.overlay, why,
                         " (%s)" % detail if detail else "")

    def permitted(fetch: Fetch, keys: "List[str]", files: "List[str]") -> bool:
        if logger is not None:
            logger.info("skill %s: %d file(s): %s", fetch.name, len(files), ", ".join(files))
            if keys:
                logger.warning("skill %s: front matter keys beyond the inert ones: %s (a harness may take such a "
                               "key as a grant, as `allowed-tools` pre-approves tools)", fetch.name, ", ".join(keys))
        return not keys or bool(allow_keys is not None and allow_keys(fetch.name, keys, files))

    for fetch in plan.to_fetch:
        lower = fetch.name.lower()
        catalog = catalog_of.get((fetch.name, fetch.overlay))
        configured = pins.get(lower)
        record = records.get(lower)
        # Every overlay naming this skill alike is one of its namers, not only the one whose entry is fetched.
        namers[lower] = {
            _aos.normalize_name(s.overlay) for s in plan.skills
            if s.name.lower() == lower and s.source == fetch.from_.shown() and s.pin == fetch.pin
            and s.origin in (PlannedOrigin.SOURCE, PlannedOrigin.CATALOG)
        } | {_aos.normalize_name(fetch.overlay)}
        if configured and fetch.pin and not _digest_equal(configured, fetch.pin):
            refuse(fetch, "pin-mismatch", "the entry's pin is not the pin in your configuration")
            continue
        if entry_matches(record, fetch, catalog, configured) and store.intact(lower, record):  # type: ignore[arg-type]
            if keys_allowed(record):  # type: ignore[arg-type]
                out.kept.append(fetch.name)
            elif permitted(fetch, record["keys"], store.files(lower)):  # type: ignore[index]
                record["allowed_keys"] = list(record["keys"])  # type: ignore[index]
                out.kept.append(fetch.name)
            else:
                refuse(fetch, "skill-keys", ", ".join(record["keys"]))  # type: ignore[index]
            continue
        if not accepted or fetcher is None:
            out.waiting.append(fetch.name)
            namers.pop(lower)
            continue
        try:
            files = fetcher.fetch(fetch.from_)
            pin = fetch.pin or configured
            checked: FetchedSkill = coqui_aos.read_fetched_skill(fetch.name, files, pin, SKILL_LIMITS)
            if configured and not _digest_equal(configured, str(checked.digest)):
                raise Error(Class.PIN_MISMATCH, fetch.name)
            by_path = dict(files)
            keys = [k for k in checked.reported if k not in INERT_KEYS]
            if not permitted(fetch, keys, list(checked.files)):
                refuse(fetch, "skill-keys", ", ".join(keys))
                continue
            store.write(fetch.name, {path: by_path[path] for path in checked.files}, str(checked.digest))
        except Error as exc:
            refuse(fetch, str(exc.class_.value), exc)
            continue
        except OSError as exc:
            refuse(fetch, "io", type(exc).__name__)
            continue
        records[lower] = {
            "name": fetch.name, "source": fetch.from_.key(), "pin": fetch.pin, "catalog": catalog,
            "digest": str(checked.digest), "keys": keys, "allowed_keys": list(keys),
            "chosen": bool(record and record.get("chosen")), "overlays": [],
        }
        out.fetched.append(fetch.name)
    for lower, named in namers.items():
        if lower in records:
            records[lower]["overlays"] = sorted(named)
    try:
        store.save(records)
    except OSError as exc:
        if logger is not None:
            logger.error("the record of the fetched skills cannot be saved: %s", type(exc).__name__)
        out.refused.append(("(record)", "", "io"))
    return out


def orphans(store: FetchedSkills, plan: SkillPlan) -> "List[str]":
    """The recorded skills no overlay in ``plan`` names any more that the person did not choose: what
    removing or syncing an overlay leaves, to be offered for removal. A name still listed but not resolved
    now (a catalog not given this time) is still named."""
    named = {s.name.lower() for s in plan.skills if s.origin is not PlannedOrigin.BUNDLED}
    return sorted(
        record.get("name", lower) for lower, record in store.records().items()
        if lower not in named and not record.get("chosen")
    )


def forget(store: FetchedSkills, shared: Path, names: "Iterable[str]", logger=None) -> int:
    """Removes ``names`` from the store and unpublishes them. Returns how many."""
    records = store.records()
    owned = []
    for name in names:
        lower = name.lower()
        record = records.get(lower)
        if record is None:
            continue
        target = shared / record["name"]
        result = _skills.unsync_path(target, store.tree(lower))
        if not result and logger is not None:
            logger.warning("kept skill %s: not this store's (edited or placed by hand)", name)
        store.forget(lower)
        del records[lower]
        owned.append(record["name"])
    store.save(records)
    if shared.is_dir():
        pub = _skills._load_record(shared, PUBLISHED_FETCHED_RECORD)
        for name in owned:
            pub.pop(name, None)
        _skills._save_record(shared, pub, PUBLISHED_FETCHED_RECORD)
        _skills.clean_broken_syncs(shared, logger=logger)
        _skills._prune_empty(shared)
    return len(owned)


def publish(store: FetchedSkills, shared: Path, plan: Optional[SkillPlan] = None, *, copy: bool = False,
            logger=None) -> int:
    """Publishes the recorded skills the plan delivers into ``shared`` as a bundled one is: a link where it can,
    a copy otherwise. Only a plan's winner is published: a name another overlay's bundled skill holds stays that
    one's (and a copy of ours there is taken down), as does a skill whose keys the person did not allow. A copy
    of an earlier version is replaced while it is what was published; one edited or placed by hand is kept."""
    delivered = {name.lower(): holder for name, holder in plan.delivered.items()} if plan is not None else None
    bundled_by = {}
    if plan is not None:
        bundled_by = {(s.name.lower(), s.overlay) for s in plan.skills if s.origin is PlannedOrigin.BUNDLED}
    published = 0
    for lower, record in sorted(store.records().items()):
        name = record["name"]
        target = shared / name
        holder = delivered.get(lower) if delivered is not None else None
        wins = (
            store.tree(lower).is_dir() and keys_allowed(record)
            and (delivered is None or (holder is None and record.get("chosen"))
                 or (holder is not None and _aos.normalize_name(holder) in record.get("overlays", [])))
        )
        if not wins:
            result = _skills.unsync_path(target, store.tree(lower)) if os.path.lexists(str(target)) else None
            if result is not None and result and result.mode != "none":
                pub = _skills._load_record(shared, PUBLISHED_FETCHED_RECORD)
                pub.pop(name, None)
                _skills._save_record(shared, pub, PUBLISHED_FETCHED_RECORD)
                if logger is not None:
                    logger.info("skill %s: taken down, %s", name, "its keys are not allowed" if holder is None
                                else "another overlay's skill holds the name")
            if holder is not None and (lower, holder) in bundled_by:
                # The name belongs to a bundled skill: it takes the place.
                source = store.root.parent / holder / "skills" / name
                if source.is_dir():
                    again = _skills.sync_path(source, target, prefer_symlink=not copy, force=False)
                    if again:
                        _skills._note_published(shared, name, again)
            continue
        shared.mkdir(parents=True, exist_ok=True)
        result = _skills.resync_path(
            store.tree(lower), target, prefer_symlink=not copy,
            published_digest=_skills._load_record(shared, PUBLISHED_FETCHED_RECORD).get(name),
        )
        if result:
            if "current" in result.message or "already" in result.message or "up to date" in result.message:
                continue
            _skills._note_published(shared, name, result, PUBLISHED_FETCHED_RECORD)
            published += 1
            if logger is not None:
                logger.info("skill %s: %s", name, result.message)
        elif logger is not None:
            logger.warning("kept skill %s: %s", name, result.message)
    return published
