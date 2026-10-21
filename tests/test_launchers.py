"""Launchers for overlays' scripts (`dotagents._launchers`): each top-level
``bin/<name>.py`` and each ``[scripts]`` entry, written into
``<venv>/overlays/`` on every venv sync, put first on PATH by `env` for the
active venv, and resolving the script at run time through the overlay's
``<NAME>_OVERLAY_ROOT``."""
import base64
import json
import logging
import os
import shutil
import subprocess
import sys
import venv as venv_module
from pathlib import Path

import pytest

from dotagents import _env, _launchers, _overlays, _pyvenv
from dotagents._overlays import Overlay
from dotagents._scope import Scope

ARGS = ["http://h/?a=1&b=2|x>y", "%PATH%", 'he said "hi"', "", "trailing\\", "$HOME", "`id`"]

PROBE = (
    "import json, os, sys\n"
    "with open(os.environ['PROBE_LOG'], 'a', encoding='utf-8') as log:\n"
    "    log.write(json.dumps({'argv': sys.argv, 'here': os.path.dirname(os.path.abspath(__file__)),\n"
    "                          'path0': sys.path[0]}) + '\\n')\n"
    "sys.exit(int(os.environ.get('PROBE_RC', '0')))\n"
)

windows = pytest.mark.skipif(os.name != "nt", reason="the .exe launcher is Windows'")
posix = pytest.mark.skipif(os.name == "nt", reason="the sh launcher is POSIX's")


