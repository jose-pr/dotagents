"""`dotagents overlays` -- add / remove / list / sync / show, with skills sync.

The logic lives in `_scope.py` / `_sources.py` / `_skills.py` / `_overlays.py`;
`cli/__init__.py` lists the `Overlays` umbrella in `_BUILTIN_COMMANDS`.
Discover-not-track: installed overlays are the dirs under `<store>/overlays/`.
Each carries an install record (`Overlay.INSTALL_RECORD`): the repo it came
from, which `sync` resolves it from again, and the digest of every file
installed, which lets `sync` prune what the source dropped.
"""

import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Optional

from duho import Cli, LoggingArgs

from dotagents._resources import base_agents_text
from dotagents.cli._common import DotAgentsArgs, _write_stdout, store_base


def _redacted(spec) -> str:
    """A repo spec (text or parsed) fit for a log line."""
    from dotagents._sources import Spec, redact

    return spec.display() if isinstance(spec, Spec) else redact(str(spec))


def _validated_names(raw_names, what: str) -> "list[str]":
    """Validate + normalize every requested name up front with the shared
    overlay-name rule: `add My_Overlay` and `add my-overlay` resolve the
    same overlay, and a bad name (`.git`, `README.md`, `2fast`, a level name) is
    rejected before anything is touched."""
    from dotagents._overlays import Overlay

    names = []
    for raw in raw_names:
        if not Overlay.is_valid_name(raw):
            raise SystemExit(
                "error: %r is not a valid overlay name to %s (a letter, then "
                "letters/digits/_/./-, ending in a letter or digit; not a "
                "contract-A level name or a Windows device name)" % (raw, what)
            )
        name = Overlay.normalize_name(raw)
        if name not in names:
            names.append(name)
    return names


def _installed_dir(scope, name: str) -> "Optional[Path]":
    """The scope's own installed dir for the normalized ``name``: the literal
    ``overlays/<name>/``, else one whose name normalizes to it (an install
    made under an older name rule -- ``foo.bar`` before dots normalized to
    ``-``, or a hand-placed ``My_Overlay``). ``None`` when not installed."""
    from dotagents._overlays import Overlay

    literal = scope.overlay_dir(name)
    if literal.is_dir():
        return literal
    for overlay in Overlay.discover(scope.overlay_root):
        if overlay.normalized_name == name:
            return overlay.path
    return None


def _install_order(
    source, names, *, follow_requires: bool, logger, installed: "frozenset[str]" = frozenset(),
) -> "list[str]":
    """The overlays to install, dependencies first: each name's manifest
    `requires` (transitively), then the name itself, de-duplicated. A required
    overlay the source does not offer is a warning, not an error (the overlay
    asked for still installs); a cycle is an error.

    ``installed`` (normalized names) is what the scope already has -- its own
    store and, for a project, the user store's too. A requirement in it is
    satisfied: it is neither copied again nor set up again (a venv build or a
    toolchain check should not rerun because a neighbour was added). A name
    asked for explicitly is never treated that way: `add x` always re-installs
    and re-sets-up `x`."""
    from dotagents._overlays import Overlay
    from dotagents._sources import OverlayNotFound

    order: "list[str]" = []
    done: "set[str]" = set()
    satisfied: "set[str]" = set()

    def visit(name: str, chain: "list[str]") -> None:
        if name in done:
            return
        if name in chain:
            raise SystemExit(
                "error: overlay dependency cycle: %s" % " -> ".join(chain + [name])
            )
        try:
            src = source.overlay_dir(name)
        except OverlayNotFound:
            # Only "no repo offers it": a repo that cannot be loaded (a failed
            # clone, a malformed registry) is an error, not a missing name.
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
                if dep in done or dep in names:
                    visit(dep, chain + [name])
                    continue
                if dep in installed:
                    if dep not in satisfied:
                        satisfied.add(dep)
                        logger.info("overlay %s requires %s: already installed", name, dep)
                    continue
                logger.info("overlay %s requires %s (installing it too)", name, dep)
                visit(dep, chain + [name])
        done.add(name)
        order.append(name)

    for name in names:
        visit(name, [])
    return order


def _is_link(path: Path) -> bool:
    """A symlink, or a Windows junction (``os.path.isjunction`` is 3.12+;
    before it, the reparse tag says so)."""
    if os.path.islink(str(path)):
        return True
    if hasattr(os.path, "isjunction"):
        return os.path.isjunction(str(path))
    try:
        tag = getattr(os.lstat(str(path)), "st_reparse_tag", 0)
    except OSError:
        return False
    return bool(tag) and tag == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -1)


