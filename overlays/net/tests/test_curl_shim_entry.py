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
    assert 'exec "$AGENTS_PYTHON" "$here/curl.py" "$@"' in text, "the Python dotagents runs under comes first"
    assert text.index("AGENTS_PYTHON") < text.index("python3"), "...before any PATH lookup"
    assert 'exec python3 "$here/curl.py" "$@"' in text
    assert 'exec python "$here/curl.py" "$@"' in text, "Git Bash on Windows has no python3"
    code = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert not any("dirname" in ln or "$(" in ln for ln in code), "no external commands: PATH may be minimal"
    assert "\r" not in text, "a CRLF shebang line is a 'bad interpreter' on POSIX"
    if os.name != "nt":
        assert sh.stat().st_mode & stat.S_IXUSR, "must be executable in the checkout"


def test_windows_entry_dispatches_to_curl_py():
    cmd = (BIN / "curl.cmd").read_text(encoding="utf-8")
    assert "curl.py" in cmd and "%*" in cmd
    assert '"%AGENTS_PYTHON%" "%~dp0curl.py" %*' in cmd, "the Python dotagents runs under comes first"
    assert cmd.index("AGENTS_PYTHON") < cmd.index("python.exe")


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


def _launcher_copy(tmp_path):
    """The two launchers beside a probe curl.py whose CHILD imports the net lib
    and another overlay's lib: what a URL hook or any script curl starts needs."""
    import shutil

    ov = tmp_path / "net"
    (ov / "bin").mkdir(parents=True)
    shutil.copytree(BIN.parent / "lib", ov / "lib")
    for name in ("curl", "curl.cmd"):
        shutil.copy2(BIN / name, ov / "bin" / name)
    (ov / "bin" / "curl").chmod(0o755)
    (ov / "bin" / "curl.py").write_text(
        "import subprocess, sys\n"
        "code = 'import httplib.proxy, other_lib; print(\"child-ok\")'\n"
        "r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)\n"
        "print(r.stdout.strip() or r.stderr.strip())\n",
        encoding="utf-8",
    )
    other = tmp_path / "other-lib"
    other.mkdir()
    (other / "other_lib.py").write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in ("PYTHONPATH", "AGENTS_PYTHONPATH", "AGENTS_PYTHON")}
    env["AGENTS_PYTHON"] = sys.executable
    return ov, other, env


def test_the_launchers_put_the_libs_on_pythonpath_for_what_they_start(tmp_path):
    """Outside a `dotagents env` session nothing else puts the overlay libs on
    PYTHONPATH, so each launcher sets it: its own lib, then $AGENTS_PYTHONPATH."""
    import shutil
    import subprocess

    ov, other, env = _launcher_copy(tmp_path)
    runs = []
    if os.name == "nt":
        env["AGENTS_PYTHONPATH"] = str(other)
        runs.append(["cmd.exe", "/d", "/c", str(ov / "bin" / "curl.cmd"), "-s"])
        for ps in ("powershell", "pwsh"):
            if shutil.which(ps):
                runs.append([ps, "-NoProfile", "-Command", "& '%s' -s" % (ov / "bin" / "curl.cmd")])
    else:
        env["AGENTS_PYTHONPATH"] = str(other)
        runs.append(["/bin/sh", str(ov / "bin" / "curl"), "-s"])
    for argv in runs:
        out = subprocess.run(argv, capture_output=True, text=True, env=env)
        assert out.stdout.strip() == "child-ok", (argv[0], out.stdout, out.stderr)


def test_the_cmd_launcher_leaves_the_callers_environment_alone(tmp_path):
    import subprocess

    if os.name != "nt":
        return
    ov, _other, env = _launcher_copy(tmp_path)
    out = subprocess.run(
        ["cmd.exe", "/d", "/c", 'call "%s" -s >nul & echo after=[%%PYTHONPATH%%]' % (ov / "bin" / "curl.cmd")],
        capture_output=True, text=True, env=env,
    ).stdout.strip().splitlines()[-1]
    assert out == "after=[%PYTHONPATH%]"
