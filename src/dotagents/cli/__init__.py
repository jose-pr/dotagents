"""dotagents CLI: init / context / env / overlays / build-pyz / about / mcp built-in
subcommands, plus command modules discovered from `cmds` directories.

The bundled command modules live in `_overlay/dotagents/cmds/` (`findings`,
`launch`) and are discovered from the package; `init` copies nothing from that
dir. A `.py` command module a user places in `<scope>/dotagents/cmds/` (a dir
they create for it) is discovered like any other.

The per-command classes live in sibling modules (`cli/init.py`, `cli/overlays.py`,
...); this package base holds the shared helpers (in `cli/_common.py`, re-exported
here), the `Dotagents(LoggingArgs, Cli)` umbrella, `main()` (the `install.py` shim +
`python -m dotagents` entrypoint) and `_discover` (command-source resolution).

Dispatch: `main()` routes through `duho.app` (not `duho.main`) so command
discovery runs; `app`'s default dispatch calls each command class's `__call__`.
Command modules are discovered from the bundled cmds dir, from each installed
overlay's `<overlay-root>/cmds/`, and from each scope's `dotagents/cmds` (one
`Scope.paths` Contract-A walk, `_cmds_dirs`).

`DotAgentsArgs` and `_write_stdout` are re-exported for command modules (the
bundled ones and overlay-shipped ones), which always run inside a real
`dotagents` process and should `from dotagents.cli import DotAgentsArgs`.
"""

import logging
import os
import sys
from pathlib import Path

import duho
from duho import Cli, LoggingArgs
from duho.runtime import build_command_parser
from duho.text import kebabcase

from dotagents import __version__

from dotagents._resources import _package_data_dir
from dotagents._scope import resolve_user_store

# Names imported through the package by command modules (the bundled ones and
# overlay-shipped ones) and tests.
from dotagents._overlays import _compose_block  # noqa: F401
from dotagents.cli._common import DotAgentsArgs, _write_stdout  # noqa: F401

# Import each built-in command class to register it as a compiled subcommand.
# Importing the command modules here (never the reverse) keeps the dependency
# edges one-directional: command modules -> cli._common / dotagents._*, and
# cli/__init__ -> command modules.
from dotagents.cli.about import About
from dotagents.cli.build_pyz import BuildPyz
from dotagents.cli.context import Context
from dotagents.cli.env import Env
from dotagents.cli.init import Init
from dotagents.cli.mcp import Mcp
from dotagents.cli.overlays import (  # noqa: F401  (re-exported for tests)
    OverlayAdd,
    OverlayList,
    OverlayRemove,
    OverlayShow,
    OverlaySync,
    Overlays,
)
from dotagents.cli.path import PathCmd

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
    PathCmd,
    Overlays,
    About,
    Mcp,
]

#: Extra env-var command search paths (os.pathsep-split), additive to the scope
#: walk.
CMDS_PATH_ENV = "AGENTS_CMDS_PATH"


def _mcp_tools(notify, name_prefix=None):
    """The overlay MCP gateway (a `duho.mcp.ToolProvider`) for the current scope."""
    from dotagents._mcp_gateway import Gateway, discover

    scope = _discovery_scope([])
    return Gateway(notify, lambda: discover(scope), name_prefix)


class Dotagents(LoggingArgs, Cli):
    """Umbrella CLI for installing and building the dotagents config."""

    _version_ = __version__
    # Every MCP server of this CLI (`dotagents mcp`, `DOTAGENTS_MCP=stdio`)
    # forwards the active overlays' MCP servers.
    _mcp_tools_ = staticmethod(_mcp_tools)
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


def _bundled_cmds_dir() -> "Path | None":
    """The bundled command-module dir, `<package>/_overlay/dotagents/cmds`.

    Ships `findings.py` (the per-scope findings queue) and `launch.py` (start
    a harness with the env and context applied). It is always a discovery
    source, and `init` copies nothing from it (a create-if-absent copy of the
    modules would pin the first-installed version). Resolved via
    `_package_data_dir`, which extracts a zip-backed `_overlay` to a real temp
    dir once, so the modules imported from here always have an on-disk
    `__file__`. Returns None if the package bundles no cmds dir."""
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


