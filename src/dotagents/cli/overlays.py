"""`dotagents overlays` -- add / remove / list / sync / show / hooks, with skills sync.

The logic lives in `_scope.py` / `_sources.py` / `_skills.py` / `_overlays.py`;
`cli/__init__.py` lists the `Overlays` umbrella in `_BUILTIN_COMMANDS`.
Discover-not-track: installed overlays are the dirs under `<store>/overlays/`.
Each has an install record beside it (`Overlay.install_record_path`): the
repo and commit it came from, which `sync` resolves it from again, its tree
digest, and the digest of every file installed, which lets `sync` tell a hand
edit from a file the source dropped.
"""

import os
import shutil
import stat
import sys
import time
from pathlib import Path
from typing import Optional

from duho import Cli, LoggingArgs

from dotagents._vendor import coqui_aos

from dotagents._resources import base_agents_text
from dotagents.cli._common import DotAgentsArgs, _write_stdout, store_base


def _redacted(spec) -> str:
    """A repo spec (text or parsed) fit for a log line."""
    from dotagents._sources import Spec, redact

    return spec.display() if isinstance(spec, Spec) else redact(str(spec))


_GLOB_CHARS = frozenset("*?[")


def _is_glob(raw: str) -> bool:
    return any(c in _GLOB_CHARS for c in raw)


def _expand_globs(raw_names, source) -> "list[str]":
    """``raw_names`` with each glob (``*``, ``py*``) replaced by the names the
    source offers that match it, in the order the repos offer them. A glob
    matching nothing is an error: ``add 'pyhton*'`` should not quietly do
    nothing."""
    from dotagents import _scope
    from dotagents._overlays import Overlay

    offered = [Overlay.normalize_name(n) for n in source.available()]
    out: "list[str]" = []
    for raw in raw_names:
        if not _is_glob(raw):
            out.append(raw)
            continue
        matched = _scope.filter_names(offered, raw.lower())
        if not matched:
            raise SystemExit(
                "error: no overlay offered by %s matches %r (offered: %s)"
                % (source.root, raw, ", ".join(offered) or "none")
            )
        out += matched
    return out


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
        # A process that just ended (a setup stopped at its timeout) can hold
        # its working directory for a moment while Windows tears it down.
        for delay in (0, 0.05, 0.1, 0.2, 0.4, 0.8, 1.6):
            time.sleep(delay)
            try:
                func(p)
                return
            except PermissionError:
                continue
        func(p)

    if sys.version_info >= (3, 12):
        shutil.rmtree(str(path), onexc=_retry)
    else:  # pragma: no cover -- exercised on the 3.9 leg
        shutil.rmtree(str(path), onerror=_retry)


def _prepare(scope, dest_dir: Path, name: str, *, change: str, no_setup=False, dry_run, logger,
             source_dir=None, timeout: "Optional[int]" = None) -> int:
    """Prepare an installed overlay in the phase ``change`` (``add``,
    ``sync`` or ``remove``) calls for (:func:`coqui_aos.setup_phase`):
    ``install`` when no preparation succeeded here (its record on this OS),
    ``sync`` on every add or sync after one, ``remove`` before the overlay is
    deleted after one. An overlay with no dependency file and no setup on
    this OS has nothing to prepare.

    ``install`` empties the state directory, then installs the dependencies,
    runs setup and records the digest and phase; ``sync`` does the same but
    the emptying; ``remove`` runs setup only (the caller deletes the rest and
    rebuilds the environments). A preparation stops at the first step that
    fails, and nothing is recorded: the overlay is then activated only for
    the digest it was last prepared for (:func:`dotagents._activate.refusal`).
    ``--no-setup`` prepares nothing.

    Dependencies are built into their environments (:mod:`dotagents._deps`),
    every member's file at once. Setup runs outside any agent session, in the
    overlay's own process environment (:func:`dotagents._env.overlay_process_env`)
    plus ``AOS_OVERLAY_ROOT`` / ``AOS_OVERLAY_NAME`` / ``AOS_OVERLAY_STATE``
    (its state directory, where it writes), ``AOS_SETUP_PHASE``, and
    ``AGENTS_HOME`` / ``AGENTS_SCOPE_ROOT`` / ``AGENTS_SCOPE``. The
    weakenings (``deps-network``, ``unlocked-dependencies``,
    ``setup-network``, ``network-not-enforced``: dotagents cannot deny a
    process the network, ``no-timeout``) are reported first. On a
    `--dry-run` the installed dir may not exist yet, so the SOURCE overlay
    (`source_dir`) is inspected. Returns the exit status, 0 when there is
    nothing to do."""
    from dotagents import _activate, _aos, _deps, _env
    from dotagents._overlays import Overlay

    overlay = Overlay(dest_dir, scope.agents_root)
    if dry_run and source_dir is not None and not overlay.path.is_dir():
        overlay = Overlay(source_dir, scope.agents_root)
    variant = overlay.setup_variant()
    try:
        dependencies = _deps.dependencies(overlay)
    except _aos.Refused as refused:
        logger.error("%s: %s %s (hash every requirement or none)", name, refused.code, refused.subject)
        return 1
    if variant is None and change != "remove":
        # A default setup.<ext> present that this machine cannot run.
        plan = _aos.plan(overlay.document(), _activate.context(overlay), pinned=True)
        _activate.report(logger, [(name, ident, subject) for ident, subject in plan["weakenings"]
                                  if ident == "script-not-run"])
    if variant is None and not dependencies:
        return 0
    if no_setup:
        logger.info("not preparing %s (--no-setup)%s", name, "" if change == "remove" else
                    ": it is not activated until a sync prepares it")
        return 0
    record = overlay.read_preparation()
    recorded = record["digest"] if record else None
    try:
        digest = recorded if change == "remove" else overlay.tree_digest()
    except coqui_aos.Error as exc:
        logger.error("%s: the overlay has no tree digest (%s)", name, exc)
        return 1
    found = coqui_aos.setup_phase(coqui_aos.Change(change), recorded, digest)
    phase = None if found is None else found.value
    if phase is None:
        return 0
    plan = _aos.plan(overlay.document(), _activate.context(overlay), pinned=True)
    _activate.report(logger, [(name, ident, subject) for ident, subject in plan["weakenings"]])
    keys = [_aos.environment_key(d, overlay.normalized_name) for d in dependencies]
    logger.info("preparing %s: %s", name, phase)
    if phase == "install":
        state = overlay.state_dir()
        if state.is_dir():
            # Whatever an earlier attempt left: install starts from an empty directory.
            logger.info("%s: emptying its state directory%s", name, " [dry-run]" if dry_run else "")
            if not dry_run:
                _remove_tree(state)
    if phase != "remove":
        for key in keys:
            try:
                blocked = _deps.rebuild(scope, overlay.store, key, dry_run=dry_run, logger=logger)
            except _deps.DependencyError as exc:
                logger.error("%s: its %s dependencies are not installed: %s", name, key[0], exc)
                return 1
            if overlay.normalized_name in blocked:
                logger.error("%s: its %s dependencies conflict with another overlay's (see above)", name, key[0])
                return 1
        if not dry_run and any(key[:2] == ("python", "shared") for key in keys):
            _refresh_layers(scope, logger)
    if variant is not None:
        if dry_run:
            return overlay.run_setup(variant, phase=phase, env={}, python=sys.executable,
                                     base_path=os.environ.get("PATH"), dry_run=True, logger=logger,
                                     timeout=timeout)
        env = _env.overlay_process_env(scope, overlay, base_env=dict(os.environ), logger=logger)
        if env is None:
            return 1
        state = overlay.state_dir()
        state.mkdir(parents=True, exist_ok=True)
        env["AOS_OVERLAY_STATE"] = env[_aos.state_variable(overlay.name)] = str(state)
        env["AGENTS_SCOPE_ROOT"] = str(scope.agents_root)
        env["AGENTS_SCOPE"] = scope.level
        rc = overlay.run_setup(variant, phase=phase, env=env, python=_env.interpreter(env),
                               base_path=os.environ.get("PATH"), dry_run=False, logger=logger, timeout=timeout)
        if rc:
            return rc
    if phase != "remove" and not dry_run:
        overlay.write_preparation(digest, phase)  # type: ignore[arg-type]
    return 0


