"""Shared CLI-only helpers used across the per-command modules.

Pure helpers with no dependency on the command classes or the umbrella, so
command modules can import from here without creating an import cycle
(`cli/__init__.py` imports the command modules, not the reverse). The public
names `_compose_block` and `_package_data_dir` are re-exported from
`dotagents.cli` (see `cli/__init__.py`) because other package modules import
them as `dotagents.cli._compose_block` / `dotagents.cli._package_data_dir`.
"""

import importlib.resources
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from duho import Cmd, LoggingArgs


_extracted_dirs_cache: "dict[str, Path]" = {}

#: The configurable user-scope store (D58). Every reader of the user store
#: resolves it through this var (default `~/.agents`) rather than hardcoding the
#: home path -- this is the same var `dotagents env` emits (D79). Defined HERE
#: rather than in `cli/__init__` so command modules can use it without importing
#: the umbrella (import cycle); `cli/__init__` re-exports both names.
AGENTS_DIR_ENV = "AGENTS_HOME"
#: back-compat: DOTAGENTS_AGENTS_DIR is deprecated, removable next release.
AGENTS_DIR_ENV_LEGACY = "DOTAGENTS_AGENTS_DIR"


def resolve_user_store(agents_dir: "Optional[Path]" = None) -> Path:
    """The USER store root, in precedence order: an explicit ``agents_dir``
    (``--agents-dir``) -> ``$AGENTS_HOME`` -> the legacy
    ``$DOTAGENTS_AGENTS_DIR`` -> ``~/.agents`` (D58/D79/D80).

    Distinct from :meth:`DotAgentsArgs.resolve_scope`, which answers "which scope
    do I install INTO" and returns ``<project>/.agents`` for the project scope.
    Commands that always walk from the user store and merely *include or skip*
    project-level files -- ``env`` and ``context``, whose Contract-A walk takes
    the user store as ``agents_dir`` and the project root separately -- want this
    instead: the store never becomes the project dir, whatever ``-g`` says.

    Never logs or prints the raw env value (Leakage rule); only the resolved path
    is ever reported.
    """
    if agents_dir:
        return Path(agents_dir).expanduser()
    value = os.environ.get(AGENTS_DIR_ENV) or os.environ.get(AGENTS_DIR_ENV_LEGACY)
    if value:
        return Path(value).expanduser()
    return Path.home() / ".agents"


class DotAgentsArgs(LoggingArgs, Cmd):
    """Shared ``-g/--global`` + ``--agents-dir`` fields for any command whose scope
    is *where the store lives* (``_scope.resolve_scope``'s two axes) -- ``init``,
    ``overlays add/remove/sync``, ``link-project``, and overlay-shipped commands
    (e.g. the ``python`` overlay's ``pyvenv``) all redeclare this same pair today;
    new commands should inherit this instead of copying the fields again.

    NOT retrofitted onto the existing duplicated commands -- each already ships
    with slightly different field defaults/help text tuned to its own command
    (``overlays add``'s ``agents_dir`` defaults eagerly to
    ``Path.home() / ".agents"``, where ``resolve_scope`` itself treats ``None`` as
    "unset, use the default") -- collapsing that difference is a separate, wider
    change, not a side effect of adding this base. Mix in as the FIRST base so a
    subclass's own ``_parsername_``/``__call__`` still take precedence:
    ``class Foo(DotAgentsArgs): ...`` -- already inherits ``LoggingArgs``/``Cmd``
    transitively, do not also list them.

    Available both as ``dotagents.cli._common.DotAgentsArgs`` and re-exported at
    ``dotagents.cli.DotAgentsArgs`` -- an overlay-shipped command module (which
    always runs inside a real ``dotagents`` process, so ``dotagents.cli`` is
    always importable there) should use the top-level import."""

    global_scope: bool = False
    "Use the user scope (~/.agents) instead of the project scope."
    ("--global", "-g")

    agents_dir: "Optional[Path]" = None
    "Store root override (default: ~/.agents for -g, else <project>/.agents)."
    ("--agents-dir",)

    def resolve_scope(self, *, project_root: "str | os.PathLike | None" = None):
        """This command's resolved ``Scope``, from the two fields above."""
        from dotagents._scope import resolve_scope

        return resolve_scope(
            self.global_scope, agents_dir=self.agents_dir, project_root=project_root,
        )


