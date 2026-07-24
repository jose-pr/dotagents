"""`_wrappers`: the `dotagents` / `dotagents.cmd` scripts that put the command on PATH.

These are the reason `init` produces a *working* command rather than a directory of
config. Everything downstream shells out to `dotagents` by name -- the SessionStart
hook, overlay `bin/` entries, overlay setup scripts calling a sibling -- so a wrapper
that resolves to the wrong interpreter fails silently everywhere at once.

Run: ``PYTHONPATH=src python -m pytest tests/test_wrappers.py``
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotagents._wrappers import write_wrappers  # noqa: E402


def test_writes_both_forms_on_every_platform(tmp_path):
    """A Windows box with Git Bash needs both: cmd.exe resolves `.cmd` via PATHEXT,
    sh picks the extensionless file. Writing only the 'native' one (as the
    precursor did) breaks the other shell on the same machine."""
    written = write_wrappers(tmp_path / "bin", tmp_path / "dotagents.pyz")

    names = sorted(p.name for p in written)
    assert names == ["dotagents", "dotagents.cmd"]
    assert all(p.is_file() for p in written)


def test_embeds_an_absolute_interpreter_not_bare_python(tmp_path):
    """Bare `python` hits the Microsoft Store alias stub on Windows: it prints
    "Python was not found" and exits 0, so the wrapper looks installed and does
    nothing."""
    write_wrappers(tmp_path / "bin", tmp_path / "dotagents.pyz")

    for name in ("dotagents", "dotagents.cmd"):
        text = (tmp_path / "bin" / name).read_text(encoding="utf-8")
        assert Path(sys.executable).name in text
        assert not text.startswith("python "), "must not invoke a bare interpreter"
        # The interpreter is quoted and absolute, so a spaced path still works.
        assert '"' in text


def test_python_argument_overrides_the_interpreter(tmp_path):
    write_wrappers(tmp_path / "bin", tmp_path / "x.pyz", python="/usr/bin/python3.12")
    assert "/usr/bin/python3.12" in (tmp_path / "bin" / "dotagents").read_text(encoding="utf-8")


def test_relative_resolves_from_the_wrapper_dir(tmp_path):
    """`<scope>/bin/` uses this so moving the store does not break the command.

    Only applies when the pyz lives INSIDE the scope -- here, alongside `bin/`.
    """
    scope = tmp_path / "scope"
    scope.mkdir()
    pyz = scope / "dotagents.pyz"
    pyz.write_text("x", encoding="utf-8")
    write_wrappers(scope / "bin", pyz, relative=True)

    sh = (scope / "bin" / "dotagents").read_text(encoding="utf-8")
    cmd = (scope / "bin" / "dotagents.cmd").read_text(encoding="utf-8")
    assert '$(dirname "$0")' in sh
    assert "%~dp0" in cmd
    assert str(tmp_path) not in sh, "relative form must not embed the absolute path"


def test_relative_declines_a_pyz_outside_the_scope(tmp_path):
    """A pyz elsewhere on disk yields a long `../` chain that is unreadable and
    breaks if either side moves. Absolute is strictly better there."""
    pyz = tmp_path / "elsewhere" / "dotagents.pyz"
    pyz.parent.mkdir()
    pyz.write_text("x", encoding="utf-8")
    write_wrappers(tmp_path / "scope" / "bin", pyz, relative=True)

    sh = (tmp_path / "scope" / "bin" / "dotagents").read_text(encoding="utf-8")
    assert ".." not in sh, "must not emit a ../ chain"
    assert pyz.as_posix() in sh


def test_absolute_by_default(tmp_path):
    pyz = tmp_path / "dotagents.pyz"
    write_wrappers(tmp_path / "bin", pyz)
    sh = (tmp_path / "bin" / "dotagents").read_text(encoding="utf-8")
    assert pyz.as_posix() in sh
    assert "dirname" not in sh


def test_crlf_in_cmd_and_lf_in_sh(tmp_path):
    """cmd.exe needs CRLF; sh must not get CR or the shebang line breaks."""
    write_wrappers(tmp_path / "bin", tmp_path / "x.pyz")

    sh = (tmp_path / "bin" / "dotagents").read_bytes()
    cmd = (tmp_path / "bin" / "dotagents.cmd").read_bytes()
    assert b"\r" not in sh
    assert b"\r\n" in cmd


def test_the_sh_wrapper_actually_runs(tmp_path):
    """End-to-end: the generated script must execute, not just look right."""
    pyz = tmp_path / "probe.pyz"
    pyz.write_text("import sys; print('ran', *sys.argv[1:])\n", encoding="utf-8")
    write_wrappers(tmp_path / "bin", pyz)

    script = tmp_path / "bin" / "dotagents"
    proc = subprocess.run(
        ["sh", str(script), "hello"], capture_output=True, text=True
    )
    if proc.returncode != 0 and "sh" in (proc.stderr or ""):  # pragma: no cover
        import pytest

        pytest.skip("no POSIX sh available")
    assert "ran hello" in proc.stdout


def test_relative_falls_back_when_no_relative_path_exists(tmp_path, monkeypatch):
    """Different drives on Windows: relpath raises, so use the absolute path."""
    def _boom(*a, **k):
        raise ValueError("different drives")

    monkeypatch.setattr(os.path, "relpath", _boom)
    pyz = tmp_path / "dotagents.pyz"
    write_wrappers(tmp_path / "bin", pyz, relative=True)

    sh = (tmp_path / "bin" / "dotagents").read_text(encoding="utf-8")
    assert pyz.as_posix() in sh, "must fall back to an absolute path, not crash"
