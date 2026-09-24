"""summarize_run.py finds the command the way a shell would, and gives every run
its own log. Runs the real script in subprocesses; tmp dirs only."""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "summarize_run.py"


def _run(*argv, cwd, env=None):
    return subprocess.run([sys.executable, str(TOOL), *argv], cwd=str(cwd), env=env,
                          capture_output=True, text=True)


def test_a_relative_interpreter_path_runs(tmp_path):
    """`.venv/<ver>/Scripts/python.exe` relative to the cwd died with WinError 2:
    CreateProcess does not resolve a relative path with a directory part."""
    exe = Path(sys.executable)
    try:
        rel = os.path.relpath(exe, tmp_path)
    except ValueError:  # another drive: no relative path exists
        pytest.skip("the interpreter is on another drive")
    out = _run("--log", "x.log", "--", rel, "-c", "print('hello')", cwd=tmp_path)
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.startswith("=== PASS")
    assert (tmp_path / "x.log").read_text(encoding="utf-8").strip() == "hello"


@pytest.mark.skipif(os.name != "nt", reason="PATHEXT and .cmd wrappers are Windows'")
def test_a_cmd_wrapper_on_path_runs(tmp_path):
    """`dotagents` is `dotagents.cmd` on Windows; CreateProcess never finds it."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "mytool.cmd").write_text("@echo wrapper-ran %*\r\n", encoding="ascii")
    env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    out = _run("--log", "x.log", "--", "mytool", "arg", cwd=tmp_path, env=env)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "wrapper-ran arg" in (tmp_path / "x.log").read_text(encoding="utf-8")


def test_a_missing_command_is_exit_127_with_a_verdict(tmp_path):
    out = _run("--", "no-such-command-xyz", cwd=tmp_path)
    assert out.returncode == 127
    assert out.stdout.startswith("=== FAIL (exit 127)") and "no-such-command-xyz" in out.stdout


def test_without_log_each_run_gets_its_own_file_outside_the_cwd(tmp_path):
    """A fixed ./run.log: two parallel runs overwrote each other's evidence."""
    a = _run("--", sys.executable, "-c", "print('first')", cwd=tmp_path)
    b = _run("--", sys.executable, "-c", "print('second')", cwd=tmp_path)
    logs = [re.search(r"-> (.+?) \(", o.stdout).group(1) for o in (a, b)]
    assert logs[0] != logs[1]
    assert Path(logs[0]).read_text(encoding="utf-8").strip() == "first"
    assert Path(logs[1]).read_text(encoding="utf-8").strip() == "second"
    assert not list(tmp_path.iterdir()), "nothing written into the cwd"