def _remove_tree(path: Path) -> None:
    """Delete an installed overlay dir: a symlink/junction to one is unlinked
    (never followed), and a read-only file inside (git objects on Windows) is
    made writable and retried instead of failing the removal half-way."""
    if _is_link(path):
        os.unlink(str(path))
        return

    def _retry(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if sys.version_info >= (3, 12):
        shutil.rmtree(str(path), onexc=_retry)
    else:  # pragma: no cover -- exercised on the 3.9 leg
        shutil.rmtree(str(path), onerror=_retry)


def _run_setup(scope, dest_dir: Path, name: str, *, no_setup, dry_run, logger, source_dir=None,
               timeout: "Optional[int]" = None) -> int:
    """Run an installed overlay's `setup` script, honoring `--no-setup`.

    The script sees the scope's assembled env (:func:`_session_env`: overlay
    libs on ``PYTHONPATH``, bins on ``PATH``), ``AGENTS_HOME`` = the USER store
    (what the variable means everywhere else) and ``AGENTS_SCOPE_ROOT`` /
    ``AGENTS_SCOPE`` = the store it is installed into. On a `--dry-run` the installed dir may not exist
    yet, so the SOURCE overlay (`source_dir`) is what gets inspected. Returns
    the exit code, 0 when skipped or absent."""
    from dotagents._overlays import Overlay

    overlay = Overlay(dest_dir)
    if dry_run and source_dir is not None and overlay.find_setup_script() is None:
        overlay = Overlay(source_dir)
    if no_setup:
        if overlay.find_setup_script() is not None:
            logger.info("skipping setup for %s (--no-setup)", name)
        return 0
    rc = overlay.run_setup(
        agents_dir=scope.user_root, scope_root=scope.agents_root, scope_level=scope.level,
        dry_run=dry_run, logger=logger,
        base_env=None if dry_run else _session_env(scope, logger), timeout=timeout,
    )
    return rc or 0


def _session_env(scope, logger) -> "dict[str, str]":
    """``os.environ`` with the scope's assembled ``dotagents env`` applied -- what
    a session in that scope sees, so a setup script run from a plain shell
    still finds every overlay's ``lib`` on ``PYTHONPATH`` and ``bin`` on ``PATH``."""
    from dotagents import _env

    env = dict(os.environ)
    changes = _env.get_environment(scope, base_env=env, logger=logger)
    env.update(changes)
    for name in changes.removed:
        env.pop(name, None)
    return env


def _backup_root(scope, name: str) -> Path:
    """Where `add` / `sync` copy a locally edited file before replacing or
    removing it: the store's timestamped ``install_backup/`` root."""
    from dotagents._merge import timestamped_backup_root

    return timestamped_backup_root(scope.agents_root) / "overlays" / name


def _recompose(scope, logger, *, dry_run: bool, extra: "Optional[list[Path]]" = None,
               without: "frozenset[str]" = frozenset()) -> None:
    """Rebuild the managed block from the pristine base over every overlay
    installed in the scope's own store, in (priority, name) order.

    ``extra`` stands in for overlays a `--dry-run` add did not copy;
    ``without`` (normalized names) drops overlays a `--dry-run` remove did
    not delete."""
    from dotagents import _overlays

    dirs = [
        o.path for o in _overlays.Overlay.discover(scope.overlay_root)
        if o.normalized_name not in without
    ] + list(extra or [])
    base_block = base_agents_text(
        store_base(scope.agents_root, logger), scope.agents_root, project=not scope.global_scope
    )
    if _overlays.recompose_overlay_block(
        scope.agents_root / "AGENTS.md", base_block, dirs, dry_run, logger
    ):
        logger.info("%s overlay rules/routing in AGENTS.md",
                    "would recompose" if dry_run else "recomposed")


def _usage_error(message: str) -> int:
    """A command-line mistake: the message on stderr and exit code 2, as
    argparse reports its own."""
    sys.stderr.write("error: %s\n" % message)
    return 2


def _source_inside_install_root(scope, name: str, src: Path) -> "Optional[str]":
    """Why ``src`` cannot be the source of overlay ``name`` in ``scope``, or
    None. A source inside ``<store>/overlays/`` is (or overlaps) an install
    dir: `add` would copy it onto itself and `remove` would then delete the
    only copy."""
    from dotagents._scope import _is_within

    if not _is_within(src, scope.overlay_root):
        return None
    return (
        "overlay %s: its source %s is inside %s, where overlays are installed "
        "(and `overlays remove` deletes them); keep the source elsewhere"
        % (name, src, scope.overlay_root)
    )


def _log_install(logger, verb: str, name: str, result, dry_run: bool) -> None:
    """One summary line per overlay, and a warning naming every file edited
    here that the install replaced or removed (each backed up first)."""
    parts = ["%d file(s) %s" % (result.written, verb), "%d unchanged" % result.unchanged]
    if result.removed:
        parts.append("%d removed" % len(result.removed))
    logger.info("overlay %s: %s%s", name, ", ".join(parts), " [dry-run]" if dry_run else "")
    if result.replaced:
        logger.warning(
            "overlay %s: replaced %s -- edited here; the install matches its source now", name,
            ", ".join(result.replaced),
        )
    if result.backed_up:
        logger.info("overlay %s: backed up %s", name, ", ".join(result.backed_up))


def _install_one(scope, name: str, src: Path, origin, *, copy: bool, no_setup: bool,
                 dry_run: bool, logger, setup_timeout: "Optional[int]" = None,
                 prune: bool = False) -> "list[str]":
    """Install (or re-install) overlay ``name`` from ``src`` into the scope,
    publish its skills and run its setup. A FRESH install is copied into a
    staging dir that discovery ignores and moved into place whole, and is
    rolled back (skills unpublished, dir removed) when its setup fails --
    never left installed but unconfigured. An EXISTING install is made to
    match the source (see `Overlay.install_to`; ``prune`` clears its ignored
    files too), local edits backed up first. Raises on setup failure. Returns
    the skills the overlay ships."""
    from dotagents import _overlays, _skills
    from dotagents._sources import source_record

    if not _overlays.Overlay.is_valid_name(name):  # backstop: never a path outside overlays/
        raise SystemExit("error: %r is not a valid overlay name" % name)
    existing = _installed_dir(scope, name)
    dest = existing or scope.overlay_dir(name)
    fresh = existing is None
    overlay = _overlays.Overlay(src)
    record = source_record(origin) if origin is not None else None
    if fresh and not dry_run:
        staging = scope.overlay_root / (".staging-%s" % name)
        if os.path.lexists(str(staging)):
            _remove_tree(staging)
        staging.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = overlay.install_to(staging, False, source=record)
            os.replace(str(staging), str(dest))
        except BaseException:
            if os.path.lexists(str(staging)):
                _remove_tree(staging)
            raise
    else:
        result = overlay.install_to(dest, dry_run, prune=prune,
                                    backup_root=_backup_root(scope, name), source=record)
    for line in result.lines:
        logger.info(line)
    _log_install(logger, "installed", name, result, dry_run)

    skills = sorted(d.name for d in (dest / "skills").iterdir() if d.is_dir()) \
        if (dest / "skills").is_dir() else []
    if not dry_run:
        # From the INSTALLED copy: a symlink into the source dir dies with a
        # temporary checkout (CI, a pyz-extracted temp dir) and can never be
        # matched by `remove`, which looks at the installed overlay's `skills/`.
        published = _skills.publish_overlay_skills(
            dest, scope.shared_skills_dir, copy=copy, logger=logger,
        )
        if published:
            logger.info("published %d skill(s) from %s", published, name)
    rc = _run_setup(scope, dest, name, no_setup=no_setup, dry_run=dry_run,
                    logger=logger, source_dir=src, timeout=setup_timeout)
    if rc:
        if fresh and not dry_run:
            owned = _skills.owned_overlay_skills(dest, scope.shared_skills_dir)
            _remove_tree(dest)
            _skills.unpublish_skills(scope.shared_skills_dir, owned, logger=logger)
            logger.error("setup for overlay %s failed; the install was rolled back", name)
        else:
            logger.error(
                "setup for overlay %s failed; it stays installed but is not set up "
                "(fix it and run `overlays sync %s`)", name, name,
            )
        raise SystemExit("error: setup for overlay %r failed (exit %d)" % (name, rc))
    return skills


def _skills_notice(logger, scope, skills: "list[str]") -> None:
    """Publishing reaches `<scope>/skills/`; an agent that reads its OWN skills
    dir (Claude: `<config>/skills/`) and was never wired by `init` for this
    store gets a new skill linked only by `init`."""
    if skills:
        logger.info(
            "skill(s) %s published to %s; re-run `dotagents init%s` to link new "
            "skills into agents' own skills dirs (Claude's <config>/skills)",
            ", ".join(skills), scope.shared_skills_dir, " -g" if scope.global_scope else "",
        )


def _link_agent_skills(scope, logger, skills: "list[str]" = ()) -> None:
    """Keep each agent's OWN skills dir in step with ``<scope>/skills/`` after
    an add / sync / remove: every adapter whose config already carries
    dotagents' wiring for this store links new skills, refreshes its copies
    and prunes removed ones (:meth:`Agent.link_skills`). With no wired agent,
    newly published ``skills`` get the "re-run init" notice instead."""
    from dotagents import _agents

    wired = False
    for agent in _agents.get_all_agents():
        agent.scope_level = scope.level
        agent.project_root = scope.project_root
        try:
            if not agent.skills_wired(scope.agents_root):
                continue
        except OSError:
            continue
        wired = True
        agent.link_skills(scope.agents_root, dry_run=False, logger=logger)
    if not wired:
        _skills_notice(logger, scope, list(skills))


def _unmet_requires(overlays) -> "dict[str, list[str]]":
    """``{overlay name: [required names no overlay in the set provides]}``."""
    have = {o.normalized_name for o in overlays}
    unmet: "dict[str, list[str]]" = {}
    for overlay in overlays:
        missing = [d for d in overlay.read_manifest()["requires"] if d not in have]  # type: ignore[union-attr]
        if missing:
            unmet[overlay.name] = missing
    return unmet


class _RepoArgs(DotAgentsArgs):
    """The ``--repo`` field of every command that resolves overlays by name
    (``add``, ``list``, ``show``, ``sync``). Not a command of its own."""

    repo: "list[str]" = []
    ("Overlay repo (repeatable): a directory of overlays, a JSON/TOML/YAML registry "
     "mapping names to sources, or a git `<repo>[@ref][#path]`; consulted before "
     "$AGENTS_OVERLAYS_REPO_<KEY>, $AGENTS_OVERLAYS_REPO and the stores' "
     "dotagents.{json,toml,yaml}. The first repo offering a name wins.")
    ("--repo",)


class OverlayAdd(_RepoArgs):
    """Install overlay(s) by name into a scope, and publish their skills.

    Resolves each ``<name>`` against the source (``--repo``, the env repos,
    the stores' registries), installs what its manifest ``requires`` first,
    copies it into ``<store>/overlays/<name>/`` with a record of the repo it
    came from, and publishes its ``skills/`` from the INSTALLED copy into the
    shared ``<store>/skills/``, linking them into each agent's own skills dir
    that ``init`` already wired for the scope (Claude's ``<config>/skills/``;
    otherwise re-run ``init``). ``--copy`` mirrors skills as real dirs instead
    of symlinks (Windows / no-symlink). The ``AGENTS.md`` managed block is then
    rebuilt from the pristine base over every installed overlay's routing and
    rules, in (priority, name) order. An overlay whose setup fails on a fresh
    install is rolled back; the block is recomposed over whatever is installed
    either way. Adding an overlay already installed makes it match its source
    again, as ``sync`` does."""

    _parsername_ = "add"

    name: "list[str]" = []
    "Overlay name(s) to install (resolved against the source)."
    ("name",)

    copy: bool = False
    "Copy skills into the shared dir instead of symlinking (no-symlink fallback)."
    ("--copy",)

    no_setup: bool = False
    "Skip running an overlay's idempotent `setup` script after install."
    ("--no-setup",)

    setup_timeout: Optional[int] = None
    "Seconds a setup script may run (default: its manifest's setup_timeout, else 300; 0 = no limit)."
    ("--setup-timeout",)

    no_requires: bool = False
    "Do not install the overlays each manifest's `requires` names."
    ("--no-requires",)

    prune: bool = False
    "Re-adding an installed overlay: also clear its .gitignore/.ignore'd files and caches."
    ("--prune",)

    dry_run: bool = False
    "Show what would happen without touching anything."
    ("--dry-run",)

    def __call__(self) -> int:
        from dotagents import _scope

        if not self.name:
            return _usage_error("overlays add needs at least one overlay name")
        names = _validated_names(self.name, "add")

        scope = self.resolve_scope()
        source = _scope.resolve_source(self.repo, scope=scope, logger=self._logger_)
        self._logger_.info("scope: %s (%s)", scope.level, scope.agents_root)

        # Resolve every name (and its requires) against the source BEFORE the
        # first install, so `add good bad` fails on `bad` with nothing touched.
        # What the scope's walk already has satisfies a requirement as it is.
        installed = frozenset(o.normalized_name for o in scope.overlays)
        order = _install_order(
            source, names, follow_requires=not self.no_requires, logger=self._logger_,
            installed=installed,
        )
        located = {name: source.locate(name) for name in order}
        for name in order:
            problem = _source_inside_install_root(scope, name, located[name][0])
            if problem:
                raise SystemExit("error: " + problem)

        skills: "list[str]" = []
        try:
            for name in order:
                src, origin = located[name]
                skills += _install_one(
                    scope, name, src, origin, copy=self.copy, no_setup=self.no_setup,
                    dry_run=self.dry_run, logger=self._logger_, setup_timeout=self.setup_timeout,
                    prune=self.prune,
                )
        finally:
            # Recompose the whole managed block from the pristine base over ALL
            # installed overlays in (priority, name) order (D68) -- also when an
            # install failed part-way, so what did land is merged.
            extra = [
                located[n][0] for n in order if self.dry_run and _installed_dir(scope, n) is None
            ]
            _recompose(scope, self._logger_, dry_run=self.dry_run, extra=extra)
        if not self.dry_run:
            _link_agent_skills(scope, self._logger_, skills)
        if self.dry_run:
            self._logger_.info("dry-run: no files were written")
        return 0


class OverlayRemove(DotAgentsArgs):
    """Remove installed overlays, unpublish their skills, recompose AGENTS.md.

    Each overlay dir is deleted, its skills unpublished (and removed from each
    agent's own skills dir ``init`` wired), and ``AGENTS.md``'s managed block
    recomposed over what remains.

    Deletes only ``<store>/overlays/<name>/`` (a symlinked or
    junctioned overlay is unlinked, its target untouched) and unpublishes only
    the skills that overlay published (matched to its own ``skills/``, by
    content) -- and only once the delete succeeded. Refuses an overlay another
    installed overlay ``requires`` (and no other store provides) unless
    ``--force``. The block is recomposed over the overlays still installed,
    even when a removal fails part-way."""

    _parsername_ = "remove"

    name: "list[str]" = []
    "Overlay name(s) to remove."
    ("name",)

    force: bool = False
    "Remove even when another installed overlay requires it."
    ("--force",)

    dry_run: bool = False
    "Show what would happen without touching anything."
    ("--dry-run",)

    def __call__(self) -> int:
        from dotagents import _overlays, _skills

        if not self.name:
            return _usage_error("overlays remove needs at least one overlay name")
        names = _validated_names(self.name, "remove")
        scope = self.resolve_scope()
        self._logger_.info("scope: %s (%s)", scope.level, scope.agents_root)

        targets = []
        for name in names:
            overlay_dir = _installed_dir(scope, name)
            if overlay_dir is None:
                self._logger_.warning(
                    "overlay %r not installed at %s", name, scope.overlay_dir(name)
                )
                continue
            targets.append((name, overlay_dir))
        removing = frozenset(name for name, _ in targets)

        if removing and not self.force:
            # What the scope's stores still provide once these are gone: a
            # requirement another store satisfies is not broken by the removal.
            after = [
                o for store in scope.stores
                for o in _overlays.Overlay.discover(store / "overlays")
                if not (store == scope.agents_root and o.normalized_name in removing)
            ]
            unmet = _unmet_requires(after)
            dependents = sorted(
                "%s (required by %s)" % (dep, ", ".join(sorted(
                    who for who, missing in unmet.items() if dep in missing
                )))
                for dep in removing if any(dep in m for m in unmet.values())
            )
            if dependents:
                raise SystemExit(
                    "error: not removing %s; pass --force to remove anyway"
                    % "; ".join(dependents)
                )

        removed_names: "set[str]" = set()
        try:
            for name, overlay_dir in targets:
                if self.dry_run:
                    removed_names.add(name)
                    self._logger_.info("removed overlay %s (%s) [dry-run]", name, overlay_dir)
                    continue
                # Which published skills are this overlay's is decided while it
                # still exists; they are unpublished only once it is gone, so a
                # failed delete leaves the overlay and its skills as they were.
                owned = _skills.owned_overlay_skills(
                    overlay_dir, scope.shared_skills_dir, logger=self._logger_
                )
                _remove_tree(overlay_dir)
                removed_names.add(name)
                unpublished = _skills.unpublish_skills(
                    scope.shared_skills_dir, owned, logger=self._logger_
                )
                if unpublished:
                    self._logger_.info("unpublished %d skill(s) from %s", unpublished, name)
                self._logger_.info("removed overlay %s (%s)", name, overlay_dir)
        finally:
            if targets:
                _recompose(
                    scope, self._logger_, dry_run=self.dry_run,
                    without=frozenset(removed_names) if self.dry_run else frozenset(),
                )
            if removed_names and not self.dry_run:
                # Prune what the removed skills left in agents' own skills dirs.
                _link_agent_skills(scope, self._logger_)

        if self.dry_run:
            self._logger_.info("dry-run: no files were written")
        return 0


class OverlayList(_RepoArgs):
    """List the overlays installed in the scope and those available from source.

    In the project scope the user store's are listed too -- both are in play
    for a project session; a same-named project overlay shadows the store's
    copy.

    ``installed`` is discovered by presence under ``<store>/overlays/``; no
    registry file. An installed overlay whose ``requires`` no installed overlay
    provides is flagged. ``available`` is what the source offers (``*`` =
    installed in either scope; a repo that cannot be loaded is a warning, the
    others are still listed). ``--json`` emits everything as a machine-readable
    object."""

    _parsername_ = "list"

    json: bool = False
    "Emit JSON instead of plain text."
    ("--json",)

    def __call__(self) -> int:
        import json as _json

        from dotagents import _scope
        from dotagents._overlays import Overlay
        from dotagents._sources import SourceError

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
        unmet = _unmet_requires(active)

        def _broken(spec, exc) -> None:
            self._logger_.warning("repo %s not listed: %s", _redacted(spec), exc)

        try:
            available = _scope.resolve_source(
                self.repo, scope=scope, logger=self._logger_,
            ).available(on_error=_broken)
        except SourceError as exc:  # nothing configured at all
            self._logger_.warning("%s", exc)
            available = []
        names = {o.normalized_name for o in active}

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
                "unmet_requires": unmet,
                "available": available,
            }
            _write_stdout(_json.dumps(payload, indent=2) + "\n")
            return 0

        self._logger_.info("scope: %s (%s)", scope.level, scope.overlay_root)
        lines = []
        for level, store, names_, shadowed_ in listing:
            lines.append("installed (%s):" % level)
            lines += [
                "  %s%s" % (n, "  (requires missing: %s)" % ", ".join(unmet[n]) if n in unmet else "")
                for n in names_
            ]
            lines += ["  %s  (shadowed by a more specific store's)" % n for n in shadowed_]
            if not names_ and not shadowed_:
                lines.append("  (none)")
        lines.append("available (source):")
        lines += [
            "  %s%s" % (n, " *" if Overlay.normalize_name(n) in names else "") for n in available
        ] or ["  (none)"]
        _write_stdout("\n".join(lines) + "\n")
        return 0


