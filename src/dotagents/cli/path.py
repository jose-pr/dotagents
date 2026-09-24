"""`dotagents path` -- the dirs dotagents puts on PATH (or PYTHONPATH)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from dotagents.cli._common import DotAgentsArgs, _write_stdout, resolve_user_store

#: Accepted ``--format`` values.
PATH_FORMATS = ("list", "native", "posix", "json")


class PathCmd(DotAgentsArgs):
    """Print the bin dirs dotagents puts on PATH (--lib: the lib dirs).

    Highest precedence first, the order ``dotagents env`` puts them at the
    front of ``PATH`` / ``PYTHONPATH``: the project's ``.agents`` first, and
    each store's own dir before its overlays'. The bin list includes dirs
    that do not exist yet (as ``env`` does, so one created later is found);
    the lib list only existing ones. Nothing is run -- no env file, no ``env.py`` -- so it is a
    cheap way to put the bins on a shell's ``PATH``::

        export PATH="$(dotagents path --format posix):$PATH"

    Formats: ``list`` (one per line, the default), ``native`` (joined with
    ``os.pathsep``), ``posix`` (``:``-joined; on Windows each dir in its
    MSYS2 form, ``/c/...``), ``json``. The scope flags mean what they mean for
    ``env``: the user store is ``--agents-dir`` -> ``$AGENTS_HOME`` ->
    ``~/.agents``, and ``-g`` leaves the project's dirs out."""

    _parsername_ = "path"

    lib: bool = False
    "Print the lib dirs (PYTHONPATH) instead of the bin dirs (PATH)."
    ("--lib",)

    format: str = "list"
    "Output format: list (one per line), native (os.pathsep-joined), posix (':'-joined, MSYS2 form on Windows), json."
    ("--format",)

    # Both flags come from `DotAgentsArgs`; only their HELP is restated, with
    # `env`'s meaning (-g skips the project tier, the store is the user store).
    global_scope: bool = False
    "Leave out the project's dirs (the store root is unaffected)."
    ("--global", "-g")

    agents_dir: "Optional[Path]" = None
    "User store root override (default: $AGENTS_HOME, else ~/.agents)."
    ("--agents-dir",)

    def __call__(self) -> int:
        from dotagents import _env, _scope

        if self.format not in PATH_FORMATS:
            raise SystemExit(
                "error: --format must be one of %s (got %r)" % (", ".join(PATH_FORMATS), self.format)
            )
        scope = _scope.Scope.of(
            agents_dir=resolve_user_store(self.agents_dir),
            project_root=_scope.project_root_default(),
            global_scope=self.global_scope,
        )
        found = _env.get_lib_paths(scope) if self.lib else _env.get_bin_paths(scope)
        dirs: "list[str]" = []
        for p in reversed(found):  # contract A lists lowest precedence first
            if str(p) not in dirs:
                dirs.append(str(p))
        if self.format == "json":
            _write_stdout(json.dumps(dirs) + "\n")
        elif self.format == "native":
            _write_stdout(os.pathsep.join(dirs) + "\n")
        elif self.format == "posix":
            if os.name == "nt":
                from dotagents.cli.env import _to_posix_path

                dirs = [_to_posix_path(d) for d in dirs]
            _write_stdout(":".join(dirs) + "\n")
        else:
            _write_stdout("".join(d + "\n" for d in dirs))
        return 0