def _rebuild_without(scope, store: Path, names: "set[str]", keys, *, dry_run: bool, logger) -> None:
    """After removing ``names``: build every environment they were in again
    from the overlays left (an isolated one, or one left empty, is deleted).
    A failure is reported, and the environment stays as it was."""
    from dotagents import _deps

    shared_python = False
    for key in keys:
        try:
            _deps.rebuild(scope, store, key, exclude=names, dry_run=dry_run, logger=logger)
        except _deps.DependencyError as exc:
            logger.error("the %s:%s environment was not rebuilt without %s (it is kept as it was; the next "
                         "add or sync rebuilds it): %s", key[0], key[1], ", ".join(sorted(names)), exc)
        shared_python = shared_python or key[:2] == ("python", "shared")
    if shared_python and not dry_run:
        _refresh_layers(scope, logger)


def _refresh_layers(scope, logger) -> None:
    """Point the venvs' ``.pth`` layering at the shared Python environments' current builds."""
    from dotagents import _pyvenv

    try:
        _pyvenv.write_layers(scope)
    except OSError as exc:
        logger.error("the venv layering was not rewritten: %s", exc)


def _probe(overlay, logger) -> "list[str]":
    """What this machine lacks for ``overlay`` (AOS runtime probing): its OS
    in ``runtime.os``, each command, each interpreter its setup, env scripts
    and scripts name, the Python and Node versions; and each script
    dotagents cannot launch. Empty when it can be placed here."""
    import shutil

    from dotagents import _activate, _aos, _run

    doc = overlay.document()
    ctx = _activate.context(overlay)
    scripts = [s for s in doc.get("env_scripts", []) if _aos.os_matches(s, ctx.os)] \
        if ctx.env_scripts_enabled else []
    try:
        plan = _aos.plan(doc, ctx, pinned=True, scripts=scripts)
    except _aos.Refused as refused:
        return ["%s %s: hash every requirement or none" % (refused.code, refused.subject)]
    problems = []
    if not plan["supported"]:
        problems.append("it does not support %s (runtime.os)" % ctx.os)
    base_path = os.environ.get("PATH")
    for command in plan["needs"]["commands"]:
        if shutil.which(command, path=base_path) is None:
            problems.append("command %s is not on PATH" % command)
    python = os.environ.get("AGENTS_PYTHON") or sys.executable
    for name in plan["needs"]["interpreters"]:
        try:
            argv = _run.interpreter_command(name, python=python, base_path=base_path)
        except _run.InterpreterError as exc:
            problems.append(str(exc))
            continue
        variant = plan.get(name) if name in ("python", "node") else None
        if variant and variant.get("version"):
            if name == "python":
                # The scope venv is made from the interpreter running dotagents.
                have = "%d.%d.%d" % sys.version_info[:3]
            else:
                have = _run.version_of(name, argv)
            if have is None or _aos.version_tuple(have) < _aos.version_tuple(variant["version"]):
                problems.append("%s %s or later is needed (found %s)" % (name, variant["version"], have or "none"))
    for script, interpreter in sorted(overlay.unsupported_scripts().items()):
        problems.append("script %s runs under %s; dotagents launches python scripts only" % (script, interpreter))
    return problems


