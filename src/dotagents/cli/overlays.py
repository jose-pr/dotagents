"""`dotagents overlays` -- add / remove / list / sync / show, with skills sync.

Self-contained block (own module deps in `_scope.py` / `_skills.py` /
`_overlays.py`); the only umbrella touch is registering `Overlays` on
`Dotagents._subcommands_` (in `cli/__init__.py`). Discover-not-track: installed
overlays are the dirs under `<scope>/overlays/`.
"""

import shutil
from pathlib import Path
from typing import Optional

from duho import Cli, LoggingArgs

from dotagents.cli._common import (
    BASE_ROOT,
    DotAgentsArgs,
    _installed_overlay_dirs,
    _no_subcommand,
    _run_overlay_setup,
    _write_stdout,
)


def _validated_names(raw_names, what: str) -> "list[str]":
    """Validate + normalize every requested name UP FRONT with the shared
    overlay-name rule (D84), so `add My_Overlay` and `add my-overlay` resolve the
    same overlay, a bad name (`.git`, `README.md`, `2fast`, a level name) is
    rejected before anything is touched -- not after the first overlay was
    already installed and its setup already run -- and never creates a junk dir
    under overlays/."""
    from dotagents._overlays import Overlay

    names = []
    for raw in raw_names:
        if not Overlay.is_valid_name(raw):
            raise SystemExit(
                "error: %r is not a valid overlay name to %s (a letter, then "
                "letters/digits/_/./-; not a contract-A level name)" % (raw, what)
            )
        name = Overlay.normalize_name(raw)
        if name not in names:
            names.append(name)
    return names


def _install_order(source, names, *, follow_requires: bool, logger) -> "list[str]":
    """The overlays to install, dependencies first: each name's manifest
    `requires` (transitively), then the name itself, de-duplicated. A required
    overlay the source does not offer is a warning, not an error (the overlay
    asked for still installs); a cycle is an error."""
    from dotagents._overlays import Overlay

    order: "list[str]" = []
    done: "set[str]" = set()

    def visit(name: str, chain: "list[str]") -> None:
        if name in done:
            return
        if name in chain:
            raise SystemExit(
                "error: overlay dependency cycle: %s" % " -> ".join(chain + [name])
            )
        try:
            src = source.overlay_dir(name)
        except SystemExit:
            if not chain:
                raise
            logger.warning(
                "overlay %s requires %s, which source %s does not offer -- skipped",
                chain[-1], name, source.root,
            )
            return
        if follow_requires:
            for dep in Overlay(src).read_manifest()["requires"]:  # type: ignore[union-attr]
                dep = Overlay.normalize_name(str(dep))
                if dep not in done and dep not in names:
                    logger.info("overlay %s requires %s (installing it too)", name, dep)
                visit(dep, chain + [name])
        done.add(name)
        order.append(name)

    for name in names:
        visit(name, [])
    return order


