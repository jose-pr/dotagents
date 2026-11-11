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
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from dotagents import _aos, _skills
from dotagents._fs import write_text_lf
from dotagents._vendor import coqui_aos
from dotagents._vendor.coqui_aos import (
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
#: The URL schemes a skill's Git source may use; a test adds ``file``.
GIT_PROTOCOLS = "http:https:ssh:git"
#: What the person's own environment may still pass to a fetch: where programs are, and a proxy.
_KEPT_ENV = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "LANG",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
)
_NONE = "none"


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


# --------------------------------------------------------------------------- #
# The plan.
# --------------------------------------------------------------------------- #


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
                from dotagents._sources import _read_url

                data = _read_url(location).encode("utf-8")
            else:
                data = Path(location).read_bytes()
            catalog = coqui_aos.read_catalog(data, os.path.basename(location.split("?", 1)[0]), location)
        except (OSError, Error, SystemExit) as exc:
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


def elsewhere(plan: SkillPlan) -> "List[PlannedSkill]":
    """The planned skills that do not come with their overlay."""
    return [skill for skill in plan.skills if skill.origin is not PlannedOrigin.BUNDLED]


def _pin_text(pin: "Optional[str]") -> str:
    return "pinned %s" % pin if pin else "UNPINNED"


def review_lines(plan: SkillPlan) -> "List[str]":
    """What a person is shown before the yes: each skill from elsewhere with the overlay naming it and its
    source, and what cannot be resolved, which is never guessed."""
    lines = []
    for skill in elsewhere(plan):
        head = "skill %s (named by overlay %s): " % (skill.name, skill.overlay)
        if skill.origin is PlannedOrigin.MISSING:
            lines.append(head + "MISSING, no catalog offers it; not installed")
        elif skill.origin is PlannedOrigin.AMBIGUOUS:
            lines.append(head + "AMBIGUOUS, catalogs %s offer it; not installed" % ", ".join(skill.catalogs or []))
        else:
            via = " (catalog %s)" % skill.catalog if skill.catalog else ""
            lines.append(head + "fetched from %s%s, %s" % (skill.source, via, _pin_text(skill.pin)))
    for ident, name, overlay in plan.non_defaults:
        lines.append("skill %s (named by overlay %s): %s" % (name, overlay, ident))
    for name, overlay in plan.shadowing:
        lines.append("skill %s (named by overlay %s): not taken, another overlay's skill keeps the name" % (name, overlay))
    return lines


# --------------------------------------------------------------------------- #
# Fetching.
# --------------------------------------------------------------------------- #


