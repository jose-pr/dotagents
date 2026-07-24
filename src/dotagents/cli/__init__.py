"""dotagents CLI: init / audit / context / env / overlays / build-pyz built-in
subcommands, plus link / sync (bundled command modules) and any user or overlay
command modules discovered from a `cmds` directory (D76/D84).

`audit` STAYS a compiled built-in: once its personal defaults are emptied (D84) it
is a generic config validator any fork needs, and CI self-validates via the
standalone `tools/audit_config.py`. `leak-check` is no longer in the repo at all:
it enforces personal plan-naming conventions, so it moves to the user's private
`.agents/cmds/` as a discovered command module (D84), not the public repo.

The per-command classes live in sibling modules (`cli/init.py`, `cli/audit.py`,
...); this package base holds the shared helpers (in `cli/_common.py`, re-exported
here), the `Dotagents(LoggingArgs, Cli)` umbrella, `main()` (the `install.py` shim +
`python -m dotagents` entrypoint), `_discover` (command-source resolution), and
`_repoint_zipapp_sources` (the zipapp source-extraction shim). (`install.py` at the
repo root is the entrypoint shim, unrelated to the removed `install` subcommand --
`init` now lays down the base + optional `--bin-dir` wrappers, D82.)

Dispatch (D76): `main()` routes through `duho.app`, not `duho.main`, so command
discovery runs. `link`/`sync` are no longer compiled built-ins -- they ship as
command MODULES in the bundled `_overlay/dotagents/cmds/` dir (`init` lays them
into `<store>/dotagents/cmds/`), discovered from there, from each installed
overlay's `<overlay-root>/cmds/`, and from a per-scope `cmds` dir (one
`get_file_paths` Contract-A walk, `_cmds_dirs`). `app`'s DEFAULT dispatch is used (dotagents has no fan-out):
a plain `(LoggingArgs, Cmd)` class command dispatches through `app` exactly as it
did through `main` -- `app` calls the class's `__call__`.

`_compose_block` and `_package_data_dir` are re-exported at package level because
other package modules import them as `dotagents.cli._compose_block` /
`dotagents.cli._package_data_dir` (see `_overlays.py`, `_scope.py`).
"""

import logging
import os
import sys
import tempfile
from pathlib import Path

import duho
from duho import Cli, LoggingArgs

from dotagents import __version__

# Re-export shared helpers so `dotagents.cli.<name>` keeps resolving for both the
# command modules and external importers (`dotagents._overlays`, `dotagents._scope`).
from dotagents.cli._common import (  # noqa: F401
    BASE_PLAIN_FILES,
    BASE_ROOT,
    _apply_base,
    _compose_block,
    _installed_overlay_dirs,
    _package_data_dir,
    _resolve_from,
    _run_overlay_setup,
)

# Import each built-in command class to register it as a compiled subcommand.
# Importing the command modules here (never the reverse) keeps the dependency
# edges one-directional: command modules -> cli._common / dotagents._*, and
# cli/__init__ -> command modules. link/sync are NOT imported here anymore --
# they are discovered command modules (see `_discover`).
from dotagents.cli.audit import Audit
from dotagents.cli.build_pyz import BuildPyz
from dotagents.cli.context import Context
from dotagents.cli.env import Env
from dotagents.cli.init import Init
from dotagents.cli.overlays import (  # noqa: F401  (re-exported for tests)
    OverlayAdd,
    OverlayList,
    OverlayRemove,
    OverlaySync,
    Overlays,
)

_LOGGER = logging.getLogger("dotagents")

# The compiled built-in command classes, in --help order. link/sync are gone
# (now discovered modules -- they ship in the bundled `_overlay/dotagents/cmds/`
# dir). audit STAYS a compiled built-in: once its personal defaults are emptied
# (D84) it is a generic config validator any fork needs, and CI self-validates via
# the standalone tools/audit_config.py. leak-check is gone from the repo entirely
# (personal -- it moves to the user's private `.agents/cmds/`, D84). `_discover`
# seeds the command set with these, then layers discovered commands over them
# (later source wins on a name clash).
_BUILTIN_COMMANDS = [
    Init,
    Audit,
    BuildPyz,
    Context,
    Env,
    Overlays,
]