# NOTE: `_resolve_required_tool` was removed. It existed so a compiled `audit` /
# `leak-check` wrapper could locate a standalone script under `tools/`. Neither
# wrapper exists now: `audit` is CI tooling for THIS repo (`tools/audit.py`, not a
# dotagents command, not shipped), and `leak-check` is a personal command module in
# the user's private `.agents/`. Nothing in the package shells out to `tools/`.


def _package_data_dir(name: str) -> "Path | None":
    """Resolve a directory under the installed `dotagents` package (e.g.
    `_overlay` or `_payload`) to a real filesystem Path, working whether the
    package is a plain directory (pip install / editable) or inside a zipapp.

    Inside a zipapp, `importlib.resources.files()` returns a `zipfile.Path`
    (a `Traversable`, not a real filesystem `Path`): `.is_dir()` correctly
    reports membership in the archive, but `str(traversable)` produces a
    path string that does not exist on disk (`Path(str(...)).exists()` is
    always False -- there is no real file there to stat). So a zip-backed
    hit is extracted once to a process-lifetime temp directory and that
    real path is cached and returned; a plain-directory hit is returned
    as-is. Returns None if the directory isn't present in the package at all.
    """
    if name in _extracted_dirs_cache:
        return _extracted_dirs_cache[name]

    traversable = importlib.resources.files("dotagents") / name
    if not traversable.is_dir():
        return None

    as_path = Path(str(traversable))
    if as_path.exists():
        _extracted_dirs_cache[name] = as_path
        return as_path

    # Zip-backed (or otherwise non-filesystem) Traversable: extract to a
    # temp dir that lives for the process lifetime.
    extract_root = Path(tempfile.mkdtemp(prefix="dotagents-%s-" % name))

    def _extract(node, dest: Path):
        dest.mkdir(parents=True, exist_ok=True)
        for child in node.iterdir():
            child_dest = dest / child.name
            if child.is_dir():
                _extract(child, child_dest)
            else:
                child_dest.write_bytes(child.read_bytes())

    _extract(traversable, extract_root)
    _extracted_dirs_cache[name] = extract_root
    return extract_root


# The base overlay (neutral minimum) is bundled package data at
# `src/dotagents/_overlay`; `init` and `install` both lay it down. Overlays
# beyond the base are opt-in examples applied from an external path via
# `install --overlays <dir>` (the installer bundles none of them).
BASE_ROOT = _package_data_dir("_overlay") or (
    Path(__file__).resolve().parent.parent / "_overlay"
)

# Base files that are create-if-absent only (never overwrite), i.e. everything
# except the managed-block files (AGENTS.md/CLAUDE.md).
BASE_PLAIN_FILES = [
    "README.md",
    "dotagents/DECISIONS.md",
]


def _compose_block(base_text: str, overlays: "list[Path]", logger) -> str:
    """Fold each overlay's `rules`/`routing` contributions into the base block.

    Rules append to "Always-on rules" and routing to "Load on demand", after the
    base's own -- the base carries the mechanism (D57) and should read first. The
    overlays fold in **`(priority, name)` order** (plan 02 / D68), NOT the caller's
    list order: lower `priority` (default `DEFAULT_PRIORITY`, 500) sorts earlier, so
    a numerically higher-priority overlay lands *last* and wins on conflict -- the
    same "lower sorts earlier / higher wins" convention `_context.py` uses. `name`
    is the tiebreaker, so equal-priority overlays produce a stable, deterministic
    block regardless of add-invocation or discovery order. Returns `base_text`
    unchanged when nothing contributes, so `init` (which takes no overlays) is
    completely unaffected."""
    from dotagents._overlays import read_manifest, rule_blocks, sort_overlays_by_priority

    rules: "list[str]" = []
    routing: "list[str]" = []
    for overlay_dir in sort_overlays_by_priority(overlays):
        manifest = read_manifest(overlay_dir)
        blocks, warnings = rule_blocks(overlay_dir, manifest["rules"])  # type: ignore[arg-type]
        for warning in warnings:
            logger.warning("overlay %s: %s", manifest["name"], warning)
        rules.extend(blocks)
        routing.extend(manifest["routing"])  # type: ignore[arg-type]

    if not rules and not routing:
        return base_text

    text = base_text
    if rules:
        # Append after the last always-on bullet, i.e. just before the next heading.
        m = re.search(r"(?m)^## Load on demand", text)
        if m is None:
            logger.warning("base AGENTS.md has no 'Load on demand' heading; "
                           "appending overlay rules at the end of the block")
        else:
            text = text[: m.start()] + "\n".join(rules) + "\n\n" + text[m.start():]
    if routing:
        # The base's placeholder sentence only makes sense with no routing lines.
        text = re.sub(
            r"(?m)^Nothing ships here by default[^\n]*\n(?:[^\n#<][^\n]*\n)*",
            "",
            text,
        )
        end = re.search(r"(?m)^<!-- dotagents:end -->", text)
        insert_at = end.start() if end else len(text)
        text = text[:insert_at] + "\n".join(routing) + "\n" + text[insert_at:]
    return text


