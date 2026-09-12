"""dotagents CLI: init / context / env / overlays / build-pyz / about built-in
subcommands, plus command modules discovered from `cmds` directories (D76/D84).

The bundled command modules live in `_overlay/dotagents/cmds/` (`findings`,
`launch`). `init` lays down that dir's README only, as the user's drop-in point:
a `.py` command module placed in `<scope>/dotagents/cmds/` is discovered like any
other (D84).

The per-command classes live in sibling modules (`cli/init.py`, `cli/overlays.py`,
...); this package base holds the shared helpers (in `cli/_common.py`, re-exported
here), the `Dotagents(LoggingArgs, Cli)` umbrella, `main()` (the `install.py` shim +
`python -m dotagents` entrypoint), `_discover` (command-source resolution), and
`_repoint_zipapp_sources` (the zipapp source-extraction shim).

Dispatch: `main()` routes through `duho.app` (not `duho.main`) so command
discovery runs; `app`'s default dispatch calls each command class's `__call__`.
Command modules are discovered from the bundled cmds dir, from each installed
overlay's `<overlay-root>/cmds/`, and from each scope's `dotagents/cmds` (one
`Scope.paths` Contract-A walk, `_cmds_dirs`).

`_compose_block` and `_package_data_dir` are re-exported at package level because
other package modules import them as `dotagents.cli._compose_block` /
`dotagents.cli._package_data_dir` (see `_overlays.py`, `_scope.py`). `DotAgentsArgs`
is re-exported for overlay-shipped command modules, which always run inside a real
`dotagents` process and should `from dotagents.cli import DotAgentsArgs`.
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
    AGENTS_DIR_ENV,
    BASE_PLAIN_FILES,
    BASE_ROOT,
    DotAgentsArgs,
    _apply_base,
    _compose_block,
    _installed_overlay_dirs,
    _no_subcommand,
    _package_data_dir,
    _resolve_from,
    _run_overlay_setup,
    _scratch_dir,
    _write_stdout,
    resolve_user_store,
)

# Import each built-in command class to register it as a compiled subcommand.
# Importing the command modules here (never the reverse) keeps the dependency
# edges one-directional: command modules -> cli._common / dotagents._*, and
# cli/__init__ -> command modules.
from dotagents.cli.about import About
from dotagents.cli.build_pyz import BuildPyz
from dotagents.cli.context import Context
from dotagents.cli.env import Env
from dotagents.cli.init import Init
from dotagents.cli.overlays import (  # noqa: F401  (re-exported for tests)
    OverlayAdd,
    OverlayList,
    OverlayRemove,
    OverlayShow,
    OverlaySync,
    Overlays,
)

_LOGGER = logging.getLogger("dotagents")

# The compiled built-in command classes, in --help order. Together with the
# bundled command modules (`findings`, `launch`) this is dotagents' whole shipped
# surface. `_discover` seeds the command set with these, then layers discovered
# commands over them (later source wins on a name clash).
_BUILTIN_COMMANDS = [
    Init,
    BuildPyz,
    Context,
    Env,
    Overlays,
    About,
]

# The cli submodules whose sources duho introspects for flag/help definitions;
# every one is repointed inside a zipapp (see `_repoint_zipapp_sources`).
# Discovered command modules never need repointing (their files are real on
# disk -- see `_bundled_cmds_dir`), but duho walks the MRO and reads each BASE
# class's module too, so `_common` (home of `DotAgentsArgs`) must be listed even
# though it ships no command class of its own.
_COMMAND_MODULES = (
    "dotagents.cli",  # the umbrella itself: `Dotagents.cmdspath` lives here
    "dotagents.cli.init",
    "dotagents.cli.context",
    "dotagents.cli.env",
    "dotagents.cli.overlays",
    "dotagents.cli.build_pyz",
    "dotagents.cli.about",
    "dotagents.cli._common",
)

#: Extra env-var command search paths (os.pathsep-split), additive to the scope
#: walk.
CMDS_PATH_ENV = "AGENTS_CMDS_PATH"


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
        return _no_subcommand(
            self,
            "pick a subcommand, e.g. `init`, `overlays`, `context`, `env`, `build-pyz`",
        )


def _bundled_cmds_dir() -> "Path | None":
    """The bundled command-module dir, `<package>/_overlay/dotagents/cmds` (D76).

    Ships `findings.py` (the per-scope findings queue), `launch.py` (start a
    harness with the env and context applied) and a README. It is always a
    discovery source; `init` lays down only the README (a create-if-absent copy
    of the modules would pin the first-installed version). Resolved via
    `_package_data_dir`, which extracts a zip-backed `_overlay` to a real temp
    dir once, so the modules imported from here always have an on-disk
    `__file__` and `_repoint_zipapp_sources` need not cover them. Returns None
    if the package bundles no cmds dir."""
    base = _package_data_dir("_overlay")
    if base is None:
        return None
    cmds = base / "dotagents" / "cmds"
    return cmds if cmds.is_dir() else None


def _describe(exc: BaseException) -> str:
    """``TypeName: message`` for a log line; a bare ``sys.exit()`` has no
    message, so say so instead of printing ``SystemExit: None``."""
    if isinstance(exc, SystemExit):
        return "SystemExit%s" % (": %s" % exc.code if exc.code not in (None, 0) else " (no message)")
    return "%s: %s" % (type(exc).__name__, exc)


def _discover_modules(directory: Path) -> "list":
    """duho's per-directory discovery, made resilient per MODULE.

    duho's own loop catches only ImportError/NotImplementedError per file and
    lets anything else propagate. Discovery runs before EVERY dotagents
    invocation (including `env`/`context` inside the SessionStart hooks), so
    one broken user module must not take down every command: the loop is
    mirrored here with a catch-all per file -- the bad module is named, with
    its exception, and skipped; the rest of the directory still loads.
    SystemExit is included because a module that `sys.exit()`s at import is
    not an Exception. Falls back to duho's own per-directory unit if a duho
    release moves these helpers."""
    try:
        from duho.discovery import _commands_in_module, _import_from_path, _unique_module_name
    except ImportError:  # pragma: no cover -- a later duho without these internals
        from duho.discovery import discover_commands

        return discover_commands(directory)
    commands = []
    for file in sorted(directory.glob("*.py")):
        if file.name.startswith("_"):
            continue
        try:
            module = _import_from_path(_unique_module_name("duho._discovered." + file.stem), file)
            commands.extend(_commands_in_module(module, stem=file.stem))
        except (Exception, SystemExit) as exc:  # noqa: BLE001 -- see docstring
            _LOGGER.warning("skipping command module %s: %s", file, _describe(exc))
    return commands


def _discover_dir(source, by_name: dict) -> None:
    """Discover command modules from one directory source, resiliently.

    A missing / unreadable / non-command source is skipped with a warning, never
    fatal -- mirroring `duho.discover_commands`'s own per-command resilience.
    Each discovered command is keyed by its
    resolved subcommand name (`_parsername_` / class name), so a LATER source
    overrides an earlier same-named command (built-in < bundled < scope < env <
    flag)."""
    path = Path(source)
    if not path.is_dir():
        return
    try:
        commands = _discover_modules(path)
    except (Exception, SystemExit) as exc:  # noqa: BLE001 -- the directory itself
        _LOGGER.warning("skipping command source %r: %s", str(source), _describe(exc))
        return
    for command in commands:
        name = getattr(command, "_parsername_", None) or getattr(
            command, "__name__", None
        )
        if name:
            by_name[name] = command  # later source wins


def _agents_dir_from_argv(argv) -> "str | None":
    """The value of an `--agents-dir X` / `--agents-dir=X` anywhere in `argv`,
    so command discovery walks the store the command itself is about to use.
    The flag belongs to the subcommands, not the umbrella, and `_discover`
    runs before the subcommand parser exists -- so it is read by hand."""
    if argv is None:
        argv = sys.argv[1:]
    for i, arg in enumerate(argv):
        if arg == "--agents-dir" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--agents-dir="):
            return arg.split("=", 1)[1]
    return None


def _cmds_dirs(argv=None) -> "list[Path]":
    """Every `cmds` dir to discover command modules from, in Contract-A order.

    Resolved with the SAME `Scope.paths` walk that backs `bin`/PATH discovery
    (`_env.get_bin_paths`) -- one resolver call yields the cmds dir at EVERY level
    in precedence order, so command discovery, PATH, and every other Contract-A
    seam agree on which roots exist and in what order. NOT a hand-rolled loop over
    installed overlays.

    The per-level name-dict maps overlay levels to `<overlay-root>/cmds` and every
    other level (system/user/project) to `<agents_root>/dotagents/cmds` (the
    `Scope.cmds_dir` layout, D76). `Scope.paths` returns them in Contract-A
    precedence: per store (system, user, project), each store's overlays' `cmds`
    first and then the store's own `dotagents/cmds`. Discovery layers later
    sources over earlier ones (see `_discover_dir`), so a project cmd overrides a
    user cmd overrides an overlay cmd of the same name -- the intended precedence
    (an overlay may SHIP a command; a user/project can still override it).

    The store location is configurable (D58/D79): the user scope resolves through
    `resolve_user_store()` (`$AGENTS_HOME`, default
    `~/.agents`) -- the same resolver `env`/`context` use, so every user-store
    reader agrees; the project scope is `<cwd>/.agents`.
    `include_missing=True`: every level's cmds dir is offered and the caller's
    `_discover_dir` skips the ones that don't exist."""
    from dotagents import _scope

    scope = _scope.Scope.of(
        agents_dir=resolve_user_store(_agents_dir_from_argv(argv)),
        project_root=_scope.project_root_default(),
    )
    resolved = scope.paths({"default": "dotagents/cmds", "overlay": "cmds"}, include_missing=True)
    return [path for _level, path, _root in resolved]