class OverlayShow(_RepoArgs):
    """Describe one overlay: where it is, its manifest, skills and file count.

    The manifest part is what it declares (description, priority, requires,
    routing, rules) and its setup script; for an installed overlay, also the
    repo it was installed from and any ``requires`` no installed overlay
    provides.

    Looks at the INSTALLED copy in the scope first (in the project scope, then
    the user store's -- the one a project session would use), else the source's."""

    _parsername_ = "show"

    name: str = ""
    "Overlay name."
    ("name",)

    json: bool = False
    "Emit JSON instead of plain text."
    ("--json",)

    def __call__(self) -> int:
        import json as _json

        from dotagents import _overlays, _scope
        from dotagents._sources import record_display

        if not self.name:
            return _usage_error("overlays show needs an overlay name")
        (name,) = _validated_names([self.name], "show")
        scope = self.resolve_scope()
        # The copy a session in this scope would use (the most specific store's),
        # else the source's.
        installed = scope.overlays
        active = [o for o in installed if o.normalized_name == name]
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
        record = overlay.read_install_record() if active else {}
        have = {o.normalized_name for o in installed}
        info = {
            "name": overlay.name,
            "where": where,
            "path": str(path),
            "root_var": overlay.root_var,
            "description": manifest["description"],
            "priority": manifest["priority"],
            "requires": manifest["requires"],
            "unmet_requires": [d for d in manifest["requires"] if d not in have]  # type: ignore[union-attr]
            if active else [],
            "installed_from": record_display(record["source"]) if record.get("source") else None,
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
            "  requires:    %s%s" % (
                ", ".join(info["requires"]) or "-",  # type: ignore[arg-type]
                "  (missing: %s)" % ", ".join(info["unmet_requires"])  # type: ignore[arg-type]
                if info["unmet_requires"] else "",
            ),
            "  rules files: %s" % (", ".join(info["rules"]) or "-"),  # type: ignore[arg-type]
            "  routing:     %d line(s)" % len(info["routing"]),  # type: ignore[arg-type]
            "  setup:       %s" % (info["setup"] or "-"),
            "  skills:      %s" % (", ".join(skills) or "-"),
            "  files:       %d" % info["files"],
        ]
        if info["installed_from"]:
            lines.append("  from:        %s" % info["installed_from"])
        _write_stdout("\n".join(lines) + "\n")
        return 0


