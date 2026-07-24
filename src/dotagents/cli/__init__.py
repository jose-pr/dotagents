"""dotagents CLI: init / install / link / sync / audit / context / env /
overlays / build-pyz subcommands.

The per-command classes live in sibling modules (`cli/init.py`, `cli/install.py`,
...); this package base holds the shared helpers (in `cli/_common.py`, re-exported
here), the `Dotagents(LoggingArgs, Cli)` umbrella that registers every command,
`main()` (the `install.py` shim + `python -m dotagents` entrypoint), and
`_repoint_zipapp_sources` (the zipapp source-extraction shim).

`_compose_block` and `_package_data_dir` are re-exported at package level because
other package modules import them as `dotagents.cli._compose_block` /
`dotagents.cli._package_data_dir` (see `_overlays.py`, `_scope.py`).
"""

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

# Import each command class to register it on the umbrella. Importing the command
# modules here (never the reverse) keeps the dependency edges one-directional:
# command modules -> cli._common / dotagents._*, and cli/__init__ -> command modules.
from dotagents.cli.audit import Audit
from dotagents.cli.build_pyz import BuildPyz
from dotagents.cli.context import Context
from dotagents.cli.env import Env
from dotagents.cli.init import Init
from dotagents.cli.install import Install
from dotagents.cli.link import Link
from dotagents.cli.overlays import (  # noqa: F401  (re-exported for tests)
    OverlayAdd,
    OverlayList,
    OverlayRemove,
    OverlaySync,
    Overlays,
)
from dotagents.cli.sync import Sync


# The cli submodules whose sources duho introspects for flag/help definitions.
# Every module that defines a field-bearing command class must be repointed
# inside a zipapp (see `_repoint_zipapp_sources`).
_COMMAND_MODULES = (
    "dotagents.cli.init",
    "dotagents.cli.install",
    "dotagents.cli.link",
    "dotagents.cli.sync",
    "dotagents.cli.audit",
    "dotagents.cli.context",
    "dotagents.cli.env",
    "dotagents.cli.overlays",
    "dotagents.cli.build_pyz",
)


class Dotagents(LoggingArgs, Cli):
    """Umbrella CLI for installing and building the dotagents config."""

    _version_ = __version__
    _subcommands_ = [Init, Install, Link, Sync, Audit, BuildPyz, Context, Env, Overlays]

    def __call__(self) -> int:
        self._logger_.info(
            "pick a subcommand, e.g. `init`, `install`, `overlays`, `link`, `sync`, "
            "`audit`, `build-pyz`"
        )
        return 0


def _repoint_zipapp_sources() -> None:
    """Make duho's AST field-introspection work when running from a zipapp.

    duho 0.3.3 discovers each command's flags + help by parsing its module
    source (`_introspect.getclsdef` -> `Path(module.__file__).read_text()`).
    Inside a `.pyz` the module `__file__` is a zip-internal path `read_text()`
    can't open, and duho catches that `OSError` *before* its `inspect.getsource`
    fallback -- so every field silently loses its declared flags and help (the
    positional `path` degrades to `--path`, `--from` to `--from-`, help text
    vanishes). Extract the affected module sources to real temp files and
    repoint `__file__` so the read succeeds. A no-op for a plain install, where
    `__file__` already exists on disk.

    After the cli split each command class lives in its own `dotagents.cli.<x>`
    module, so EVERY such module is repointed (not just `dotagents.cli`), plus
    duho's `LoggingArgs` preset (`duho.presets`) -- the only field-bearing
    sources dispatched here.

    Tracked upstream: jose-pr/duho#1 — drop this shim (and the build-pyz CI
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
    return duho.main(Dotagents, argv)


if __name__ == "__main__":
    sys.exit(main())