def _check_buildable(command) -> None:
    """Build ``command``'s subparser the way `duho.app` will, into a throwaway
    parser, and let whatever that raises propagate.

    `duho.app` builds every subparser up front with nothing around it, so a
    module that imports cleanly but whose parser cannot be built (an annotation
    `get_type_hints` cannot resolve, PEP 604 `X | None` on Python 3.9, two
    fields claiming the same flag, a flag the umbrella already owns) would
    otherwise fail every invocation. The parents are the umbrella's options,
    as in `duho.app`, so a clash with them surfaces here too."""
    build_command_parser(command, Dotagents)


def _source_of(command) -> str:
    """The file a discovered command came from, for a log line."""
    module = getattr(command, "module", None) or sys.modules.get(
        getattr(command, "__module__", ""), None
    )
    return str(getattr(module, "__file__", None) or command)


def _buildable(commands, file=None) -> "list":
    """``commands`` minus the ones whose parser cannot be built, each skipped
    with a warning that names its file (see `_check_buildable`)."""
    out = []
    for command in commands:
        try:
            _check_buildable(command)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 -- see _check_buildable
            name = getattr(command, "_parsername_", None) or getattr(command, "__name__", "?")
            _LOGGER.warning(
                "skipping command %r from %s: %s",
                name, file or _source_of(command), _describe(exc),
            )
            continue
        out.append(command)
    return out


def kebab_name(class_name: str) -> str:
    """A command class's default subcommand name: ``LeakCheck`` ->
    ``leak-check``, ``HTTPServer`` -> ``http-server``, ``my_cmd`` -> ``my-cmd``."""
    return kebabcase(class_name)


def _named(commands) -> "list":
    """``commands``, each discovered class that declares no ``_parsername_``
    of its own given the kebab-case of its class name (:func:`kebab_name`),
    which is also the name duho gives it. Setting it makes the name readable as
    an attribute, which the command-set keys and the tests rely on."""
    for command in commands:
        if isinstance(command, type) and "_parsername_" not in vars(command):
            command._parsername_ = kebab_name(command.__name__)
    return commands


def _discover_modules(directory: Path) -> "list":
    """duho's per-directory discovery, made resilient per MODULE.

    Discovery runs before EVERY dotagents invocation (including `env`/`context`
    inside the SessionStart hooks), so one broken user module must not take
    down every command. `on_error` is called for any exception raised importing
    one file, `SystemExit` included (a module that `sys.exit()`s at import is
    not an Exception): the bad module is named, with its exception, and skipped;
    the rest of the directory still loads. A command that imports but cannot
    build its parser is skipped the same way (`_buildable`)."""

    def skip(source, exc) -> None:
        _LOGGER.warning("skipping command module %s: %s", source, _describe(exc))

    found = duho.discover_commands(directory, on_error=skip, sort=False)
    return _named(_buildable(found))


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


#: Commands whose `--agents-dir` names the USER store with or without `-g`;
#: elsewhere it names the project store unless `-g` is given.
_USER_STORE_COMMANDS = frozenset({"env", "context", "launch"})
#: Umbrella options that take a value, skipped when looking for the command.
_UMBRELLA_VALUE_FLAGS = frozenset({"--loglevel", "--cmdspath", "--agents-dir"})


def _agents_dir_from_argv(argv) -> "str | None":
    """The user store an `--agents-dir X` / `--agents-dir=X` in `argv` names,
    so command discovery walks the store the command itself is about to use.
    The flag belongs to the subcommands, not the umbrella, and `_discover`
    runs before the subcommand parser exists -- so it is read by hand.

    Only dotagents' own arguments count: scanning stops at `--`, after which
    everything belongs to a harness (`launch claude -- --agents-dir x`). And
    the value is the user store only under `-g` / `--global` or for `env`,
    `context` and `launch`; for `init`, `overlays` and `findings` without
    `-g` it is the PROJECT store, so it leaves the user tier as it is."""
    if argv is None:
        argv = sys.argv[1:]
    argv = list(argv)
    if "--" in argv:
        argv = argv[:argv.index("--")]
    value = command = None
    global_scope = skip = False
    for i, arg in enumerate(argv):
        if skip:
            skip = False
            continue
        if arg in ("-g", "--global"):
            global_scope = True
        elif arg == "--agents-dir":
            value = argv[i + 1] if i + 1 < len(argv) else None
            skip = True
        elif arg.startswith("--agents-dir="):
            value = arg.split("=", 1)[1]
        elif arg in _UMBRELLA_VALUE_FLAGS:
            skip = True
        elif command is None and not arg.startswith("-"):
            command = arg
    if value is not None and (global_scope or command in _USER_STORE_COMMANDS):
        return value
    return None