def _check_source(scope, name: str, src: Path, cache, logger) -> "tuple[Optional[str], Optional[str]]":
    """Refuse a source before anything of it is installed: a manifest that
    does not validate (named files included), a tree with no digest, a git
    commit holding a link or a submodule, a machine that lacks what it
    needs, a digest that is not the store's pin. Returns ``(tree digest,
    commit)``; raises SystemExit naming the reason."""
    from dotagents import _aos, _policy
    from dotagents._overlays import Overlay

    overlay = Overlay(src)
    problems = list(overlay.manifest_problems())
    try:
        members = overlay.members()
        digest = overlay.tree_digest()
    except coqui_aos.Error as exc:
        raise SystemExit("error: overlay %s has no tree digest: %s" % (name, exc))
    if not problems:
        problems = ["%s at %s" % (code, at) for code, at in coqui_aos.validate(overlay.document(), coqui_aos.OverlayTree(members))]
    if problems:
        raise SystemExit("error: overlay %s: its manifest is invalid: %s" % (name, "; ".join(problems)))
    commit = None
    checkout = _git_checkout(src, cache)
    if checkout is not None:
        rel = src.resolve().relative_to(checkout.resolve()).as_posix()
        special = cache.special_entries(checkout, None if rel == "." else rel)
        if special:
            raise SystemExit("error: overlay %s holds a link or submodule: %s" % (name, ", ".join(special)))
        commit = cache.commit(checkout)
    problems = _probe(Overlay(src, scope.agents_root), logger)
    if problems:
        raise SystemExit("error: overlay %s cannot be placed here: %s" % (name, "; ".join(problems)))
    try:
        pin = _policy.for_store(scope.agents_root).pin(name)
    except _policy.PolicyError as exc:
        raise SystemExit(exc.code)
    if pin is not None and not _aos.same_digest(digest, pin):
        raise SystemExit("error: overlay %s is refused: its tree digest %s is not its pin %s"
                         % (name, digest, pin))
    return digest, commit


def _git_checkout(src: Path, cache) -> "Optional[Path]":
    """The dotagents cache checkout ``src`` lies in, or None."""
    if cache is None:
        return None
    try:
        root = Path(cache.root).resolve()
        here = src.resolve()
    except OSError:
        return None
    for candidate in [here] + list(here.parents):
        if candidate == root or root not in candidate.parents:
            return None
        if (candidate / ".git").exists():
            return candidate
    return None
def _sync_venvs(scope, *, no_venv: bool, dry_run: bool, logger) -> bool:
    """Bring the scope's Python venvs up to date (see `dotagents._pyvenv`):
    every installed overlay's ``requirements.txt`` installed, the ``.pth``
    layering rewritten. False, with the reason logged, when that failed."""
    if no_venv:
        return True
    from dotagents import _pyvenv

    try:
        _pyvenv.sync_venvs(scope, dry_run=dry_run, logger=logger)
    except SystemExit as exc:  # VenvError, PipUnavailable: the message is the code
        logger.error("%s", exc.code)
        return False
    return True


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


def _record_hooks(scope, dest: Path, name: str, *, fresh: bool, no_hooks: bool, dry_run: bool, logger) -> None:
    """Installing or syncing an overlay allows its hooks at the digest installed (the person chose
    this source, and the overlay declares them), and says which are on. ``--no-hooks`` installs
    without allowing and remembers it; an overlay whose hooks were revoked stays off across syncs;
    a request through the MCP tool allows like any other, unless the server was started `--strict`
    (or the user's own setting says so), when it installs without allowing."""
    from dotagents import _overlay_hooks
    from dotagents._overlays import Overlay

    overlay = Overlay(dest if dest.is_dir() else dest, scope.agents_root)
    outcome, entries = _overlay_hooks.record_install(scope.user_root, overlay, fresh=fresh, no_hooks=no_hooks,
                                                     dry_run=dry_run)
    if outcome == "none":
        return
    listed = ", ".join("%s (%s)" % (e["event"], e["path"] or "no hook.<ext> can run here") for e in entries)
    if outcome == "on":
        logger.info("overlay %s: %s its hooks, now on: %s (dotagents overlays hooks %s; revoke-hooks turns them off)",
                    name, "would allow" if dry_run else "allowed", listed, name)
    else:
        why = {"declined": "--no-hooks", "revoked": "they were revoked and a sync keeps them off",
               "strict": "installed through `dotagents mcp --strict`, which allows no hooks"}[outcome]
        logger.warning("overlay %s: its hooks are off (%s): %s -- allow them with `dotagents overlays "
                       "allow-hooks %s`", name, why, listed, name)


