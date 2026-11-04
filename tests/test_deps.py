"""Overlays' dependencies (`dotagents._deps`): `lib/python/requirements.txt`
and `lib/node/package.json` install by default into the environment their
runtime variant chooses (shared, named or isolated), before setup, rebuilt on
every add, sync and removal; two overlays pinning one package to two versions
in one environment are both refused.

pip runs offline from local wheels; npm is a fake (there is none here)."""

import hashlib
import json
import logging
import os
import sys
from pathlib import Path

import pytest

from dotagents import _activate, _deps, _env
from dotagents._overlays import Overlay
from dotagents._scope import Scope
from dotagents.cli import OverlayAdd, OverlayRemove, OverlaySync

from _helpers import run_cmd as _run
from test_pyvenv_core import make_wheel

BASE_AGENTS = "<!-- dotagents:begin -->\n# BASE\n## Load on demand\n<!-- dotagents:end -->\n"
SHARED = ("python", "shared", "")


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


@pytest.fixture
def store(tmp_path):
    store = tmp_path / "store"
    _write(store / "AGENTS.md", BASE_AGENTS)
    _deps._BUILT.clear()
    return store


@pytest.fixture
def wheels(tmp_path, monkeypatch):
    """A local index: pip finds only these wheels."""
    directory = tmp_path / "wheels"
    directory.mkdir()
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_FIND_LINKS", str(directory))
    return directory


def _add(src, store, *names, **extra):
    return _run(OverlayAdd, name=list(names), repo=[str(src)], global_scope=True, agents_dir=store,
                copy=True, dry_run=False, **extra)


def _remove(store, *names):
    return _run(OverlayRemove, name=list(names), global_scope=True, agents_dir=store, dry_run=False)


def _env_of(store):
    return _env.get_environment(Scope("user", store), base_env={"PATH": ""}, logger=logging.getLogger("t"))


def test_python_dependencies_go_into_the_shared_environment_before_setup(tmp_path, store, wheels, caplog):
    make_wheel(wheels, "depa", "WHO = 'a'\n")
    src = tmp_path / "src"
    _write(src / "tool" / "lib" / "python" / "requirements.txt", "depa\n")
    _write(src / "tool" / "lib" / "python" / "toolmod.py", "import depa\n")
    _write(src / "tool" / "setup.py", "import os, sys, depa\nopen(os.path.join(os.environ['AOS_OVERLAY_STATE'], "
                                      "'seen'), 'w').write(sys.argv[1] + ':' + depa.WHO)\n")
    with caplog.at_level(logging.INFO):
        assert _add(src, store, "tool") == 0
    installed = Overlay(store / "overlays" / "tool")
    built = _deps.packages_dir(store, SHARED)
    assert built is not None and (built / "depa.py").is_file()
    assert (installed.state_dir() / "seen").read_text(encoding="utf-8") == "install:a", "deps before setup"
    assert installed.read_preparation() == {"digest": installed.tree_digest(), "phase": "install"}
    assert "deps-network lib/python/requirements.txt" in caplog.text
    assert "unlocked-dependencies lib/python/requirements.txt" in caplog.text
    # Venvs are off in this suite, so the shared environment is on PYTHONPATH after lib/python/.
    env = _env_of(store)
    assert env["PYTHONPATH"].split(os.pathsep)[:2] == [str(installed.path / "lib" / "python"), str(built)]


def test_a_requirements_file_with_every_hash_is_a_lock(tmp_path, store, wheels, caplog):
    wheel = make_wheel(wheels, "depa", "WHO = 'a'\n")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    src = tmp_path / "src"
    _write(src / "bad" / "lib" / "python" / "requirements.txt", "depa==1.0 --hash=sha256:%s\n" % ("0" * 64))
    with pytest.raises(SystemExit):
        _add(src, store, "bad")
    assert _deps.packages_dir(store, SHARED) is None, "pip checked the hash"
    _write(src / "tool" / "lib" / "python" / "requirements.txt", "depa==1.0 --hash=sha256:%s\n" % digest)
    with caplog.at_level(logging.WARNING):
        assert _add(src, store, "tool") == 0
    assert (_deps.packages_dir(store, SHARED) / "depa.py").is_file()
    assert "unlocked-dependencies" not in caplog.text


def test_a_requirements_file_hashed_in_part_is_refused(tmp_path, store, caplog):
    src = tmp_path / "src"
    _write(src / "tool" / "lib" / "python" / "requirements.txt",
           "depa==1.0 --hash=sha256:%s\nrich\n" % ("0" * 64))
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit):
        _add(src, store, "tool")
    assert "partial-lock lib/python/requirements.txt" in caplog.text
    assert not (store / "overlays" / "tool").exists()


def test_deps_false_installs_nothing(tmp_path, store):
    src = tmp_path / "src"
    _write(src / "tool" / "lib" / "python" / "requirements.txt", "never-fetched\n")
    _write(src / "tool" / "overlay.cfg", "[defaults]\ndeps = false\n")
    assert _add(src, store, "tool") == 0
    assert _deps.packages_dir(store, SHARED) is None
    assert Overlay(store / "overlays" / "tool").read_preparation() is None, "nothing to prepare"
    assert "TOOL_OVERLAY_ROOT" in _env_of(store)