def _discovery_scope(argv=None):
    """The scope command discovery walks: the user store an `--agents-dir` in
    `argv` names (see `_agents_dir_from_argv`), and the pinned project root."""
    from dotagents import _scope

    return _scope.Scope.of(
        agents_dir=resolve_user_store(_agents_dir_from_argv(argv)),
        project_root=_scope.project_root_default(),
    )


def _add_overlay_libs(argv=None) -> "list[str]":
    """Put every existing overlay/store ``lib`` dir on ``sys.path`` before the
    command modules import, so a command can import any overlay's library even
    when ``dotagents`` runs from a plain shell (a session already has them on
    ``PYTHONPATH``). APPENDED, in contract-A precedence: in this process a lib
    never shadows an installed package, dotagents' own dependencies included.
    Returns the dirs added."""
    from dotagents import _env

    added = []
    for lib in reversed(_env.get_lib_paths(_discovery_scope(argv))):
        entry = str(lib)
        if entry not in sys.path:
            sys.path.append(entry)
            added.append(entry)
    return added


def _cmds_dirs(argv=None) -> "list[Path]":
    """Every `cmds` dir to discover command modules from, in Contract-A order.

    Resolved with the SAME `Scope.paths` walk that backs `bin`/PATH discovery
    (`_env.get_bin_paths`) -- one resolver call yields the cmds dir at EVERY level
    in precedence order, so command discovery, PATH, and every other Contract-A
    seam agree on which roots exist and in what order. NOT a hand-rolled loop over
    installed overlays.

    The per-level name-dict maps overlay levels to `<overlay-root>/cmds` and every
    other level (system/user/project) to `<agents_root>/dotagents/cmds` (the
    `Scope.cmds_dir` layout). `Scope.paths` returns them in Contract-A
    precedence: per store (system, user, project), each store's overlays' `cmds`
    first and then the store's own `dotagents/cmds`. Discovery layers later
    sources over earlier ones (see `_discover_dir`), so a project cmd overrides a
    user cmd overrides an overlay cmd of the same name -- the intended precedence
    (an overlay may SHIP a command; a user/project can still override it).

    The store location is configurable: the user scope resolves through
    `resolve_user_store()` (`$AGENTS_HOME`, default
    `~/.agents`) -- the same resolver `env`/`context` use, so every user-store
    reader agrees; the project is `project_root_default()` (the pinned root, else
    the cwd), with no `<project>/dotagents/cmds` tier outside `.agents`.
    `include_missing=True`: every level's cmds dir is offered and the caller's
    `_discover_dir` skips the ones that don't exist."""
    scope = _discovery_scope(argv)
    # No project-root tier: a checkout's own `dotagents/cmds/` is not a store,
    # and importing it would run a cloned repo's code on every invocation.
    resolved = scope.paths(
        {"default": "dotagents/cmds", "overlay": "cmds", "project-root": ""},
        include_missing=True,
    )
    return [path for _level, path, _root in resolved]


def _discover(argv=None) -> "list":
    """Resolve the full command set: built-ins, then discovered modules.

    Sources, earliest-to-latest (a later source overrides a same-named command,
    dedup by resolved subcommand name):

    1. the compiled built-in command classes (`_BUILTIN_COMMANDS`);
    2. the bundled command-module dir `<package>/_overlay/dotagents/cmds` --
       always available, even before an install (ships `findings` and `launch`);
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

    # 2. bundled cmds dir (ships `findings` and `launch`)
    bundled = _bundled_cmds_dir()
    if bundled is not None:
        _discover_dir(bundled, by_name)

    # 3. Contract-A cmds dirs: overlay cmds + scope (user/project) cmds, in
    #    precedence order (overlays first, project last -> project wins). The
    #    overlay libs go on sys.path first, so a command imports them.
    _add_overlay_libs(argv)
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


def main(argv=None) -> int:
    # `commands=` makes `run` use `duho.app` (not `duho.main`) so the resolved
    # built-ins + discovered set are the commands; dispatch calls each command's
    # `__call__`. `exit=False` returns the status, whatever ended the run.
    return duho.run(
        Dotagents,
        argv,
        exit=False,
        commands=_discover(argv),
        name="dotagents",
    )


if __name__ == "__main__":
    sys.exit(main())
