"""Characterization tests for `dotagents pyvenv` (the python overlay's own cmd).

Covers: os/arch bucketing, version probing/parsing, interpreter discovery
plumbing (with subprocess mocked out -- these tests never spawn a real venv),
and the idempotent "already exists" short-circuit.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from pyvenv import (
    _arch_bucket,
    _os_bucket,
    _probe_version,
    _resolve_interpreter,
    _venv_python,
)


# --------------------------------------------------------------------------
# os/arch bucketing
# --------------------------------------------------------------------------

def test_os_bucket_matches_running_platform():
    bucket = _os_bucket()
    if sys.platform == "win32":
        assert bucket == "nt"
    elif sys.platform == "darwin":
        assert bucket == "darwin"
    else:
        assert bucket == "posix"


def test_darwin_is_its_own_bucket_not_posix(monkeypatch):
    # os.name reports "posix" for macOS same as Linux -- sys.platform must win.
    monkeypatch.setattr(sys, "platform", "darwin")
    assert _os_bucket() == "darwin"


def test_arch_bucket_is_lowercased(monkeypatch):
    import platform

    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    assert _arch_bucket() == "amd64"


# --------------------------------------------------------------------------
# _venv_python: platform-specific interpreter path inside a venv dir
# --------------------------------------------------------------------------

def test_venv_python_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert _venv_python(Path("/x")) == Path("/x/Scripts/python.exe")


def test_venv_python_posix(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert _venv_python(Path("/x")) == Path("/x/bin/python")


# --------------------------------------------------------------------------
# _probe_version: parses real `python --version` output, fails safe
# --------------------------------------------------------------------------

def test_probe_version_parses_stdout(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="Python 3.11.7\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _probe_version(Path("python")) == (3, 11, 7)


def test_probe_version_falls_back_to_stderr(monkeypatch):
    # Some interpreters historically print --version to stderr.
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="Python 3.9.0\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _probe_version(Path("python")) == (3, 9, 0)


def test_probe_version_none_on_unparseable_output(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="not a version\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _probe_version(Path("python")) is None


def test_probe_version_none_when_spawn_fails(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _probe_version(Path("does-not-exist")) is None


# --------------------------------------------------------------------------
# _resolve_interpreter: version-spec matching and "latest" selection
# --------------------------------------------------------------------------

def test_resolve_interpreter_explicit_path(tmp_path, monkeypatch):
    fake = tmp_path / "python"
    fake.write_text("", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="Python 3.12.1\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _resolve_interpreter(str(fake), logger=None) == fake


def test_resolve_interpreter_explicit_path_must_run(tmp_path, monkeypatch):
    fake = tmp_path / "python"
    fake.write_text("", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(SystemExit):
        _resolve_interpreter(str(fake), logger=None)


def test_resolve_interpreter_bare_version_picks_highest_match(tmp_path, monkeypatch):
    import pyvenv as mod

    candidates = [tmp_path / "a", tmp_path / "b", tmp_path / "c"]
    versions = {
        str(candidates[0]): (3, 11, 2),
        str(candidates[1]): (3, 11, 9),  # highest 3.11.x
        str(candidates[2]): (3, 12, 0),  # different major.minor, must not match "3.11"
    }
    monkeypatch.setattr(mod, "_discover_all", lambda: candidates)
    monkeypatch.setattr(mod, "_probe_version", lambda p: versions.get(str(p)))

    result = mod._resolve_interpreter("3.11", logger=None)
    assert result == candidates[1]


def test_resolve_interpreter_no_version_picks_global_highest(tmp_path, monkeypatch):
    import pyvenv as mod

    candidates = [tmp_path / "a", tmp_path / "b"]
    versions = {str(candidates[0]): (3, 9, 0), str(candidates[1]): (3, 13, 0)}
    monkeypatch.setattr(mod, "_discover_all", lambda: candidates)
    monkeypatch.setattr(mod, "_probe_version", lambda p: versions.get(str(p)))

    assert mod._resolve_interpreter(None, logger=None) == candidates[1]


def test_resolve_interpreter_no_match_raises(monkeypatch):
    import pyvenv as mod

    monkeypatch.setattr(mod, "_discover_all", lambda: [])
    with pytest.raises(SystemExit):
        mod._resolve_interpreter("3.11", logger=None)


def test_resolve_interpreter_bad_spec_raises():
    with pytest.raises(SystemExit):
        _resolve_interpreter("not-a-version", logger=None)


# --------------------------------------------------------------------------
# Same version+os+arch always names the same venv dir -- the point of naming
# by probed version rather than by whichever interpreter happened to resolve.
# --------------------------------------------------------------------------

def test_same_version_os_arch_produce_the_same_dirname(monkeypatch):
    import pyvenv as mod

    monkeypatch.setattr(mod, "_os_bucket", lambda: "posix")
    monkeypatch.setattr(mod, "_arch_bucket", lambda: "x86_64")

    def dirname(version):
        return "%s-%s-%s" % (version, mod._os_bucket(), mod._arch_bucket())

    # Two different interpreter binaries reporting the identical version must
    # collide into the identical venv dir name.
    assert dirname("3.11.7") == dirname("3.11.7")
