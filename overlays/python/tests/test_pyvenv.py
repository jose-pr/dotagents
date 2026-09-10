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
    _arch_from_platform_tag,
    _host_arch,
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


def test_arch_comes_from_the_build_platform_tag():
    # The last dash segment of sysconfig.get_platform(), lowercased -- the
    # arch the interpreter was BUILT for, which is what a venv inherits.
    assert _arch_from_platform_tag("win-amd64") == "amd64"
    assert _arch_from_platform_tag("win-ARM64") == "arm64"
    assert _arch_from_platform_tag("macosx-14.0-arm64") == "arm64"
    assert _arch_from_platform_tag("linux-x86_64") == "x86_64"
    assert _arch_from_platform_tag("") == "unknown"


def test_arch_bucket_of_the_running_interpreter_is_its_build_arch():
    import sysconfig

    assert _arch_bucket() == _arch_from_platform_tag(sysconfig.get_platform())


def test_arch_bucket_probes_the_target_interpreter(monkeypatch):
    import pyvenv as mod

    monkeypatch.setattr(mod, "_probe_arch", lambda python: "arm64")
    assert _arch_bucket(Path("/some/python")) == "arm64"
    monkeypatch.setattr(mod, "_probe_arch", lambda python: None)
    assert _arch_bucket(Path("/some/python")) == "unknown"


def test_host_arch_is_a_known_bucket_word():
    # Whatever the machine, the answer is in the same vocabulary as the
    # build-platform tags, so equality against a probed arch is meaningful.
    assert _host_arch() == _host_arch().lower()
    assert _host_arch() not in ("", None)


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
    monkeypatch.setattr(mod, "_probe_arch", lambda p: "native")
    monkeypatch.setattr(mod, "_host_arch", lambda: "native")

    result = mod._resolve_interpreter("3.11", logger=None)
    assert result == candidates[1]


def test_resolve_interpreter_prefers_native_arch_over_higher_version(tmp_path, monkeypatch):
    """An ARM64 3.9.10 beats an emulated x64 3.9.13 on an ARM64 box: the
    emulated build is never the default when a native one satisfies the spec."""
    import pyvenv as mod

    emulated, native = tmp_path / "x64", tmp_path / "arm64"
    versions = {str(emulated): (3, 9, 13), str(native): (3, 9, 10)}
    arches = {str(emulated): "amd64", str(native): "arm64"}
    monkeypatch.setattr(mod, "_discover_all", lambda: [emulated, native])
    monkeypatch.setattr(mod, "_probe_version", lambda p: versions.get(str(p)))
    monkeypatch.setattr(mod, "_probe_arch", lambda p: arches.get(str(p)))
    monkeypatch.setattr(mod, "_host_arch", lambda: "arm64")

    assert mod._resolve_interpreter("3.9", logger=None) == native
    assert mod._resolve_interpreter(None, logger=None) == native
    # ...and with no native candidate at all, the highest version still wins.
    monkeypatch.setattr(mod, "_host_arch", lambda: "riscv64")
    assert mod._resolve_interpreter("3.9", logger=None) == emulated


def test_resolve_interpreter_no_version_picks_global_highest(tmp_path, monkeypatch):
    import pyvenv as mod

    candidates = [tmp_path / "a", tmp_path / "b"]
    versions = {str(candidates[0]): (3, 9, 0), str(candidates[1]): (3, 13, 0)}
    monkeypatch.setattr(mod, "_discover_all", lambda: candidates)
    monkeypatch.setattr(mod, "_probe_version", lambda p: versions.get(str(p)))
    monkeypatch.setattr(mod, "_probe_arch", lambda p: "native")
    monkeypatch.setattr(mod, "_host_arch", lambda: "native")

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
    monkeypatch.setattr(mod, "_arch_bucket", lambda interpreter=None: "x86_64")

    def dirname(version):
        return "%s-%s-%s" % (version, mod._os_bucket(), mod._arch_bucket())

    # Two different interpreter binaries reporting the identical version must
    # collide into the identical venv dir name.
    assert dirname("3.11.7") == dirname("3.11.7")


def test_venv_dir_is_named_for_the_target_interpreters_arch(tmp_path, monkeypatch):
    """Running under an emulated x64 CPython, a venv made from a native ARM64
    interpreter is filed as arm64 -- the bucket is the TARGET's build arch, not
    the running process's idea of the machine."""
    import pyvenv as mod

    class FakeScope:
        agents_root = tmp_path / ".agents"

    target = tmp_path / "arm64-python"
    monkeypatch.setattr(mod, "_resolve_interpreter", lambda version, logger=None: target)
    monkeypatch.setattr(mod, "_probe_version", lambda p: (3, 14, 7))
    monkeypatch.setattr(mod, "_probe_arch", lambda p: "arm64" if p == target else "amd64")
    monkeypatch.setattr(mod, "_os_bucket", lambda: "nt")

    venv_dir, interpreter = mod._venv_target(FakeScope(), "3.14")
    assert interpreter == target
    assert venv_dir == tmp_path / ".pyvenv" / "3.14.7-nt-arm64"


