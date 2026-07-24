"""Make the overlay's lib/ importable for the tests, regardless of cwd.

Mirrors the `net` overlay's conftest. The commands themselves do NOT rely on this
(they load `lib/_link.py` by path -- see `cmds/link_project.py`); the tests import
`_link` directly, so they put `lib/` on `sys.path` here.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT / "lib",):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)
