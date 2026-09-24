"""Regression tests for the packaging fixes: what `build-pyz` vendors and how
it writes the archive, the single-sourced version, and the declared
dependencies/extras behind the TOML and YAML readers.

`build-pyz` runs for real against a copy of the tree, with only pip's
vendoring step stubbed (no network); the stub plants what `pip --target`
leaves behind so the cleanup can be checked.
"""

import re
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from dotagents import __version__
from dotagents._sources import SourceError, parse_document
from dotagents.cli import build_pyz
from dotagents.cli.build_pyz import BuildPyz

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

#: A line appended to the copied `__init__.py`: it must reach the pyz as-is.
EXTRA_LINE = "PACKAGING_PROBE = 1\n"


def _pyproject() -> dict:
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))


def _copy_tree(dest: Path) -> Path:
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(REPO / name, dest / name)
    shutil.copytree(SRC, dest / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    init_py = dest / "src" / "dotagents" / "__init__.py"
    with open(init_py, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("\n" + EXTRA_LINE)
    return dest


def _fake_pip(calls: list):
    """Stand-in for ``subprocess.call`` running ``pip install --target``:
    records argv and plants a vendored module, its dist-info, and the
    console-script launcher pip writes under ``<target>/bin``."""

    def call(argv, *args, **kwargs):
        calls.append(list(argv))
        target = Path(argv[argv.index("--target") + 1])
        (target / "fakedep").mkdir(parents=True)
        (target / "fakedep" / "__init__.py").write_text("", encoding="utf-8")
        info = target / "fakedep-1.0.dist-info"
        info.mkdir()
        (info / "METADATA").write_text("Name: fakedep\nVersion: 1.0\n\n", encoding="utf-8")
        (target / "bin").mkdir()
        (target / "bin" / "uripath.exe").write_bytes(b"MZ#!C:\\builder\\python.exe\n")
        return 0

    return call


@pytest.fixture
def built(tmp_path, monkeypatch):
    """Build a pyz from a copy of the tree; returns ``(archive, pip argv)``."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _copy_tree(proj)
    monkeypatch.setattr(build_pyz, "__file__", str(proj / "src" / "dotagents" / "cli" / "build_pyz.py"))
    calls: list = []
    monkeypatch.setattr(build_pyz.subprocess, "call", _fake_pip(calls))
    cmd = BuildPyz()
    cmd.out = tmp_path / "out" / "dotagents.pyz"
    assert cmd() == 0
    assert len(calls) == 1
    return zipfile.ZipFile(cmd.out), calls[0]


# --------------------------------------------------------------------------
# What gets vendored
# --------------------------------------------------------------------------


def test_pip_launchers_are_not_vendored(built):
    archive, _ = built
    names = archive.namelist()
    assert "fakedep/__init__.py" in names
    assert not [n for n in names if n.startswith("bin/")], names


def test_pip_resolves_pure_python_wheels_for_the_python_floor(built):
    _, argv = built
    requires = _pyproject()["project"]["requires-python"]
    floor = re.fullmatch(r">=\s*([\d.]+)", requires).group(1)
    assert "--only-binary=:all:" in argv
    assert argv[argv.index("--platform") + 1] == "any"
    assert argv[argv.index("--implementation") + 1] == "py"
    assert argv[argv.index("--python-version") + 1] == floor


def test_extras_help_says_sftp_cannot_be_vendored():
    source = (SRC / "dotagents" / "cli" / "build_pyz.py").read_text(encoding="utf-8")
    help_text = source[source.index("extras: str"):source.index('("--extras",)')]
    assert "sftp" in help_text and "cannot" in help_text


# --------------------------------------------------------------------------
# How the archive is written
# --------------------------------------------------------------------------


def test_package_init_is_copied_not_rewritten(built):
    archive, _ = built
    text = archive.read("dotagents/__init__.py").decode("utf-8")
    assert EXTRA_LINE in text
    assert '__version__ = "%s"' % __version__ in text


# --------------------------------------------------------------------------
# Single-sourced version
# --------------------------------------------------------------------------


def test_version_is_single_sourced_from_the_package():
    doc = _pyproject()
    assert "version" not in doc["project"]
    assert "version" in doc["project"]["dynamic"]
    assert doc["tool"]["hatch"]["version"]["path"] == "src/dotagents/__init__.py"


def test_build_pyz_no_longer_stamps_a_version():
    source = (SRC / "dotagents" / "cli" / "build_pyz.py").read_text(encoding="utf-8")
    assert "_PYPROJECT_VERSION_RE" not in source
    assert "__version__ =" not in source
