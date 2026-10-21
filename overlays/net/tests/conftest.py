"""Make the overlay's bin/ and lib/ importable for the tests, regardless of
cwd; and give each test the caller's SYSHTTP_PREFIX and PYTHONPATH back,
which the launcher sets for what it starts."""
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT / "bin", _ROOT / "lib"):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)


@pytest.fixture(autouse=True)
def _restore_prefix(monkeypatch):
    # setenv first, so monkeypatch records the original state (set or not)
    # and restores it after the test.
    monkeypatch.setenv("SYSHTTP_PREFIX", "")
    monkeypatch.delenv("SYSHTTP_PREFIX")
    pythonpath = os.environ.get("PYTHONPATH")
    monkeypatch.setenv("PYTHONPATH", "")
    if pythonpath is None:
        monkeypatch.delenv("PYTHONPATH")
    else:
        monkeypatch.setenv("PYTHONPATH", pythonpath)
