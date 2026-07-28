"""Unit coverage for `build-pyz`'s pyproject.toml version-stamping (D-latest).

Only the pure regex/string piece -- the real pip-install-and-zip flow is
network-heavy and covered by the CI "Built .pyz keeps flags/help/positional"
job, not duplicated here.

Run from the repo root: ``python -m pytest tests/``.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from dotagents.cli.build_pyz import _PYPROJECT_VERSION_RE  # noqa: E402


def test_matches_real_pyproject_version_line():
    text = (
        '[project]\n'
        'name = "dotagents-cli"\n'
        'version = "0.3.2"\n'
        'authors = [{ name = "Jose A." }]\n'
    )
    match = _PYPROJECT_VERSION_RE.search(text)
    assert match is not None
    assert match.group(1) == "0.3.2"


def test_ignores_a_version_looking_value_elsewhere():
    # Only the top-level `version = "..."` under [project] should match, not
    # some other quoted string that happens to contain the word "version".
    text = 'description = "the version field below is what matters"\nversion = "1.2.3"\n'
    match = _PYPROJECT_VERSION_RE.search(text)
    assert match is not None
    assert match.group(1) == "1.2.3"


def test_reads_the_real_repo_pyproject_toml():
    """Regression: __init__.py's __version__ went stale for two releases
    because nothing enforced it matched pyproject.toml. This pins that the
    two are in sync RIGHT NOW -- bump both together, or this fails."""
    from dotagents import __version__

    pyproject_text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    match = _PYPROJECT_VERSION_RE.search(pyproject_text)
    assert match is not None
    assert __version__ == match.group(1)
