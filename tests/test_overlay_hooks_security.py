"""What an overlay's hooks cannot do: see another overlay's secrets or the harness's own variables,
run bytes that were not hashed, write the env file past its own block, escape the deadline, or
fail open as a guard."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time

import pytest

from _hook_helpers import HOOK_PY, Rig, store_env, write
from dotagents import _hook_dispatch, _overlay_hooks, _pyvenv
from dotagents._scope import Scope

PRE_TOOL = {"session_id": "s", "cwd": "/", "tool_name": "Bash", "tool_input": {"command": "ls"}}
GUARD = "[hooks]\npre-tool = { on_failure = \"refuse\" }\nprompt = {}\n"


@pytest.fixture(autouse=True)
def _user_store(tmp_path, monkeypatch):
    store_env(tmp_path, monkeypatch)


def reply(stdout: str) -> dict:
    return json.loads(stdout) if stdout else {}


def decision(stdout: str):
    return reply(stdout).get("hookSpecificOutput", {}).get("permissionDecision")


# ---- H1: the session's secrets and the harness's own variables ------------------------------


def test_the_sessions_environment_reaches_a_hook_secrets_included(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n")
    rig.allow()
    rig.fire("claude-code", "prompt", {"session_id": "s", "cwd": "/", "prompt": "x"},
             SECRET_TOKEN="hunter2", SECRET_KEPT="overlay value")
    saw = rig.calls()[0]["saw"]
    assert "SECRET_TOKEN" in saw and "SECRET_KEPT" in saw


def test_the_harness_own_variables_do_not_reach_a_hook(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n")
    rig.allow()
    rig.fire("claude-code", "prompt", {"session_id": "s", "cwd": "/", "prompt": "x"},
             CLAUDE_ENV_FILE="/x/env", CLAUDE_CODE_OAUTH_TOKEN="t", CODEX_API_KEY="k")
    assert not {"CLAUDE_ENV_FILE", "CLAUDE_CODE_OAUTH_TOKEN", "CODEX_API_KEY"} & set(rig.calls()[0]["saw"])


def test_pre_env_hooks_see_the_base_and_the_roots(tmp_path):
    import logging

    from dotagents import _env

    rig = Rig(tmp_path, "[hooks]\npre-env = {}\n")
    rig.allow()
    env = dict(os.environ, PATH="", HOOK_LOG=str(rig.log), SECRET_TOKEN="hunter2")
    _env.get_environment(rig.scope, base_env=env, logger=logging.getLogger("t"))
    assert "SECRET_TOKEN" in rig.calls()[0]["saw"]


# ---- H3: a guard that is off is a failed guard ----------------------------------------------


def test_a_guard_whose_digest_changed_denies_and_is_reported_at_session_start(tmp_path):
    rig = Rig(tmp_path, GUARD)
    rig.allow()
    write(rig.root / "hook.py", "# edited after the allowance\n")
    stdout, _ = rig.fire("claude-code", "pre-tool", PRE_TOOL)
    assert decision(stdout) == "deny"
    assert rig.calls() == []
    start, _ = rig.fire("claude-code", "session-start", {"session_id": "s", "cwd": "/", "source": "startup"})
    assert "hook-not-allowed guard" in reply(start)["systemMessage"]
    # An event it has no guard for is not refused.
    assert rig.fire("claude-code", "post-tool", PRE_TOOL)[0] == ""


def test_a_guard_whose_tree_has_no_digest_denies(tmp_path):
    rig = Rig(tmp_path, GUARD)
    rig.allow()
    outside = tmp_path / "outside"
    write(outside, "x")
    try:
        os.symlink(str(outside), str(rig.root / "planted-link"))
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")
    assert decision(rig.fire("claude-code", "pre-tool", PRE_TOOL)[0]) == "deny"


def test_a_guard_never_allowed_denies(tmp_path):
    rig = Rig(tmp_path, GUARD)
    assert decision(rig.fire("claude-code", "pre-tool", PRE_TOOL)[0]) == "deny"
    assert reply(rig.fire("claude-code", "prompt", {"session_id": "s", "cwd": "/", "prompt": "x"})[0]) == {}


def test_a_guard_on_a_prompt_blocks_while_off(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nprompt = { on_failure = \"refuse\" }\n")
    answer = reply(rig.fire("claude-code", "prompt", {"session_id": "s", "cwd": "/", "prompt": "x"})[0])
    assert answer["decision"] == "block"


# ---- H4: what runs is what was hashed --------------------------------------------------------


def test_a_file_swapped_after_it_was_hashed_does_not_run(tmp_path, monkeypatch):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n")
    rig.allow()
    marker = tmp_path / "EVIL-RAN"
    real = _overlay_hooks.read_tree

    def swapping(overlay, keep=False):
        got = real(overlay, keep)
        write(overlay.path / "hook.py", "open(%r, 'w').write('x')\n" % str(marker))
        return got

    monkeypatch.setattr(_overlay_hooks, "read_tree", swapping)
    rig.fire("claude-code", "prompt", {"session_id": "s", "cwd": "/", "prompt": "x"})
    assert not marker.exists(), "the swapped file ran"
    assert len(rig.calls()) == 1, "the hashed bytes ran"


def test_the_snapshot_holds_exactly_the_hashed_bytes_and_no_unhashed_directory(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n", extra={"node_modules/planted/index.js": "evil",
                                                          "__pycache__/x.pyc": "evil", ".venv/lib/evil.pth": "x"})
    rig.allow()
    rig.fire("claude-code", "prompt", {"session_id": "s", "cwd": "/", "prompt": "x"})
    root = rig.calls()[0]["root"]
    names = sorted(os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/")
                   for d, _, files in os.walk(root) for f in files)
    assert names == ["hook.py", "overlay.cfg"]
    assert _overlay_hooks.snapshot(rig.store, rig.overlay, rig.digest(), {}) == type(rig.store)(root)


def test_a_planted_bytecode_beside_a_hashed_module_does_not_run(tmp_path):
    import py_compile

    module = "VALUE = 'real'\n"
    hook = ("import helper, json, os, sys\n"
            "open(os.environ['HOOK_LOG'], 'a').write(json.dumps({'value': helper.VALUE}) + '\\n')\n")
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n", extra={"helper.py": module}, script=hook)
    rig.allow()
    evil = tmp_path / "evil.py"
    write(evil, "VALUE = 'planted'\n")
    cache = rig.root / "__pycache__"
    cache.mkdir()
    py_compile.compile(str(evil), cfile=str(cache / ("helper.cpython-%d%d.pyc" % sys.version_info[:2])),
                       doraise=True, invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    rig.fire("claude-code", "prompt", {"session_id": "s", "cwd": "/", "prompt": "x"},
             PYTHONPYCACHEPREFIX="")
    assert rig.calls()[0]["value"] == "real"


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_a_planted_node_modules_is_not_on_a_hooks_module_path(tmp_path):
    hook = ("let found = 'none';\ntry { found = require('planted').name } catch (e) {}\n"
            "require('fs').appendFileSync(process.env.HOOK_LOG, JSON.stringify({found}) + '\\n')\n")
    rig = Rig(tmp_path, "[hooks]\nprompt = { run = \"hook.js\" }\n", extra={"hook.js": hook,
              "node_modules/planted/index.js": "exports.name = 'planted'"})
    rig.allow()
    rig.fire("claude-code", "prompt", {"session_id": "s", "cwd": "/", "prompt": "x"})
    assert rig.calls()[0]["found"] == "none"


def test_a_hook_never_runs_with_a_projects_venv(tmp_path, monkeypatch):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n")
    project = tmp_path / "proj" / ".agents"
    venv = _pyvenv.venv_dir(project)
    write(_pyvenv.venv_python(venv), "not python")
    write(venv / _pyvenv.STATE_FILE, "{}")
    scope = Scope("project", project, user_root=rig.store, project_root=project.parent)
    overlay = next(o for o in scope.installed_overlays if o.name == "guard")
    environ = {k: v for k, v in os.environ.items() if k != "AGENTS_NO_PYVENV"}
    assert _pyvenv.active_venv(scope, environ) == venv, "the setup of this test: a project venv is there"
    env = _hook_dispatch.hook_environment(scope, overlay, environ)
    assert env["AGENTS_PYTHON"] == sys.executable
    assert str(project) not in env["AGENTS_PYTHON"]


# ---- M2: the env file ------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["a\r: dotagents-hook-env-end cd\rtouch PWNED #", "a\n: x\ntouch PWNED",
                                   "a\x0bb", "a\x0cb", "a\x85b", "a\u2028b", "a\u2029b", "a\x00b"])
def test_a_value_with_a_line_separator_is_not_written_to_the_env_file(tmp_path, value):
    rig = Rig(tmp_path, "[hooks]\ncd = { sets = [\"WHERE\"] }\n")
    rig.allow()
    env_file = tmp_path / "claude.env"
    env_file.write_text("export KEEP='1'\n", encoding="utf-8")
    stdout, _ = rig.fire("claude-code", "cd", {"session_id": "s", "cwd": "/", "old_cwd": "/", "new_cwd": "/x"},
                         CLAUDE_ENV_FILE=str(env_file), HOOK_OUT=json.dumps({"env": {"WHERE": value}})
                         if "\x00" not in value else json.dumps({"env": {"WHERE": "a"}}))
    text = env_file.read_text(encoding="utf-8")
    assert "PWNED" not in text
    if "\x00" not in value:
        assert text == "export KEEP='1'\n"
        assert "line separator" in reply(stdout).get("systemMessage", "") or stdout == ""


def test_the_env_block_is_replaced_not_grown_and_other_lines_survive(tmp_path):
    path = tmp_path / "env"
    path.write_text("export OTHER='x'\n", encoding="utf-8")
    for value in ("one", "two"):
        _hook_dispatch.write_env_block(path, "cd", {"WHERE": value})
    text = path.read_text(encoding="utf-8")
    assert text.count("export WHERE=") == 1 and "'two'" in text and "export OTHER='x'" in text
    with pytest.raises(ValueError):
        _hook_dispatch.write_env_block(path, "cd", {"WHERE": "a\rb"})


def test_a_sensitive_variable_from_session_start_is_reported(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nsession-start = { sets = [\"PATH\", \"BASH_ENV\", \"LD_PRELOAD\", \"OK\"] }\n")
    rig.allow()
    env_file = tmp_path / "claude.env"
    stdout, _ = rig.fire("claude-code", "session-start", {"session_id": "s", "cwd": "/", "source": "startup"},
                         CLAUDE_ENV_FILE=str(env_file),
                         HOOK_OUT=json.dumps({"env": {"PATH": "/evil", "BASH_ENV": "/evil", "LD_PRELOAD": "/e", "OK": "1"}}))
    message = reply(stdout)["systemMessage"]
    for name in ("PATH", "BASH_ENV", "LD_PRELOAD"):
        assert "env-sensitive %s" % name in message
    assert "env-sensitive OK" not in message


# ---- M5: the payload -------------------------------------------------------------------------


def test_an_oversize_or_unreadable_payload_is_a_failure_and_a_guard_refuses(tmp_path):
    rig = Rig(tmp_path, GUARD)
    rig.allow()
    env = dict(rig.environ)
    for payload in (b"{" + b" " * (_hook_dispatch.MAX_NATIVE + 1), b"not json", b"[1]", b"\xff"):
        stdout, stderr = _hook_dispatch.dispatch("claude-code", "pre-tool", payload, rig.scope, "user", env)
        assert decision(stdout) == "deny", payload[:10]
        assert "hook-failed pre-tool payload" in stderr
    assert rig.calls() == []


def test_an_unreadable_payload_without_a_guard_runs_nothing_and_says_so(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n")
    rig.allow()
    stdout, stderr = _hook_dispatch.dispatch("claude-code", "prompt", b"not json", rig.scope, "user", dict(rig.environ))
    assert stdout == "" and "hook-failed prompt payload" in stderr and rig.calls() == []


# ---- M3 / Q5: the deadline and the settings ---------------------------------------------------


def test_everything_from_the_start_is_inside_one_deadline(tmp_path):
    rig = Rig(tmp_path, GUARD)
    rig.allow()
    long_ago = time.monotonic() - 10000
    stdout, stderr = _hook_dispatch.dispatch("claude-code", "pre-tool", json.dumps(PRE_TOOL).encode(), rig.scope,
                                             "user", dict(rig.environ), started=long_ago)
    assert decision(stdout) == "deny" and "hook-failed guard pre-tool timeout" in reply(stdout)["systemMessage"]
    assert rig.calls() == [], "a hook is not started past the deadline"


def test_settings_come_from_the_users_store_only_and_are_bounded(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nprompt = { timeout = 30 }\n")
    rig.allow()
    write(_overlay_hooks.settings_path(rig.store), json.dumps({"timeout": 1, "deadline": 60, "max_output": 1024,
                                                              "event_timeout": 99999, "bogus": 1, "max_payload": 5}))
    assert _overlay_hooks.read_settings(rig.store) == {"timeout": 1, "deadline": 60, "max_output": 1024}
    assert _overlay_hooks.limits_for(rig.store)["timeout"] == 1
    assert _overlay_hooks.entry_timeout(rig.store) == 80
    forged = tmp_path / "proj" / ".agents"
    write(_overlay_hooks.settings_path(forged), json.dumps({"timeout": 3600}))
    assert _overlay_hooks.read_settings(rig.store)["timeout"] == 1
    stdout, _ = rig.fire("claude-code", "prompt", {"session_id": "s", "cwd": "/", "prompt": "x"},
                         HOOK_OUT=json.dumps({"context": "x" * 2000}))
    assert "hook-failed guard prompt too-large" in reply(stdout)["systemMessage"]


def test_the_entry_timeout_is_capped_below_the_harness_limit(tmp_path):
    write(_overlay_hooks.settings_path(tmp_path), json.dumps({"deadline": 580}))
    assert _overlay_hooks.entry_timeout(tmp_path) <= 600
