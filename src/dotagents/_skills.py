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
the contract; ``--copy`` forces the copy path up front (Windows / no-symlink).

Ported from the precursor's ``managers/skills.py`` + ``helpers.py`` sync
primitives; the external ``skills``-CLI registry (``register``/``skills.txt``) is
deliberately **dropped** -- publish-to-shared-dir is the stdlib, useful part.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


class SyncResult:
    __slots__ = ("success", "mode", "message")

    def __init__(self, success: bool, mode: str, message: str = ""):
        self.success = success
        self.mode = mode
        self.message = message

    def __bool__(self) -> bool:
        return self.success


def _paths_match(source: Path, target: Path) -> bool:
    """True if ``target`` already mirrors ``source`` (same file set, by name).

    A cheap structural check -- enough to decide "already published, leave it" from
    "conflicting content, overwrite". Not a content hash: republish on ``sync`` is a
    separate, explicit path (``resync_path``)."""
    if not source.exists() or not target.exists():
        return False
    if source.is_file() and target.is_file():
        return source.stat().st_size == target.stat().st_size
    if source.is_dir() and target.is_dir():
        src_files = {str(p.relative_to(source)) for p in source.rglob("*") if p.is_file()}
        tgt_files = {str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()}
        return src_files == tgt_files
    return False


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
            if os.path.islink(str(target)):
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
    if os.path.islink(str(target)):
        if not _resolves_to(target, source):
            return SyncResult(False, "symlink", "symlink points elsewhere")
        os.unlink(str(target))
        return SyncResult(True, "symlink", "removed symlink")
    if not _paths_match(source, target):
        return SyncResult(False, "copy", "content differs from source")
    try:
        _remove(target)
        return SyncResult(True, "copy", "removed copy")
    except OSError as exc:
        return SyncResult(False, "copy", "removal failed: %s" % exc)


def resync_path(source: Path, target: Path) -> SyncResult:
    """Refresh a published skill from its overlay source. A symlink is inherently
    current; a copy is re-copied when its file set drifted."""
    if not source.exists():
        return SyncResult(False, "error", "source does not exist")
    if not os.path.lexists(str(target)):
        return sync_path(source, target, prefer_symlink=True)
    if os.path.islink(str(target)):
        return SyncResult(True, "symlink", "symlink is current")
    if _paths_match(source, target):
        return SyncResult(True, "copy", "current")
    return sync_path(source, target, prefer_symlink=False, force=True)


def _remove(path: Path) -> None:
    if os.path.islink(str(path)) or path.is_file():
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
        if os.path.islink(str(entry)) and not entry.exists():
            os.unlink(str(entry))
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

    Symlink-preferred unless ``copy`` forces copies. A conflicting target is
    overwritten (the overlay owns its skill names). Returns the count published.
    Sweeps broken syncs first so a stale symlink can't block a re-publish."""
    clean_broken_syncs(shared_skills, logger=logger)
    skill_dirs = _overlay_skill_dirs(overlay_dir)
    if not skill_dirs:
        return 0
    shared_skills.mkdir(parents=True, exist_ok=True)
    published = 0
    for skill_dir in skill_dirs:
        target = shared_skills / skill_dir.name
        result = sync_path(skill_dir, target, prefer_symlink=not copy, force=False)
        if not result and result.mode == "conflict":
            result = sync_path(skill_dir, target, prefer_symlink=not copy, force=True)
        if result:
            published += 1
            if logger is not None and "already" not in result.message:
                logger.info("skill %s: %s", skill_dir.name, result.message)
        elif logger is not None:
            logger.warning("failed to publish skill %s: %s", skill_dir.name, result.message)
    return published


def resync_overlay_skills(overlay_dir: Path, shared_skills: Path, *, logger=None) -> int:
    """Refresh already-published skills of this overlay (copy-mode drift). New
    skills are published; symlinks are inherently current."""
    skill_dirs = _overlay_skill_dirs(overlay_dir)
    if not skill_dirs or not shared_skills.is_dir():
        # Nothing published yet -> fall back to a fresh publish.
        return publish_overlay_skills(overlay_dir, shared_skills, logger=logger)
    updated = 0
    for skill_dir in skill_dirs:
        target = shared_skills / skill_dir.name
        result = resync_path(skill_dir, target)
        if result and result.mode == "copy" and "current" not in result.message:
            updated += 1
            if logger is not None:
                logger.info("skill %s: %s", skill_dir.name, result.message)
    return updated


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
            removed += 1
            if logger is not None:
                logger.info("removed skill: %s (%s)", skill_dir.name, result.mode)
        elif not result and logger is not None:
            logger.warning("kept skill %s: %s", skill_dir.name, result.message)
    clean_broken_syncs(shared_skills, logger=logger)
    _prune_empty(shared_skills)
    return removed
