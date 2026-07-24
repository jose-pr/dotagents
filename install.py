#!/usr/bin/env python3
"""Entry point for the dotagents CLI (init / install / overlays / build-pyz / ...).

Thin front over ``dotagents.cli.main()``, kept at this filename so existing
muscle-memory/docs pointing at ``python install.py`` still work.

Self-bootstrapping: the CLI needs the ``dotagents`` package and its
``duho``/``pathlib_next`` dependencies importable. If they aren't -- e.g. a raw
checkout with nothing installed yet -- this shim installs them **into the same
interpreter that is running it** (``sys.executable -m pip install -e .``) and
retries, exactly once, so a first-time user can run ``python install.py init``
with no separate ``pip install`` step. Using ``sys.executable -m pip`` (not a
bare ``pip`` off PATH) guarantees the install lands in *this* Python, so the
import that follows actually sees it. When everything is already importable it
just dispatches -- no pip is ever run.

Usage: python install.py <init|install|overlays|audit|build-pyz|...> [options]
Run `python install.py --help` for the full subcommand/flag reference.
"""
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE / "src"

# Set to "1" once we've already tried a pip install, so a still-broken import
# can't loop forever re-installing.
_BOOTSTRAP_FLAG = "DOTAGENTS_BOOTSTRAPPED"


def _import_main():
    """Return ``dotagents.cli.main`` if importable, else None.

    The ``src/`` fallback lets an un-pip-installed checkout resolve its
    ``src/`` layout directly, provided ``duho``/``pathlib_next`` are importable
    some other way. A plain editable install makes all three importable without
    the fallback.
    """
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    try:
        from dotagents.cli import main
    except ImportError:
        return None
    return main


def _bootstrap():
    """pip-install this checkout (editable) into the running interpreter, once.

    Returns True on a successful install, False otherwise. Only attempts an
    install from an actual checkout (a ``pyproject.toml`` next to this file);
    a stray copy of this shim elsewhere just reports the manual command.
    """
    if not (_HERE / "pyproject.toml").is_file():
        return False
    sys.stderr.write(
        "dotagents: dependencies not found; installing this checkout "
        "(%s -m pip install -e .) ...\n" % Path(sys.executable).name
    )
    sys.stderr.flush()
    # -m pip on THIS interpreter -> the package lands where the import looks.
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(_HERE)],
        cwd=str(_HERE),
    )
    return proc.returncode == 0


def main():
    entry = _import_main()
    if entry is None and not os.environ.get(_BOOTSTRAP_FLAG):
        os.environ[_BOOTSTRAP_FLAG] = "1"
        if _bootstrap():
            # Drop any negatively-cached import attempts, then retry.
            for mod in ("dotagents", "dotagents.cli"):
                sys.modules.pop(mod, None)
            entry = _import_main()
    if entry is None:
        sys.stderr.write(
            "dotagents: could not import the CLI after bootstrapping.\n"
            "Install it manually with:  %s -m pip install -e %s\n"
            % (sys.executable, _HERE)
        )
        return 1
    return entry()


if __name__ == "__main__":
    sys.exit(main())
