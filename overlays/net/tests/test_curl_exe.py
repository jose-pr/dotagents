"""The Windows entry: the curl.exe pip generates from launcher/ into the
agents' venv (setup.py), put on PATH by env.py for the venv $AGENTS_PYTHON
names. Reached by name from PowerShell 7 and cmd.exe, it hands the launcher
every argument as passed: no command interpreter acts on a URL's & | > or
%VAR%. The executable names its venv's interpreter by absolute path, so it is
never tracked, never written into the overlay, and never reachable once its
venv is not the one in use.
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

import fake_curl

OVERLAY = Path(__file__).resolve().parents[1]
ARGS = ["http://h/?a=1&b=2|x>y", "%PATH%", 'he said "hi"', "", "trailing\\"]

windows = pytest.mark.skipif(os.name != "nt", reason="Windows entry; elsewhere bin/curl (POSIX sh) is curl")


def test_no_executable_or_batch_file_is_tracked():
    git = shutil.which("git")
    if not git:
        pytest.skip("git is not installed")
    out = subprocess.run([git, "-C", str(OVERLAY), "ls-files", "-z", "."], capture_output=True, text=True)
    if out.returncode:
        pytest.skip("not a git checkout")
    tracked = [p for p in out.stdout.split("\0") if p]
    assert "bin/curl.py" in tracked
    assert [p for p in tracked if p.lower().endswith((".exe", ".cmd", ".bat"))] == []


def test_the_launcher_package_makes_curl():
    text = (OVERLAY / "launcher" / "pyproject.toml").read_text(encoding="utf-8")
    assert '\n[project.scripts]\ncurl = "net_curl_launcher:main"\n' in text


@pytest.mark.skipif(os.name == "nt", reason="POSIX: setup has nothing to make")
def test_setup_does_nothing_off_windows():
    out = subprocess.run([sys.executable, str(OVERLAY / "setup.py")], capture_output=True, text=True, cwd=OVERLAY)
    assert (out.returncode, out.stdout, out.stderr) == (0, "", "")


def _overlay_copy(root):
    ov = root / "net"
    skip = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in ("lib", "launcher"):
        shutil.copytree(OVERLAY / name, ov / name, ignore=skip)
    (ov / "bin").mkdir()
    for name in ("bin/curl", "bin/curl.py", "setup.py", "env.py"):
        shutil.copy2(OVERLAY / name, ov / name)
    return ov


def _venv(path):
    """A venv that also sees the test interpreter's packages (curlike and
    syshttp when installed), as dotagents layers its venvs."""
    subprocess.run([sys.executable, "-m", "venv", str(path)], check=True, capture_output=True)
    paths = sysconfig.get_paths()
    site = path / "Lib" / "site-packages"
    (site / "test-layers.pth").write_text("%s\n%s\n" % (paths["purelib"], paths["platlib"]), encoding="utf-8")
    return path / "Scripts" / "python.exe"


def _setup(ov, python):
    out = subprocess.run([str(python), str(ov / "setup.py")], cwd=str(ov), capture_output=True, text=True)
    assert out.returncode == 0, out.stdout[-2000:] + out.stderr[-2000:]
    return out


def _files(tree):
    return sorted(p.relative_to(tree).as_posix() for p in tree.rglob("*") if "__pycache__" not in p.parts)


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    """The overlay with its setup run in a venv, then again in a venv rebuilt
    at another path, the first one left in place."""
    if os.name != "nt":
        pytest.skip("Windows entry; elsewhere bin/curl (POSIX sh) is curl")
    root = tmp_path_factory.mktemp("curlexe")
    ov = _overlay_copy(root)
    before = _files(ov)
    old = _venv(root / "old" / "venv")
    _setup(ov, old)
    new = _venv(root / "elsewhere" / "venv")
    _setup(ov, new)
    return {"root": root, "ov": ov, "before": before, "old": old, "new": new}


def _venv_dir(python):
    return python.parent.parent


@windows
def test_setup_puts_curl_exe_in_the_venv_alone_and_nothing_in_the_overlay(made):
    for python in (made["old"], made["new"]):
        assert sorted(os.listdir(_venv_dir(python) / "net-curl")) == ["curl.exe"]
    assert _files(made["ov"]) == made["before"]
    exe = _venv_dir(made["new"]) / "net-curl" / "curl.exe"
    stamp = exe.stat().st_mtime_ns
    again = _setup(made["ov"], made["new"])
    assert (again.stdout, again.stderr) == ("", ""), "a second run installs nothing"
    assert exe.stat().st_mtime_ns == stamp


def _env_py_path(ov, python, path):
    env = {k: v for k, v in os.environ.items() if k.upper() not in ("PATH", "SYSHTTP_PREFIX")}
    env.update(PATH=path, AGENTS_PYTHON=str(python), SYSHTTP_PREFIX="AGENTS_")
    out = subprocess.run([str(python), str(ov / "env.py")], capture_output=True, text=True, env=env, check=True)
    return json.loads(out.stdout)["PATH"] if out.stdout.strip() else path


@windows
def test_env_py_puts_only_the_current_venvs_curl_on_path(made):
    """A venv rebuilt elsewhere: its curl leads PATH, the old venv's is gone
    from it, and nothing else of either venv is reachable."""
    old_dir = str(_venv_dir(made["old"]) / "net-curl")
    system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    path = _env_py_path(made["ov"], made["new"], os.pathsep.join([old_dir, str(made["ov"] / "bin"), system]))
    new_dir = str(_venv_dir(made["new"]) / "net-curl")
    parts = path.split(os.pathsep)
    assert parts == [new_dir, str(made["ov"] / "bin"), system]
    assert Path(shutil.which("curl", path=path)) == Path(new_dir) / "curl.exe"
    for name in ("python", "pip"):
        found = shutil.which(name, path=path)
        assert found is None or _venv_dir(made["new"]) not in Path(found).parents
    assert _env_py_path(made["ov"], made["new"], path) == path, "a second run changes nothing"


def _run_env(made, tmp_path):
    system_dir = tmp_path / "system"
    fake_curl.install(system_dir)
    log = tmp_path / "log.jsonl"
    system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    old_dir = str(_venv_dir(made["old"]) / "net-curl")
    path = _env_py_path(made["ov"], made["new"],
                        os.pathsep.join([old_dir, str(made["ov"] / "bin"), str(system_dir), system]))
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("AGENTS_", "SYSHTTP_", "NET_")) and k.upper() != "PYTHONPATH"}
    env.update(fake_curl.environment(log))
    env.update(PATH=path, NET_OVERLAY_ROOT=str(made["ov"]), AGENTS_PYTHON=str(made["new"]), AGENTS_CURL="0",
               FAKE_CURL_ENV="PYTHONPATH")
    work = tmp_path / "work"
    work.mkdir()
    return env, log, work


def _pwsh(text):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 (pwsh) is not installed")
    encoded = base64.b64encode(text.encode("utf-16-le")).decode("ascii")
    return [pwsh, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


def _ps_quote(arg):
    return "'%s'" % arg.replace("'", "''")


@windows
@pytest.mark.parametrize("shell", ["pwsh", "cmd"])
def test_curl_by_name_gets_every_argument_and_nothing_else_runs(made, tmp_path, shell):
    env, log, work = _run_env(made, tmp_path)
    if shell == "pwsh":
        command = _pwsh("curl " + " ".join(_ps_quote(a) for a in ARGS))
    else:
        # cmd's own syntax: operators inside quotes, and %^PATH% for a literal
        # %PATH% (the caret goes after expansion finds no variable "^PATH").
        command = 'cmd.exe /d /c curl "http://h/?a=1&b=2|x>y" %^PATH% "he said \\"hi\\"" "" "trailing\\\\"'
    out = subprocess.run(command, cwd=str(work), env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "not recognized" not in out.stdout + out.stderr
    [record] = fake_curl.records(log)
    assert record["argv"] == ARGS
    assert os.listdir(str(work)) == [], "a redirection or a second command ran"
    first = record["env"]["PYTHONPATH"].split(os.pathsep)[0]
    assert os.path.normcase(os.path.realpath(first)) == os.path.normcase(os.path.realpath(str(made["ov"] / "lib")))


@windows
def test_without_the_overlay_root_the_exe_says_so(made, tmp_path):
    env, log, _work = _run_env(made, tmp_path)
    del env["NET_OVERLAY_ROOT"]
    exe = _venv_dir(made["new"]) / "net-curl" / "curl.exe"
    out = subprocess.run([str(exe), "-s", "https://h/"], env=env, capture_output=True, text=True)
    assert out.returncode == 2 and "NET_OVERLAY_ROOT" in out.stderr
    assert fake_curl.records(log) == []
