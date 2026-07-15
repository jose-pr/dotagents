#!/usr/bin/env python3
"""Entry point for the dotagents CLI (init / install / build-pyz / audit).

Thin shim over ``dotagents.cli.main()``, kept at this filename so existing
muscle-memory/docs pointing at ``python install.py`` still work. Requires the
``dotagents`` package (and its ``duho``/``pathlib_next`` dependencies) to be
importable -- run ``pip install -e .`` first from a raw checkout, or install
the published ``dotagents`` package. The ``sys.path`` fallback below lets an
un-pip-installed checkout's ``src/`` layout resolve directly, provided
``duho``/``pathlib_next`` are already importable some other way (e.g. also
pip-installed editable).

Usage: python install.py <init|install|audit|build-pyz> [options]
Run `python install.py --help` for the full subcommand/flag reference.
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from dotagents.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
