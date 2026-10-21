"""The launcher's entry points (bin/curl for POSIX sh, on Windows the curl.exe
setup.py makes in the agents' venv: see test_curl_exe.py), lib/curlshim behind them, and the
one trap they share: once the overlay's bin/ is on PATH, "the real curl" must
never resolve to the launcher.
"""
import os
import stat
import sys
from pathlib import Path

import pytest

import curl  # noqa: E402  (bin/, via conftest)

BIN = Path(curl.__file__).resolve().parent


def test_posix_entry_exists_and_dispatches_to_curl_py():
    sh = BIN / "curl"
    text = sh.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert 'exec "$AGENTS_PYTHON" "$here/curl.py" "$@"' in text, "the Python dotagents runs under comes first"
    assert text.index("AGENTS_PYTHON") < text.index("python3"), "...before any PATH lookup"
    assert 'exec python3 "$here/curl.py" "$@"' in text
    assert 'exec python "$here/curl.py" "$@"' in text, "Git Bash on Windows has no python3"
    code = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert not any("dirname" in ln or "$(" in ln for ln in code), "no external commands: PATH may be minimal"
    assert "\r" not in text, "a CRLF shebang line is a 'bad interpreter' on POSIX"
    if os.name != "nt":
        assert sh.stat().st_mode & stat.S_IXUSR, "must be executable in the checkout"


def test_no_entry_is_a_batch_file():
    """cmd.exe parses a .cmd / .bat command line before its first line runs,
    so a URL's & | > or %VAR% would act: Windows gets a generated curl.exe."""
    assert not [p.name for p in BIN.iterdir() if p.suffix.lower() in (".cmd", ".bat")]
    import curlshim

    assert curl.entry() == (curlshim.venv_curl() if os.name == "nt" else str(BIN / "curl"))


