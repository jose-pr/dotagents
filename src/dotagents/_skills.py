"""Publish an overlay's skills into a scope's shared ``skills/`` dir, so every
agent that reads that dir sees the same skills (the "skills synced between agents"
behavior).

An overlay may ship ``skills/<skill-name>/`` directories. Publishing symlinks (or,
where symlinks aren't available, copies) each into ``<scope>/skills/<skill-name>/``.
Removing an overlay unpublishes only the skills **it** published (matched against
its own ``skills/`` source), never a skill another overlay or the user placed there,
then sweeps any now-broken symlinks.

Pure stdlib (``os``/``shutil``) -- no ``pathlib_next`` -- so it works in a plain
``pip install`` and inside the ``.pyz``. Symlink-preferred with a copy fallback is
the contract (on Windows a directory junction is tried between the two: it
needs no privilege and, like a symlink, is always current); ``--copy`` forces
the copy path up front (Windows / no-symlink).

:func:`link_skills_into` is the second hop: a store's ``skills/`` linked per
skill into an agent's OWN skills dir (Claude's ``<config>/skills/``), with an
ownership record there so a copy can be refreshed and a skill the store no
longer has can be removed -- never a same-named skill the user placed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from pathlib import Path

#: Beside the published skills: the tree digest of each COPY at the moment it
#: was published, so a later sync can tell "unchanged since we copied it"
#: (refresh silently) from "edited here" (keep, unless ``--overwrite``).
PUBLISHED_RECORD = ".dotagents-published.json"

#: Beside the skills linked into an agent's own skills dir: which entries
#: dotagents put there -- ``"link"`` for a symlink/junction, else the tree
#: digest of the copy as it was made. Only recorded entries (and links into
#: the store's ``skills/``) are ever refreshed or removed.
LINKED_RECORD = ".dotagents-linked.json"
_LINK = "link"


def _is_junction(path: "str | os.PathLike[str]") -> bool:
    """True for a Windows directory junction (a mount-point reparse point),
    which ``os.path.islink`` does not report before Python 3.12."""
    try:
        st = os.lstat(str(path))
    except OSError:
        return False
    tag = getattr(st, "st_reparse_tag", 0)
    return bool(tag) and tag == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -1)


def _is_link(path: "str | os.PathLike[str]") -> bool:
    """A symlink or a junction: an entry that points somewhere, never a copy."""
    return os.path.islink(str(path)) or _is_junction(path)


def _create_junction(source: Path, target: Path) -> None:
    """Seam for tests. Windows only: a directory junction needs no privilege
    (unlike a symlink without Developer Mode)."""
    import _winapi  # type: ignore[import-not-found]

    _winapi.CreateJunction(str(source.resolve()), str(target))


class SyncResult:
    __slots__ = ("success", "mode", "message")

    def __init__(self, success: bool, mode: str, message: str = ""):
        self.success = success
        self.mode = mode
        self.message = message

    def __bool__(self) -> bool:
        return self.success


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _paths_match(source: Path, target: Path) -> bool:
    """True if ``target`` already mirrors ``source``: the same file set AND the
    same bytes in each file.

    This is what ``unsync_path`` uses to decide "this copy is ours, remove it"
    from "the user edited it, leave it alone", and what ``resync_path`` uses to
    detect drift -- so it has to see content, not just names. Skill dirs are
    small; hashing them is cheap."""
    if not source.exists() or not target.exists():
        return False
    if source.is_file() and target.is_file():
        return _digest(source) == _digest(target)
    if source.is_dir() and target.is_dir():
        src_files = {str(p.relative_to(source)): p for p in source.rglob("*") if p.is_file()}
        tgt_files = {str(p.relative_to(target)): p for p in target.rglob("*") if p.is_file()}
        if set(src_files) != set(tgt_files):
            return False
        try:
            return all(_digest(src_files[k]) == _digest(tgt_files[k]) for k in src_files)
        except OSError:
            return False
    return False


def _tree_digest(path: Path) -> str:
    """One digest over a file or a directory's (relative path, bytes) pairs."""
    if path.is_file():
        return _digest(path)
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(path).as_posix().encode("utf-8") + b"\0")
            h.update(_digest(p).encode("ascii") + b"\n")
    return h.hexdigest()