class GitFetcher(object):
    """Reads a skill's directory out of a Git source (``coqui_aos.SkillSource``).

    ``extra_config`` are ``-c`` settings a test adds (a ``url.<base>.insteadOf`` over a made-up host) and
    ``protocols`` the schemes Git may use; neither reaches a manifest."""

    def __init__(self, scratch: Path, *, git: str = "git", protocols: str = GIT_PROTOCOLS,
                 extra_config: "Sequence[str]" = (), ssl_revoke: "Optional[str]" = None, timeout: int = 300,
                 settings: "Optional[Settings]" = None):
        self.scratch = Path(scratch)
        self.git = git
        self.protocols = protocols
        self.extra_config = list(extra_config)
        self.ssl_revoke = ssl_revoke
        self.timeout = timeout
        self.settings = settings or Settings()

    def environment(self) -> "Dict[str, str]":
        """The environment of every Git command: the person's own credentials, agent, askpass and Git
        configuration are not in it."""
        env = {key: value for key, value in os.environ.items() if key in _KEPT_ENV or key.upper() in _KEPT_ENV}
        null = os.devnull
        env.update({
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": null,
            "GIT_ALLOW_PROTOCOL": self.protocols,
            "GCM_INTERACTIVE": "never",
            "GIT_SSH_COMMAND": "ssh -F %s -o BatchMode=yes -o IdentityAgent=none -o IdentitiesOnly=yes "
                               "-o IdentityFile=%s" % (null, null),
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

    def _run(self, args: "Sequence[str]", cwd: Path, *, data: "Optional[bytes]" = None) -> "subprocess.CompletedProcess":
        try:
            proc = subprocess.run(
                self.arguments(args), cwd=str(cwd), input=data, capture_output=True, env=self.environment(),
                timeout=self.timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise Error(Class.IO, "git: %s" % type(exc).__name__) from None
        return proc

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
        got = self._run(["fetch", "--quiet", "--no-tags", "--depth=1", "--", source.location, ref], work)
        tip = "FETCH_HEAD"
        if got.returncode != 0:
            # A commit the server will not give by itself: everything it has, then the commit.
            got = self._run(["fetch", "--quiet", "--tags", "--", source.location, "+refs/heads/*:refs/remotes/o/*"], work)
            if got.returncode != 0:
                raise Error(Class.IO, "the source cannot be fetched")
            probe = self._run(["rev-parse", "--verify", "--quiet", ref + "^{commit}"], work)
            if probe.returncode != 0:
                probe = self._run(["rev-parse", "--verify", "--quiet", "o/" + ref + "^{commit}"], work)
            if probe.returncode != 0:
                raise Error(Class.NOT_FOUND, "the ref")
            tip = probe.stdout.decode("ascii", "replace").strip()
        treeish = tip + (":" + source.path if source.path else "")
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
            if mode == "120000":
                raise Error(Class.SYMLINK, "a link in the skill")
            if kind != "blob":
                raise Error(Class.SPECIAL_FILE, "a submodule in the skill")
            try:
                name = path.decode("utf-8")
            except UnicodeDecodeError:
                raise Error(Class.NON_UTF8_NAME) from None
            if not _aos.valid_relpath(name):
                raise Error(Class.INVALID_NAME, "a path in the skill")
            size_n = int(size.strip())
            total += size_n
            if not self.settings.allows("tree_files", len(out) + 1):
                raise Error(Class.TOO_LARGE, "too many files")
            if not self.settings.allows("file_bytes", size_n) or not self.settings.allows("tree_bytes", total):
                raise Error(Class.TOO_LARGE, "more bytes than the limit")
            out.append((name, sha, size_n))
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


class FetchedSkills(object):
    """The fetched skills of a store: ``<overlay_root>/.dotagents-skills/<lowercase name>/`` holds the
    files the digest covers and ``record.json`` what the person accepted for each (source, pin, digest,
    catalog, the overlays naming it, whether the person chose it themselves)."""

    def __init__(self, overlay_root: Path):
        self.root = Path(overlay_root) / STORE_DIR

    def tree(self, name: str) -> Path:
        return self.root / name.lower()

    def records(self) -> "Dict[str, Dict[str, Any]]":
        try:
            data = json.loads((self.root / RECORD_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        skills = data.get("skills") if isinstance(data, dict) else None
        return {k: v for k, v in skills.items() if isinstance(v, dict)} if isinstance(skills, dict) else {}

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
        if self.tree(name).exists():
            _remove_tree(self.tree(name))


def entry_matches(record: "Optional[Mapping[str, Any]]", fetch: Fetch, catalog: "Optional[str]") -> bool:
    """Whether what was accepted is what the plan now asks for: the same source in full, pin and catalog."""
    return (
        record is not None
        and record.get("source") == fetch.from_.key()
        and record.get("pin") == fetch.pin
        and record.get("catalog") == catalog
    )


class Delivery(object):
    """The outcome of :func:`deliver`: the skills in place, those not fetched because their entry is new or
    changed and no yes was given, and those refused, each with its reason."""

    def __init__(self) -> None:
        self.kept: "List[str]" = []
        self.fetched: "List[str]" = []
        self.waiting: "List[str]" = []
        self.refused: "List[Tuple[str, str, str]]" = []  # name, overlay, class


def deliver(store: FetchedSkills, plan: SkillPlan, fetcher: "Optional[GitFetcher]", *, accepted: bool,
            logger=None) -> Delivery:
    """Puts each skill of ``plan.to_fetch`` in the store and records who names it.

    An entry as accepted before, its files intact, is kept as it is, with no fetch. Any other is fetched only
    with ``accepted`` (the person's yes for it) and checked against its pin first. A refusal of one skill is
    that skill's: the others still go in."""
    out = Delivery()
    records = store.records()
    catalog_of = {(s.name, s.overlay): s.catalog for s in plan.skills}
    namers: "Dict[str, set]" = {}
    for fetch in plan.to_fetch:
        lower = fetch.name.lower()
        catalog = catalog_of.get((fetch.name, fetch.overlay))
        record = records.get(lower)
        # Every overlay naming this skill alike is one of its namers, not only the one whose entry is fetched.
        namers[lower] = {
            _aos.normalize_name(s.overlay) for s in plan.skills
            if s.name.lower() == lower and s.source == fetch.from_.shown() and s.pin == fetch.pin
            and s.origin in (PlannedOrigin.SOURCE, PlannedOrigin.CATALOG)
        } | {_aos.normalize_name(fetch.overlay)}
        if entry_matches(record, fetch, catalog) and store.intact(lower, record):  # type: ignore[arg-type]
            out.kept.append(fetch.name)
            continue
        if not accepted or fetcher is None:
            out.waiting.append(fetch.name)
            namers.pop(lower)
            continue
        try:
            files = fetcher.fetch(fetch.from_)
            checked: FetchedSkill = coqui_aos.read_fetched_skill(fetch.name, files, fetch.pin, Settings())
            by_path = dict(files)
            store.write(fetch.name, {path: by_path[path] for path in checked.files}, str(checked.digest))
        except Error as exc:
            out.refused.append((fetch.name, fetch.overlay, str(exc.class_.value)))
            namers.pop(lower)
            if logger is not None:
                logger.error("skill %s (named by overlay %s) is not delivered: %s", fetch.name, fetch.overlay, exc)
            continue
        records[lower] = {
            "name": fetch.name, "source": fetch.from_.key(), "pin": fetch.pin, "catalog": catalog,
            "digest": str(checked.digest), "keys": list(checked.reported),
            "chosen": bool(record and record.get("chosen")), "overlays": [],
        }
        out.fetched.append(fetch.name)
    for lower, named in namers.items():
        if lower in records:
            records[lower]["overlays"] = sorted(named)
    store.save(records)
    return out


def orphans(store: FetchedSkills, plan: SkillPlan) -> "List[str]":
    """The recorded skills no overlay in ``plan`` names any more that the person did not choose: what
    removing or syncing an overlay leaves, to be offered for removal."""
    named = {s.name.lower() for s in plan.skills if s.origin in (PlannedOrigin.SOURCE, PlannedOrigin.CATALOG)}
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
        target = shared / record.get("name", name)
        result = _skills.unsync_path(target, store.tree(lower))
        if not result and logger is not None:
            logger.warning("kept skill %s: not this store's (edited or placed by hand)", name)
        store.forget(lower)
        del records[lower]
        owned.append(name)
    store.save(records)
    if shared.is_dir():
        pub = _skills._load_record(shared)
        for name in owned:
            pub.pop(name, None)
        _skills._save_record(shared, pub)
        _skills.clean_broken_syncs(shared, logger=logger)
        _skills._prune_empty(shared)
    return len(owned)


def publish(store: FetchedSkills, shared: Path, *, copy: bool = False, logger=None) -> int:
    """Publishes every recorded skill into ``shared`` as a bundled one is: a link where it can, a copy
    otherwise. A same-named skill that is not this one (placed by hand) is kept and reported."""
    published = 0
    for lower, record in sorted(store.records().items()):
        if not store.tree(lower).is_dir():
            continue
        shared.mkdir(parents=True, exist_ok=True)
        name = record.get("name", lower)
        # A copy of an earlier version is replaced while it is what was published; one edited or placed by
        # hand is kept.
        result = _skills.resync_path(
            store.tree(lower), shared / name, prefer_symlink=not copy,
            published_digest=_skills._load_record(shared).get(name),
        )
        if result:
            if "current" in result.message or "already" in result.message or "up to date" in result.message:
                continue
            _skills._note_published(shared, name, result)
            published += 1
            if logger is not None:
                logger.info("skill %s: %s", name, result.message)
        elif logger is not None:
            logger.warning("kept skill %s: %s", name, result.message)
    return published