def _fake_real_curl(directory):
    name = "curl.exe" if os.name == "nt" else "curl"
    exe = directory / name
    exe.write_text("", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return exe


def _path(monkeypatch, *dirs):
    monkeypatch.setenv("PATH", os.pathsep.join(str(d) for d in dirs))
    if os.name == "nt":
        monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")


def test_real_curl_is_never_this_launcher(tmp_path, monkeypatch):
    pytest.importorskip("curlike")
    import curlshim

    real_dir = tmp_path / "system"
    real_dir.mkdir()
    real = _fake_real_curl(real_dir)
    # The overlay's bin/ FIRST on PATH, exactly as `dotagents env` arranges it.
    _path(monkeypatch, BIN, real_dir)
    found = curlshim.find_real_curl(curl.entry())
    assert found is not None and Path(found).resolve() == real.resolve()


def test_fallback_runs_when_only_the_launcher_is_on_path(monkeypatch, capsysbinary):
    """End to end: PATH holds nothing but the launcher's own dir, so main()
    must take curlike's stdlib path instead of re-executing bin/curl."""
    pytest.importorskip("curlike")
    _path(monkeypatch, BIN)
    assert curl.main(["--help"]) == 0
    assert b"Pure Python curl-like tool" in capsysbinary.readouterr().out


def test_the_launcher_reads_agents_settings(monkeypatch):
    monkeypatch.delenv("SYSHTTP_PREFIX", raising=False)
    pytest.importorskip("curlike")
    import curlshim as shim

    seen = {}
    monkeypatch.setattr(shim, "main", lambda argv, entry=None: seen.update(argv=argv, entry=entry) or 0)
    assert curl.main(["-s", "https://h/"]) == 0
    assert os.environ["SYSHTTP_PREFIX"] == "AGENTS_"
    assert seen == {"argv": ["-s", "https://h/"], "entry": curl.entry()}


def test_without_curlike_the_real_curl_runs_as_typed(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "curlike", None)  # import fails
    real = _fake_real_curl(tmp_path)
    _path(monkeypatch, BIN, tmp_path)
    calls = []

    class _Done:
        returncode = 0

    monkeypatch.setattr(curl.subprocess, "run", lambda argv, *a, **k: calls.append(argv) or _Done())
    assert curl.main(["-s", "https://h/"]) == 0
    assert Path(calls[0][0]).resolve() == real.resolve() and calls[0][1:] == ["-s", "https://h/"]
    _path(monkeypatch, BIN)
    assert curl.main(["-s", "https://h/"]) == 2
    assert "curlike is not installed" in capsys.readouterr().err


def test_without_curlike_the_current_directory_is_never_searched(tmp_path, monkeypatch, capsys):
    """shutil.which searches the current directory first on Windows before
    Python 3.12: a curl planted there must not run, nor a .cmd on PATH."""
    monkeypatch.setitem(sys.modules, "curlike", None)
    _fake_real_curl(tmp_path)
    (tmp_path / "curl.cmd").write_text("@echo off\n", encoding="utf-8")
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    (batch_dir / "curl.cmd").write_text("@echo off\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    _path(monkeypatch, BIN, ".", batch_dir)
    calls = []
    monkeypatch.setattr(curl.subprocess, "run", lambda argv, *a, **k: calls.append(argv))
    assert curl.main(["-s", "https://h/"]) == 2
    assert calls == [] and "no curl on PATH" in capsys.readouterr().err


def test_env_py_sets_the_agents_prefix_unless_one_is_set():
    import json
    import subprocess

    env_py = str(BIN.parent / "env.py")
    base = {k: v for k, v in os.environ.items() if k.upper() not in ("SYSHTTP_PREFIX", "AGENTS_PYTHON")}
    out = subprocess.run([sys.executable, env_py], capture_output=True, text=True, env=base, check=True)
    assert json.loads(out.stdout) == {"SYSHTTP_PREFIX": "AGENTS_"}
    out = subprocess.run([sys.executable, env_py], capture_output=True, text=True,
                         env=dict(base, SYSHTTP_PREFIX="MINE"), check=True)
    assert out.stdout.strip() == ""


def test_requirements_take_both_packages_from_pypi_in_one_minor_series():
    lines = [ln.strip() for ln in (BIN.parent / "requirements.txt").read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    assert sorted(lines) == ["curlike>=0.1.0,<0.2", "syshttp>=0.1.0,<0.2"]


def test_the_launcher_puts_the_libs_on_pythonpath_for_what_it_starts(tmp_path):
    """Outside a `dotagents env` session nothing else puts the overlay libs on
    PYTHONPATH, so the launcher does, for the real curl, a URL hook and what
    they start: its own lib, then $AGENTS_PYTHONPATH, then the caller's."""
    import shutil
    import subprocess

    import fake_curl

    ov = tmp_path / "net"
    (ov / "bin").mkdir(parents=True)
    shutil.copytree(BIN.parent / "lib", ov / "lib", ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("curl", "curl.py"):
        shutil.copy2(BIN / name, ov / "bin" / name)
    (ov / "bin" / "curl").chmod(0o755)
    other = tmp_path / "other-lib"
    other.mkdir()
    system = tmp_path / "system"
    fake_curl.install(system)
    log = tmp_path / "log.jsonl"
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("AGENTS_", "SYSHTTP_")) and k.upper() != "PYTHONPATH"}
    env.update(fake_curl.environment(log))
    env.update(PATH=os.pathsep.join([str(ov / "bin"), str(system)]), AGENTS_PYTHON=sys.executable,
               AGENTS_PYTHONPATH=str(other), AGENTS_CURL="0", FAKE_CURL_ENV="PYTHONPATH")
    if os.name == "nt":
        argv = [sys.executable, str(ov / "bin" / "curl.py")]  # curl.exe: test_curl_exe.py
    else:
        argv = ["/bin/sh", str(ov / "bin" / "curl")]
    out = subprocess.run([*argv, "-s", "https://h/"], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    [record] = fake_curl.records(log)
    assert record["argv"] == ["-s", "https://h/"]
    seen = [os.path.normcase(os.path.realpath(p)) for p in record["env"]["PYTHONPATH"].split(os.pathsep)]
    want = [ov / "lib", other, fake_curl.HERE]
    assert seen == [os.path.normcase(os.path.realpath(str(p))) for p in want]