# The cli submodules whose sources duho introspects for flag/help definitions.
# Every module that defines a field-bearing BUILT-IN command class must be
# repointed inside a zipapp (see `_repoint_zipapp_sources`). link/sync are no
# longer here -- they are discovered from the bundled cmds dir, which is
# extracted to a real temp file before import (so its `__file__` already exists;
# no repoint needed -- see `_bundled_cmds_dir`).
_COMMAND_MODULES = (
    "dotagents.cli.init",
    "dotagents.cli.audit",
    "dotagents.cli.context",
    "dotagents.cli.env",
    "dotagents.cli.overlays",
    "dotagents.cli.build_pyz",
)

#: Extra env-var command search paths (os.pathsep-split), additive to the scope
#: walk.
CMDS_PATH_ENV = "AGENTS_CMDS_PATH"
#: back-compat: DOTAGENTS_CMDS_PATH is deprecated, removable next release.
CMDS_PATH_ENV_LEGACY = "DOTAGENTS_CMDS_PATH"

#: The configurable user-scope store (D58). Discovery resolves the user scope's
#: cmds dir through this, not a hardcoded ~/.agents, when set. This is the same
#: var `dotagents env` emits (D79).
AGENTS_DIR_ENV = "AGENTS_HOME"
#: back-compat: DOTAGENTS_AGENTS_DIR is deprecated, removable next release.
AGENTS_DIR_ENV_LEGACY = "DOTAGENTS_AGENTS_DIR"


class Dotagents(LoggingArgs, Cli):
    """Umbrella CLI for installing and building the dotagents config."""

    _version_ = __version__
    # Built-ins are handed to `duho.app` via `commands=` (see `main`/`_discover`),
    # NOT resolved from `_subcommands_`: `app` returns the `commands=` list as-is
    # and does not merge `_subcommands_` on top of it, so keeping the built-ins
    # here too would double-register them. `_discover` is the single source.
    _subcommands_ = []

    #: Extra command search path(s), additive to the scope walk + env var.
    #: `duho.parse_globals(Dotagents, argv)` reads this before the full parser is
    #: built so `_discover` can honor it (an extra `--cmdspath` search path).
    cmdspath: "list[str]" = []
    "Extra directory to discover command modules from (repeatable)."
    ("--cmdspath",)

    def __call__(self) -> int:
        self._logger_.info(
            "pick a subcommand, e.g. `init`, `overlays`, `link`, `sync`, "
            "`audit`, `build-pyz`"
        )
        return 0


def _bundled_cmds_dir() -> "Path | None":
    """The bundled command-module dir shipped inside the package (D76).

    `<package>/_overlay/dotagents/cmds` holds `link.py`/`sync.py` (and any other
    bundled command module). Resolved `.pyz`-safe via `_package_data_dir`, which
    extracts a zip-backed `_overlay` to a real temp dir once -- so the modules
    `discover_commands` imports from here always have a real on-disk `__file__`,
    and the zipapp AST-introspection shim (`_repoint_zipapp_sources`) does NOT
    need to cover them. Returns None if the package bundles no cmds dir."""
    base = _package_data_dir("_overlay")
    if base is None:
        return None
    cmds = base / "dotagents" / "cmds"
    return cmds if cmds.is_dir() else None


