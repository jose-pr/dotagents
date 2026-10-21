"""`curl` on PATH: dotagents makes a launcher for bin/curl.py (as for every
top-level bin/*.py) in the agents' venv, <venv>/overlays/curl (curl.exe on
Windows), which runs the script through $NET_OVERLAY_ROOT. Reached by name
from PowerShell 7 and cmd.exe, or from sh, it hands the launcher every
argument as passed: no command interpreter acts on a URL's & | > or %VAR%.
Nothing generated is tracked. The tests that make a launcher need a dotagents
with script launchers; they skip without one.
"""

import base64
import os
import shutil
import subprocess
import sysconfig
import venv as venv_module
from pathlib import Path

import pytest

import fake_curl

OVERLAY = Path(__file__).resolve().parents[1]
ARGS = ["http://h/?a=1&b=2|x>y", "%PATH%", 'he said "hi"', "", "trailing\\", "$HOME"]


def _dotagents():
    """dotagents' overlay reader and launcher module, or a skip."""
    try:
        from dotagents import _launchers, _overlays
    except ImportError:
        pytest.skip("dotagents with script launchers is not importable")
    if not hasattr(_overlays.Overlay, "scripts"):
        pytest.skip("this dotagents makes no script launchers")
    return _overlays, _launchers


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


def test_dotagents_makes_curl_from_bin_curl_py():
    overlays, _launchers = _dotagents()
    assert overlays.Overlay(OVERLAY).scripts() == {"curl": "bin/curl.py"}


@pytest.fixture(scope="module")
def launcher_dir(tmp_path_factory):
    """A venv (no pip) that also sees the test interpreter's packages
    (curlike, syshttp), as dotagents layers its venvs, with dotagents' curl
    launcher in it."""
    overlays, launchers = _dotagents()
    root = tmp_path_factory.mktemp("venv")
    venv_module.EnvBuilder(with_pip=False).create(str(root))
    python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    paths = sysconfig.get_paths()
    site = subprocess.run([str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                          capture_output=True, text=True, check=True).stdout.strip()
    Path(site, "test-layers.pth").write_text("%s\n%s\n" % (paths["purelib"], paths["platlib"]), encoding="utf-8")
    launchers.sync(root, python, [overlays.Overlay(OVERLAY)])
    return launchers.launcher_dir(root)


def _pwsh(text):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 (pwsh) is not installed")
    encoded = base64.b64encode(text.encode("utf-16-le")).decode("ascii")
    return [pwsh, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


SHELLS = ["pwsh", "cmd"] if os.name == "nt" else ["sh"]


@pytest.mark.parametrize("shell", SHELLS)
def test_curl_by_name_gets_every_argument_and_nothing_else_runs(launcher_dir, tmp_path, shell):
    system_dir = tmp_path / "system"
    fake_curl.install(system_dir)
    log = tmp_path / "log.jsonl"
    system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32") if os.name == "nt" else "/usr/bin:/bin"
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("AGENTS_", "SYSHTTP_", "NET_")) and k.upper() != "PYTHONPATH"}
    env.update(fake_curl.environment(log))
    env.update(PATH=os.pathsep.join([str(launcher_dir), str(OVERLAY / "bin"), str(system_dir), system]),
               NET_OVERLAY_ROOT=str(OVERLAY), AGENTS_CURL="0", FAKE_CURL_ENV="PYTHONPATH")
    work = tmp_path / "work"
    work.mkdir()
    if shell == "pwsh":
        command = _pwsh("curl " + " ".join("'%s'" % a.replace("'", "''") for a in ARGS) + "; exit $LASTEXITCODE")
    elif shell == "cmd":
        # cmd's own syntax: operators inside quotes, and %^PATH% for a literal
        # %PATH% (the caret goes after expansion finds no variable "^PATH").
        command = 'cmd.exe /d /c curl "http://h/?a=1&b=2|x>y" %^PATH% "he said \\"hi\\"" "" "trailing\\\\" $HOME'
    else:
        command = ["/bin/sh", "-c", 'curl "$@"', "sh", *ARGS]
    out = subprocess.run(command, cwd=str(work), env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "not recognized" not in out.stdout + out.stderr
    [record] = fake_curl.records(log)
    assert record["argv"] == ARGS
    assert os.listdir(str(work)) == [], "a redirection or a second command ran"
    first = record["env"]["PYTHONPATH"].split(os.pathsep)[0]
    assert os.path.normcase(os.path.realpath(first)) == os.path.normcase(os.path.realpath(str(OVERLAY / "lib")))


def test_without_the_overlay_root_the_launcher_says_so(launcher_dir):
    env = {k: v for k, v in os.environ.items() if k.upper() != "NET_OVERLAY_ROOT"}
    exe = launcher_dir / ("curl.exe" if os.name == "nt" else "curl")
    out = subprocess.run([str(exe), "-s", "https://h/"], env=env, capture_output=True, text=True)
    assert out.returncode == 2 and "NET_OVERLAY_ROOT" in out.stderr