def _installed_overlay_dirs(scope, source, *, adding=None, dry_run=False) -> "list[Path]":
    """The overlay dirs to recompose the managed block over (plan 02 / D68).

    Every overlay installed in `scope` contributes to the block, so the recompose is
    a pure function of *which* overlays are present -- not of add-invocation order.
    Each installed overlay ships its own `overlay.toml` + rules files (see
    `install_overlay_dir`), so the installed dir is self-describing and used directly.

    `adding` is the names being added/synced this call; on a `--dry-run` `add` they
    are not yet on disk in the scope, so their **source** dir stands in so the dry-run
    preview reflects what a real run would produce. A real (non-dry-run) run reads them
    from the scope like any other installed overlay. Order here is irrelevant --
    `_compose_block` sorts by `(priority, name)`.
    """
    from dotagents import _scope

    adding = list(adding or [])
    installed = set(_scope.discover_overlays(scope))
    names = sorted(installed | set(adding))
    dirs: "list[Path]" = []
    for name in names:
        if dry_run and name in adding and name not in installed:
            # Not on disk yet (dry-run add): describe it from the source instead.
            try:
                dirs.append(source.overlay_dir(name))
            except SystemExit:
                pass
        else:
            dirs.append(scope.overlay_dir(name))
    return dirs


def _resolved_env(dest: Path, logger, agent_name: str) -> "dict[str, str]":
    """The env CHANGES dotagents contributes, for agents that need a static
    snapshot rather than a per-session hook.

    `agent_name` is passed as `explicit` so the identity vars describe the agent the
    file is FOR, not whichever harness happens to be running `init` -- a snapshot
    written from Claude must still say `AGENT=codex` inside Codex's own config.

    Assembling this executes overlay `env.py` files, so a broken overlay must not
    take `init` down with it -- failure degrades to an empty set with a warning.
    """
    from dotagents import _env, _scope

    try:
        return _env.get_environment(
            agents_dir=Path(dest),
            project_root=_scope.project_root_default(),
            base_env=dict(os.environ),
            explicit=agent_name,
            logger=logger,
        )
    except Exception as exc:  # noqa: BLE001 -- never let one overlay break init
        logger.warning("could not assemble env for the static env block: %s", exc)
        return {}


