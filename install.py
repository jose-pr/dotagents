#!/usr/bin/env python3
"""Entry point for the dotagents CLI (init / overlays / context / env / build-pyz).

Thin front over ``dotagents.cli.main()``, kept at this filename so existing
muscle-memory/docs pointing at ``python install.py`` still work.

Self-bootstrapping, inside a virtual environment: the CLI needs the
``dotagents`` package and its ``duho``/``pathlib_next`` dependencies
importable. If they aren't -- e.g. a fresh venv with nothing installed yet --
this shim installs this checkout **into the interpreter that is running it**
(``sys.executable -m pip install -e .``) and retries, exactly once. When
everything is already importable it just dispatches -- no pip is ever run.

Outside a virtual environment it refuses to install anything unless
``--bootstrap`` is passed, and it refuses even then when the interpreter is
externally managed (PEP 668), where pip would fail anyway. The alternatives
it names: a venv, ``pip install dotagents-cli``, or the release ``dotagents.pyz``.

Usage: python install.py [--bootstrap] <init|overlays|context|env|build-pyz|...> [options]
Run `python install.py --help` for the full subcommand/flag reference.
"""
import importlib
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE / "src"

# Set to "1" once we've already tried a pip install, so a still-broken import
# can't loop forever re-installing.
_BOOTSTRAP_FLAG = "DOTAGENTS_BOOTSTRAPPED"

#: Opt-in to installing into an interpreter that is not a virtual environment.
_BOOTSTRAP_ARG = "--bootstrap"

_ALTERNATIVES = (
    "  - create a virtual environment and run this again from it:\n"
    "      python -m venv .venv  (then activate it)\n"
    "  - install the released CLI into one:  pip install dotagents-cli\n"
    "  - or use the self-contained release pyz, which needs no install:\n"
    "      https://github.com/jose-pr/dotagents/releases/latest/download/dotagents.pyz\n"
)


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


def _in_virtualenv():
    """True inside a venv/virtualenv, or a conda environment."""
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return True
    conda = os.environ.get("CONDA_PREFIX")
    return bool(conda) and Path(conda).resolve() == Path(sys.prefix).resolve()


def _externally_managed():
    """True when this interpreter carries a PEP 668 ``EXTERNALLY-MANAGED``
    marker, which makes pip refuse to install into it."""
    stdlib = sysconfig.get_path("stdlib")
    return bool(stdlib) and (Path(stdlib) / "EXTERNALLY-MANAGED").is_file()


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


def _refusal():
    """Why this interpreter will not be bootstrapped, or None when it may be."""
    if _in_virtualenv():
        return None
    if _BOOTSTRAP_ARG not in sys.argv[1:]:
        return (
            "dotagents: the CLI is not importable, and %s is not a virtual "
            "environment, so nothing was installed into it. Either:\n%s"
            "  - or pass %s to install this checkout into it anyway.\n"
            % (sys.executable, _ALTERNATIVES, _BOOTSTRAP_ARG)
        )
    if _externally_managed():
        return (
            "dotagents: %s is externally managed (PEP 668), so pip will not "
            "install into it. Instead:\n%s" % (sys.executable, _ALTERNATIVES)
        )
    return None


def main():
    entry = _import_main()
    if entry is None and not os.environ.get(_BOOTSTRAP_FLAG):
        refusal = _refusal()
        if refusal:
            sys.stderr.write(refusal)
            return 1
        os.environ[_BOOTSTRAP_FLAG] = "1"
        if _bootstrap():
            # The dependencies landed in a directory already on sys.path; the
            # import system caches directory listings, so drop those caches
            # before retrying.
            importlib.invalidate_caches()
            entry = _import_main()
    if entry is None:
        sys.stderr.write(
            "dotagents: could not import the CLI after bootstrapping.\n"
            "Install it manually with:  %s -m pip install -e %s\n"
            % (sys.executable, _HERE)
        )
        return 1
    if _BOOTSTRAP_ARG in sys.argv[1:]:
        sys.argv.remove(_BOOTSTRAP_ARG)
    return entry()


if __name__ == "__main__":
    sys.exit(main())
