"""The bypasses an independent verifier reproduced, each as a test: a project folder that shadows a
user guard, interpreters found on a PATH the project leads, a project's Python running env scripts,
the project's own name lost to the session base, and limits past what a harness allows."""

from __future__ import annotations

import json
import logging
import os
import sys

import pytest

from _hook_helpers import Rig, store_env, write
from dotagents import _env, _hook_dispatch, _overlay_hooks, _pyvenv, _run
from dotagents._scope import Scope

PRE_TOOL = {"session_id": "s", "cwd": "/", "tool_name": "Bash", "tool_input": {"command": "ls"}}
GUARD = "[hooks]\npre-tool = { on_failure = \"refuse\" }\n"


@pytest.fixture(autouse=True)
def _user_store(tmp_path, monkeypatch):
    store_env(tmp_path, monkeypatch)


def reply(stdout: str) -> dict:
    return json.loads(stdout) if stdout else {}


# ---- a same-named project folder shadows a user guard ---------------------------------------


def shadow(tmp_path):
    project = tmp_path / "proj" / ".agents" / "overlays" / "guard"
    write(project / "README.md", "an empty folder of the same name\n")
    return tmp_path / "proj"


def test_a_project_folder_of_the_same_name_does_not_hide_a_user_guard(tmp_path):
    rig = Rig(tmp_path, GUARD)
    rig.allow()
    project = shadow(tmp_path)
    full = Scope.of(agents_dir=rig.store, project_root=project)
    assert [o.path for o in full.installed_overlays if o.name == "guard"] == [project / ".agents" / "overlays" / "guard"], \
        "the setup of this test: the project's folder wins in a scope that has both"
    scope = _hook_dispatch.scope_for("user", rig.store)
    stdout, _ = _hook_dispatch.dispatch("claude-code", "pre-tool", json.dumps(PRE_TOOL).encode(), scope, "user",
                                        dict(rig.environ, HOOK_OUT=json.dumps({"decision": "deny", "reason": "no"})),
                                        shadow_scope=full)
    assert reply(stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert len(rig.calls()) == 1, "the user's guard ran"


def test_the_shadowing_is_reported_at_session_start(tmp_path):
    rig = Rig(tmp_path, GUARD)
    rig.allow()
    full = Scope.of(agents_dir=rig.store, project_root=shadow(tmp_path))
    scope = _hook_dispatch.scope_for("user", rig.store)
    stdout, stderr = _hook_dispatch.dispatch(
        "claude-code", "session-start", json.dumps({"session_id": "s", "cwd": "/", "source": "startup"}).encode(),
        scope, "user", dict(rig.environ), shadow_scope=full)
    assert "hook-shadowed guard" in reply(stdout)["systemMessage"] and "hook-shadowed guard" in stderr


def test_a_user_pre_env_hook_still_fires_when_a_project_folder_hides_it(tmp_path, caplog):
    rig = Rig(tmp_path, "[hooks]\npre-env = {}\n")
    rig.allow()
    project = shadow(tmp_path)
    scope = Scope.of(agents_dir=rig.store, project_root=project)
    with caplog.at_level(logging.WARNING):
        env = _env.get_environment(scope, base_env=dict(os.environ, PATH="", HOOK_LOG=str(rig.log),
                                                         HOOK_OUT=json.dumps({"FIRST": "one"})),
                                   logger=logging.getLogger("probe"))
    assert env.get("FIRST") == "one" and len(rig.calls()) == 1
    assert "hook-shadowed guard" in caplog.text


def test_a_user_level_hook_is_looked_for_in_a_scope_with_no_project_store(tmp_path):
    rig = Rig(tmp_path, GUARD)
    scope = _hook_dispatch.scope_for("user", rig.store)
    assert scope.project_store is None
    assert all(os.path.normcase(str(s)) != os.path.normcase(str(tmp_path / "proj" / ".agents")) for s in scope.stores)


# ---- interpreters come from a PATH no project leads ----------------------------------------


def test_clean_path_drops_what_the_agent_can_put_an_interpreter_in(tmp_path):
    store = tmp_path / "store"
    good = tmp_path / "tools"
    bad = [tmp_path / "proj" / ".agents" / "bin", store / "bin", store / "overlays" / "x" / "bin",
           tmp_path / "proj" / ".agents" / ".pyvenv" / "t" / "Scripts"]
    path = os.pathsep.join(["", ".", "relative/bin", str(good)] + [str(b) for b in bad])
    assert _overlay_hooks.clean_path(path, store) == str(good)
    assert _overlay_hooks.clean_path(None, store) == ""


def test_a_hook_interpreter_is_found_on_the_clean_path_not_the_session_path(tmp_path, monkeypatch):
    rig = Rig(tmp_path, "[hooks]\nprompt = { run = \"hook.sh\" }\n", extra={"hook.sh": "exit 0\n"})
    rig.allow()
    seen = []

    def fake(name, *, python, base_path, **kw):
        seen.append((name, base_path))
        raise _run.InterpreterError("none")

    monkeypatch.setattr(_run, "interpreter_command", fake)
    leading = tmp_path / "proj" / ".agents" / "bin"
    session_path = os.pathsep.join([str(leading), str(tmp_path / "tools")])
    base = json.dumps({"PATH": session_path.replace(str(leading) + os.pathsep, "")})
    rig.fire("claude-code", "prompt", {"session_id": "s", "cwd": "/", "prompt": "x"},
             PATH=session_path, AGENTS_SESSION_BASE=base)
    assert seen and all(str(leading) not in (path or "") for _name, path in seen)
    assert seen[0][1] == str(tmp_path / "tools")


def test_an_env_script_finds_its_interpreter_on_the_clean_path_too(tmp_path, monkeypatch):
    rig = Rig(tmp_path, "[[env_scripts]]\ninterpreter = \"node\"\npath = \"env.js\"\n", extra={"env.js": "//"})
    seen = []

    def fake(name, *, python, base_path, **kw):
        seen.append(base_path)
        raise _run.InterpreterError("none")

    monkeypatch.setattr(_run, "interpreter_command", fake)
    monkeypatch.setattr("dotagents._activate.interpreters", lambda: set(_run.__dict__.get("X", ())) | {"python", "node"})
    _env.overlay_process_env(rig.scope, rig.overlay, base_env=dict(os.environ), base_path="/the/clean/path")
    assert seen == ["/the/clean/path"]


# ---- the Python of a hook's env scripts comes from the user store ---------------------------


def test_env_scripts_building_a_hooks_environment_run_under_the_users_python(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n",
              extra={"env.py": "import json, sys\nprint(json.dumps({'RAN_UNDER': sys.executable}))\n"})
    project = tmp_path / "proj" / ".agents"
    venv = _pyvenv.venv_dir(project)
    # A project venv whose "python" is something else entirely.
    write(_pyvenv.venv_python(venv), "this is not a python\n")
    write(venv / _pyvenv.STATE_FILE, "{}")
    scope = Scope.of(agents_dir=rig.store, project_root=project.parent)
    environ = {k: v for k, v in os.environ.items() if k != "AGENTS_NO_PYVENV"}
    assert _pyvenv.active_venv(scope, environ) == venv, "the setup of this test: a project venv is there"
    env = _hook_dispatch.hook_environment(scope, rig.overlay, environ)
    assert env is not None and os.path.normcase(env["RAN_UNDER"]) == os.path.normcase(sys.executable)
    assert str(project) not in env["AGENTS_PYTHON"]


def test_the_session_s_own_agents_python_is_not_trusted_for_a_hook(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n")
    environ = dict(os.environ, AGENTS_PYTHON=str(tmp_path / "proj" / ".agents" / ".pyvenv" / "x" / "python"))
    env = _hook_dispatch.hook_environment(_hook_dispatch.scope_for("user", rig.store), rig.overlay, environ)
    assert env["AGENTS_PYTHON"] == sys.executable


# ---- the project's name for itself stays ----------------------------------------------------


def test_the_session_base_keeps_the_projects_own_name_for_itself():
    environ = {"AGENTS_PROJECT_ROOT": "/the/project", "CLAUDE_PROJECT_DIR": "/the/project", "SECRET_X": "s",
               "AGENTS_SESSION_BASE": json.dumps({"AGENTS_PROJECT_ROOT": None, "CLAUDE_PROJECT_DIR": None,
                                                   "SECRET_X": None})}
    base = _overlay_hooks.session_base(environ)
    assert base["AGENTS_PROJECT_ROOT"] == "/the/project" and base["CLAUDE_PROJECT_DIR"] == "/the/project"
    assert "SECRET_X" not in base


# ---- bounds ---------------------------------------------------------------------------------


def test_no_run_or_event_setting_is_above_what_a_harness_allows(tmp_path):
    write(_overlay_hooks.settings_path(tmp_path), json.dumps({"timeout": 3600, "event_timeout": 3600}))
    assert _overlay_hooks.read_settings(tmp_path) == {}
    write(_overlay_hooks.settings_path(tmp_path), json.dumps({"timeout": 580, "event_timeout": 580}))
    assert _overlay_hooks.read_settings(tmp_path) == {"timeout": 580, "event_timeout": 580}
    assert _overlay_hooks.entry_timeout(tmp_path) <= _overlay_hooks.ENTRY_TIMEOUT_CAP


def test_pre_env_has_a_deadline_of_its_own(tmp_path, monkeypatch):
    rig = Rig(tmp_path, "[hooks]\npre-env = { on_failure = \"refuse\" }\n")
    rig.allow()
    monkeypatch.setattr(_overlay_hooks, "deadline_for", lambda store: -1000)
    with pytest.raises(SystemExit):
        _env.get_environment(rig.scope, base_env=dict(os.environ, PATH="", HOOK_LOG=str(rig.log)),
                             logger=logging.getLogger("probe"), can_refuse_session=True)
    assert rig.calls() == [], "a hook is not started past the deadline"