def test_two_versions_of_one_package_in_one_environment_refuse_both(tmp_path, store, caplog):
    for name, version in (("a", "1.0"), ("b", "2.0")):
        overlay = store / "overlays" / name
        _write(overlay / "lib" / "python" / "requirements.txt", "Dep_A==%s\n" % version)
        Overlay(overlay).write_preparation(Overlay(overlay).tree_digest(), "install")
    _write(store / "overlays" / "c" / "kb" / "C.md", "c\n")
    with caplog.at_level(logging.WARNING):
        active = _activate.active(Scope("user", store).installed_overlays)
    assert [o.name for o in active] == ["c"]
    assert "asks for dep-a at another version than b in the python:shared environment" in caplog.text


def test_a_conflicting_add_is_rolled_back_and_the_environment_kept(tmp_path, store, wheels, caplog):
    make_wheel(wheels, "depa", "WHO = 'one'\n", version="1.0")
    make_wheel(wheels, "depa", "WHO = 'two'\n", version="2.0")
    src = tmp_path / "src"
    _write(src / "a" / "lib" / "python" / "requirements.txt", "depa==1.0\n")
    _write(src / "b" / "lib" / "python" / "requirements.txt", "depa==2.0\n")
    assert _add(src, store, "a") == 0
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit):
        _add(src, store, "b")
    assert "a and b ask for depa at different versions" in caplog.text
    assert not (store / "overlays" / "b").exists()
    assert "one" in (_deps.packages_dir(store, SHARED) / "depa.py").read_text(encoding="utf-8")


def test_an_isolated_environment_is_the_overlays_own_and_goes_with_it(tmp_path, store, wheels):
    make_wheel(wheels, "depa", "WHO = 'a'\n")
    src = tmp_path / "src"
    for name in ("own", "common"):
        _write(src / name / "lib" / "python" / "requirements.txt", "depa\n")
    _write(src / "own" / "overlay.cfg", '[[runtime.python]]\nenvironment = "isolated"\n')
    assert _add(src, store, "own", "common") == 0
    isolated = _deps.packages_dir(store, ("python", "isolated", "own"))
    assert isolated is not None and (isolated / "depa.py").is_file()
    assert _deps.packages_dir(store, SHARED) is not None
    assert str(isolated) in _env_of(store)["PYTHONPATH"].split(os.pathsep)
    assert _remove(store, "own", "common") == 0
    assert not _deps.env_dir(store, ("python", "isolated", "own")).exists()
    assert not _deps.env_dir(store, SHARED).exists(), "the shared environment's last member left"


def test_removing_a_member_rebuilds_the_shared_environment_without_it(tmp_path, store, wheels):
    make_wheel(wheels, "depa", "WHO = 'a'\n")
    make_wheel(wheels, "depb", "WHO = 'b'\n")
    src = tmp_path / "src"
    _write(src / "a" / "lib" / "python" / "requirements.txt", "depa\n")
    _write(src / "b" / "lib" / "python" / "requirements.txt", "depb\n")
    assert _add(src, store, "a", "b") == 0
    built = _deps.packages_dir(store, SHARED)
    assert (built / "depa.py").is_file() and (built / "depb.py").is_file(), "installed together"
    assert _remove(store, "b") == 0
    built = _deps.packages_dir(store, SHARED)
    assert (built / "depa.py").is_file() and not (built / "depb.py").exists()


FAKE_NPM = '''import json, os, sys
verb, target = sys.argv[1], sys.argv[3]
with open(os.environ["FAKE_NPM_LOG"], "a") as log:
    log.write("%s %s\\n" % (verb, sorted(os.listdir(target))))
for name in json.load(open(os.path.join(target, "package.json")))["dependencies"]:
    os.makedirs(os.path.join(target, "node_modules", name))
    open(os.path.join(target, "node_modules", name, "index.js"), "w").write("module.exports = 1\\n")
'''


def test_node_dependencies_install_with_npm_ci_when_locked(tmp_path, store, monkeypatch, caplog):
    npm = _write(tmp_path / "npm.py", FAKE_NPM)
    log = tmp_path / "npm.log"
    monkeypatch.setenv("FAKE_NPM_LOG", str(log))
    monkeypatch.setattr(_deps, "npm_command", lambda: [sys.executable, str(npm)])
    # This machine has no node or npm for the probe to find.
    monkeypatch.setattr("dotagents.cli.overlays._probe", lambda overlay, logger: [])
    src = tmp_path / "src"
    _write(src / "web" / "lib" / "node" / "package.json", json.dumps({"dependencies": {"left-pad": "1.3.0"}}))
    _write(src / "web" / "lib" / "node" / "package-lock.json", "{}")
    _write(src / "web" / "lib" / "node" / "tool.js", "")
    assert _add(src, store, "web") == 0
    assert log.read_text(encoding="utf-8").splitlines() == ["ci ['package-lock.json', 'package.json']"]
    modules = _deps.packages_dir(store, ("node", "shared", ""))
    assert (modules / "left-pad" / "index.js").is_file()
    root = store / "overlays" / "web"
    assert _env_of(store)["NODE_PATH"].split(os.pathsep) == [str(root / "lib" / "node"), str(modules)]
    (src / "web" / "lib" / "node" / "package-lock.json").unlink()
    with caplog.at_level(logging.WARNING):
        assert _run(OverlaySync, pattern="web", repo=[str(src)], global_scope=True, agents_dir=store,
                    copy=True, dry_run=False) == 0
    assert log.read_text(encoding="utf-8").splitlines()[-1] == "install ['package.json']"
    assert "unlocked-dependencies lib/node/package.json" in caplog.text