def _load_record(shared_skills: Path, name: str = PUBLISHED_RECORD) -> "dict[str, str]":
    try:
        data = json.loads((shared_skills / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def _save_record(shared_skills: Path, record: "dict[str, str]", name: str = PUBLISHED_RECORD) -> None:
    from dotagents._fs import write_text_lf

    path = shared_skills / name
    if not record:
        if path.exists():
            path.unlink()
        return
    write_text_lf(path, json.dumps(record, indent=1, sort_keys=True) + "\n")


def _note_published(shared_skills: Path, name: str, result: "SyncResult") -> None:
    """Record a copy's digest (a symlink needs none: it is always current)."""
    record = _load_record(shared_skills)
    target = shared_skills / name
    if result.mode == "copy" and target.is_dir() and not _is_link(target):
        record[name] = _tree_digest(target)
    else:
        record.pop(name, None)
    _save_record(shared_skills, record)


def _resolves_to(target: Path, source: Path) -> bool:
    try:
        return Path(os.path.realpath(str(target))) == source.resolve()
    except OSError:
        return False


def sync_path(
    source: Path, target: Path, *, prefer_symlink: bool = True, force: bool = False
) -> SyncResult:
    """Publish ``source`` at ``target`` -- symlink if possible, else copy.

    An existing correct symlink / matching copy is a no-op success. A conflicting
    target is only replaced with ``force=True``; otherwise it is reported as a
    conflict so the caller can decide (the publish path retries once with force)."""
    if not source.exists():
        return SyncResult(False, "error", "source does not exist: %s" % source)

    if os.path.lexists(str(target)):
        if not force:
            if _is_link(target):
                if _resolves_to(target, source):
                    return SyncResult(True, "symlink", "already linked")
                return SyncResult(False, "symlink", "target symlink points elsewhere")
            if _paths_match(source, target):
                return SyncResult(True, "copy", "already synced")
            return SyncResult(False, "conflict", "target exists with different content")
        _remove(target)

    target.parent.mkdir(parents=True, exist_ok=True)
    if prefer_symlink:
        try:
            os.symlink(str(source), str(target), target_is_directory=source.is_dir())
            return SyncResult(True, "symlink", "linked %s" % target.name)
        except (OSError, NotImplementedError):
            pass  # a junction, else a copy
        if os.name == "nt" and source.is_dir():
            try:
                _create_junction(source, target)
                return SyncResult(True, "junction", "linked %s (junction)" % target.name)
            except (OSError, ImportError, AttributeError):
                pass  # fall through to copy

    try:
        if source.is_dir():
            shutil.copytree(str(source), str(target))
            return SyncResult(True, "copy", "copied %s" % target.name)
        shutil.copy2(str(source), str(target))
        return SyncResult(True, "copy", "copied %s" % target.name)
    except (OSError, shutil.Error) as exc:
        return SyncResult(False, "copy", "copy failed: %s" % exc)


def unsync_path(target: Path, source: Path) -> SyncResult:
    """Unpublish ``target`` -- but only if it is this overlay's (a symlink to
    ``source``, or a copy matching it). A symlink pointing elsewhere, or a copy whose
    content differs, is left untouched (it is someone else's)."""
    if not os.path.lexists(str(target)):
        return SyncResult(True, "none", "does not exist")
    if _is_link(target):
        if not _resolves_to(target, source):
            return SyncResult(False, "symlink", "symlink points elsewhere")
        _remove(target)
        return SyncResult(True, "symlink", "removed symlink")
    if not _paths_match(source, target):
        return SyncResult(False, "copy", "content differs from source")
    try:
        _remove(target)
        return SyncResult(True, "copy", "removed copy")
    except OSError as exc:
        return SyncResult(False, "copy", "removal failed: %s" % exc)


def resync_path(
    source: Path,
    target: Path,
    *,
    prefer_symlink: bool = True,
    published_digest: "str | None" = None,
    overwrite: bool = False,
) -> SyncResult:
    """Refresh a published skill from its overlay source. A symlink is inherently
    current; an unpublished skill is published (symlink-preferred unless
    ``prefer_symlink`` is False). A copy that differs from the source is
    re-copied only when it is still exactly what was published
    (``published_digest``) or ``overwrite`` is set -- otherwise it was edited
    in place (or placed by hand) and is kept, reported as a conflict."""
    if not source.exists():
        return SyncResult(False, "error", "source does not exist")
    if not os.path.lexists(str(target)):
        return sync_path(source, target, prefer_symlink=prefer_symlink)
    if _is_link(target):
        return SyncResult(True, "symlink", "symlink is current")
    if _paths_match(source, target):
        return SyncResult(True, "copy", "current")
    if not overwrite and (published_digest is None or _tree_digest(target) != published_digest):
        return SyncResult(
            False, "conflict",
            "modified here since it was published; kept (sync --overwrite replaces it)",
        )
    return sync_path(source, target, prefer_symlink=False, force=True)


def _remove(path: Path) -> None:
    """Delete ``path``: a link (never what it points at), a file, or a tree."""
    if _is_junction(path):
        os.rmdir(str(path))  # removes the junction itself, not its target
    elif os.path.islink(str(path)) or path.is_file():
        os.unlink(str(path))
    elif path.is_dir():
        shutil.rmtree(str(path))


def _prune_empty(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def clean_broken_syncs(shared_skills: Path, logger=None) -> None:
    """Drop symlinks under ``shared_skills`` whose target no longer exists (an
    overlay's ``skills/`` went away), then remove the dir if it emptied."""
    if not shared_skills.is_dir():
        return
    for entry in shared_skills.iterdir():
        if entry.name.startswith("."):
            continue
        if _is_link(entry) and not entry.exists():
            _remove(entry)
            if logger is not None:
                logger.info("removed broken skill sync: %s", entry.name)
    _prune_empty(shared_skills)


def _overlay_skill_dirs(overlay_dir: Path) -> "list[Path]":
    skills_dir = overlay_dir / "skills"
    if not skills_dir.is_dir():
        return []
    return sorted(d for d in skills_dir.iterdir() if d.is_dir())


def publish_overlay_skills(
    overlay_dir: Path, shared_skills: Path, *, copy: bool = False, logger=None
) -> int:
    """Publish each ``overlay_dir/skills/<name>/`` into ``shared_skills``.

    Symlink-preferred unless ``copy`` forces copies. A target that already
    holds something else (a skill the user placed or edited) is kept and
    reported, never replaced. Returns the count published. Sweeps broken syncs
    first so a stale symlink can't block a re-publish."""
    clean_broken_syncs(shared_skills, logger=logger)
    skill_dirs = _overlay_skill_dirs(overlay_dir)
    if not skill_dirs:
        return 0
    shared_skills.mkdir(parents=True, exist_ok=True)
    published = 0
    for skill_dir in skill_dirs:
        target = shared_skills / skill_dir.name
        result = sync_path(skill_dir, target, prefer_symlink=not copy, force=False)
        if result:
            _note_published(shared_skills, skill_dir.name, result)
            published += 1
            if logger is not None and "already" not in result.message:
                logger.info("skill %s: %s", skill_dir.name, result.message)
        elif logger is not None:
            logger.warning("failed to publish skill %s: %s", skill_dir.name, result.message)
    return published


def resync_overlay_skills(
    overlay_dir: Path,
    shared_skills: Path,
    *,
    copy: bool = False,
    overwrite: bool = False,
    logger=None,
) -> int:
    """Refresh already-published skills of this overlay (copy-mode drift). New
    skills are published (as copies with ``copy``, the ``sync --copy`` form);
    symlinks are inherently current. A copy edited since it was published is
    kept unless ``overwrite`` (``sync --overwrite``)."""
    skill_dirs = _overlay_skill_dirs(overlay_dir)
    if not skill_dirs or not shared_skills.is_dir():
        # Nothing published yet -> fall back to a fresh publish.
        return publish_overlay_skills(overlay_dir, shared_skills, copy=copy, logger=logger)
    record = _load_record(shared_skills)
    updated = 0
    for skill_dir in skill_dirs:
        target = shared_skills / skill_dir.name
        result = resync_path(
            skill_dir, target, prefer_symlink=not copy,
            published_digest=record.get(skill_dir.name), overwrite=overwrite,
        )
        if not result:
            if logger is not None:
                logger.warning("kept skill %s: %s", skill_dir.name, result.message)
            continue
        if "current" in result.message:
            continue
        _note_published(shared_skills, skill_dir.name, result)
        updated += 1
        if logger is not None:
            logger.info("skill %s: %s", skill_dir.name, result.message)
    return updated


def owned_overlay_skills(overlay_dir: Path, shared_skills: Path, *, logger=None) -> "list[str]":
    """The names under ``shared_skills`` that are THIS overlay's publications
    (a symlink to its skill, or a copy matching it) -- decided while the
    overlay still exists, so ``overlays remove`` can delete the overlay first
    and unpublish only once that succeeded (:func:`unpublish_skills`). A
    same-named skill that is someone else's is reported and left out."""
    owned: "list[str]" = []
    if not shared_skills.is_dir():
        return owned
    for skill_dir in _overlay_skill_dirs(overlay_dir):
        target = shared_skills / skill_dir.name
        if not os.path.lexists(str(target)):
            continue
        if _is_link(target):
            mine = _resolves_to(target, skill_dir)
        else:
            mine = _paths_match(skill_dir, target)
        if mine:
            owned.append(skill_dir.name)
        elif logger is not None:
            logger.warning("kept skill %s: not this overlay's (edited or placed by hand)", skill_dir.name)
    return owned


def unpublish_skills(shared_skills: Path, names: "list[str]", *, logger=None) -> int:
    """Remove ``names`` from ``shared_skills`` (see :func:`owned_overlay_skills`),
    drop their published-copy records, then sweep broken syncs. Returns the
    count removed."""
    removed = 0
    record = _load_record(shared_skills)
    for name in names:
        target = shared_skills / name
        if not os.path.lexists(str(target)):
            continue
        try:
            _remove(target)
        except OSError as exc:
            if logger is not None:
                logger.warning("could not unpublish skill %s: %s", name, exc)
            continue
        record.pop(name, None)
        removed += 1
        if logger is not None:
            logger.info("removed skill: %s", name)
    if shared_skills.is_dir():
        _save_record(shared_skills, record)
    clean_broken_syncs(shared_skills, logger=logger)
    _prune_empty(shared_skills)
    return removed


def remove_overlay_skills(overlay_dir: Path, shared_skills: Path, *, logger=None) -> int:
    """Unpublish only the skills *this* overlay published (matched to its source),
    then sweep broken syncs. Returns the count removed."""
    if not shared_skills.is_dir():
        return 0
    removed = 0
    for skill_dir in _overlay_skill_dirs(overlay_dir):
        target = shared_skills / skill_dir.name
        result = unsync_path(target, skill_dir)
        if result and result.mode != "none":
            record = _load_record(shared_skills)
            if record.pop(skill_dir.name, None) is not None:
                _save_record(shared_skills, record)
            removed += 1
            if logger is not None:
                logger.info("removed skill: %s (%s)", skill_dir.name, result.mode)
        elif not result and logger is not None:
            logger.warning("kept skill %s: %s", skill_dir.name, result.message)
    clean_broken_syncs(shared_skills, logger=logger)
    _prune_empty(shared_skills)
    return removed


# --------------------------------------------------------------------------- #
# Second hop: a store's skills into an agent's own skills dir.
# --------------------------------------------------------------------------- #


class LinkResult:
    """What :func:`link_skills_into` did (or, in a dry run, would do), by
    skill name: ``linked`` (a symlink/junction made or re-pointed),
    ``copied`` (a copy made or refreshed), ``removed`` and ``kept`` (left
    alone: someone else's, or edited since it was copied). ``owned`` is every
    name dotagents holds in the target afterwards."""

    __slots__ = ("linked", "copied", "removed", "kept", "owned")

    def __init__(self) -> None:
        self.linked: "list[str]" = []
        self.copied: "list[str]" = []
        self.removed: "list[str]" = []
        self.kept: "list[str]" = []
        self.owned: "list[str]" = []


def _store_key(shared_skills: Path) -> str:
    """The linked-record key of one store's ``skills/``: a digest of its real
    path, so the record names no machine path and two stores linking into one
    agent dir never prune each other's entries."""
    try:
        where = os.path.realpath(str(shared_skills))
    except (OSError, ValueError):
        where = os.path.abspath(str(shared_skills))
    return hashlib.sha256(os.path.normcase(where).encode("utf-8")).hexdigest()[:16]


def _load_linked(target_dir: Path) -> "dict[str, dict[str, str]]":
    try:
        data = json.loads((target_dir / LINKED_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: {n: v for n, v in names.items() if isinstance(n, str) and isinstance(v, str)}
        for key, names in data.items()
        if isinstance(key, str) and isinstance(names, dict)
    }


def _save_linked(target_dir: Path, data: "dict[str, dict[str, str]]") -> None:
    from dotagents._fs import write_text_lf

    data = {key: names for key, names in data.items() if names}
    path = target_dir / LINKED_RECORD
    if not data:
        if path.exists():
            path.unlink()
        return
    write_text_lf(path, json.dumps(data, indent=1, sort_keys=True) + "\n", atomic=True)


def linked_names(shared_skills: Path, target_dir: Path) -> "list[str]":
    """The names the linked record says ``shared_skills`` owns in ``target_dir``."""
    return sorted(_load_linked(target_dir).get(_store_key(shared_skills), {}))


def _dir_keys(directory: Path) -> "set[str]":
    keys = set()
    for fn in (os.path.abspath, os.path.realpath):
        try:
            keys.add(os.path.normcase(fn(str(directory))))
        except (OSError, ValueError):
            pass
    return keys


def _points_into(link: Path, directory: Path) -> bool:
    """True if ``link`` (a symlink or junction, dangling or not) names an entry
    directly inside ``directory`` -- how a link dotagents made before the
    record existed is still recognized as its own."""
    try:
        raw = os.readlink(str(link))
    except (OSError, ValueError, NotImplementedError):
        return False
    if raw.startswith("\\\\?\\"):
        raw = raw[4:]
    target = Path(raw)
    if not target.is_absolute():
        target = link.parent / target
    return bool(_dir_keys(target.parent) & _dir_keys(directory))


def link_skills_into(
    shared_skills: Path, target_dir: Path, *, dry_run: bool = False, logger=None
) -> LinkResult:
    """Link each ``shared_skills/<name>/`` into ``target_dir/<name>`` (an
    agent's own skills dir), per skill, and drop the entries a skill the
    store no longer has left behind.

    A symlink where the OS allows one, else (Windows) a junction, else a
    copy. Ownership is recorded in ``target_dir/.dotagents-linked.json``
    (per store): a link is dotagents' when the record says so or it points
    into ``shared_skills``; a copy only while it is byte-for-byte what was
    copied (its recorded digest). An owned copy that went stale is refreshed
    (re-linked when links work now); an owned entry whose skill is gone is
    removed. Anything else of the same name -- a skill the user placed, or a
    copy edited in place -- is kept and reported in ``kept``, never replaced.
    """
    result = LinkResult()
    store = (
        {d.name: d for d in sorted(shared_skills.iterdir())
         if d.is_dir() and not d.name.startswith(".")}
        if shared_skills.is_dir() else {}
    )
    data = _load_linked(target_dir)
    key = _store_key(shared_skills)
    record = dict(data.get(key, {}))

    def say(level: str, msg: str, *args) -> None:
        if logger is not None:
            getattr(logger, level)(("would " if dry_run else "") + msg, *args)

    for name, source in store.items():
        target = target_dir / name
        mine = record.get(name)
        if not os.path.lexists(str(target)):
            force = False
        elif _is_link(target):
            if _resolves_to(target, source):
                record[name] = _LINK
                result.owned.append(name)
                continue
            if not _points_into(target, shared_skills):
                result.kept.append(name)
                if logger is not None:
                    logger.warning("skill %s not linked: %s already holds a link that is not dotagents'", name, target)
                continue
            force = True
        else:
            if _paths_match(source, target):
                if mine is None or mine == _LINK:
                    record[name] = _tree_digest(target)  # an identical copy is ours
                result.owned.append(name)
                continue
            if mine is None or mine == _LINK or _tree_digest(target) != mine:
                result.kept.append(name)
                if logger is not None:
                    logger.warning(
                        "skill %s not linked: %s differs from the store's copy and was %s; kept",
                        name, target, "placed by hand" if mine is None else "edited since it was copied",
                    )
                continue
            force = True
        if dry_run:
            result.linked.append(name)
            result.owned.append(name)
            say("info", "%s skill %s: %s", "refresh" if force else "link", name, target)
            continue
        synced = sync_path(source, target, prefer_symlink=True, force=force)
        if not synced:
            result.kept.append(name)
            if logger is not None:
                logger.warning("skill %s not linked: %s", name, synced.message)
            continue
        if synced.mode == "copy":
            record[name] = _tree_digest(target)
            result.copied.append(name)
        else:
            record[name] = _LINK
            result.linked.append(name)
        result.owned.append(name)
        say("info", "skill %s (%s): %s%s", name, synced.mode, synced.message,
            " (refreshed)" if force else "")

    # Prune: recorded names the store no longer has, and links into the
    # store's skills/ made before there was a record.
    stale = set(record) - set(store)
    if target_dir.is_dir():
        for entry in target_dir.iterdir():
            if entry.name not in store and not entry.name.startswith(".") and _is_link(entry) \
                    and _points_into(entry, shared_skills):
                stale.add(entry.name)
    for name in sorted(stale):
        target = target_dir / name
        mine = record.pop(name, None)
        if not os.path.lexists(str(target)):
            continue
        if _is_link(target):
            ours = _points_into(target, shared_skills)
        else:
            ours = mine not in (None, _LINK) and _tree_digest(target) == mine
        if not ours:
            result.kept.append(name)
            if logger is not None:
                logger.warning("kept skill %s: the store no longer has it, but %s is not dotagents' as it stands", name, target)
            continue
        result.removed.append(name)
        if dry_run:
            say("info", "remove skill %s: %s", name, target)
            continue
        try:
            _remove(target)
        except OSError as exc:
            if logger is not None:
                logger.warning("could not remove skill %s: %s", name, exc)
            continue
        say("info", "removed skill %s: %s", name, target)

    if not dry_run:
        data[key] = record
        if target_dir.is_dir():
            _save_linked(target_dir, data)
    return result