class OverlayAdd(DotAgentsArgs):
    """Install overlay(s) by name into a scope, and publish their skills.

    Resolves each ``<name>`` against the source (``--repo``,
    the env repos, the stores' registries, the bundled ``overlays/``), installs what
    its manifest ``requires`` first, copies it into
    ``<scope>/.agents/overlays/<name>/`` (discoverable), merges its D59
    routing/rules into the installed ``AGENTS.md`` managed block (additive), and
    publishes its ``skills/`` from the INSTALLED copy into the shared
    ``<scope>/.agents/skills/`` so every agent sees them. ``--copy`` mirrors
    skills as real dirs instead of symlinks (Windows / no-symlink)."""

    _parsername_ = "add"

    name: "list[str]" = []
    "Overlay name(s) to install (resolved against the source)."
    ("name",)

    repo: "list[str]" = []
    ("Overlay repo (repeatable): a directory of overlays, a JSON/TOML/YAML registry "
     "mapping names to sources, or a git <repo>[@ref][#path]; consulted before "
     "$AGENTS_OVERLAYS_REPO_<KEY>, $AGENTS_OVERLAYS_REPO, the stores' "
     "dotagents.{json,toml,yaml} and the bundled overlays/. The first repo "
     "offering a name wins.")
    ("--repo",)

    copy: bool = False
    "Copy skills into the shared dir instead of symlinking (no-symlink fallback)."
    ("--copy",)

    no_setup: bool = False
    "Skip running an overlay's idempotent `setup` script after install."
    ("--no-setup",)

    no_requires: bool = False
    "Do not install the overlays each manifest's `requires` names."
    ("--no-requires",)

    dry_run: bool = False
    "Show what would happen without touching anything."
    ("--dry-run",)

    def __call__(self) -> int:
        from dotagents import _overlays, _scope, _skills

        if not self.name:
            self._logger_.warning("no overlay name given; nothing to add")
            return 0
        names = _validated_names(self.name, "add")

        scope = self.resolve_scope()
        source = _scope.resolve_source(self.repo, scope=scope, logger=self._logger_)
        self._logger_.info("scope: %s (%s)", scope.level, scope.agents_root)
        agents_md = scope.agents_root / "AGENTS.md"

        # Resolve every name (and its requires) against the source BEFORE the
        # first install, so `add good bad` fails on `bad` with nothing touched.
        order = _install_order(
            source, names, follow_requires=not self.no_requires, logger=self._logger_,
        )
        sources = {name: source.overlay_dir(name) for name in order}

        for name in order:
            overlay_src = sources[name]
            dest_dir = scope.overlay_dir(name)
            copied, skipped, lines = _overlays.Overlay(overlay_src).install_to(
                dest_dir, self.dry_run
            )
            for line in lines:
                self._logger_.info(line)
            self._logger_.info(
                "overlay %s: %d file(s) installed, %d skipped%s",
                name, copied, skipped, " [dry-run]" if self.dry_run else "",
            )
            if not self.dry_run:
                # From the INSTALLED copy: a symlink into the source dir dies
                # with a temporary checkout (CI, a pyz-extracted temp dir) and
                # can never be matched by `remove`, which looks at the installed
                # overlay's own `skills/`.
                published = _skills.publish_overlay_skills(
                    dest_dir, scope.shared_skills_dir, copy=self.copy,
                    logger=self._logger_,
                )
                if published:
                    self._logger_.info("published %d skill(s) from %s", published, name)
            rc = _run_overlay_setup(
                dest_dir, name, scope=scope, no_setup=self.no_setup,
                dry_run=self.dry_run, logger=self._logger_, source_dir=overlay_src,
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
            scope, source, adding=order, dry_run=self.dry_run
        )
        base_block = (BASE_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        if _overlays.recompose_overlay_block(
            agents_md, base_block, overlay_dirs, self.dry_run, self._logger_
        ):
            self._logger_.info("recomposed overlay rules/routing in AGENTS.md")

        if self.dry_run:
            self._logger_.info("dry-run: no files were written")
        return 0


class OverlayRemove(DotAgentsArgs):
    """Remove installed overlay(s): delete the overlay dir, unpublish its skills,
    and recompose ``AGENTS.md``'s managed block over what remains.

    Deletes only ``<scope>/.agents/overlays/<name>/`` and unpublishes only the
    skills that overlay published (matched to its own ``skills/``, by content) --
    never a file outside the overlay dir, never another overlay's skill. Broken
    skill symlinks are then swept. The overlay's rules/routing leave the managed
    block the same way they arrived: the block is recomposed from the pristine
    base over the overlays still installed."""

    _parsername_ = "remove"

    name: "list[str]" = []
    "Overlay name(s) to remove."
    ("name",)

    dry_run: bool = False
    "Show what would happen without touching anything."
    ("--dry-run",)

    def __call__(self) -> int:
        from dotagents import _overlays, _scope, _skills

        if not self.name:
            self._logger_.warning("no overlay name given; nothing to remove")
            return 0
        names = _validated_names(self.name, "remove")
        scope = self.resolve_scope()
        self._logger_.info("scope: %s (%s)", scope.level, scope.agents_root)

        removed_names: "list[str]" = []
        for name in names:
            overlay_dir = scope.overlay_dir(name)
            if not overlay_dir.is_dir():
                self._logger_.warning("overlay %r not installed at %s", name, overlay_dir)
                continue
            if not self.dry_run:
                removed = _skills.remove_overlay_skills(
                    overlay_dir, scope.shared_skills_dir, logger=self._logger_
                )
                if removed:
                    self._logger_.info("unpublished %d skill(s) from %s", removed, name)
                shutil.rmtree(str(overlay_dir))
            removed_names.append(name)
            self._logger_.info(
                "removed overlay %s (%s)%s",
                name, overlay_dir, " [dry-run]" if self.dry_run else "",
            )

        if removed_names:
            remaining = [
                overlay.path
                for overlay in _overlays.Overlay.discover(scope.overlay_root)
                if overlay.name not in removed_names
            ]
            base_block = (BASE_ROOT / "AGENTS.md").read_text(encoding="utf-8")
            if _overlays.recompose_overlay_block(
                scope.agents_root / "AGENTS.md", base_block, remaining,
                self.dry_run, self._logger_,
            ):
                self._logger_.info("recomposed overlay rules/routing in AGENTS.md")

        if self.dry_run:
            self._logger_.info("dry-run: no files were written")
        return 0


class OverlayList(DotAgentsArgs):
    """List overlays: those installed in the scope (plus, in the project scope,
    the user store's -- both are in play for a project session; a same-named
    project overlay shadows the store's copy), and those available from source.

    ``installed`` is discovered by presence under ``<scope>/.agents/overlays/``; no
    registry file. ``available`` is what the source offers (``*`` = installed in
    either scope). ``--json`` emits everything as a machine-readable object."""

    _parsername_ = "list"

    repo: "list[str]" = []
    ("Overlay repo (repeatable): a directory of overlays, a JSON/TOML/YAML registry "
     "mapping names to sources, or a git <repo>[@ref][#path]; consulted before "
     "$AGENTS_OVERLAYS_REPO_<KEY>, $AGENTS_OVERLAYS_REPO, the stores' "
     "dotagents.{json,toml,yaml} and the bundled overlays/. The first repo "
     "offering a name wins.")
    ("--repo",)

    json: bool = False
    "Emit JSON instead of plain text."
    ("--json",)

    def __call__(self) -> int:
        import json as _json

        from dotagents import _scope

        from dotagents._overlays import Overlay

        scope = self.resolve_scope()
        # `scope.overlays`: every store in play (system, user, and the project's
        # in a project scope), a same-named overlay in a later store shadowing
        # the earlier copies. With -g there is no project store.
        active = scope.overlays
        by_store = {store: [o.name for o in active if o.store == store] for store in scope.stores}
        # Shadowed copies are not in `active` at all; list them for the eye.
        shadowed = {
            store: [
                o.name for o in Overlay.discover(store / "overlays")
                if o.name not in by_store[store]
            ]
            for store in scope.stores
        }
        try:
            available = _scope.resolve_source(self.repo, scope=scope, logger=self._logger_).available()
        except SystemExit:
            available = []
        names = {o.name for o in active}

        # Listed most-specific first: the scope's own store, then the stores
        # beneath it (user, then system); a store with nothing is shown only
        # when it is the scope's own.
        listing = []
        for store in reversed(scope.stores):
            level = scope.store_level(store)
            if store != scope.agents_root and not by_store[store] and not shadowed[store]:
                continue
            listing.append((level, store, by_store[store], shadowed[store]))

        if self.json:
            payload = {
                "scope": scope.level,
                "root": str(scope.overlay_root),
                "installed": by_store[scope.agents_root],
                "stores": [
                    {"level": level, "root": str(store / "overlays"),
                     "installed": names_, "shadowed": shadowed_}
                    for level, store, names_, shadowed_ in listing
                ],
                "available": available,
            }
            _write_stdout(_json.dumps(payload, indent=2) + "\n")
            return 0

        self._logger_.info("scope: %s (%s)", scope.level, scope.overlay_root)
        lines = []
        for level, store, names_, shadowed_ in listing:
            lines.append("installed (%s):" % level)
            lines += ["  %s" % n for n in names_]
            lines += ["  %s  (shadowed by a more specific store's)" % n for n in shadowed_]
            if not names_ and not shadowed_:
                lines.append("  (none)")
        lines.append("available (source):")
        lines += ["  %s%s" % (n, " *" if n in names else "") for n in available] or ["  (none)"]
        _write_stdout("\n".join(lines) + "\n")
        return 0


class OverlayShow(DotAgentsArgs):
    """Describe one overlay: where it is, what its manifest declares (description,
    priority, requires, routing, rules), its setup script, skills and file count.

    Looks at the INSTALLED copy in the scope first (in the project scope, then
    the user store's -- the one a project session would use), else the source's."""

    _parsername_ = "show"

    name: str = ""
    "Overlay name."
    ("name",)

    repo: "list[str]" = []
    ("Overlay repo (repeatable): a directory of overlays, a JSON/TOML/YAML registry "
     "mapping names to sources, or a git <repo>[@ref][#path]; consulted before "
     "$AGENTS_OVERLAYS_REPO_<KEY>, $AGENTS_OVERLAYS_REPO, the stores' "
     "dotagents.{json,toml,yaml} and the bundled overlays/. The first repo "
     "offering a name wins.")
    ("--repo",)

    json: bool = False
    "Emit JSON instead of plain text."
    ("--json",)

    def __call__(self) -> int:
        import json as _json

        from dotagents import _overlays, _scope

        (name,) = _validated_names([self.name], "show") if self.name else (None,)
        if not name:
            raise SystemExit("error: overlays show needs an overlay name")
        scope = self.resolve_scope()
        # The copy a session in this scope would use (the most specific store's),
        # else the source's.
        active = [o for o in scope.overlays if o.name == name]
        if active:
            path = active[0].path
            where = "installed (%s)" % scope.store_level(active[0].store)
        else:
            where = "source"
            path = _scope.resolve_source(self.repo, scope=scope, logger=self._logger_).overlay_dir(name)
        overlay = _overlays.Overlay(path)
        manifest = overlay.read_manifest()
        setup = overlay.find_setup_script()
        skills = sorted(
            d.name for d in (path / "skills").iterdir() if d.is_dir()
        ) if (path / "skills").is_dir() else []
        info = {
            "name": overlay.name,
            "where": where,
            "path": str(path),
            "root_var": overlay.root_var,
            "description": manifest["description"],
            "priority": manifest["priority"],
            "requires": manifest["requires"],
            "routing": manifest["routing"],
            "rules": manifest["rules"],
            "setup": setup.name if setup else None,
            "skills": skills,
            "files": len(overlay.files()),
        }
        if self.json:
            _write_stdout(_json.dumps(info, indent=2) + "\n")
            return 0
        lines = [
            "%s (%s): %s" % (info["name"], where, info["path"]),
            "  root var:    %s" % info["root_var"],
            "  description: %s" % (info["description"] or "-"),
            "  priority:    %s" % info["priority"],
            "  requires:    %s" % (", ".join(info["requires"]) or "-"),  # type: ignore[arg-type]
            "  rules files: %s" % (", ".join(info["rules"]) or "-"),  # type: ignore[arg-type]
            "  routing:     %d line(s)" % len(info["routing"]),  # type: ignore[arg-type]
            "  setup:       %s" % (info["setup"] or "-"),
            "  skills:      %s" % (", ".join(skills) or "-"),
            "  files:       %d" % info["files"],
        ]
        _write_stdout("\n".join(lines) + "\n")
        return 0


class OverlaySync(DotAgentsArgs):
    """Refresh installed overlays from source, and resync their skills.

    Re-applies each installed overlay -- new files land; an existing file is
    left alone unless ``--overwrite``, which replaces files whose content
    differs from the source (hand-edits included) -- re-merges its
    rules/routing, and refreshes its published skills. An optional ``<glob>``
    filters which installed overlays to sync (``sync 'py*'``)."""

    _parsername_ = "sync"

    pattern: Optional[str] = None
    "Glob over installed overlay names to sync (default: all)."
    ("pattern",)

    repo: "list[str]" = []
    ("Overlay repo (repeatable): a directory of overlays, a JSON/TOML/YAML registry "
     "mapping names to sources, or a git <repo>[@ref][#path]; consulted before "
     "$AGENTS_OVERLAYS_REPO_<KEY>, $AGENTS_OVERLAYS_REPO, the stores' "
     "dotagents.{json,toml,yaml} and the bundled overlays/. The first repo "
     "offering a name wins.")
    ("--repo",)

    copy: bool = False
    "Copy skills into the shared dir instead of symlinking (no-symlink fallback)."
    ("--copy",)

    overwrite: bool = False
    "Replace installed files whose content differs from the source (hand-edits included)."
    ("--overwrite",)

    no_setup: bool = False
    "Skip running each overlay's idempotent `setup` script after sync."
    ("--no-setup",)

    dry_run: bool = False
    "Show what would happen without touching anything."
    ("--dry-run",)

    def __call__(self) -> int:
        from dotagents import _overlays, _scope, _skills

        scope = self.resolve_scope()
        source = _scope.resolve_source(self.repo, scope=scope, logger=self._logger_)
        installed = [o.name for o in _overlays.Overlay.discover(scope.overlay_root)]
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
            copied, skipped, lines = _overlays.Overlay(overlay_src).install_to(
                dest_dir, self.dry_run, overwrite=self.overwrite
            )
            for line in lines:
                if not line.startswith("skip"):
                    self._logger_.info(line)
            self._logger_.info(
                "synced %s: %d file(s) written, %d unchanged%s",
                name, copied, skipped, " [dry-run]" if self.dry_run else "",
            )
            if not self.dry_run:
                _skills.resync_overlay_skills(
                    dest_dir, scope.shared_skills_dir, copy=self.copy,
                    logger=self._logger_,
                )
            rc = _run_overlay_setup(
                dest_dir, name, scope=scope, no_setup=self.no_setup,
                dry_run=self.dry_run, logger=self._logger_, source_dir=overlay_src,
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
    """Manage opt-in overlays by name: add / remove / list / sync / show (+ skills sync)."""

    _parsername_ = "overlays"
    _subcommands_ = [OverlayAdd, OverlayRemove, OverlayList, OverlaySync, OverlayShow]

    def __call__(self) -> int:
        return _no_subcommand(self, "pick an overlays subcommand: add, remove, list, sync, show")