def _apply_base(
    src: Path, dest: Path, force: bool, dry_run: bool, logger,
    agents: "list[str] | None" = None,
    wire_hooks: bool = False,
) -> None:
    """Lay down the base overlay: managed-block merge AGENTS.md/CLAUDE.md,
    create-if-absent the plain files. Shared by `init` and `install`.

    With `wire_hooks`, each active agent also gets its hooks merged and the shared
    skills dir linked into its config dir (a no-op for adapters that don't
    implement it). Done here because this is where the active-agent list is
    already resolved."""
    from dotagents import _agents
    import os

    base_agents = (Path(src) / "AGENTS.md").read_text(encoding="utf-8")

    # True only when the caller named agents with `--agents`. Writes that touch an
    # agent's own main config file are gated on this, so merely *running* under a
    # harness never rewrites its config behind the user's back.
    explicitly_requested = bool(agents)

    active_agents = []
    if agents:
        for name in agents:
            agent = _agents.get_agent(name)
            if agent:
                active_agents.append(agent)
            else:
                logger.warning(f"Unknown agent: {name}")
    else:
        # Default: all detected + claude
        all_agents = _agents.get_all_agents()
        active_agents = [a for a in all_agents if a.detect_env(os.environ)]
        if not any(a.name == "claude" for a in active_agents):
            active_agents.append(_agents.ClaudeAgent())

    for agent in active_agents:
        agent.write_base_config(
            dest, src, base_agents, force=force, dry_run=dry_run, logger=logger
        )
        if wire_hooks:
            agent.wire_hooks(dest, dry_run=dry_run, logger=logger)
            # Agents with no per-session env mechanism (Codex) take a static
            # snapshot of the resolved env instead. Only ever on an EXPLICIT
            # `--agents <name>`: this edits the user's main config file with values
            # that go stale, so it must be asked for, never triggered by
            # auto-detection. Resolved lazily -- assembling it runs overlay `env.py`.
            if explicitly_requested and hasattr(agent, "write_env_block"):
                agent.write_env_block(
                    _resolved_env(dest, logger, agent.name),
                    dry_run=dry_run,
                    logger=logger,
                )

    for rel in BASE_PLAIN_FILES:
        source_path = Path(src) / rel
        if not source_path.exists():
            continue
        target_path = dest / rel
        if target_path.exists():
            logger.info("skipped (present): %s", rel)
            continue
        logger.info("created: %s", rel)
        if not dry_run:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source_path), str(target_path))

    # Create `<dest>/dotagents/cmds/` and lay down whatever the base overlay
    # bundles for it. dotagents itself now bundles NO command module (D85:
    # link/sync moved to the private-sync overlay as link-project/sync-project,
    # with their logic) -- but the DIR is still created unconditionally, because
    # it is the documented user extension point: a `*.py` command module dropped
    # here is discovered with zero config (see the README laid down beside it).
    # Create-if-absent per file, exactly like the plain files -- a user's own
    # edits to an installed command module are never clobbered on a reinstall.
    cmds_src = Path(src) / "dotagents" / "cmds"
    cmds_dest = dest / "dotagents" / "cmds"
    if not dry_run:
        cmds_dest.mkdir(parents=True, exist_ok=True)
    if cmds_src.is_dir():
        sources = sorted(cmds_src.glob("*.py")) + sorted(cmds_src.glob("*.md"))
        for source_path in sources:
            if source_path.name.startswith("_"):
                continue
            rel = "dotagents/cmds/%s" % source_path.name
            target_path = cmds_dest / source_path.name
            if target_path.exists():
                logger.info("skipped (present): %s", rel)
                continue
            logger.info("created: %s", rel)
            if not dry_run:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(source_path), str(target_path))


def _resolve_from(from_arg: "str | None", default: Path) -> Path:
    """Resolve --from to a local directory Path. A bare local path/dir is used
    directly; a URI string is constructed via pathlib_next's UriPath (lazy
    import so the `uri` extra is only required when actually used)."""
    if from_arg is None:
        return default
    candidate = Path(from_arg)
    if candidate.exists():
        return candidate
    if "://" in from_arg or from_arg.startswith(("http:", "https:", "sftp:", "s3:", "zip:")):
        try:
            from pathlib_next import UriPath
        except ImportError as e:
            raise SystemExit(
                'error: --from %r needs URI support. Install it with: pip install "dotagents-cli[uri]"'
                % from_arg
            ) from e
        return UriPath(from_arg)
    raise SystemExit("error: --from path does not exist: %s" % from_arg)


def _run_overlay_setup(dest_dir, name, *, scope, no_setup, dry_run, logger):
    """Run an installed overlay's `setup` script, honoring `--no-setup`.

    Thin wrapper over `_overlays.run_overlay_setup` that resolves the store path
    from the scope (D58 configurable store, passed as `AGENTS_HOME`) and
    short-circuits when `--no-setup` is given or the overlay ships no script.
    Returns the setup exit code (0 when skipped / absent), so a non-zero result
    surfaces as a clear error rather than a silent skip."""
    from dotagents import _overlays

    if no_setup:
        if _overlays.find_setup_script(dest_dir) is not None:
            logger.info("skipping setup for %s (--no-setup)", name)
        return 0
    rc = _overlays.run_overlay_setup(
        dest_dir, name, agents_dir=scope.agents_root, dry_run=dry_run, logger=logger,
    )
    return rc or 0
