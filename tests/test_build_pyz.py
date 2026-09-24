"""Unit coverage for `build-pyz` and for what the built artifacts carry.

The version-stamping regex, the dependency pins, and a real staging of the
package into a zipapp (with pip's vendoring step stubbed out, so no network).
The wheel/sdist case builds for real with ``python -m build --no-isolation``
and skips when `build`/`hatchling` are not installed (the `dev` extra has both).

Run from the repo root: ``python -m pytest tests/``.
"""

import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

from dotagents.cli import build_pyz
from dotagents.cli.build_pyz import BuildPyz, _PYPROJECT_VERSION_RE

#: Files planted in a copy of the tree that must never reach an artifact.
PRIVATE = ("AGENTS.local.md", "config.local.toml", "CLAUDE.md", "CLAUDE.local.md")


def test_no_tools_bundling_surface():
    """The repo's `tools/` is CI tooling and is not shipped in the .pyz.

    Cheap guard for the part of that promise a unit test can see: the command
    exposes no `--tools-dir` knob and never references a `_tools` destination.
    (That the built archive actually contains no tools/ entry is asserted by
    tools/pyz_smoke.sh, run in CI against a real build.)
    """
    assert not hasattr(BuildPyz, "tools_dir")
    source = (SRC / "dotagents" / "cli" / "build_pyz.py").read_text(encoding="utf-8")
    # Only the explanatory comment may mention it -- no code path may.
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "_tools" not in code
    assert "tools_dir" not in code


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


def test_ignores_the_word_version_inside_another_value():
    # A quoted value that merely contains the word "version" is not a match:
    # the pattern wants a line that STARTS with `version =`. It is not
    # table-aware -- the first such line in the file wins, whatever table it
    # sits in -- which holds for this repo's pyproject, where `[project]`'s
    # comes first.
    text = 'description = "the version field below is what matters"\nversion = "1.2.3"\n'
    match = _PYPROJECT_VERSION_RE.search(text)
    assert match is not None
    assert match.group(1) == "1.2.3"


def test_reads_the_real_repo_pyproject_toml():
    """Regression: __init__.py's __version__ went stale for two releases
    because nothing enforced it matched pyproject.toml. This pins that the
    two are in sync RIGHT NOW -- bump both together, or this fails."""
    from dotagents import __version__

    match = _PYPROJECT_VERSION_RE.search((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert match is not None
    assert __version__ == match.group(1)


def test_vendored_pins_are_the_declared_floors():
    """The .pyz vendors the FLOOR of each range pyproject.toml declares.

    The two copies are maintained by hand; a floor raised without moving the
    pin ships a zipapp bundling a version `pip install dotagents-cli` refuses.
    """
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    floors = dict(re.findall(r'"(duho|pathlib_next)>=([^,"]+)', text))
    assert floors == {
        "duho": BuildPyz.duho_version,
        "pathlib_next": BuildPyz.pathlib_next_version,
    }


def _copy_tree(dest: Path) -> Path:
    """A copy of the buildable project (no VCS, no .gitignore) with private
    files planted at the root and inside the package."""
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(REPO / name, dest / name)
    shutil.copytree(SRC, dest / "src", ignore=ignore)
    pkg = dest / "src" / "dotagents"
    for name in PRIVATE:
        (pkg / name).write_text("private\n", encoding="utf-8")
        (dest / name).write_text("private\n", encoding="utf-8")
    # `CLAUDE*` is case-sensitive: a lower-case module name is ordinary code.
    (pkg / "claude_probe.py").write_text("", encoding="utf-8")
    return dest


def _assert_clean(names: "list[str]", prefix: str) -> None:
    basenames = [n.rsplit("/", 1)[-1] for n in names]
    leaked = [n for n in names if re.search(r"\.local\.|(^|/)CLAUDE", n)]
    assert not leaked, "private files shipped: %s" % leaked
    assert prefix + "dotagents/AGENTS.md" in names
    assert prefix + "dotagents/py.typed" in names
    assert "claude_probe.py" in basenames


def test_pyz_leaves_private_files_out(tmp_path, monkeypatch):
    """A locally built pyz never carries `*.local.*` or `CLAUDE*` files.

    The package is staged exactly as a real build does; only pip's vendoring
    of duho/pathlib_next is stubbed, which touches nothing under test here.
    """
    (tmp_path / "proj").mkdir()
    proj = _copy_tree(tmp_path / "proj")
    fake_module = proj / "src" / "dotagents" / "cli" / "build_pyz.py"
    monkeypatch.setattr(build_pyz, "__file__", str(fake_module))
    calls = []
    monkeypatch.setattr(
        build_pyz.subprocess, "call", lambda argv, *a, **k: calls.append(argv) or 0
    )
    cmd = BuildPyz()
    cmd.out = tmp_path / "out" / "dotagents.pyz"
    assert cmd() == 0
    assert calls and "--target" in calls[0]
    names = zipfile.ZipFile(cmd.out).namelist()
    _assert_clean(names, "")


def test_wheel_and_sdist_leave_private_files_out(tmp_path):
    """`python -m build` output carries the package and no private files,
    on the pyproject excludes alone (the copy has no .gitignore)."""
    pytest.importorskip("build")
    pytest.importorskip("hatchling")
    proj = tmp_path / "proj"
    proj.mkdir()
    _copy_tree(proj)
    out = tmp_path / "dist"
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--sdist", "--wheel",
         "--outdir", str(out), str(proj)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    (wheel,) = out.glob("*.whl")
    (sdist,) = out.glob("*.tar.gz")
    _assert_clean(zipfile.ZipFile(wheel).namelist(), "")
    with tarfile.open(sdist) as tar:
        members = [m.name.split("/", 1)[1] for m in tar.getmembers() if "/" in m.name]
    _assert_clean(members, "src/")
    metadata = zipfile.ZipFile(wheel).read(
        next(n for n in zipfile.ZipFile(wheel).namelist() if n.endswith(".dist-info/METADATA"))
    ).decode("utf-8")
    assert "License-Expression: MIT" in metadata
