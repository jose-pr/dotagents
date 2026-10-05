"""The fallback when its stdout goes away (``curl ... | head -c 16``): curl's
exit 23 and one line on stderr, whatever is written after the failed write.

Run as a child process over a real pipe, closed by the reader: a failed write
leaves Python's stdout unusable, so a later ``-w`` or the flush at interpreter
exit would otherwise end the run with a traceback and exit 120. The expected
codes are what curl 8.18 gives for the same nine invocations.
"""
import io
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from httplib.cli import output, writeout

LIB = Path(output.__file__).resolve().parents[2]
BIG = b"x" * (4 * 1024 * 1024)


class _Origin(BaseHTTPRequestHandler):
    def do_GET(self):
        body = BIG
        if self.path == "/small":
            time.sleep(0.5)  # the reader is gone before anything is written
            body = b"hello"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def origin():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Origin)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % server.server_address[1]
    server.shutdown()
    server.server_close()


def _run(argv, take):
    """The fallback over a pipe whose reader takes ``take`` bytes and closes.
    ``(exit code, stderr lines)``."""
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy") and k != "AGENTS_PROXY"}
    env["PYTHONPATH"] = os.pathsep.join([str(LIB), env.get("PYTHONPATH", "")])
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1"
    proc = subprocess.Popen(
        [sys.executable, "-m", "httplib", *argv],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    try:
        if take:
            proc.stdout.read(take)
        proc.stdout.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        code = proc.wait(timeout=60)
    finally:
        proc.stderr.close()
        if proc.poll() is None:
            proc.kill()
    return code, [line for line in err.splitlines() if line.strip()]


WRITE_FAILURE = "curl: (23) Failure writing output to destination"

CASES = [
    # (name, argv after the URL is appended, path, bytes read, exit, stderr lines)
    ("body", ["-sS"], "/big", 16, 23, 1),
    ("body, silent", ["-s"], "/big", 16, 23, 0),
    ("write-out to stdout", ["-sS", "-w", "[%{exitcode}]"], "/big", 16, 23, 1),
    ("write-out to stderr", ["-sS", "-w", "%{stderr}[%{exitcode}]"], "/big", 16, 23, 2),
    ("-i", ["-sS", "-i"], "/big", 16, 23, 1),
    ("-D -", ["-sS", "-D", "-"], "/big", 16, 23, 1),
    ("small body, reader gone", ["-sS"], "/small", 0, 23, 1),
    ("small body and write-out", ["-sS", "-w", "[%{exitcode}]"], "/small", 0, 23, 1),
]


@pytest.mark.parametrize("name,argv,path,take,code,lines", CASES, ids=[c[0] for c in CASES])
def test_a_closed_stdout_is_exit_23_and_one_line(name, argv, path, take, code, lines, origin):
    rc, err = _run([*argv, origin + path], take)
    assert (rc, len(err)) == (code, lines), err
    if lines:
        assert err[0].startswith(WRITE_FAILURE)
    if "%{stderr}" in " ".join(argv):
        assert err[-1] == "[23]"


def test_a_write_out_that_cannot_be_written_does_not_fail_the_transfer(origin):
    """The body went to a file; only -w had stdout. curl loses it and exits 0."""
    rc, err = _run(["-sS", "-o", os.devnull, "-w", "%{http_code}", origin + "/small"], 0)
    assert (rc, err) == (0, [])


class _Gone(object):
    """A captured stdout whose reader went away: no descriptor to redirect."""

    def __init__(self):
        self.buffer = self

    def write(self, data):
        raise BrokenPipeError(32, "Broken pipe")

    def flush(self):
        raise BrokenPipeError(32, "Broken pipe")

    def fileno(self):
        raise io.UnsupportedOperation("fileno")


def test_a_stream_without_a_descriptor_is_replaced(monkeypatch):
    monkeypatch.setattr(sys, "stdout", _Gone())
    with pytest.raises(BrokenPipeError):
        output.write_stdout(b"body")
    replacement = sys.stdout
    try:
        assert not isinstance(replacement, _Gone)
        output.write_stdout(b"more")  # goes nowhere, and does not raise
        writeout._write_stream("stdout", "[23]")
    finally:
        replacement.close()


def test_write_out_to_a_dead_stdout_is_skipped(monkeypatch):
    monkeypatch.setattr(sys, "stdout", _Gone())
    writeout._write_stream("stdout", "[0]")  # no exception
    replacement = sys.stdout
    try:
        assert not isinstance(replacement, _Gone)
    finally:
        replacement.close()
