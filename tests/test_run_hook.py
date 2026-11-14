"""The bounded runner a hook runs under: time, output, standard error, standard input and the
processes it starts."""

from __future__ import annotations

import os
import sys
import time

import pytest

from dotagents import _run

PYTHON = sys.executable


def run(code: str, *, timeout: int = 20, stdin: bytes = b"", max_output: int = 1024,
        max_stderr: int = 1024, env=None, cwd=None):
    return _run.run_hook([PYTHON, "-c", code], cwd=cwd or os.getcwd(), env=env or dict(os.environ),
                         timeout=timeout, stdin=stdin, max_output=max_output, max_stderr=max_stderr)


def test_output_and_status():
    done = run("print('hi'); raise SystemExit(3)")
    assert (done["exit"], done["stdout"].strip()) == (3, b"hi")
    assert not done["timed_out"] and not done["too_large"]


def test_the_payload_reaches_standard_input():
    done = run("import sys; sys.stdout.write(sys.stdin.read().upper())", stdin=b"abc")
    assert done["stdout"] == b"ABC"


def test_a_hook_that_never_reads_its_input_has_not_failed():
    big = b"x" * (4 * 1024 * 1024)
    done = run("print('ok')", stdin=big)
    assert done["exit"] == 0 and done["stdout"].strip() == b"ok"


def test_a_run_past_its_time_is_stopped():
    started = time.monotonic()
    done = run("import time; time.sleep(60)", timeout=1)
    assert done["timed_out"] is True
    assert time.monotonic() - started < 20


def test_output_past_the_bound_stops_the_run_and_keeps_nothing_past_it():
    done = run("import sys\nwhile True:\n    sys.stdout.write('x' * 4096); sys.stdout.flush()",
               max_output=100, timeout=30)
    assert done["too_large"] is True
    assert len(done["stdout"]) <= 100


def test_standard_error_is_kept_to_its_bound_and_never_blocks_the_hook():
    done = run("import sys; sys.stderr.write('e' * 100000); print('done')", max_stderr=10)
    assert done["exit"] == 0 and done["stdout"].strip() == b"done"
    assert len(done["stderr"]) == 10


def test_a_process_the_hook_started_is_its_own_business(tmp_path):
    marker = tmp_path / "alive"
    code = (
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', "
        "'import sys, time, pathlib; time.sleep(3); pathlib.Path(sys.argv[1]).write_text(\"x\")', %r])\n"
        "print('parent done')" % str(marker)
    )
    done = run(code)
    assert done["exit"] == 0
    time.sleep(4)
    assert marker.exists()


def test_the_environment_is_exactly_the_one_given():
    done = run("import os; print(os.environ.get('HOOK_PROBE'), os.environ.get('HOME_PROBE_ABSENT'))",
               env=dict(os.environ, HOOK_PROBE="yes"))
    assert done["stdout"].split() == [b"yes", b"None"]


def test_a_program_that_cannot_start_is_reported():
    done = _run.run_hook(["/no/such/program-xyz"], cwd=os.getcwd(), env=dict(os.environ), timeout=5,
                         stdin=b"", max_output=10, max_stderr=10)
    assert done["not_run"] is True


@pytest.mark.skipif(os.name == "nt", reason="needs a POSIX shell script")
def test_arguments_are_a_list_never_parsed(tmp_path):
    script = tmp_path / "echo.sh"
    script.write_bytes(b"#!/bin/sh\nprintf '%s' \"$1\"\n")  # Path.write_text(newline=) needs 3.10
    hostile = "$(touch %s/pwned); `touch %s/pwned2`" % (tmp_path, tmp_path)
    done = _run.run_hook(["/bin/sh", str(script), hostile], cwd=str(tmp_path), env=dict(os.environ),
                         timeout=10, stdin=b"", max_output=1000, max_stderr=100)
    assert done["stdout"].decode() == hostile
    assert not (tmp_path / "pwned").exists() and not (tmp_path / "pwned2").exists()