def _discover_dir(source, by_name: dict) -> None:
    """Discover command modules from one directory source, resiliently.

    A missing / unreadable / non-command source is skipped with a warning, never
    fatal -- mirroring `duho.discover_commands`'s own per-command resilience.
    Each discovered command is keyed by its
    resolved subcommand name (`_parsername_` / class name), so a LATER source
    overrides an earlier same-named command (built-in < bundled < scope < env <
    flag)."""
    from duho.discovery import discover_commands

    path = Path(source)
    if not path.is_dir():
        return
    try:
        commands = discover_commands(path)
    except (ImportError, NotImplementedError, OSError) as exc:
        _LOGGER.warning("skipping command source %r: %s", str(source), exc)
        return
    for command in commands:
        name = getattr(command, "_parsername_", None) or getattr(
            command, "__name__", None
        )
        if name:
            by_name[name] = command  # later source wins


def _cmds_dirs() -> "list[Path]":
    """Every `cmds` dir to discover command modules from, in Contract-A order.

    Resolved with the SAME `get_file_paths` walk that backs `bin`/PATH discovery
    (`_env.get_bin_paths`) -- one resolver call yields the cmds dir at EVERY level
    in precedence order, so command discovery, PATH, and every other Contract-A
    seam agree on which roots exist and in what order. NOT a hand-rolled loop over
    installed overlays.

    The per-level name-dict maps overlay levels to `<overlay-root>/cmds` and every
    other level (system/user/project) to `<agents_root>/dotagents/cmds` (the
    `Scope.cmds_dir` layout, D76). `get_file_paths` returns them in Contract-A
    precedence: overlays first, then system, user, project. Discovery layers later
    sources over earlier ones (see `_discover_dir`), so a project cmd overrides a
    user cmd overrides an overlay cmd of the same name -- the intended precedence
    (an overlay may SHIP a command; a user/project can still override it).

    The store location is configurable (D58/D79): the user scope resolves through
    `$AGENTS_HOME` (default `~/.agents`); the project scope is `<cwd>/.agents`.
    `include_missing=True` (precursor semantics): every level's cmds dir is offered
    and the caller's `_discover_dir` skips the ones that don't exist."""
    from dotagents import _resolve, _scope

    agents_dir = (
        os.environ.get(AGENTS_DIR_ENV)
        or os.environ.get(AGENTS_DIR_ENV_LEGACY)
        or None
    )
    user = _scope.resolve_scope(global_scope=True, agents_dir=agents_dir)
    resolved = _resolve.get_file_paths(
        {"default": "dotagents/cmds", "overlay": "cmds"},
        agents_dir=user.agents_root,
        project_root=_scope.project_root_default(),
        global_scope=False,
        include_missing=True,
    )
    return [path for _level, path, _root in resolved]


def _discover(argv=None) -> "list":
    """Resolve the full command set: built-ins, then discovered modules.

    Sources, earliest-to-latest (a later source overrides a same-named command,
    dedup by resolved subcommand name):

    1. the compiled built-in command classes (`_BUILTIN_COMMANDS`);
    2. the bundled command modules (`link`/`sync`) in `<package>/_overlay/
       dotagents/cmds` -- always available, even before an install;
    3. the Contract-A `cmds` dirs (`_cmds_dirs`): each installed overlay's
       `<overlay-root>/cmds`, then system/user/project `<scope>/dotagents/cmds`,
       in Contract-A precedence (overlays < system < user < project). This is what
       lets an installed overlay SHIP a command, or a user drop a personal command
       module into their private `<scope>/dotagents/cmds` (e.g. `leak-check`), while
       a user/project cmd still overrides a same-named overlay cmd;
    4. `$AGENTS_CMDS_PATH` entries (os.pathsep-split);
    5. `--cmdspath` entries, read via `duho.parse_globals` before the full parser.

    Every directory source is resilient: a missing / bad one is skipped with a
    warning, never fatal.
    """
    by_name: "dict[str, object]" = {}

    # 1. built-ins (lowest precedence)
    for command in _BUILTIN_COMMANDS:
        name = getattr(command, "_parsername_", None) or command.__name__
        by_name[name] = command

    # 2. bundled cmds (link/sync)
    bundled = _bundled_cmds_dir()
    if bundled is not None:
        _discover_dir(bundled, by_name)

    # 3. Contract-A cmds dirs: overlay cmds + scope (user/project) cmds, in
    #    precedence order (overlays first, project last -> project wins).
    for cmds_dir in _cmds_dirs():
        _discover_dir(cmds_dir, by_name)

    # 4. $AGENTS_CMDS_PATH (back-compat: $DOTAGENTS_CMDS_PATH, removable next release)
    raw = os.environ.get(CMDS_PATH_ENV) or os.environ.get(CMDS_PATH_ENV_LEGACY)
    if raw:
        for entry in raw.split(os.pathsep):
            if entry:
                _discover_dir(entry, by_name)

    # 5. --cmdspath (read the global before the full subcommand parser is built)
    try:
        globals_ = duho.parse_globals(Dotagents, argv)
        for entry in getattr(globals_, "cmdspath", None) or []:
            if entry:
                _discover_dir(entry, by_name)
    except Exception as exc:  # pragma: no cover - parse_globals is best-effort
        _LOGGER.warning("could not read --cmdspath globals: %s", exc)

    return list(by_name.values())


