"""Make the overlay's cmds/ importable for the tests, regardless of cwd.

`pyvenv.py`'s `Pyvenv` class inherits `dotagents.cli.DotAgentsArgs` (the shared
scope-args base every scope-aware command uses), so unlike private-sync/net's
tests, `dotagents` itself must be importable to even collect this module -- add
the repo's `src/` when running straight out of a checkout (dev use); a real
install exercises the actually-installed package instead.
"""
import sys
from pathlib import Path

_OVERLAY_ROOT = Path(__file__).resolve().parents[1]
# Best-effort: only present when this branch is checked out inside (or beside)
# a dotagents repo checkout, e.g. as the `overlays` worktree this project uses.
_REPO_SRC = _OVERLAY_ROOT.parents[2] / "src"
_candidates = [_OVERLAY_ROOT / "cmds"]
if (_REPO_SRC / "dotagents").is_dir():
    _candidates.append(_REPO_SRC)
for _p in _candidates:
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)
