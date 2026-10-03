"""Make the overlay's bin/ and lib/ importable for the tests, regardless of cwd;
and pin NET_CURL_COMPAT, so the fallback answers as the newest curl measured
whatever curl the machine has (the parity tests set the real curl's own)."""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT / "bin", _ROOT / "lib"):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)


@pytest.fixture(autouse=True)
def _newest_curl(monkeypatch):
    from httplib.cli import compat

    monkeypatch.setenv(compat.VAR, "%d.%d.%d" % compat.NEWEST)
    monkeypatch.setattr(compat, "_cached", {})