class OverlaySync(_RepoArgs):
    """Refresh installed overlays from source, and resync their skills.

    Each installed overlay is fetched again from the repo it was INSTALLED
    from (its install record) -- not from whichever repo now offers the name
    first -- unless ``--repo`` is given, which replaces each recorded source:
    the overlay resolves through that chain instead. The install is made to
    match the source exactly: new and changed files land, ``overlay.toml`` is
    refreshed, and files the source does not ship are removed -- a file edited
    here is backed up first (``<store>/install_backup/``). What the overlay's
    ``.gitignore`` / ``.ignore`` files match (setup output, local state) and
    tool caches are left alone; ``--prune`` clears them too. A ``requires``
    added upstream is installed. The managed
    block is recomposed over every installed overlay. An optional ``<glob>``
    filters which installed overlays to sync (``sync 'py*'``). A repo that
    cannot be loaded fails the sync (after the others are synced); a repo
    that no longer offers an overlay is a warning."""

    _parsername_ = "sync"

    pattern: Optional[str] = None
    "Glob over installed overlay names to sync (default: all)."
    ("pattern",)

    copy: bool = False
    "Copy skills into the shared dir instead of symlinking (no-symlink fallback)."
    ("--copy",)

    prune: bool = False
    "Also clear the overlay's .gitignore/.ignore'd files and caches: exactly the upstream files."
    ("--prune",)

    no_setup: bool = False
    "Skip running each overlay's idempotent `setup` script after sync."
    ("--no-setup",)

    setup_timeout: Optional[int] = None
    "Seconds a setup script may run (default: its manifest's setup_timeout, else 300; 0 = no limit)."
    ("--setup-timeout",)

    dry_run: bool = False
    "Show what would happen without touching anything."
    ("--dry-run",)

    def _resolve(self, chain, overlay) -> "tuple[Optional[Path], object]":
        """``(source dir, repo spec)`` for an installed ``overlay``: its
        recorded repo unless ``--repo`` was given (then the chain). ``(None,
        None)`` when it cannot be synced (a warning says why). A repo that
        cannot be loaded raises SourceError."""
        from dotagents._sources import (
            NO_SOURCE_MESSAGE, OverlayNotFound, SourceError, record_display, spec_from_record,
        )

        name = overlay.name
        recorded = overlay.read_install_record().get("source")
        repo_spec = None
        if recorded and not self.repo:
            repo_spec = chain.find_repo(recorded)
            if repo_spec is None:
                if recorded.get("lossy"):
                    self._logger_.warning(
                        "overlay %s was installed from %s, which is not configured now "
                        "and whose credentials are not recorded; pass --repo to sync it",
                        name, record_display(recorded),
                    )
                    return None, None
                repo_spec = spec_from_record(recorded)
        if repo_spec is None and not chain.specs:
            raise SourceError(NO_SOURCE_MESSAGE)  # no record to fall back on, and no repo
        try:
            if repo_spec is not None:
                src, origin = chain.only(repo_spec).locate(name)
            else:
                src, origin = chain.locate(name)
        except OverlayNotFound:
            where = record_display(recorded) if repo_spec is not None else "source"
            self._logger_.warning("overlay %r not in %s; skipping", name, where)
            return None, None
        if recorded and self.repo:
            from dotagents._sources import source_record

            now = source_record(origin)
            if record_display(now) != record_display(recorded):
                self._logger_.info(
                    "overlay %s: installed from %s, now from %s",
                    name, record_display(recorded), record_display(now),
                )
        return src, origin

    def __call__(self) -> int:
        from dotagents import _overlays, _scope, _skills
        from dotagents._sources import SourceError, source_record

        scope = self.resolve_scope()
        chain = _scope.resolve_source(
            self.repo, scope=scope, logger=self._logger_, allow_empty=True,
        )
        installed = _overlays.Overlay.discover(scope.overlay_root)
        wanted = set(_scope.filter_names([o.name for o in installed], self.pattern))
        targets = [o for o in installed if o.name in wanted]
        if not targets:
            self._logger_.info(
                "no installed overlays%s to sync",
                "" if self.pattern in (None, "*") else " matching %r" % self.pattern,
            )
            return 0
        self._logger_.info("scope: %s (%s)", scope.level, scope.agents_root)

        failures: "list[str]" = []
        skills: "list[str]" = []
        synced_sources: "dict[str, Path]" = {}
        try:
            for overlay in targets:
                name, dest_dir = overlay.name, overlay.path
                try:
                    overlay_src, origin = self._resolve(chain, overlay)
                except SourceError as exc:
                    self._logger_.error("overlay %s: %s", name, exc)
                    failures.append(name)
                    continue
                if overlay_src is None:
                    continue
                problem = _source_inside_install_root(scope, name, overlay_src)
                if problem:
                    self._logger_.error("%s", problem)
                    failures.append(name)
                    continue
                result = _overlays.Overlay(overlay_src).install_to(
                    dest_dir, self.dry_run, prune=self.prune,
                    backup_root=_backup_root(scope, name), source=source_record(origin),
                )
                for line in result.lines:
                    if not line.startswith("skip"):
                        self._logger_.info("sync: %s", line)
                _log_install(self._logger_, "written", name, result, self.dry_run)
                synced_sources[overlay.normalized_name] = overlay_src
                if not self.dry_run:
                    _skills.resync_overlay_skills(
                        dest_dir, scope.shared_skills_dir, copy=self.copy,
                        overwrite=True, logger=self._logger_,  # a published copy matches too
                    )
                rc = _run_setup(scope, dest_dir, name, no_setup=self.no_setup,
                                dry_run=self.dry_run, logger=self._logger_,
                                source_dir=overlay_src, timeout=self.setup_timeout)
                if rc:
                    self._logger_.error("setup for overlay %s failed (exit %d)", name, rc)
                    failures.append(name)

            # A `requires` added upstream: install what no store provides yet.
            have = frozenset(o.normalized_name for o in scope.overlays)
            wanted_deps: "list[str]" = []
            for norm, src in synced_sources.items():
                manifest_dir = src if self.dry_run else _installed_dir(scope, norm) or src
                for dep in _overlays.Overlay(manifest_dir).read_manifest()["requires"]:  # type: ignore[union-attr]
                    if dep not in have and dep not in wanted_deps:
                        wanted_deps.append(dep)
            if wanted_deps:
                self._logger_.info("installing newly required overlay(s): %s", ", ".join(wanted_deps))
                order = _install_order(
                    chain, wanted_deps, follow_requires=True, logger=self._logger_, installed=have,
                )
                for dep in order:
                    src, origin = chain.locate(dep)
                    problem = _source_inside_install_root(scope, dep, src)
                    if problem:
                        self._logger_.error("%s", problem)
                        failures.append(dep)
                        continue
                    skills += _install_one(
                        scope, dep, src, origin, copy=self.copy, no_setup=self.no_setup,
                        dry_run=self.dry_run, logger=self._logger_, setup_timeout=self.setup_timeout,
                    )
        finally:
            # Recompose over ALL installed overlays in (priority, name) order
            # (D68) -- not just the pattern-matched subset synced above.
            _recompose(scope, self._logger_, dry_run=self.dry_run)
        if not self.dry_run:
            _link_agent_skills(scope, self._logger_, skills)
        if self.dry_run:
            self._logger_.info("dry-run: no files were written")
        if failures:
            raise SystemExit("error: sync failed for: %s" % ", ".join(failures))
        return 0


class Overlays(LoggingArgs, Cli):
    """Manage opt-in overlays by name: add / remove / list / sync / show (+ skills sync)."""

    _parsername_ = "overlays"
    _subcommands_ = [OverlayAdd, OverlayRemove, OverlayList, OverlaySync, OverlayShow]
