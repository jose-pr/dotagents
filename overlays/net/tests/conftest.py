"""Make the overlay's bin/ and lib/ importable for the tests, regardless of cwd."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT / "bin", _ROOT / "lib"):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)
