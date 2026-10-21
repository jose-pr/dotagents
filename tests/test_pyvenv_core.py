"""Scope venvs (`dotagents._pyvenv`): one per store, created from the running
interpreter, holding duho plus every installed overlay's requirements, and
layered project > user > system through a `.pth` -- and `env` pointing
AGENTS_PYTHON at the active one while keeping its libs off PYTHONPATH."""
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from dotagents import _env, _launchers, _pipwheel, _pyvenv
from dotagents._scope import Scope

ROOT = Path(__file__).resolve().parents[1]


def make_wheel(directory: Path, name: str, source: str = "X = 1\n", version: str = "1.0") -> Path:
    """A minimal pure-Python wheel holding one module, ``<name>.py``."""
    path = directory / ("%s-%s-py3-none-any.whl" % (name, version))
    info = "%s-%s.dist-info" % (name, version)
    with zipfile.ZipFile(str(path), "w") as wheel:
        wheel.writestr(name + ".py", source)
        wheel.writestr(info + "/METADATA", "Metadata-Version: 2.1\nName: %s\nVersion: %s\n" % (name, version))
        wheel.writestr(info + "/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        wheel.writestr(info + "/RECORD", "%s.py,,\n%s/METADATA,,\n%s/WHEEL,,\n%s/RECORD,,\n" % ((name,) + (info,) * 3))
    return path


def _store_with_lib_overlay(store: Path, name: str, requirement: "str | None" = None) -> Path:
    overlay = store / "overlays" / name
    (overlay / "lib").mkdir(parents=True)
    (overlay / "lib" / ("%s_lib.py" % name)).write_text("WHO = %r\n" % name, encoding="utf-8")
    if requirement:
        (overlay / "requirements.txt").write_text(requirement + "\n", encoding="utf-8")
    return overlay


def test_base_requirements_are_dotagents_own_duho_range():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'"(duho[^"]*)"', text).group(1).replace(" ", "")
    assert _pyvenv.BASE_REQUIREMENTS == (declared,)


def test_venv_dir_is_named_by_the_running_interpreter(tmp_path):
    version, os_name, arch = _pyvenv.triplet().split("-", 2)
    assert version == "%d.%d.%d" % sys.version_info[:3]
    assert os_name in ("nt", "posix", "darwin") and arch
    assert _pyvenv.venv_dir(tmp_path) == tmp_path / ".pyvenv" / _pyvenv.triplet()


def test_layers_run_project_then_user_highest_first(tmp_path):
    user, project = tmp_path / "home" / ".agents", tmp_path / "proj" / ".agents"
    _store_with_lib_overlay(user, "u1")
    (user / "lib").mkdir()
    _store_with_lib_overlay(project, "p1")
    scope = Scope("project", project, user_root=user, project_root=project.parent)
    stack = _pyvenv.layers(scope)
    assert [(level, store) for level, store, _libs in stack] == [("project", project), ("user", user)]
    assert stack[0][2] == [project / "overlays" / "p1" / "lib"]
    # The store's own lib outranks its overlays' libs.
    assert stack[1][2] == [user / "lib", user / "overlays" / "u1" / "lib"]


def test_a_project_without_libs_or_requirements_uses_the_user_venv(tmp_path):
    user, project = tmp_path / "home" / ".agents", tmp_path / "proj" / ".agents"
    project.mkdir(parents=True)
    scope = Scope("project", project, user_root=user, project_root=project.parent)
    assert not _pyvenv.wants_venv(scope, "project", project, [])
    assert _pyvenv.wants_venv(scope, "user", user, [])


def test_pyvenv_off_does_nothing(tmp_path):
    scope = Scope("user", tmp_path / ".agents")
    assert _pyvenv.disabled()  # the suite sets AGENTS_NO_PYVENV
    assert _pyvenv.sync_venvs(scope) == []
    assert not (tmp_path / ".agents" / ".pyvenv").exists()


