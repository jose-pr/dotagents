"""Shared CLI-only helpers used across the per-command modules.

Pure helpers with no dependency on the command classes or the umbrella, so
command modules can import from here without creating an import cycle
(`cli/__init__.py` imports the command modules, not the reverse). The public
names `_compose_block` and `_package_data_dir` are re-exported from
`dotagents.cli` (see `cli/__init__.py`) because other package modules import
them as `dotagents.cli._compose_block` / `dotagents.cli._package_data_dir`.
"""

import importlib.resources
import re
import shutil
import tempfile
from pathlib import Path


_extracted_dirs_cache: "dict[str, Path]" = {}


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


def _apply_base(
    src: Path, dest: Path, force: bool, dry_run: bool, logger,
    agents: "list[str] | None" = None,
) -> None:
    """Lay down the base overlay: managed-block merge AGENTS.md/CLAUDE.md,
    create-if-absent the plain files. Shared by `init` and `install`."""
    from dotagents import _agents
    import os

    base_agents = (Path(src) / "AGENTS.md").read_text(encoding="utf-8")

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
                'error: --from %r needs URI support. Install it with: pip install "dotagents[uri]"'
                % from_arg
            ) from e
        return UriPath(from_arg)
    raise SystemExit("error: --from path does not exist: %s" % from_arg)


def _run_overlay_setup(dest_dir, name, *, scope, no_setup, dry_run, logger):
    """Run an installed overlay's `setup` script, honoring `--no-setup`.

    Thin wrapper over `_overlays.run_overlay_setup` that resolves the store path
    from the scope (D58 configurable store, passed as `DOTAGENTS_AGENTS_DIR`) and
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
