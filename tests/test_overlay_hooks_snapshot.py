"""A hook, and everything that builds its environment, runs from the bytes that were hashed: env
scripts, paths and the manifest too; interpreters are never found in the working directory or the
project; a guard that has gone missing fails; and the same through the real forwarder and child."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from _hook_helpers import Rig, store_env, write
from dotagents import _hook_dispatch, _overlay_hooks, _pyvenv, _run
from dotagents._agents import ClaudeAgent
from dotagents._scope import resolve_user_store

PRE_TOOL = {"session_id": "s", "cwd": "/", "tool_name": "Bash", "tool_input": {"command": "ls"}}
PROMPT = {"session_id": "s", "cwd": "/", "prompt": "x"}

ENV_PY = "import json, os\nprint(json.dumps({'ENV_CWD': os.getcwd(), 'ENV_FILE': os.path.abspath(__file__)}))\n"


@pytest.fixture(autouse=True)
def _user_store(tmp_path, monkeypatch):
    store_env(tmp_path, monkeypatch)


def snapshots(rig: Rig) -> str:
    return os.path.realpath(str(rig.store / ".cache" / "hook-snapshots"))


# ---- B: declarations, env scripts and paths are resolved against the snapshot ---------------


def test_env_scripts_and_paths_of_a_hooks_environment_are_the_snapshots(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n",
              extra={"env.py": ENV_PY, "lib/python/helper.py": "", "lib/node/x.js": "", "bin/tool": ""})
    rig.allow()
    rig.fire("claude-code", "prompt", PROMPT)
    seen = rig.calls()[0]
    live = os.path.realpath(str(rig.root))
    assert os.path.realpath(seen["paths"]["ENV_CWD"]).startswith(snapshots(rig))
    assert os.path.realpath(seen["paths"]["ENV_FILE"]).startswith(snapshots(rig)), "the env script that ran"
    for name in ("PYTHONPATH", "NODE_PATH", "PATH"):
        entries = [os.path.realpath(e) for e in (seen["paths"][name] or "").split(os.pathsep) if e]
        assert not [e for e in entries if e == live or e.startswith(live + os.sep)], (name, entries)
    assert any(os.path.realpath(e).startswith(snapshots(rig)) for e in seen["paths"]["PYTHONPATH"].split(os.pathsep))


def test_a_python_hook_is_isolated(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n", extra={"lib/python/ownlib.py": "VALUE = 'own'\n"},
              script="import ownlib, json, os, sys\n"
                     "open(os.environ['HOOK_LOG'], 'a').write(json.dumps({'isolated': sys.flags.isolated, "
                     "'value': ownlib.VALUE, 'cwd': os.getcwd(), 'first': sys.path[0]}) + '\\n')\n")
    rig.allow()
    rig.fire("claude-code", "prompt", PROMPT, PYTHONPATH="")
    seen = rig.calls()[0]
    assert seen["isolated"] == 1, "run under -I"
    assert seen["value"] == "own", "the snapshot's own lib is on the path explicitly"
    assert os.path.realpath(seen["first"]).startswith(snapshots(rig))


def test_an_env_script_swapped_after_hashing_does_not_run(tmp_path, monkeypatch):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n", extra={"env.py": ENV_PY})
    rig.allow()
    marker = tmp_path / "EVIL-ENV-RAN"
    real = _overlay_hooks.read_tree

    def swapping(overlay, keep=False):
        got = real(overlay, keep)
        write(overlay.path / "env.py", "open(%r, 'w').write('x')\nprint('{}')\n" % str(marker))
        return got

    monkeypatch.setattr(_overlay_hooks, "read_tree", swapping)
    rig.fire("claude-code", "prompt", PROMPT)
    assert not marker.exists(), "the swapped env script ran"
    assert rig.calls()[0]["paths"]["ENV_FILE"], "the hashed one did"


def test_the_manifest_is_the_one_that_was_hashed(tmp_path, monkeypatch):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n")
    rig.allow()
    real = _overlay_hooks.read_tree

    def swapping(overlay, keep=False):
        got = real(overlay, keep)
        write(overlay.path / "overlay.cfg", "[hooks]\nprompt = false\n")
        return got

    monkeypatch.setattr(_overlay_hooks, "read_tree", swapping)
    rig.fire("claude-code", "prompt", PROMPT)
    assert len(rig.calls()) == 1, "the hashed manifest says the hook runs; the swapped one did not decide"


def test_a_snapshot_that_was_tampered_with_is_made_again_and_older_ones_are_kept(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n")
    rig.allow()
    rig.fire("claude-code", "prompt", PROMPT)
    first = rig.calls()[0]["root"]
    write(Path(first) / "hook.py", "open(%r, 'w').write('tampered')\n" % str(tmp_path / "TAMPERED"))
    rig.log.unlink()
    rig.fire("claude-code", "prompt", PROMPT)
    assert not (tmp_path / "TAMPERED").exists() and len(rig.calls()) == 1, "the copy was checked, not trusted"
    # An update leaves the earlier digest's copy for the age-based sweep: a session may still run from it.
    old_holder = os.path.dirname(first)
    write(rig.root / "hook.py", (rig.root / "hook.py").read_text(encoding="utf-8") + "# v2\n")
    rig.allow()
    rig.fire("claude-code", "prompt", PROMPT)
    assert os.path.isdir(old_holder) and os.path.isdir(os.path.dirname(rig.calls()[-1]["root"]))
    assert os.path.dirname(rig.calls()[-1]["root"]) != old_holder


# ---- 3: interpreters are never the working directory's or the project's ---------------------


def test_which_in_never_looks_in_the_working_directory(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    name = "planted-tool"
    planted = tmp_path / "cwd" / (name + (".exe" if os.name == "nt" else ""))
    write(planted, "x")
    planted.chmod(0o755)
    monkeypatch.chdir(tmp_path / "cwd")
    assert _run.which_in(name, str(elsewhere)) is None
    assert _run.which_in(name, os.pathsep.join(["", ".", "relative"])) is None
    assert os.path.normcase(_run.which_in(name, str(tmp_path / "cwd"))) == os.path.normcase(str(planted))


def test_a_windows_bash_is_not_looked_for_in_the_environments_path(tmp_path, monkeypatch):
    from dotagents import _env

    marker = tmp_path / "BASH-RAN"
    bin_dir = tmp_path / "planted"
    write(bin_dir / ("bash.cmd" if os.name == "nt" else "bash"),
          "@echo off\r\necho x > %s\r\n" % marker if os.name == "nt" else "#!/bin/sh\ntouch %s\n" % marker)
    (bin_dir / ("bash.cmd" if os.name == "nt" else "bash")).chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "nothing"))
    monkeypatch.setenv("ProgramW6432", str(tmp_path / "nothing"))
    assert _env.find_bash_on("") is None
    assert not marker.exists(), "a candidate from the session's PATH was executed"


def test_clean_path_drops_everything_inside_the_project_and_the_stores(tmp_path):
    project, store = tmp_path / "proj", tmp_path / "store"
    good = tmp_path / "tools"
    dropped = [project / ".venv" / "Scripts", project / "node_modules" / ".bin", project / "vendor" / "bin",
               project / ".agents" / "bin", store / "bin", store / "anything"]
    path = os.pathsep.join([str(good)] + [str(d) for d in dropped])
    assert _overlay_hooks.clean_path(path, store, [str(project)]) == str(good)
    # The project root being the user's whole home would drop their own tools: it is not used.
    home_tools = os.path.join(os.path.expanduser("~"), "bin")
    assert _overlay_hooks.clean_path(home_tools, store, [os.path.expanduser("~")]) == home_tools


def test_project_roots_find_the_project_from_where_the_agent_works(tmp_path):
    project = tmp_path / "proj"
    (project / ".agents").mkdir(parents=True)
    (project / "src" / "deep").mkdir(parents=True)
    roots = _overlay_hooks.project_roots({}, str(project / "src" / "deep"))
    assert str(project) in roots and str(project / "src" / "deep") in roots
    assert str(tmp_path / "x") in _overlay_hooks.project_roots({"AGENTS_PROJECT_ROOT": str(tmp_path / "x")}, str(tmp_path))


# ---- D: a guard that has gone missing is a failed guard --------------------------------------


def test_a_recorded_guard_that_is_not_found_refuses(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n")  # no guard on pre-tool any more
    rig.allow()
    scope = _hook_dispatch.scope_for("user", rig.store)
    stdout, stderr = _hook_dispatch.dispatch("claude-code", "pre-tool", json.dumps(PRE_TOOL).encode(), scope, "user",
                                             dict(rig.environ), expect_guard=True)
    assert json.loads(stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "guard-missing" in stderr
    stdout, _ = _hook_dispatch.dispatch("claude-code", "pre-tool", json.dumps(PRE_TOOL).encode(), scope, "user",
                                        dict(rig.environ))
    assert stdout == "", "without the record's word there is no guard to miss"


# ---- E ----------------------------------------------------------------------------------------


def test_a_failure_checking_for_shadowing_does_not_lose_the_answer(tmp_path, monkeypatch):
    rig = Rig(tmp_path, "[hooks]\nsession-start = {}\n")
    rig.allow()

    def broken(scope):
        raise OSError("boom")

    monkeypatch.setattr(_overlay_hooks, "shadowed_with_hooks", broken)
    scope = _hook_dispatch.scope_for("user", rig.store)
    stdout, stderr = _hook_dispatch.dispatch(
        "claude-code", "session-start", json.dumps({"session_id": "s", "cwd": "/", "source": "startup"}).encode(),
        scope, "user", dict(rig.environ, HOOK_OUT=json.dumps({"context": "still here"})), shadow_scope=scope)
    assert "still here" in json.loads(stdout)["hookSpecificOutput"]["additionalContext"]
    assert "shadow-check" in stderr and len(rig.calls()) == 1


# ---- the same through the real forwarder and child ----------------------------------------


def wire(rig: Rig, tmp_path, dest=None, level="user"):
    root = tmp_path / "claude-config"
    agent = ClaudeAgent()
    agent.scope_level = level
    agent.wire_overlay_hooks(dest or rig.store, dry_run=False, logger=None, config_root=root)
    return root / "hooks" / "overlay_hook.py"


def plant_project_venv(project):
    venv = _pyvenv.venv_dir(project / ".agents")
    write(_pyvenv.venv_python(venv), "this is not a python\n")
    write(venv / _pyvenv.STATE_FILE, "{}")
    return venv


def test_env_scripts_run_under_the_users_python_with_a_planted_project_venv_end_to_end(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n",
              extra={"env.py": "import json, sys\nprint(json.dumps({'RAN_UNDER': sys.executable}))\n"})
    rig.allow()
    forwarder = wire(rig, tmp_path)
    project = tmp_path / "project"
    plant_project_venv(project)
    env = {k: v for k, v in rig.environ.items() if k != "AGENTS_NO_PYVENV"}
    env.update(AGENTS_PROJECT_ROOT=str(project), CLAUDE_PROJECT_DIR=str(project))
    done = subprocess.run([sys.executable, "-I", str(forwarder), "claude-code", "prompt", "user"],
                          input=json.dumps(PROMPT).encode(), capture_output=True, env=env, cwd=str(project), timeout=120)
    assert done.returncode == 0, done.stderr
    seen = rig.calls()[0]["paths"]
    assert os.path.normcase(seen["RAN_UNDER"]) == os.path.normcase(sys.executable)


def test_a_project_overlays_env_scripts_run_under_the_users_python_end_to_end(tmp_path, monkeypatch):
    rig = Rig(tmp_path, "")  # the user store, with its own overlay out of the way
    project = tmp_path / "project"
    store = project / ".agents"
    root = store / "overlays" / "projx"
    write(rig.store / "AGENTS.md", "<!-- dotagents:begin -->\n# B\n<!-- dotagents:end -->\n")
    write(root / "overlay.cfg", "[hooks]\nprompt = {}\n")
    write(root / "hook.py", (rig.root / "hook.py").read_text(encoding="utf-8"))
    write(root / "env.py", "import json, sys\nprint(json.dumps({'RAN_UNDER': sys.executable}))\n")
    plant_project_venv(project)
    from dotagents._overlays import Overlay

    live = Overlay(root, store)
    _overlay_hooks.allow(rig.store, "projx", live.tree_digest(), store)
    token = _overlay_hooks.level_token(store, resolve_user_store())
    agent = ClaudeAgent()
    agent.scope_level, agent.project_root = "project", project
    config = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    agent.wire_overlay_hooks(store, dry_run=False, logger=None)
    forwarder = config / "hooks" / "overlay_hook.py"
    env = {k: v for k, v in rig.environ.items() if k != "AGENTS_NO_PYVENV"}
    env.update(AGENTS_PROJECT_ROOT=str(project), CLAUDE_PROJECT_DIR=str(project))
    done = subprocess.run([sys.executable, "-I", str(forwarder), "claude-code", "prompt", token],
                          input=json.dumps(PROMPT).encode(), capture_output=True, env=env, cwd=str(project), timeout=120)
    assert done.returncode == 0, done.stderr
    seen = rig.calls()[0]["paths"]
    assert os.path.normcase(seen["RAN_UNDER"]) == os.path.normcase(sys.executable)
    assert os.path.realpath(rig.calls()[0]["root"]).startswith(snapshots(rig))


def test_a_guard_missing_from_the_overlay_denies_through_the_real_forwarder(tmp_path):
    rig = Rig(tmp_path, "[hooks]\npre-tool = { on_failure = \"refuse\" }\n")
    rig.allow()
    forwarder = wire(rig, tmp_path)
    write(rig.root / "overlay.cfg", "[hooks]\nprompt = {}\n")   # the guard taken out afterwards
    rig.allow()
    done = subprocess.run([sys.executable, "-I", str(forwarder), "claude-code", "pre-tool", "user"],
                          input=json.dumps(PRE_TOOL).encode(), capture_output=True, env=rig.environ,
                          cwd=str(tmp_path), timeout=120)
    assert json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert rig.calls() == []