def _fake_venv(store: Path, pth: "list[str]") -> Path:
    """A venv as `env` sees one: an interpreter file and the state a sync writes."""
    venv = _pyvenv.venv_dir(store)
    python = _pyvenv.venv_python(venv)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    (venv / _pyvenv.STATE_FILE).write_text(json.dumps({"pth": pth}), encoding="utf-8")
    return venv


def test_env_runs_skills_under_the_venv_and_keeps_its_libs_off_pythonpath(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTS_NO_PYVENV")
    user = tmp_path / "home" / ".agents"
    lib = _store_with_lib_overlay(user, "u1") / "lib"
    venv = _fake_venv(user, [str(lib)])
    scope = Scope("user", user)
    env = _env.get_environment(scope, base_env={"PATH": os.environ.get("PATH", "")})
    assert env["AGENTS_PYTHON"] == str(_pyvenv.venv_python(venv))
    assert env["AGENTS_PYVENV"] == str(venv)
    assert str(lib) in env["AGENTS_PYTHONPATH"].split(os.pathsep)
    assert str(lib) not in env.get("PYTHONPATH", "").split(os.pathsep)


def test_a_venv_no_sync_made_is_never_the_agents_venv(tmp_path, monkeypatch):
    # A project venv someone created in .pyvenv by hand (or a sync that failed
    # before installing) has no state file: skills must not run in it.
    monkeypatch.delenv("AGENTS_NO_PYVENV")
    user = tmp_path / "home" / ".agents"
    venv = _fake_venv(user, [])
    (venv / _pyvenv.STATE_FILE).unlink()
    assert _pyvenv.active_venv(Scope("user", user)) is None


def test_env_without_a_venv_keeps_libs_on_pythonpath(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTS_NO_PYVENV")
    user = tmp_path / "home" / ".agents"
    lib = _store_with_lib_overlay(user, "u1") / "lib"
    env = _env.get_environment(Scope("user", user), base_env={"PATH": "", "AGENTS_PYVENV": "stale"})
    assert env["AGENTS_PYTHON"] == sys.executable
    assert "AGENTS_PYVENV" in env.removed
    assert str(lib) in env["PYTHONPATH"].split(os.pathsep)


def test_env_replaces_a_pin_it_set_but_keeps_the_users(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTS_NO_PYVENV")
    user = tmp_path / "home" / ".agents"
    venv = _fake_venv(user, [])
    scope = Scope("user", user)
    mine = _env.get_environment(scope, base_env={"PATH": "", "AGENTS_PYTHON": sys.executable})
    assert mine["AGENTS_PYTHON"] == str(_pyvenv.venv_python(venv))
    own = tmp_path / "python3.12"
    own.write_bytes(b"")
    theirs = _env.get_environment(scope, base_env={"PATH": "", "AGENTS_PYTHON": str(own)})
    assert "AGENTS_PYTHON" not in theirs


def test_sync_installs_layers_and_skips_pip_when_unchanged(tmp_path, monkeypatch, pip_wheel_file):
    """Real venvs, made WITHOUT pip, so pip runs from the wheel via --python;
    every distribution comes from a local directory (no index)."""
    monkeypatch.delenv("AGENTS_NO_PYVENV")
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    (wheels / pip_wheel_file.name).write_bytes(pip_wheel_file.read_bytes())
    make_wheel(wheels, "basedep", "WHO = 'base'\n")
    make_wheel(wheels, "projdep", "WHO = 'project'\n")
    monkeypatch.setenv(_pipwheel.PIP_SOURCE_ENV, str(wheels))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setattr(_pyvenv, "BASE_REQUIREMENTS", ("basedep",))
    real_ensure = _pyvenv.ensure_venv
    monkeypatch.setattr(_pyvenv, "ensure_venv",
                        lambda target, python=None, **kw: real_ensure(target, python, **dict(kw, with_pip=False)))
    installs = []
    real_install = _pyvenv._install
    monkeypatch.setattr(_pyvenv, "_install", lambda *a, **kw: installs.append(a[1]) or real_install(*a, **kw))

    user, project = tmp_path / "home" / ".agents", tmp_path / "proj" / ".agents"
    u1 = _store_with_lib_overlay(user, "u1")
    (u1 / "overlay.toml").write_text('name = "u1"\n[scripts]\nu1tool = "lib/u1_lib.py"\n', encoding="utf-8")
    _store_with_lib_overlay(project, "p1", requirement="projdep")
    scope = Scope("project", project, user_root=user, project_root=project.parent)

    assert _pyvenv.sync_venvs(scope) == [_pyvenv.venv_dir(user), _pyvenv.venv_dir(project)]
    # A user overlay's script gets a launcher in the user venv and in the
    # project venv layered over it, each naming its own interpreter.
    for venv in (_pyvenv.venv_dir(user), _pyvenv.venv_dir(project)):
        launcher = _launchers.launcher_dir(venv) / _launchers.file_name("u1tool")
        out = subprocess.run([str(launcher)], env=dict(os.environ, U1_OVERLAY_ROOT=str(u1)),
                             capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
    assert len(installs) == 2
    pvenv = _pyvenv.venv_dir(project)
    assert not (_pyvenv.site_packages(pvenv) / "pip").exists(), "pip ran from the wheel"
    pth = (_pyvenv.site_packages(pvenv) / _pyvenv.PTH_FILE).read_text(encoding="utf-8").splitlines()
    assert pth == [str(project / "overlays" / "p1" / "lib"),
                   str(_pyvenv.site_packages(_pyvenv.venv_dir(user))),
                   str(user / "overlays" / "u1" / "lib"),
                   _pyvenv.runtime_entry()]
    code = ("import basedep, projdep, u1_lib, p1_lib, dotagents._fs; "
            "print(basedep.WHO, projdep.WHO, u1_lib.WHO, p1_lib.WHO)")
    out = subprocess.run([str(_pyvenv.venv_python(pvenv)), "-c", code], capture_output=True, text=True)
    assert out.stdout.split() == ["base", "project", "u1", "p1"], out.stderr

    _pyvenv.sync_venvs(scope)
    assert len(installs) == 2, "nothing changed, so pip did not run"
    (project / "overlays" / "p1" / "requirements.txt").write_text("projdep\nbasedep\n", encoding="utf-8")
    _pyvenv.sync_venvs(scope)
    assert len(installs) == 3


def _overlay_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    overlay = repo / "needsreq"
    overlay.mkdir(parents=True)
    (overlay / "requirements.txt").write_text("somedep\n", encoding="utf-8")
    (overlay / "overlay.toml").write_text('name = "needsreq"\ndescription = "x"\n', encoding="utf-8")
    return repo


def test_overlays_add_syncs_the_venv_unless_told_not_to(tmp_path, monkeypatch):
    from dotagents import cli

    calls = []
    monkeypatch.setattr(_pyvenv, "sync_venvs", lambda scope, **kw: calls.append(scope.level) or [])
    repo, store = _overlay_repo(tmp_path), tmp_path / "store"
    argv = ["overlays", "add", "needsreq", "--repo", str(repo), "-g", "--agents-dir", str(store)]
    assert cli.main(argv + ["--no-venv"]) == 0
    assert calls == []
    assert cli.main(argv) == 0
    assert calls == ["user"]


def test_a_failed_requirements_install_rolls_a_fresh_install_back(tmp_path, monkeypatch):
    from dotagents import cli

    def fail(scope, **kw):
        raise _pyvenv.VenvError("error: pip could not install somedep")

    monkeypatch.setattr(_pyvenv, "sync_venvs", fail)
    repo, store = _overlay_repo(tmp_path), tmp_path / "store"
    rc = cli.main(["overlays", "add", "needsreq", "--repo", str(repo), "-g", "--agents-dir", str(store)])
    assert rc != 0
    assert not (store / "overlays" / "needsreq").exists()