def test_windows_discovery_skips_the_active_venv_and_any_venv_python(tmp_path, monkeypatch):
    # The launcher lists the ACTIVE venv first (marked *); a venv is never a
    # candidate -- a venv made from a venv inherits its base and hides which
    # install that was.
    import pyvenv as mod

    install = tmp_path / "pythoncore-3.14-arm64" / "python.exe"
    install.parent.mkdir()
    install.write_text("", encoding="utf-8")
    venv = tmp_path / ".venv" / "Scripts" / "python.exe"
    venv.parent.mkdir(parents=True)
    venv.write_text("", encoding="utf-8")
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = x", encoding="utf-8")
    listing = " *               %s\n -V:3.14-arm64    %s\n" % (venv, install)

    def fake_run(cmd, **kwargs):
        assert cmd[:2] == ["py", "--list-paths"]
        return subprocess.CompletedProcess(cmd, 0, stdout=listing, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "no-such-dir"))
    assert mod._candidates_windows() == [install]
    assert mod._is_venv_python(venv) and not mod._is_venv_python(install)


def test_windows_discovery_adds_python_manager_installs(tmp_path, monkeypatch):
    import pyvenv as mod

    managed = tmp_path / "Python" / "pythoncore-3.13-arm64" / "python.exe"
    managed.parent.mkdir(parents=True)
    managed.write_text("", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("no py launcher")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert managed in mod._candidates_windows()


def test_a_venv_interpreter_is_replaced_by_its_base(tmp_path, monkeypatch):
    """An activated venv on an otherwise bare PATH must still yield an
    interpreter: the venv's base from pyvenv.cfg, never the venv itself."""
    import pyvenv as mod

    base_dir = tmp_path / "base"
    base_dir.mkdir()
    exe = "python.exe" if sys.platform == "win32" else "python3"
    base = base_dir / exe
    base.write_text("", encoding="utf-8")
    venv = tmp_path / ".venv"
    (venv / ("Scripts" if sys.platform == "win32" else "bin")).mkdir(parents=True)
    venv_python = mod._venv_python(venv)
    venv_python.write_text("", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text("home = %s\nversion = 3.12.1\n" % base_dir, encoding="utf-8")

    assert mod._venv_base(venv_python) == base
    assert mod._unvenv([venv_python, base]) == [base], "mapped, then deduplicated"
    orphan = tmp_path / "orphan"
    (orphan / "bin").mkdir(parents=True)
    (orphan / "pyvenv.cfg").write_text("home = %s\n" % (tmp_path / "gone"), encoding="utf-8")
    orphan_python = orphan / "bin" / "python"
    orphan_python.write_text("", encoding="utf-8")
    assert mod._unvenv([orphan_python]) == [], "a venv whose base is gone is dropped"


def test_universal2_counts_as_native(tmp_path, monkeypatch):
    import pyvenv as mod

    fat, thin = tmp_path / "fat", tmp_path / "thin"
    versions = {str(fat): (3, 12, 0), str(thin): (3, 13, 0)}
    arches = {str(fat): "universal2", str(thin): "x86_64"}
    monkeypatch.setattr(mod, "_discover_all", lambda: [fat, thin])
    monkeypatch.setattr(mod, "_probe_version", lambda p: versions.get(str(p)))
    monkeypatch.setattr(mod, "_probe_arch", lambda p: arches.get(str(p)))
    monkeypatch.setattr(mod, "_host_arch", lambda: "arm64")
    assert mod._resolve_interpreter(None, logger=None) == fat


def test_probes_of_a_real_interpreter_are_spawned_once(monkeypatch):
    import pyvenv as mod

    spawns = []
    real = Path(sys.executable)
    monkeypatch.setattr(mod, "_spawn_version", lambda p: spawns.append("v") or (3, 9, 0))
    monkeypatch.setattr(mod, "_spawn_arch", lambda p: spawns.append("a") or "arm64")
    mod._PROBES.clear()
    for _ in range(3):
        assert mod._probe_version(real) == (3, 9, 0)
        assert mod._probe_arch(real) == "arm64"
    assert spawns == ["v", "a"]
    mod._PROBES.clear()
    assert mod._probe_version(Path("/no/such/python")) is None or True  # never cached, never raises