def _discover(argv=None) -> "list":
    """Resolve the full command set: built-ins, then discovered modules.

    Sources, earliest-to-latest (a later source overrides a same-named command,
    dedup by resolved subcommand name):

    1. the compiled built-in command classes (`_BUILTIN_COMMANDS`);
    2. the bundled command-module dir `<package>/_overlay/dotagents/cmds` --
       always available, even before an install (ships `findings`);
    3. the Contract-A `cmds` dirs (`_cmds_dirs`): each installed overlay's
       `<overlay-root>/cmds`, then system/user/project `<scope>/dotagents/cmds`,
       in Contract-A precedence (overlays < system < user < project). This is what
       lets an installed overlay SHIP a command, or a user drop a personal command
       module into their private `<scope>/dotagents/cmds`, while a user/project
       cmd still overrides a same-named overlay cmd;
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

    # 2. bundled cmds dir (ships `findings`)
    bundled = _bundled_cmds_dir()
    if bundled is not None:
        _discover_dir(bundled, by_name)

    # 3. Contract-A cmds dirs: overlay cmds + scope (user/project) cmds, in
    #    precedence order (overlays first, project last -> project wins).
    for cmds_dir in _cmds_dirs(argv):
        _discover_dir(cmds_dir, by_name)

    # 4. $AGENTS_CMDS_PATH
    raw = os.environ.get(CMDS_PATH_ENV)
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
    `duho.presets`). DISCOVERED command modules do NOT need repointing: an
    overlay's or a scope's cmds live on the real filesystem, and for the bundled
    `_overlay/dotagents/cmds` dir `_package_data_dir` extracts a zip-backed
    `_overlay` to real temp files before `discover_commands` imports from it, so
    their `__file__` already exists on disk.

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
        # A package's source is its `__init__.py` (`dotagents.cli` ->
        # `cli/__init__.py`), a plain module's is `<name>.py`. Getting this
        # wrong is silent: the resource is simply not found and the module
        # keeps its zip-internal `__file__`.
        if hasattr(mod, "__path__"):
            rel = (rest.replace(".", "/") + "/" if rest else "") + "__init__.py"
        else:
            rel = rest.replace(".", "/") + ".py"
        try:
            resource = _ir.files(top).joinpath(rel)
            if not resource.is_file():
                continue
            text = resource.read_text(encoding="utf-8")
        except (FileNotFoundError, ModuleNotFoundError, OSError, TypeError):
            continue
        # Under the one per-process scratch dir, removed at exit.
        tmp = _scratch_dir() / "src" / (modname.replace(".", "_") + ".py")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text, encoding="utf-8")
        mod.__file__ = str(tmp)


def main(argv=None) -> int:
    _repoint_zipapp_sources()
    # `duho.app` (not `duho.main`) so command discovery runs; default dispatch
    # calls each command's `__call__`. `commands=` is the resolved built-ins +
    # discovered set.
    return duho.app(
        Dotagents,
        commands=_discover(argv),
        argv=argv,
        name="dotagents",
    )


if __name__ == "__main__":
    sys.exit(main())