def _overlay(store: Path, name: str, scripts: "dict[str, object]", probe: bool = True) -> Overlay:
    """An overlay whose manifest declares ``scripts`` (``False``: opted out),
    each declared path holding the probe script."""
    path = store / "overlays" / name
    (path / "bin").mkdir(parents=True, exist_ok=True)
    lines = ['name = "%s"' % name, "", "[scripts]"]
    lines += ["%s = %s" % (key, "false" if value is False else '"%s"' % value) for key, value in scripts.items()]
    (path / "overlay.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if probe:
        for script in (v for v in scripts.values() if v is not False):
            (path / script).parent.mkdir(parents=True, exist_ok=True)
            (path / script).write_text(PROBE, encoding="utf-8")
    return Overlay(path, store)


def _records(log: Path) -> list:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _env_for(overlay: Overlay, target: Path, log: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.upper().endswith("_OVERLAY_ROOT")}
    env.update({overlay.root_var: str(overlay.path), "PROBE_LOG": str(log),
                "PATH": os.pathsep.join([str(target), os.environ.get("PATH", "")])})
    return env


# -- overlay.toml ---------------------------------------------------------------


@pytest.mark.parametrize("reader", ["toml", "hand"])
def test_the_manifest_reads_scripts_and_drops_what_cannot_be_one(tmp_path, monkeypatch, reader):
    if reader == "hand":
        monkeypatch.setattr(_overlays, "_toml_module", lambda: None)
    monkeypatch.setattr(_overlays, "_warned", set(), raising=False)
    path = tmp_path / "demo"
    path.mkdir()
    (path / "overlay.toml").write_text(
        'name = "demo"\n\n[scripts]\ntool = "bin/tool.py"  # the entry\n"a.b" = \'lib/x/y.py\'\n'
        'up = "../x.py"\nabs = "/etc/x.py"\nwin = "bin\\\\x.py"\n"bad name" = "bin/x.py"\n'
        'con = "bin/x.py"\n"t.exe" = "bin/x.py"\noff = false\non = true\n',
        encoding="utf-8",
    )
    assert Overlay(path).read_manifest()["scripts"] == {"tool": "bin/tool.py", "a.b": "lib/x/y.py", "off": False}


def test_bin_scripts_are_detected_and_the_manifest_adds_replaces_or_drops(tmp_path):
    path = tmp_path / "demo"
    (path / "bin" / "sub").mkdir(parents=True)
    for name in ("tool.py", "helper.py", "replaced.py", "_private.py", "__init__.py", "con.py", "shell", "x.txt"):
        (path / "bin" / name).write_text("", encoding="utf-8")
    (path / "bin" / "sub" / "deep.py").write_text("", encoding="utf-8")
    overlay = Overlay(path)
    assert overlay.scripts() == {"tool": "bin/tool.py", "helper": "bin/helper.py", "replaced": "bin/replaced.py"}
    (path / "overlay.toml").write_text(
        '[scripts]\nhelper = false\nreplaced = "lib/main.py"\nextra = "lib/extra.py"\nmissing = false\n',
        encoding="utf-8",
    )
    assert overlay.scripts() == {"tool": "bin/tool.py", "replaced": "lib/main.py", "extra": "lib/extra.py"}


def test_a_manifest_without_scripts_declares_none(tmp_path):
    (tmp_path / "overlay.toml").write_text('name = "plain"\n', encoding="utf-8")
    assert Overlay(tmp_path).read_manifest()["scripts"] == {}
    assert Overlay(tmp_path / "missing").read_manifest()["scripts"] == {}


# -- generation -----------------------------------------------------------------


def test_sync_writes_one_launcher_per_script_and_is_idempotent(tmp_path):
    store = tmp_path / ".agents"
    overlay = _overlay(store, "demo", {"tool": "bin/tool.py"})
    venv = tmp_path / "venv"
    assert _launchers.sync(venv, Path(sys.executable), [overlay]) == ["tool"]
    target = _launchers.launcher_dir(venv)
    launcher = target / _launchers.file_name("tool")
    assert sorted(os.listdir(str(target))) == sorted([_launchers.MANIFEST, launcher.name])
    if os.name != "nt":
        assert launcher.stat().st_mode & 0o777 == 0o755
        assert launcher.read_bytes().startswith(b"#!/bin/sh\n")
    else:
        assert launcher.read_bytes()[:2] == b"MZ"
    assert json.loads((target / _launchers.MANIFEST).read_text(encoding="utf-8")) == {"tool": "demo"}
    stamp = launcher.stat().st_mtime_ns
    assert _launchers.sync(venv, Path(sys.executable), [overlay]) == []
    assert launcher.stat().st_mtime_ns == stamp, "unchanged bytes are not rewritten"


def test_a_launcher_names_its_interpreter(tmp_path):
    overlay = _overlay(tmp_path / ".agents", "demo", {"tool": "bin/tool.py"})
    one = _launchers.render("tool", overlay.root_var, "bin/tool.py", str(tmp_path / "one" / "python"))
    two = _launchers.render("tool", overlay.root_var, "bin/tool.py", str(tmp_path / "two" / "python"))
    assert one != two and str(tmp_path / "two" / "python").encode("utf-8") in two
    assert one == _launchers.render("tool", overlay.root_var, "bin/tool.py", str(tmp_path / "one" / "python"))


def test_launchers_no_overlay_declares_are_removed_and_others_left(tmp_path):
    store = tmp_path / ".agents"
    first = _overlay(store, "first", {"one": "bin/one.py", "two": "bin/two.py"})
    venv = tmp_path / "venv"
    _launchers.sync(venv, Path(sys.executable), [first])
    target = _launchers.launcher_dir(venv)
    (target / "someone-elses").write_text("x", encoding="utf-8")
    first = _overlay(store, "first", {"one": "bin/one.py", "two": False})
    assert _launchers.sync(venv, Path(sys.executable), [first]) == ["two"], "opted out: bin/two.py stays"
    assert not (target / _launchers.file_name("two")).exists()
    assert (target / _launchers.file_name("one")).exists()
    assert _launchers.sync(venv, Path(sys.executable), []) == ["one"]
    assert sorted(os.listdir(str(target))) == ["someone-elses"], "the manifest goes, foreign files stay"


@pytest.mark.parametrize("declared", [True, False])
def test_two_overlays_with_one_script_name_is_an_error_naming_both(tmp_path, declared):
    store = tmp_path / ".agents"
    a = _overlay(store, "alpha", {"tool": "bin/tool.py"} if declared else {})
    b = _overlay(store, "beta", {"tool": "lib/other.py"} if declared else {})
    for overlay in (a, b):
        (overlay.path / "bin" / "tool.py").write_text(PROBE, encoding="utf-8")
    with pytest.raises(_launchers.LauncherError) as caught:
        _launchers.sync(tmp_path / "venv", Path(sys.executable), [a, b])
    assert "alpha" in str(caught.value.code) and "beta" in str(caught.value.code)
    assert not (tmp_path / "venv").exists(), "nothing written before the check"


# -- running ------------------------------------------------------------------------


def _made(tmp_path):
    overlay = _overlay(tmp_path / ".agents", "demo", {"probe": "bin/probe.py"})
    venv = tmp_path / "venv"
    _launchers.sync(venv, Path(sys.executable), [overlay])
    work = tmp_path / "work"
    work.mkdir()
    return overlay, _launchers.launcher_dir(venv), work


def _check_run(out, log: Path, overlay: Overlay, work: Path):
    assert out.returncode == 5, out.stdout + out.stderr
    assert "not recognized" not in out.stdout + out.stderr
    [record] = _records(log)
    assert record["argv"][1:] == ARGS
    script = overlay.path / "bin" / "probe.py"
    assert os.path.normcase(record["argv"][0]) == os.path.normcase(str(script)), "argv[0] is the script"
    assert os.path.normcase(record["path0"]) == os.path.normcase(str(script.parent))
    assert os.listdir(str(work)) == [], "a redirection or a second command ran"


@posix
def test_by_name_from_sh_every_argument_arrives_as_passed(tmp_path):
    overlay, target, work = _made(tmp_path)
    log = tmp_path / "log.jsonl"
    env = dict(_env_for(overlay, target, log), PROBE_RC="5")
    out = subprocess.run(["/bin/sh", "-c", 'probe "$@"', "sh", *ARGS], cwd=str(work), env=env,
                         capture_output=True, text=True)
    _check_run(out, log, overlay, work)


def _pwsh(text: str) -> list:
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 (pwsh) is not installed")
    return [pwsh, "-NoProfile", "-NonInteractive", "-EncodedCommand",
            base64.b64encode(text.encode("utf-16-le")).decode("ascii")]


@windows
@pytest.mark.parametrize("shell", ["pwsh", "cmd"])
def test_by_name_from_powershell_and_cmd_every_argument_arrives_as_passed(tmp_path, shell):
    overlay, target, work = _made(tmp_path)
    log = tmp_path / "log.jsonl"
    env = dict(_env_for(overlay, target, log), PROBE_RC="5")
    if shell == "pwsh":
        command = _pwsh("probe " + " ".join("'%s'" % a.replace("'", "''") for a in ARGS) + "; exit $LASTEXITCODE")
    else:
        # cmd's own syntax: operators inside quotes, and %^PATH% for a literal
        # %PATH% (the caret goes after expansion finds no variable "^PATH").
        command = ('cmd.exe /d /c probe "http://h/?a=1&b=2|x>y" %^PATH% "he said \\"hi\\"" "" '
                   '"trailing\\\\" $HOME `id`')
    out = subprocess.run(command, cwd=str(work), env=env, capture_output=True, text=True)
    _check_run(out, log, overlay, work)


def _run(target: Path, env: dict):
    launcher = target / _launchers.file_name("probe")
    return subprocess.run([str(launcher), "x"], env=env, capture_output=True, text=True)


def test_the_script_is_found_through_the_overlay_root_when_it_runs(tmp_path):
    overlay, target, _work = _made(tmp_path)
    log = tmp_path / "log.jsonl"
    env = _env_for(overlay, target, log)
    moved = tmp_path / "moved" / "demo"
    shutil.copytree(str(overlay.path), str(moved))
    env[overlay.root_var] = str(moved)
    assert _run(target, env).returncode == 0
    assert os.path.normcase(_records(log)[0]["here"]) == os.path.normcase(str(moved / "bin"))

    del env[overlay.root_var]
    out = _run(target, env)
    assert out.returncode == 2 and overlay.root_var in out.stderr and "dotagents env" in out.stderr
    env[overlay.root_var] = str(tmp_path / "nowhere")
    out = _run(target, env)
    assert out.returncode == 2 and "bin/probe.py" in out.stderr
    assert len(_records(log)) == 1


# -- PATH and the venv sync -----------------------------------------------------


def _synced_venv(store: Path, real: bool = False) -> Path:
    venv = _pyvenv.venv_dir(store)
    if real:
        venv_module.EnvBuilder(with_pip=False).create(str(venv))
    else:
        python = _pyvenv.venv_python(venv)
        python.parent.mkdir(parents=True)
        python.write_bytes(b"")
    (venv / _pyvenv.STATE_FILE).write_text(json.dumps({"pth": []}), encoding="utf-8")
    return venv


def test_env_puts_the_active_venvs_launchers_first_and_no_other_venvs(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTS_NO_PYVENV")
    old_store, store = tmp_path / "old" / ".agents", tmp_path / "home" / ".agents"
    old_dir = _launchers.launcher_dir(_synced_venv(old_store))
    old_dir.mkdir()
    venv = _synced_venv(store)
    system = str(tmp_path / "system")
    scope = Scope("user", store)

    env = _env.get_environment(scope, base_env={"PATH": os.pathsep.join([str(old_dir), system])})
    assert str(old_dir) not in env["PATH"].split(os.pathsep), "another venv's launchers are never reachable"
    assert str(_launchers.launcher_dir(venv)) not in env["PATH"], "a missing dir adds nothing"

    _launchers.launcher_dir(venv).mkdir()
    env = _env.get_environment(scope, base_env={"PATH": os.pathsep.join([str(old_dir), system])})
    parts = env["PATH"].split(os.pathsep)
    assert parts[0] == str(_launchers.launcher_dir(venv))
    assert parts.index(str(store / "bin")) > 0 and parts[-1] == system
    again = _env.get_environment(scope, base_env={"PATH": env["PATH"], "AGENTS_PYTHON": env["AGENTS_PYTHON"]})
    assert again.get("PATH", env["PATH"]) == env["PATH"], "a second run changes nothing"

    own = tmp_path / "python3.12"
    own.write_bytes(b"")
    pinned = _env.get_environment(scope, base_env={"PATH": system, "AGENTS_PYTHON": str(own)})
    assert str(_launchers.launcher_dir(venv)) not in pinned["PATH"], "only the venv AGENTS_PYTHON names"


def test_a_venv_rebuilt_elsewhere_gets_working_launchers_on_path(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTS_NO_PYVENV")
    old_store, store = tmp_path / "old" / ".agents", tmp_path / "moved" / ".agents"
    overlay = _overlay(store, "demo", {"probe": "bin/probe.py"})
    old_venv = _synced_venv(old_store)
    _launchers.sync(old_venv, _pyvenv.venv_python(old_venv), [overlay])
    venv = _synced_venv(store, real=True)
    scope = Scope("user", store)
    _launchers.sync(venv, _pyvenv.venv_python(venv), _pyvenv.store_overlays(scope, [store]))

    system = os.environ.get("PATH", "")
    env = _env.get_environment(scope, base_env={"PATH": os.pathsep.join([str(_launchers.launcher_dir(old_venv)),
                                                                          system])})
    found = shutil.which("probe", path=env["PATH"])
    assert found and Path(found).parent == _launchers.launcher_dir(venv)
    log = tmp_path / "log.jsonl"
    run_env = dict(os.environ, PATH=env["PATH"], PROBE_LOG=str(log), **{overlay.root_var: str(overlay.path)})
    out = subprocess.run(["probe" if os.name != "nt" else found, "ok"], env=run_env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert _records(log)[0]["argv"][1:] == ["ok"]


def test_sync_venvs_refuses_launchers_without_a_venv(tmp_path, caplog):
    store = tmp_path / ".agents"
    _overlay(store, "demo", {"tool": "bin/tool.py"})
    logger = logging.getLogger("test-launchers")
    with caplog.at_level(logging.INFO, logger="test-launchers"):
        assert _pyvenv.sync_venvs(Scope("user", store), logger=logger) == []
    assert "AGENTS_NO_PYVENV" in caplog.text and "tool" in caplog.text
    assert not (store / ".pyvenv").exists()


def test_a_project_overlay_with_scripts_gets_a_venv(tmp_path):
    user, project = tmp_path / "home" / ".agents", tmp_path / "proj" / ".agents"
    _overlay(project, "demo", {"tool": "bin/tool.py"})
    scope = Scope("project", project, user_root=user, project_root=project.parent)
    assert _pyvenv.wants_venv(scope, "project", project, [])
