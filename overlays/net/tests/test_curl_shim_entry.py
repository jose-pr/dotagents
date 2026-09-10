"""The shim's two entry points (bin/curl for POSIX, bin/curl.cmd for Windows)
and the one trap they share: once the overlay's bin/ is on PATH, "the real curl"
must never resolve to the shim itself.
"""
import os
import stat
import sys
from pathlib import Path

import curl  # noqa: E402  (bin/, via conftest)

BIN = Path(curl.__file__).resolve().parent


def test_posix_entry_exists_and_dispatches_to_curl_py():
    sh = BIN / "curl"
    text = sh.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert 'exec python3 "$here/curl.py" "$@"' in text
    assert 'exec python "$here/curl.py" "$@"' in text, "Git Bash on Windows has no python3"
    assert "\r" not in text, "a CRLF shebang line is a 'bad interpreter' on POSIX"
    if os.name != "nt":
        assert sh.stat().st_mode & stat.S_IXUSR, "must be executable in the checkout"


def test_windows_entry_dispatches_to_curl_py():
    cmd = (BIN / "curl.cmd").read_text(encoding="utf-8")
    assert "curl.py" in cmd and "%*" in cmd


def _fake_real_curl(directory):
    name = "curl.exe" if os.name == "nt" else "curl"
    exe = directory / name
    exe.write_text("", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return exe


def test_real_curl_is_never_this_shim(tmp_path, monkeypatch):
    real_dir = tmp_path / "system"
    real_dir.mkdir()
    real = _fake_real_curl(real_dir)
    # The overlay's bin/ FIRST on PATH, exactly as `dotagents env` arranges it.
    monkeypatch.setenv("PATH", os.pathsep.join([str(BIN), str(real_dir)]))
    if os.name == "nt":
        monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    found = curl.find_real_curl()
    assert found is not None
    assert Path(found).resolve() == real.resolve()


def test_no_real_curl_means_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", os.pathsep.join([str(BIN), str(tmp_path)]))
    assert curl.find_real_curl() is None


def test_fallback_runs_when_only_the_shim_is_on_path(tmp_path, monkeypatch, capsysbinary):
    """End to end: PATH holds nothing but the shim's own dir, so main() must
    take the stdlib path instead of re-executing bin/curl."""
    monkeypatch.setenv("PATH", str(BIN))
    rc = curl.main(["--help"])
    assert rc == 0
    assert b"Pure Python curl-like tool" in capsysbinary.readouterr().out
    assert sys.platform  # (silence the unused-import lint on some setups)