def _install_one(scope, name: str, src: Path, origin, *, copy: bool, no_setup: bool,
                 dry_run: bool, logger, setup_timeout: "Optional[int]" = None,
                 no_venv: bool = False, cache=None, no_hooks: bool = False) -> "list[str]":
    """Install (or re-install) overlay ``name`` from ``src`` into the scope,
    publish its skills, install its Python dependencies into the scope's venv
    and run its setup (``install`` the first time, then ``sync``). Nothing is written for a source
    :func:`_check_source` refuses (an invalid manifest, a tree with no
    digest, a machine lacking what it needs, a pin it does not match). A
    FRESH install is copied into a staging dir that discovery ignores and
    moved into place whole, and is rolled back (skills unpublished, dir
    removed) when its dependencies or setup fail -- never left installed but
    unconfigured. An EXISTING install is made to match the source (see
    `Overlay.install_to`), local edits backed up first. Raises on setup
    failure. Returns the skills the overlay ships."""
    from dotagents import _activate, _aos, _overlays, _policy, _skills
    from dotagents._sources import record_display, source_record

    if not _overlays.Overlay.is_valid_name(name):  # backstop: never a path outside overlays/
        raise SystemExit("error: %r is not a valid overlay name" % name)
    digest, commit = _check_source(scope, name, src, cache, logger)
    record = source_record(origin) if origin is not None else None
    logger.info("overlay %s: %s%s, %s", name, record_display(record) if record else src,
                " at %s" % commit if commit else "", digest)
    existing = _installed_dir(scope, name)
    dest = existing or scope.overlay_dir(name)
    fresh = existing is None
    overlay = _overlays.Overlay(src)
    if fresh and not dry_run:
        staging = scope.overlay_root / (".staging-%s" % name)
        if os.path.lexists(str(staging)):
            _remove_tree(staging)
        staging.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = overlay.install_to(staging, False, source=record, commit=commit)
            os.replace(str(staging), str(dest))
            # The record was written beside the staging dir.
            os.replace(str(_overlays.Overlay(staging).install_record_path),
                       str(_overlays.Overlay(dest).install_record_path))
        except BaseException:
            if os.path.lexists(str(staging)):
                _remove_tree(staging)
            raise
    else:
        result = overlay.install_to(dest, dry_run, backup_root=_backup_root(scope, name), source=record,
                                    commit=commit)
    for line in result.lines:
        logger.info(line)
    _log_install(logger, "installed", name, result, dry_run)
    pin = _policy.for_store(scope.agents_root).pin(name)
    if pin is not None and not dry_run:
        installed = _overlays.Overlay(dest).tree_digest()
        if not _aos.same_digest(installed, pin):
            raise SystemExit("error: overlay %s: the installed copy's digest %s is not its pin %s"
                             % (name, installed, pin))

    skills = _overlays.Overlay(dest if dest.is_dir() else src).skills()
    if not dry_run:
        # From the INSTALLED copy: a symlink into the source dir dies with a
        # temporary checkout (CI, a pyz-extracted temp dir) and can never be
        # matched by `remove`, which looks at the installed overlay's `skills/`.
        published = _skills.publish_overlay_skills(
            dest, scope.shared_skills_dir, copy=copy, logger=logger,
        )
        if published:
            logger.info("published %d skill(s) from %s", published, name)
    # The venv before the preparation, so setup runs under the scope's interpreter.
    what = "the venv"
    with _activate.preparing(dest):
        rc = 0 if _sync_venvs(scope, no_venv=no_venv, dry_run=dry_run, logger=logger) else 1
        if not rc:
            what = "preparation"
            rc = _prepare(scope, dest, name, change="add", no_setup=no_setup,
                          dry_run=dry_run, logger=logger, source_dir=src, timeout=setup_timeout)
    if rc:
        if fresh and not dry_run:
            from dotagents import _deps

            owned = _skills.owned_overlay_skills(dest, scope.shared_skills_dir)
            keys = _deps.keys_of(_overlays.Overlay(dest, scope.agents_root))
            _remove_tree(dest)
            _overlays.Overlay(dest).forget()
            _rebuild_without(scope, scope.agents_root, {_overlays.Overlay.normalize_name(name)}, keys,
                             dry_run=False, logger=logger)
            _skills.unpublish_skills(scope.shared_skills_dir, owned, logger=logger)
            logger.error("%s for overlay %s failed; the install was rolled back", what, name)
        else:
            logger.error(
                "%s for overlay %s failed; it stays installed and is activated only for the digest it "
                "was last prepared for (fix it and run `overlays sync %s`)", what, name, name,
            )
        raise SystemExit("error: %s for overlay %r failed (exit %d)" % (what, name, rc))
    _record_hooks(scope, dest if dest.is_dir() else src, name, fresh=True, no_hooks=no_hooks, dry_run=dry_run,
                  logger=logger)
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
        agent.wire_overlay_hooks(scope.agents_root, dry_run=False, logger=logger)
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
    the stores' registries); a glob (``'*'``, ``'py*'``) stands for every name
    the source offers that matches it, so ``add '*'`` installs what a store's
    registry names. Installs what each manifest ``requires`` first,
    copies it into ``<store>/overlays/<name>/`` with a record of the repo it
    came from, and publishes its ``skills/`` from the INSTALLED copy into the
    shared ``<store>/skills/``, linking them into each agent's own skills dir
    that ``init`` already wired for the scope (Claude's ``<config>/skills/``;
    otherwise re-run ``init``). ``--copy`` mirrors skills as real dirs instead
    of symlinks (Windows / no-symlink). The ``AGENTS.md`` managed block is then
    rebuilt from the pristine base over every installed overlay's routing and
    rules, in (priority, name) order. An overlay whose manifest does not
    validate, whose tree has no digest, that its store pins to another
    digest, or whose ``runtime`` this machine lacks is refused before
    anything of it is written. Its ``setup`` runs in the ``install`` phase
    the first time, ``sync`` after that. When an overlay's dependencies or setup
    fail, only that overlay is affected: one this command was installing for
    the first time is removed again, one that was already installed stays,
    not activated until its setup succeeds. Every other overlay is still installed, except those that require
    the failed one (directly or not), and the command exits 1 naming every
    overlay that did not install. The block is recomposed over
    whatever is installed either way. Adding an overlay already installed
    makes it match its source again, as ``sync`` does."""

    _parsername_ = "add"

    name: "list[str]" = []
    "Overlay name(s) or glob(s) to install, resolved against the source ('*': every overlay it offers)."
    ("name",)

    copy: bool = False
    "Copy skills into the shared dir instead of symlinking (no-symlink fallback)."
    ("--copy",)

    no_setup: bool = False
    "Skip running an overlay's declared `setup` after install."
    ("--no-setup",)

    no_hooks: bool = False
    "Do not allow the overlay's hooks (installing allows them at the digest installed; `allow-hooks` does it later)."
    ("--no-hooks",)

    setup_timeout: Optional[int] = None
    "Seconds setup may run (default: the setup variant's timeout, else 300; 0 = no limit)."
    ("--setup-timeout",)

    no_requires: bool = False
    "Do not install the overlays each manifest's `requires` names."
    ("--no-requires",)

    no_venv: bool = False
    "Do not create or update the scope's Python venv (overlay requirements, duho, the .pth layering)."
    ("--no-venv",)

    dry_run: bool = False
    "Show what would happen without touching anything."
    ("--dry-run",)

    def __call__(self) -> int:
        from dotagents import _overlays, _scope

        if not self.name:
            return _usage_error("overlays add needs at least one overlay name (or '*')")
        _validated_names([n for n in self.name if not _is_glob(n)], "add")

        scope = self.resolve_scope()
        source = _scope.resolve_source(self.repo, scope=scope, logger=self._logger_)
        self._logger_.info("scope: %s (%s)", scope.level, scope.agents_root)
        names = _validated_names(_expand_globs(self.name, source), "add")

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

        # One overlay failing (its copy, requirements or setup) does not stop
        # the rest: each is tried in dependency order, and only what requires a
        # failed one -- directly or through another -- is skipped.
        requires = {
            name: {
                _overlays.Overlay.normalize_name(str(dep))
                for dep in _overlays.Overlay(located[name][0]).read_manifest()["requires"]  # type: ignore[union-attr]
            }
            for name in order
        }
        failed: "dict[str, str]" = {}  # name -> why, in the order they failed
        skills: "list[str]" = []
        try:
            for name in order:
                blocked = sorted(requires[name] & set(failed))
                if blocked:
                    why = "it requires %s, which failed" % ", ".join(blocked)
                    self._logger_.error("overlay %s not installed: %s", name, why)
                    failed[name] = why
                    continue
                src, origin = located[name]
                try:
                    skills += _install_one(
                        scope, name, src, origin, copy=self.copy, no_setup=self.no_setup,
                        dry_run=self.dry_run, logger=self._logger_,
                        setup_timeout=self.setup_timeout, no_venv=self.no_venv,
                        cache=source.cache, no_hooks=self.no_hooks,
                    )
                except (SystemExit, OSError) as exc:
                    why = str(exc.code if isinstance(exc, SystemExit) else exc)
                    why = why[len("error: "):] if why.startswith("error: ") else why
                    self._logger_.error("overlay %s: %s", name, why)
                    failed[name] = why
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
        if failed:
            raise SystemExit("error: add failed for: %s" % "; ".join(
                "%s (%s)" % (name, why) for name, why in failed.items()
            ))
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
    even when a removal fails part-way.

    Before an overlay is deleted, its setup runs in the ``remove`` phase when
    a setup of it succeeded on this OS; a failure is reported and the
    removal goes ahead, deleting its state and setup record."""

    _parsername_ = "remove"

    name: "list[str]" = []
    "Overlay name(s) to remove."
    ("name",)

    force: bool = False
    "Remove even when another installed overlay requires it."
    ("--force",)

    no_setup: bool = False
    "Do not run an overlay's setup in its `remove` phase."
    ("--no-setup",)

    setup_timeout: Optional[int] = None
    "Seconds setup may run (default: the setup variant's timeout, else 300; 0 = no limit)."
    ("--setup-timeout",)

    no_venv: bool = False
    "Do not create or update the scope's Python venv (overlay requirements, duho, the .pth layering)."
    ("--no-venv",)

    dry_run: bool = False
    "Show what would happen without touching anything."
    ("--dry-run",)

    def __call__(self) -> int:
        from dotagents import _deps, _overlays, _skills

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
                    _prepare(scope, overlay_dir, name, change="remove", no_setup=self.no_setup, dry_run=True,
                             logger=self._logger_, timeout=self.setup_timeout)
                    _rebuild_without(scope, scope.agents_root, {_overlays.Overlay.normalize_name(name)},
                                     _deps.keys_of(_overlays.Overlay(overlay_dir, scope.agents_root)),
                                     dry_run=True, logger=self._logger_)
                    removed_names.add(name)
                    self._logger_.info("removed overlay %s (%s) [dry-run]", name, overlay_dir)
                    continue
                # Which published skills are this overlay's is decided while it
                # still exists; they are unpublished only once it is gone, so a
                # failed delete leaves the overlay and its skills as they were.
                owned = _skills.owned_overlay_skills(
                    overlay_dir, scope.shared_skills_dir, logger=self._logger_
                )
                # Setup's `remove` phase, while its copy and state still exist;
                # a failure is reported and the removal goes ahead.
                keys = _deps.keys_of(_overlays.Overlay(overlay_dir, scope.agents_root))
                rc = _prepare(scope, overlay_dir, name, change="remove", no_setup=self.no_setup,
                              dry_run=False, logger=self._logger_, timeout=self.setup_timeout)
                if rc:
                    self._logger_.error("setup for overlay %s failed in its remove phase (exit %d); "
                                        "removing it anyway", name, rc)
                # Its state directory, preparation record and environments go with it.
                _remove_tree(overlay_dir)
                _overlays.Overlay(overlay_dir).forget()
                removed_names.add(name)
                _rebuild_without(scope, scope.agents_root, {_overlays.Overlay.normalize_name(name)}, keys,
                                 dry_run=False, logger=self._logger_)
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

        # The layering drops the removed overlays' libs.
        if removed_names and not _sync_venvs(
            scope, no_venv=self.no_venv, dry_run=self.dry_run, logger=self._logger_
        ):
            raise SystemExit("error: the scope's Python venv could not be updated")
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
    provides is flagged, and so is one refused because it does not match its pin. ``available`` is what the source offers (``*`` =
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
        active = scope.installed_overlays
        from dotagents import _activate

        refused = {o.name: why for o in active for why in [_activate.refusal(o)] if why}
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
                "refused": refused,
                "available": available,
            }
            _write_stdout(_json.dumps(payload, indent=2) + "\n")
            return 0

        self._logger_.info("scope: %s (%s)", scope.level, scope.overlay_root)
        lines = []
        for level, store, names_, shadowed_ in listing:
            lines.append("installed (%s):" % level)
            lines += [
                "  %s%s%s" % (n, "  (requires missing: %s)" % ", ".join(unmet[n]) if n in unmet else "",
                              "  (REFUSED: %s)" % refused[n] if n in refused else "")
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
    routing, rules, context, environment, path additions, runtime, setup,
    scripts, harness hints) and whether it was refused; the integrity part
    its tree digest, its pin and whether the two agree; then what this
    machine lacks for it and its weakenings (the AOS ids, reported every
    time). For an installed overlay, also the repo and commit it was
    installed from and any ``requires`` no installed overlay provides.

    Looks at the INSTALLED copy in the scope first (in the project scope, then
    the user store's -- the one a session would use), else the source's."""

    _parsername_ = "show"

    name: str = ""
    "Overlay name."
    ("name",)

    json: bool = False
    "Emit JSON instead of plain text."
    ("--json",)

    def __call__(self) -> int:
        import json as _json

        from dotagents import _activate, _aos, _overlays, _policy, _scope
        from dotagents._sources import record_display

        if not self.name:
            return _usage_error("overlays show needs an overlay name")
        (name,) = _validated_names([self.name], "show")
        scope = self.resolve_scope()
        # The copy a session in this scope would use (the most specific store's),
        # else the source's.
        installed = scope.installed_overlays
        found = [o for o in installed if o.normalized_name == name]
        if found:
            overlay = found[0]
            where = "installed (%s)" % scope.store_level(overlay.store)
        else:
            where = "source"
            overlay = _overlays.Overlay(
                _scope.resolve_source(self.repo, scope=scope, logger=self._logger_).overlay_dir(name),
                scope.agents_root)
        path = overlay.path
        manifest = overlay.read_manifest()
        doc = manifest["doc"]
        record = overlay.read_install_record() if found else {}
        have = {o.normalized_name for o in installed}
        digest, digest_problem = _activate.digest_of(overlay)
        try:
            pin = _policy.for_overlay(overlay).pin(overlay.name) if found else None
        except _policy.PolicyError as exc:
            pin = None
            digest_problem = str(exc.code)
        ctx = _activate.context(overlay)
        env_scripts = [s for s in doc.get("env_scripts", []) if _aos.os_matches(s, ctx.os)] \
            if ctx.env_scripts_enabled else []
        try:
            plan = _aos.plan(doc, ctx, pinned=pin is not None, scripts=env_scripts)
        except _aos.Refused:
            # A requirements file only partly hashed: `refused` says so; the rest is shown without it.
            unread = dict(doc, defaults=dict(doc.get("defaults", {}), deps=False))
            plan = _aos.plan(unread, ctx, pinned=pin is not None, scripts=env_scripts)
        weakenings = list(plan["weakenings"])
        activation = _aos.effective(doc, ctx, base_env={}, pinned=pin is not None)
        if activation.error is None:
            weakenings += [w for w in activation.weakenings if w not in weakenings]
        info = {
            "name": overlay.name,
            "where": where,
            "path": str(path),
            "root_var": overlay.root_var,
            "description": manifest["description"],
            "priority": manifest["priority"],
            "requires": manifest["requires"],
            "unmet_requires": [d for d in manifest["requires"] if d not in have] if found else [],
            "installed_from": record_display(record["source"]) if record.get("source") else None,
            "commit": record.get("commit"),
            "manifest_problems": overlay.manifest_problems(),
            "refused": _activate.refusal(overlay) if found else None,
            "digest": digest,
            "digest_problem": digest_problem,
            "pin": pin,
            "routing": manifest["routing"],
            "rules": overlay.rules(),
            "context": overlay.context_files(),
            "context_ignored": overlay.context_ignored(),
            "env": sorted(doc.get("env", {})),
            "env_files": [e if isinstance(e, str) else e["path"] for e in doc.get("env_files", [])],
            "paths": ["%s %s %s" % (e["variable"], e.get("position", "prepend"), e["path"])
                      for e in doc.get("paths", [])],
            "env_scripts": [s["path"] for s in doc.get("env_scripts", [])],
            "env_scripts_enabled": ctx.env_scripts_enabled,
            "setup": plan["setup"],
            "dependencies": plan["dependencies"],
            "prepared": overlay.read_preparation() if found else None,
            "runtime": doc.get("runtime", {}),
            "needs": plan["needs"],
            "missing": _probe(overlay, self._logger_),
            "scripts": overlay.declared_scripts(),
            "harness": doc.get("harness", {}),
            "skills": overlay.skills(),
            "weakenings": [list(w) for w in weakenings],
            "files": len(overlay.members()) if digest else None,
        }
        if self.json:
            _write_stdout(_json.dumps(info, indent=2) + "\n")
            return 0

        def listed(items) -> str:
            return ", ".join(items) or "-"

        setup = info["setup"]
        lines = [
            "%s (%s): %s" % (info["name"], where, info["path"]),
            "  root var:    %s" % info["root_var"],
            "  description: %s" % (info["description"] or "-"),
            "  priority:    %s" % info["priority"],
            "  requires:    %s%s" % (
                listed(info["requires"]),
                "  (missing: %s)" % ", ".join(info["unmet_requires"]) if info["unmet_requires"] else "",
            ),
            "  rules files: %s" % listed(info["rules"]),
            "  routing:     %d line(s)" % len(info["routing"]),
            "  context:     %s%s" % (listed(info["context"]),
                                      "  (%s ignored: the body of OVERLAY.md is the context)"
                                      % ", ".join(info["context_ignored"]) if info["context_ignored"] else ""),
            "  env:         %s" % listed(info["env"]),
            "  env files:   %s" % listed(info["env_files"]),
            "  paths:       %s" % listed(info["paths"]),
            "  env scripts: %s%s" % (listed(info["env_scripts"]), "" if not info["env_scripts"] else
                                     (" (enabled)" if info["env_scripts_enabled"] else " (not enabled)")),
            "  setup:       %s" % ("%s %s" % (setup["interpreter"], setup["path"]) if setup else "-"),
            "  deps:        %s" % listed("%s (%s:%s, %s)" % (d["file"], d["runtime"], d["environment"],
                                                           "locked" if d["lock"] else "unlocked")
                                         for d in info["dependencies"]),
            "  prepared:    %s" % ("%s (%s)" % (info["prepared"]["digest"], info["prepared"]["phase"])
                                   if info["prepared"] else "-"),
            "  needs:       %s" % listed(info["needs"]["interpreters"] + info["needs"]["commands"]),
            "  scripts:     %s" % listed(sorted(info["scripts"])),
            "  skills:      %s" % listed(info["skills"]),
            "  harness:     %s" % listed(sorted(info["harness"])),
            "  digest:      %s" % (digest or info["digest_problem"]),
            "  pin:         %s" % (pin or "- (unpinned)"),
            "  files:       %s" % (info["files"] if info["files"] is not None else "-"),
        ]
        if info["installed_from"]:
            lines.append("  from:        %s%s" % (info["installed_from"],
                                                  " at %s" % info["commit"] if info["commit"] else ""))
        if info["manifest_problems"]:
            lines.append("  INVALID:     %s" % "; ".join(info["manifest_problems"]))
        if info["refused"]:
            lines.append("  REFUSED:     %s" % info["refused"])
        if info["missing"]:
            lines.append("  lacking:     %s" % "; ".join(info["missing"]))
        lines.append("  weakenings:  %s" % listed(
            "%s %s" % (ident, subject) if subject else ident for ident, subject in weakenings))
        _write_stdout("\n".join(lines) + "\n")
        return 0


class OverlaySync(_RepoArgs):
    """Refresh installed overlays from source, and resync their skills.

    Each installed overlay is fetched again from the repo it was INSTALLED
    from (its install record) -- not from whichever repo now offers the name
    first -- unless ``--repo`` is given, which replaces each recorded source:
    the overlay resolves through that chain instead. The install is made to
    match the source exactly: it holds the overlay's members and nothing else
    -- a file edited here is backed up first (``<store>/install_backup/``);
    tool caches and setup output inside it go (setup writes
    into its state directory). The source is checked as ``add`` checks it,
    and setup runs in its ``sync`` phase (``install`` when none succeeded yet). A ``requires``
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

    no_setup: bool = False
    "Skip running each overlay's declared `setup` after sync."
    ("--no-setup",)

    no_hooks: bool = False
    "Do not allow the overlay's hooks (installing allows them at the digest installed; `allow-hooks` does it later)."
    ("--no-hooks",)

    no_venv: bool = False
    "Do not create or update the scope's Python venv (overlay requirements, duho, the .pth layering)."
    ("--no-venv",)

    setup_timeout: Optional[int] = None
    "Seconds setup may run (default: the setup variant's timeout, else 300; 0 = no limit)."
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
        from dotagents import _activate, _overlays, _scope, _skills
        from dotagents._sources import SourceError, source_record

        scope = self.resolve_scope()
        chain = _scope.resolve_source(
            self.repo, scope=scope, logger=self._logger_, allow_empty=True,
        )
        installed = _overlays.Overlay.discover(scope.overlay_root, scope.agents_root)
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
                try:
                    digest, commit = _check_source(scope, name, overlay_src, chain.cache, self._logger_)
                except SystemExit as exc:
                    self._logger_.error("%s", str(exc.code)[len("error: "):] if str(exc.code).startswith(
                        "error: ") else exc.code)
                    failures.append(name)
                    continue
                result = _overlays.Overlay(overlay_src).install_to(
                    dest_dir, self.dry_run, backup_root=_backup_root(scope, name),
                    source=source_record(origin), commit=commit,
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
                with _activate.preparing(dest_dir):
                    if not _sync_venvs(scope, no_venv=self.no_venv, dry_run=self.dry_run,
                                       logger=self._logger_):
                        failures.append(name)
                        continue
                    rc = _prepare(scope, dest_dir, name, change="sync", dry_run=self.dry_run,
                                  logger=self._logger_, no_setup=self.no_setup, source_dir=overlay_src,
                                  timeout=self.setup_timeout)
                if rc:
                    self._logger_.error("preparing overlay %s failed (exit %d); it is activated only for the "
                                        "digest it was last prepared for", name, rc)
                    failures.append(name)
                else:
                    _record_hooks(scope, dest_dir, name, fresh=False, no_hooks=self.no_hooks,
                                  dry_run=self.dry_run, logger=self._logger_)

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
                        no_venv=self.no_venv, cache=chain.cache, no_hooks=self.no_hooks,
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


def _hooks_overlay(self_, name: str):
    """``(scope, overlay)`` for the installed overlay ``name`` a session in this scope would use."""
    (name,) = _validated_names([name], "show hooks of")
    scope = self_.resolve_scope()
    found = [o for o in scope.installed_overlays if o.normalized_name == name]
    if not found:
        raise SystemExit("error: overlay %r is not installed in this scope" % name)
    return scope, found[0]


def _hooks_lines(overlay, plan) -> "list[str]":
    """What an overlay's hooks would do: per event, the script and its settings, and for each
    harness dotagents has hooks for whether the event fires natively, is fired by dotagents
    itself, or cannot fire."""
    from dotagents import _overlay_hooks

    lines = ["overlay %s  digest %s" % (overlay.name, plan["digest"] or "(none)")]
    if not plan["hooks"]:
        lines.append("  no hooks")
        return lines
    lines.append("  hooks are %s" % (
        "allowed at this digest" if plan["on"]
        else "off: not allowed at this digest (dotagents overlays allow-hooks %s)" % overlay.name))
    for entry in plan["hooks"]:
        how = []
        for harness in _overlay_hooks.HARNESSES.values():
            if entry["event"] == "pre-env":
                how.append("%s: fired by dotagents" % harness.id)
            elif entry["event"] in harness.native:
                how.append("%s: fires natively%s%s" % (
                    harness.id,
                    " (some occurrences)" if entry["event"] in _overlay_hooks.partial_events(harness) else "",
                    " once you trust the entry in Codex (dotagents cannot read Codex's trust state)"
                    if harness.id == "codex" else ""))
            else:
                how.append("%s: cannot fire" % harness.id)
        lines.append("  %s: %s (%s)%s%s  timeout %ss  on_failure %s  [%s]" % (
            entry["event"], entry["path"] or "no hook.<ext> can run here", entry["interpreter"] or "-",
            "  match %s" % ",".join(entry["match"]) if entry["match"] else "",
            "  sets %s" % ",".join(entry["sets"]) if entry["sets"] else "", entry["timeout"],
            entry["on_failure"], "; ".join(how)))
    from dotagents import _agents

    for harness, config in ((_overlay_hooks.CLAUDE, _agents.ClaudeAgent._user_config_dir()),
                            (_overlay_hooks.CODEX, _agents.CodexAgent()._config_root())):
        problem = _overlay_hooks.interpreter_problem(config, "user")
        if problem:
            lines.append("  %s: the hook entry will not run: %s (run dotagents init -g)" % (harness.id, problem))
    lines.append("  A hook runs outside the harness's sandbox with your access, and is told what its event "
                 "gives it (a prompt's text, a tool call's input).")
    return lines


class OverlayHooks(DotAgentsArgs):
    """List an installed overlay's hooks and whether they are allowed at its digest.

    Per event: the script and its settings, and on each harness whether dotagents fires it
    through the harness's own hook, fires it itself, or cannot. Without a name, every installed
    overlay that has hooks."""

    _parsername_ = "hooks"

    name: str = ""
    "Overlay name (default: every installed overlay with hooks)."
    ("name",)

    def __call__(self) -> int:
        from dotagents import _overlay_hooks

        scope = self.resolve_scope()
        if self.name:
            overlays = [_hooks_overlay(self, self.name)[1]]
        else:
            overlays = scope.installed_overlays
        out: "list[str]" = []
        for overlay in overlays:
            plan = _overlay_hooks.overlay_plan(overlay, _overlay_hooks.ANYWHERE, scope.user_root)
            if plan["hooks"] or self.name:
                out += _hooks_lines(overlay, plan)
        _write_stdout("\n".join(out) + "\n" if out else "no overlay has hooks\n")
        return 0


def _read_tty(prompt: str) -> "Optional[str]":
    """A line typed at the terminal itself (``/dev/tty``, ``CONIN$``), not standard input: a program
    that drives this one's input cannot answer for the user. None when there is no terminal."""
    try:
        if os.name == "nt":
            out, source = open("CONOUT$", "w"), open("CONIN$", "r")
        else:
            out, source = open("/dev/tty", "w"), open("/dev/tty", "r")
    except OSError:
        return None
    try:
        out.write(prompt)
        out.flush()
        return source.readline().strip()
    except (OSError, ValueError):
        return None
    finally:
        out.close()
        source.close()


def _harness_marker() -> "Optional[str]":
    """The name of the harness whose environment this process runs in, or None: an agent's own
    shell is not where the user gives an allowance."""
    from dotagents import _agents

    agent = _agents.detect_runtime_agent(dict(os.environ))
    if agent is not None:
        return agent.harness_id or agent.name
    return os.environ.get("AGENTS_HARNESS") or None


class OverlayAllowHooks(DotAgentsArgs):
    """Allow an installed overlay's hooks at its current digest.

    The allowance is the user's own (kept in the user store, never in an overlay or a project);
    there is one digest per overlay, so any change to the overlay turns its hooks off until this
    is run again.

    Run it once to see what would be allowed and the digest; run it again with `--digest` set to
    that digest, and confirm at the terminal, to allow them. It refuses to run inside an agent
    harness's session. The allowance is only as strong as the agent's lack of write access to the
    user store: an agent that can write there can write an allowance."""

    _parsername_ = "allow-hooks"
    # An allowance is never an agent's to give.
    _mcp_ = False

    no_prompt: bool = False
    "Do not ask at the terminal (for the user's own setup script, outside any harness)."
    ("--no-prompt",)

    name: str = ""
    "Overlay name."
    ("name",)

    digest: str = ""
    "The digest shown by the first run: the one to allow."
    ("--digest",)

    def __call__(self) -> int:
        from dotagents import _aos, _overlay_hooks

        if not self.name:
            return _usage_error("overlays allow-hooks needs an overlay name")
        scope, overlay = _hooks_overlay(self, self.name)
        plan = _overlay_hooks.overlay_plan(overlay, _overlay_hooks.ANYWHERE, scope.user_root)
        _write_stdout("\n".join(_hooks_lines(overlay, plan)) + "\n")
        if not plan["hooks"]:
            return 0
        if plan["digest"] is None:
            return _usage_error("the overlay's tree has no digest, so its hooks cannot be allowed")
        if not self.digest:
            _write_stdout("not allowed yet: run again with --digest %s to allow them\n" % plan["digest"])
            return 0
        if not _aos.DIGEST_FORM.match(self.digest) or not _aos.same_digest(self.digest, plan["digest"]):
            return _usage_error("--digest is not the overlay's current digest %s" % plan["digest"])
        marker = _harness_marker()
        if marker is not None:
            return _usage_error("this runs inside a %s session; an allowance is the user's to give, from "
                                "their own terminal" % marker)
        if not self.no_prompt:
            typed = _read_tty("type 'allow %s' to allow these hooks: " % overlay.name)
            if typed is None:
                return _usage_error("no terminal to confirm at (use --no-prompt in your own setup script)")
            if typed != "allow %s" % overlay.name:
                return _usage_error("not confirmed")
        _overlay_hooks.allow(scope.user_root, overlay.name, plan["digest"], overlay.store)
        _write_stdout("allowed the hooks of %s at %s\n" % (overlay.name, plan["digest"]))
        return 0


class OverlayRevokeHooks(DotAgentsArgs):
    """Take back the allowance of an overlay's hooks."""

    _parsername_ = "revoke-hooks"

    name: str = ""
    "Overlay name."
    ("name",)

    def __call__(self) -> int:
        from dotagents import _overlay_hooks

        if not self.name:
            return _usage_error("overlays revoke-hooks needs an overlay name")
        scope, overlay = _hooks_overlay(self, self.name)
        name = overlay.name
        if _overlay_hooks.revoke(scope.user_root, name, overlay.store):
            _write_stdout("revoked the hooks of %s\n" % name)
        else:
            _write_stdout("the hooks of %s were not allowed\n" % name)
        return 0


class Overlays(LoggingArgs, Cli):
    """Manage opt-in overlays by name: add / remove / list / sync / show (+ skills sync)."""

    _parsername_ = "overlays"
    _subcommands_ = [OverlayAdd, OverlayRemove, OverlayList, OverlaySync, OverlayShow, OverlayHooks,
                     OverlayAllowHooks, OverlayRevokeHooks]
