"""`dotagents overlays` -- add / remove / list / sync, with skills sync.

Self-contained block (own module deps in `_scope.py` / `_skills.py` /
`_overlays.py`); the only umbrella touch is registering `Overlays` on
`Dotagents._subcommands_` (in `cli/__init__.py`). Discover-not-track: installed
overlays are the dirs under `<scope>/overlays/`.
"""

import shutil
from pathlib import Path
from typing import Optional

from duho import Cli, Cmd, LoggingArgs

from dotagents.cli._common import (
    BASE_ROOT,
    _installed_overlay_dirs,
    _run_overlay_setup,
)


class OverlayAdd(LoggingArgs, Cmd):
    """Install overlay(s) by name into a scope, and publish their skills.

    Resolves each ``<name>`` against the source (``--source`` /
    ``$AGENTS_OVERLAYS_SRC``, default the bundled ``overlays/``), copies it into
    ``<scope>/.agents/overlays/<name>/`` (discoverable), merges its D59
    routing/rules into the installed ``AGENTS.md`` managed block (additive), and
    publishes its ``skills/`` into the shared ``<scope>/.agents/skills/`` so every
    agent sees them. ``--copy`` mirrors skills as real dirs instead of symlinks
    (Windows / no-symlink)."""

    _parsername_ = "add"

    name: "list[str]" = []
    "Overlay name(s) to install (resolved against the source)."
    ("name",)

    source: Optional[str] = None
    "Overlay source directory (default: $AGENTS_OVERLAYS_SRC or the bundled overlays/)."
    ("--source",)

    global_scope: bool = False
    "Install into the user scope (~/.agents) instead of the project scope."
    ("--global", "-g")

    agents_dir: Path = Path.home() / ".agents"
    "User-scope agents dir (default: ~/.agents)."
    ("--agents-dir",)

    copy: bool = False
    "Copy skills into the shared dir instead of symlinking (no-symlink fallback)."
    ("--copy",)

    no_setup: bool = False
    "Skip running an overlay's idempotent `setup` script after install."
    ("--no-setup",)

    dry_run: bool = False
    "Show what would happen without touching anything."
    ("--dry-run",)

    def __call__(self) -> int:
        from dotagents import _overlays, _scope, _skills

        if not self.name:
            self._logger_.warning("no overlay name given; nothing to add")
            return 0
        source = _scope.resolve_source(self.source)
        scope = _scope.resolve_scope(self.global_scope, agents_dir=self.agents_dir)
        self._logger_.info("scope: %s (%s)", scope.level, scope.agents_root)
        agents_md = scope.agents_root / "AGENTS.md"

        for name in self.name:
            overlay_src = source.overlay_dir(name)
            dest_dir = scope.overlay_dir(name)
            copied, skipped, lines = _overlays.install_overlay_dir(
                overlay_src, dest_dir, self.dry_run
            )
            for line in lines:
                self._logger_.info(line)
            self._logger_.info(
                "overlay %s: %d file(s) installed, %d skipped%s",
                name, copied, skipped, " [dry-run]" if self.dry_run else "",
            )
            if not self.dry_run:
                published = _skills.publish_overlay_skills(
                    overlay_src, scope.shared_skills_dir, copy=self.copy,
                    logger=self._logger_,
                )
                if published:
                    self._logger_.info("published %d skill(s) from %s", published, name)
            rc = _run_overlay_setup(
                dest_dir, name, scope=scope, no_setup=self.no_setup,
                dry_run=self.dry_run, logger=self._logger_,
            )
            if rc:
                raise SystemExit(
                    "error: setup for overlay %r failed (exit %d)" % (name, rc)
                )

        # Recompose the whole managed block from the pristine base over ALL installed
        # overlays in (priority, name) order (plan 02 / D68), so a high-priority overlay
        # lands last regardless of when it was added -- not merely appended after
        # whatever was already in the block.
        overlay_dirs = _installed_overlay_dirs(
            scope, source, adding=self.name, dry_run=self.dry_run
        )
        base_block = (BASE_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        if _overlays.recompose_overlay_block(
            agents_md, base_block, overlay_dirs, self.dry_run, self._logger_
        ):
            self._logger_.info("recomposed overlay rules/routing in AGENTS.md")

        if self.dry_run:
            self._logger_.info("dry-run: no files were written")
        return 0


class OverlayRemove(LoggingArgs, Cmd):
    """Remove installed overlay(s): delete the overlay dir + unpublish its skills.

    Deletes only ``<scope>/.agents/overlays/<name>/`` and unpublishes only the
    skills that overlay published (matched to its own ``skills/`` source) -- never a
    file outside the overlay dir, never another overlay's skill. Broken skill
    symlinks are then swept. The overlay's D59 rules/routing in ``AGENTS.md`` are
    **not** auto-unmerged (a clean managed-block un-merge is deferred, see the
    decision); a warning points at the manual prune when the overlay carried any."""

    _parsername_ = "remove"

    name: "list[str]" = []
    "Overlay name(s) to remove."
    ("name",)

    global_scope: bool = False
    "Operate on the user scope (~/.agents) instead of the project scope."
    ("--global", "-g")

    agents_dir: Path = Path.home() / ".agents"
    "User-scope agents dir (default: ~/.agents)."
    ("--agents-dir",)

    dry_run: bool = False
    "Show what would happen without touching anything."
    ("--dry-run",)

    def __call__(self) -> int:
        from dotagents import _overlays, _scope, _skills

        if not self.name:
            self._logger_.warning("no overlay name given; nothing to remove")
            return 0
        scope = _scope.resolve_scope(self.global_scope, agents_dir=self.agents_dir)
        self._logger_.info("scope: %s (%s)", scope.level, scope.agents_root)

        for name in self.name:
            overlay_dir = scope.overlay_dir(name)
            if not overlay_dir.is_dir():
                self._logger_.warning("overlay %r not installed at %s", name, overlay_dir)
                continue
            manifest = _overlays.read_manifest(overlay_dir)
            has_rules = bool(manifest["routing"] or manifest["rules"])
            if not self.dry_run:
                removed = _skills.remove_overlay_skills(
                    overlay_dir, scope.shared_skills_dir, logger=self._logger_
                )
                if removed:
                    self._logger_.info("unpublished %d skill(s) from %s", removed, name)
                shutil.rmtree(str(overlay_dir))
            self._logger_.info(
                "removed overlay %s (%s)%s",
                name, overlay_dir, " [dry-run]" if self.dry_run else "",
            )
            if has_rules:
                self._logger_.warning(
                    "overlay %s contributed rules/routing to AGENTS.md; those are "
                    "NOT auto-removed -- prune its lines from the managed block by "
                    "hand (or re-run `dotagents install` to regenerate it).", name,
                )

        if self.dry_run:
            self._logger_.info("dry-run: no files were written")
        return 0


class OverlayList(LoggingArgs, Cmd):
    """List overlays: those installed in the scope, and those available from source.

    ``installed`` is discovered by presence under ``<scope>/.agents/overlays/``; no
    registry file. ``available`` is what the source offers. ``--json`` emits both as
    a machine-readable object."""

    _parsername_ = "list"

    source: Optional[str] = None
    "Overlay source directory (default: $AGENTS_OVERLAYS_SRC or the bundled overlays/)."
    ("--source",)

    global_scope: bool = False
    "List the user scope (~/.agents) instead of the project scope."
    ("--global", "-g")

    agents_dir: Path = Path.home() / ".agents"
    "User-scope agents dir (default: ~/.agents)."
    ("--agents-dir",)

    json: bool = False
    "Emit JSON instead of plain text."
    ("--json",)

    def __call__(self) -> int:
        import json as _json

        from dotagents import _scope

        scope = _scope.resolve_scope(self.global_scope, agents_dir=self.agents_dir)
        installed = _scope.discover_overlays(scope)
        try:
            available = _scope.resolve_source(self.source).available()
        except SystemExit:
            available = []

        if self.json:
            print(_json.dumps({
                "scope": scope.level,
                "root": str(scope.overlay_root),
                "installed": installed,
                "available": available,
            }, indent=2))
            return 0

        self._logger_.info("scope: %s (%s)", scope.level, scope.overlay_root)
        print("installed (%s):" % scope.level)
        if installed:
            for n in installed:
                print("  %s" % n)
        else:
            print("  (none)")
        print("available (source):")
        if available:
            for n in available:
                mark = " *" if n in installed else ""
                print("  %s%s" % (n, mark))
        else:
            print("  (none)")
        return 0


class OverlaySync(LoggingArgs, Cmd):
    """Refresh installed overlays from source, and resync their skills.

    Re-applies each installed overlay (additive: new files land, hand-edits stay),
    re-merges its rules/routing, and refreshes its published skills. An optional
    ``<glob>`` filters which installed overlays to sync (``sync 'py*'``)."""

    _parsername_ = "sync"

    pattern: Optional[str] = None
    "Glob over installed overlay names to sync (default: all)."
    ("pattern",)

    source: Optional[str] = None
    "Overlay source directory (default: $AGENTS_OVERLAYS_SRC or the bundled overlays/)."
    ("--source",)

    global_scope: bool = False
    "Sync the user scope (~/.agents) instead of the project scope."
    ("--global", "-g")

    agents_dir: Path = Path.home() / ".agents"
    "User-scope agents dir (default: ~/.agents)."
    ("--agents-dir",)

    copy: bool = False
    "Copy skills into the shared dir instead of symlinking (no-symlink fallback)."
    ("--copy",)

    no_setup: bool = False
    "Skip running each overlay's idempotent `setup` script after sync."
    ("--no-setup",)

    dry_run: bool = False
    "Show what would happen without touching anything."
    ("--dry-run",)

    def __call__(self) -> int:
        from dotagents import _overlays, _scope, _skills

        scope = _scope.resolve_scope(self.global_scope, agents_dir=self.agents_dir)
        source = _scope.resolve_source(self.source)
        installed = _scope.discover_overlays(scope)
        names = _scope.filter_names(installed, self.pattern)
        if not names:
            self._logger_.info(
                "no installed overlays%s to sync",
                "" if self.pattern in (None, "*") else " matching %r" % self.pattern,
            )
            return 0
        self._logger_.info("scope: %s (%s)", scope.level, scope.agents_root)
        agents_md = scope.agents_root / "AGENTS.md"

        for name in names:
            try:
                overlay_src = source.overlay_dir(name)
            except SystemExit:
                self._logger_.warning("overlay %r not in source; skipping", name)
                continue
            dest_dir = scope.overlay_dir(name)
            copied, skipped, lines = _overlays.install_overlay_dir(
                overlay_src, dest_dir, self.dry_run
            )
            self._logger_.info(
                "synced %s: %d new file(s), %d unchanged%s",
                name, copied, skipped, " [dry-run]" if self.dry_run else "",
            )
            if not self.dry_run:
                _skills.resync_overlay_skills(
                    overlay_src, scope.shared_skills_dir, logger=self._logger_
                )
            rc = _run_overlay_setup(
                dest_dir, name, scope=scope, no_setup=self.no_setup,
                dry_run=self.dry_run, logger=self._logger_,
            )
            if rc:
                raise SystemExit(
                    "error: setup for overlay %r failed (exit %d)" % (name, rc)
                )

        # Recompose the managed block over ALL installed overlays in (priority, name)
        # order (plan 02 / D68) -- not just the pattern-matched subset synced above, so
        # priority ordering holds across the full installed set.
        overlay_dirs = _installed_overlay_dirs(scope, source, dry_run=self.dry_run)
        base_block = (BASE_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        if _overlays.recompose_overlay_block(
            agents_md, base_block, overlay_dirs, self.dry_run, self._logger_
        ):
            self._logger_.info("recomposed overlay rules/routing in AGENTS.md")

        if self.dry_run:
            self._logger_.info("dry-run: no files were written")
        return 0


class Overlays(LoggingArgs, Cli):
    """Manage opt-in overlays by name: add / remove / list / sync (+ skills sync)."""

    _parsername_ = "overlays"
    _subcommands_ = [OverlayAdd, OverlayRemove, OverlayList, OverlaySync]

    def __call__(self) -> int:
        self._logger_.info("pick an overlays subcommand: add, remove, list, sync")
        return 0
