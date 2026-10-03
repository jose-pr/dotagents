"""`dotagents summarize-run` finds the command the way a shell would, gives every
run its own log, and returns the command's own exit code."""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest

duho = pytest.importorskip("duho")

MODULE = Path(__file__).resolve().parents[1] / "cmds" / "summarize_run.py"
_spec = importlib.util.spec_from_file_location("summarize_run_cmd", MODULE)
summarize_run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = summarize_run  # duho resolves field annotations through sys.modules
_spec.loader.exec_module(summarize_run)


def run(capsys, *argv, cwd):
    """Build the command from an argument vector and call it in `cwd`."""
    here = os.getcwd()
    os.chdir(cwd)
    try:
        code = duho.parse(summarize_run.SummarizeRun, [str(a) for a in argv])()
    finally:
        os.chdir(here)
    return code, capsys.readouterr().out


def test_a_relative_interpreter_path_runs(tmp_path, capsys):
    """Process creation on Windows does not resolve a relative path with a
    directory part."""
    try:
        rel = os.path.relpath(Path(sys.executable), tmp_path)
    except ValueError:  # another drive: no relative path exists
        pytest.skip("the interpreter is on another drive")
    code, out = run(capsys, "--output", "x.log", "--", rel, "-c", "print('hello')", cwd=tmp_path)
    assert code == 0, out
    assert out.startswith("=== PASS")
    assert (tmp_path / "x.log").read_text(encoding="utf-8").strip() == "hello"


@pytest.mark.skipif(os.name != "nt", reason="PATHEXT and .cmd wrappers are Windows'")
def test_a_cmd_wrapper_on_path_runs(tmp_path, capsys, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "mytool.cmd").write_bytes(b"@echo wrapper-ran %*\r\n")
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    code, out = run(capsys, "--output", "x.log", "--", "mytool", "arg", cwd=tmp_path)
    assert code == 0, out
    assert "wrapper-ran arg" in (tmp_path / "x.log").read_text(encoding="utf-8")


def test_a_missing_command_is_exit_127_with_a_verdict(tmp_path, capsys):
    code, out = run(capsys, "--", "no-such-command-xyz", cwd=tmp_path)
    assert code == 127
    assert out.startswith("=== FAIL (exit 127)") and "no-such-command-xyz" in out


def test_the_exit_code_and_the_notable_lines_come_through(tmp_path, capsys):
    script = "import sys; print('collecting'); print('FAILED test_x'); sys.exit(3)"
    code, out = run(capsys, "--", sys.executable, "-c", script, cwd=tmp_path)
    assert code == 3
    assert out.startswith("=== FAIL (exit 3)")
    assert "FAILED test_x" in out and "collecting" not in out.split("\n", 2)[2]


def test_without_log_each_run_gets_its_own_file_outside_the_cwd(tmp_path, capsys):
    _, a = run(capsys, "--", sys.executable, "-c", "print('first')", cwd=tmp_path)
    _, b = run(capsys, "--", sys.executable, "-c", "print('second')", cwd=tmp_path)
    logs = [re.search(r"-> (.+?) \(", o).group(1) for o in (a, b)]
    assert logs[0] != logs[1]
    assert Path(logs[0]).read_text(encoding="utf-8").strip() == "first"
    assert Path(logs[1]).read_text(encoding="utf-8").strip() == "second"
    assert not list(tmp_path.iterdir()), "nothing written into the cwd"


def test_a_timeout_stops_the_command_and_fails(tmp_path, capsys):
    script = "import time; time.sleep(30)"
    code, out = run(capsys, "--timeout", "0.5", "--", sys.executable, "-c", script, cwd=tmp_path)
    assert code == 127
    assert "timed out" in out


def test_no_command_is_a_usage_error(tmp_path, capsys):
    with pytest.raises(SystemExit, match="no command given"):
        run(capsys, cwd=tmp_path)