def _repoint_zipapp_sources() -> None:
    """Make duho's AST field-introspection work when running from a zipapp.

    duho discovers each command's flags + help by parsing its module source
    (`_introspect.getclsdef` -> `Path(module.__file__).read_text()`). Inside a
    `.pyz` the module `__file__` is a zip-internal path `read_text()` can't open,
    and duho catches that `OSError` *before* its `inspect.getsource` fallback --
    so every field silently loses its declared flags and help (the positional
    `path` degrades to `--path`, `--from` to `--from-`, help text vanishes).
    Extract the affected module sources to real temp files and repoint `__file__`
    so the read succeeds. A no-op for a plain install, where `__file__` already
    exists on disk.

    Each BUILT-IN command class lives in its own `dotagents.cli.<x>` module, so
    EVERY such module is repointed (plus duho's `LoggingArgs` preset,
    `duho.presets`). The DISCOVERED command modules (`link`/`sync` from the
    bundled `_overlay/dotagents/cmds`) do NOT need repointing: `_package_data_dir`
    extracts a zip-backed `_overlay` to real temp files before `discover_commands`
    imports them, so their `__file__` already exists on disk.

    Tracked upstream: jose-pr/duho#1 -- drop this shim (and the build-pyz CI
    guard) once duho's getclsdef falls through to inspect.getsource when
    _module_index raises."""
    import importlib.resources as _ir

    for modname in _COMMAND_MODULES + ("duho.presets",):
        mod = sys.modules.get(modname)
        if mod is None:
            continue
        current = getattr(mod, "__file__", None)
        if current and Path(current).exists():
            continue  # plain install: source already readable
        top, _sep, rest = modname.partition(".")
        rel = (rest.replace(".", "/") or "__init__") + ".py"
        try:
            resource = _ir.files(top).joinpath(rel)
            if not resource.is_file():
                continue
            text = resource.read_text(encoding="utf-8")
        except (FileNotFoundError, ModuleNotFoundError, OSError, TypeError):
            continue
        tmp = Path(tempfile.mkdtemp(prefix="dotagents-src-")) / (
            modname.replace(".", "_") + ".py"
        )
        tmp.write_text(text, encoding="utf-8")
        mod.__file__ = str(tmp)


def main(argv=None) -> int:
    _repoint_zipapp_sources()
    # `duho.app` (not `duho.main`) so command discovery runs. Default dispatch:
    # dotagents has no fan-out, so `app` calls each command's `__call__` exactly
    # as `duho.main` did. `commands=` is the resolved built-ins + discovered set.
    return duho.app(
        Dotagents,
        commands=_discover(argv),
        argv=argv,
        name="dotagents",
    )


if __name__ == "__main__":
    sys.exit(main())
