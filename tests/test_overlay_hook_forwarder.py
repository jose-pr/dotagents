"""The forwarder a harness's hook entry runs (`overlay_hook.py`) end to end: the harness's payload on
standard input, a real `dotagents hook` child, the harness's answer on standard output."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from _hook_helpers import Rig, store_env, write
from conftest import SRC
from dotagents import _resources

FORWARDER = Path(_resources.BASE_ROOT) / "dotagents" / "hooks" / "overlay_hook.py"


@pytest.fixture(autouse=True)
def _user_store(tmp_path, monkeypatch):
    store_env(tmp_path, monkeypatch)


def install_shim(store: Path) -> None:
    """A `dotagents` in the store's bin that runs this tree."""
    if os.name == "nt":
        write(store / "bin" / "dotagents.cmd", '@"%s" -m dotagents %%*\r\n' % sys.executable)
    else:
        shim = write(store / "bin" / "dotagents", '#!/bin/sh\nexec "%s" -m dotagents "$@"\n' % sys.executable)
        shim.chmod(0o755)


def run(rig: Rig, harness: str, event: str, native: dict, *flags: str, **environ):
    env = dict(rig.environ, AGENTS_HOME=str(rig.store), PYTHONPATH=str(SRC), **environ)
    return subprocess.run([sys.executable, str(FORWARDER), harness, event, "user"] + list(flags),
                          input=json.dumps(native).encode(), capture_output=True, env=env, cwd=str(rig.store.parent),
                          timeout=120)


def test_a_prompt_goes_through_the_forwarder_and_back(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n")
    rig.allow()
    install_shim(rig.store)
    done = run(rig, "claude-code", "prompt", {"session_id": "s", "cwd": str(tmp_path), "prompt": "write it"},
               HOOK_OUT=json.dumps({"context": "mind the style"}))
    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout)["hookSpecificOutput"]
    assert answer["hookEventName"] == "UserPromptSubmit" and "mind the style" in answer["additionalContext"]
    assert rig.calls()[0]["payload"]["prompt"] == "write it"


def test_a_guard_denies_through_the_forwarder_and_a_bad_hook_never_approves(tmp_path):
    rig = Rig(tmp_path, "[hooks]\npre-tool = { on_failure = \"refuse\" }\n")
    rig.allow()
    install_shim(rig.store)
    native = {"session_id": "s", "cwd": str(tmp_path), "tool_name": "Bash", "tool_input": {"command": "ls"}}
    done = run(rig, "claude-code", "pre-tool", native, "--guard", HOOK_OUT=json.dumps({"decision": "allow"}))
    assert done.returncode == 0
    assert json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_the_forwarder_that_cannot_find_dotagents_fails_closed_for_a_guard_only(tmp_path):
    rig = Rig(tmp_path, "[hooks]\npre-tool = { on_failure = \"refuse\" }\n")
    native = {"session_id": "s", "cwd": str(tmp_path), "tool_name": "Bash", "tool_input": {"command": "ls"}}
    env = dict(os.environ, AGENTS_HOME=str(tmp_path / "nowhere"), PATH=str(tmp_path / "empty"))
    guarded = subprocess.run([sys.executable, str(FORWARDER), "codex", "pre-tool", "user", "--guard"],
                             input=json.dumps(native).encode(), capture_output=True, env=env, cwd=str(tmp_path))
    assert guarded.returncode == 0
    assert json.loads(guarded.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    plain = subprocess.run([sys.executable, str(FORWARDER), "codex", "pre-tool", "user"],
                           input=json.dumps(native).encode(), capture_output=True, env=env, cwd=str(tmp_path))
    assert plain.returncode == 0 and plain.stdout == b""
    compact = subprocess.run([sys.executable, str(FORWARDER), "codex", "pre-compact", "user", "--guard"],
                             input=b"{}", capture_output=True, env=env, cwd=str(tmp_path))
    assert json.loads(compact.stdout)["continue"] is False
