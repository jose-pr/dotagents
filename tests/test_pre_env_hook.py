"""``pre-env``: dotagents fires it itself as it builds the environment, on every harness, and its
output applies as an env script's does, after the root variables and before any declaration."""

from __future__ import annotations

import json
import logging

import pytest

from _hook_helpers import Rig, store_env, write
from dotagents import _env, _overlay_hooks


@pytest.fixture(autouse=True)
def _user_store(tmp_path, monkeypatch):
    store_env(tmp_path, monkeypatch)


def build(rig, base=None, can_refuse=False, **environ):
    env = dict(base or {"PATH": ""}, HOOK_LOG=str(rig.log), **environ)
    return _env.get_environment(rig.scope, base_env=env, logger=logging.getLogger("pre-env-test"),
                                can_refuse_session=can_refuse)


def test_pre_env_output_is_an_env_scripts_and_applies_before_the_overlays_declarations(tmp_path):
    rig = Rig(tmp_path, "[hooks]\npre-env = {}\n\n[env]\nSECOND = \"${FIRST}-seen\"\n")
    rig.allow()
    env = build(rig, HOOK_OUT=json.dumps({"FIRST": "one"}))
    assert env["FIRST"] == "one" and env["SECOND"] == "one-seen"
    call = rig.calls()[0]
    assert call["event"] == "pre-env" and call["argv"] == ["pre-env"]
    assert call["payload"].keys() == {"event", "harness", "cwd"}
    assert call["cwd"].startswith(str(rig.store / ".cache" / "hook-snapshots"))


def test_pre_env_runs_on_a_harness_with_no_native_hooks(tmp_path):
    rig = Rig(tmp_path, "[hooks]\npre-env = {}\n")
    rig.allow()
    env = build(rig, base={"PATH": "", "AGENTS_HARNESS": "gemini-cli"}, HOOK_OUT=json.dumps({"FIRST": "one"}))
    assert env["FIRST"] == "one"
    assert rig.calls()[0]["payload"]["harness"] == "gemini-cli"


def test_pre_env_is_off_until_allowed(tmp_path):
    rig = Rig(tmp_path, "[hooks]\npre-env = {}\n")
    env = build(rig, HOOK_OUT=json.dumps({"FIRST": "one"}))
    assert "FIRST" not in env and rig.calls() == []


def test_pre_env_does_not_see_another_overlays_declarations(tmp_path):
    rig = Rig(tmp_path, "[hooks]\npre-env = {}\n")
    rig.allow()
    write(tmp_path / "store" / "overlays" / "other" / "overlay.cfg", "[env]\nSEEN = \"yes\"\nSECRET_X = \"s\"\n")
    build(rig)
    assert rig.calls()[0]["saw"] == []


def test_a_guard_that_fails_stops_the_session_dotagents_launches(tmp_path):
    rig = Rig(tmp_path, "[hooks]\npre-env = { on_failure = \"refuse\" }\n")
    rig.allow()
    with pytest.raises(SystemExit) as stopped:
        build(rig, can_refuse=True, HOOK_EXIT="1")
    assert "guard refused the session" in str(stopped.value)


def test_a_guard_cannot_stop_a_session_that_is_already_running_and_says_so(tmp_path, caplog):
    rig = Rig(tmp_path, "[hooks]\npre-env = { on_failure = \"refuse\" }\n\n[env]\nKEPT = \"yes\"\n")
    rig.allow()
    with caplog.at_level(logging.WARNING):
        env = build(rig, HOOK_EXIT="1", HOOK_OUT=json.dumps({"FIRST": "one"}))
    assert "hook-not-applied guard pre-env refuse" in caplog.text
    assert "FIRST" not in env and env["KEPT"] == "yes"


def test_a_report_only_pre_env_that_fails_applies_nothing_and_the_rest_goes_on(tmp_path, caplog):
    rig = Rig(tmp_path, "[hooks]\npre-env = {}\n\n[env]\nKEPT = \"yes\"\n")
    rig.allow()
    with caplog.at_level(logging.WARNING):
        env = build(rig, HOOK_EXIT="3", HOOK_OUT=json.dumps({"FIRST": "one"}))
    assert "FIRST" not in env and env["KEPT"] == "yes"
    assert "hook-failed guard pre-env exit" in caplog.text


def test_pre_env_output_that_is_not_an_env_script_s_fails(tmp_path):
    rig = Rig(tmp_path, "[hooks]\npre-env = {}\n")
    rig.allow()
    assert "FIRST" not in build(rig, HOOK_OUT="not an env script")


def test_the_hooks_of_a_harness_that_cannot_fire_them_are_reported(tmp_path, caplog):
    rig = Rig(tmp_path, "[hooks]\ncd = {}\nprompt = {}\n")
    rig.allow()
    with caplog.at_level(logging.WARNING):
        build(rig, base={"PATH": "", "AGENTS_HARNESS": "codex"})
    assert "hook-unsupported guard cd" in caplog.text
    assert "hook-unsupported guard prompt" not in caplog.text
    assert "hook-partial" not in caplog.text


def test_tool_hooks_that_fire_for_some_calls_only_are_reported_partial(tmp_path, caplog):
    rig = Rig(tmp_path, "[hooks]\npost-tool = {}\n")
    rig.allow()
    with caplog.at_level(logging.WARNING):
        build(rig, base={"PATH": "", "AGENTS_HARNESS": "claude-code"})
    assert "hook-partial guard post-tool" in caplog.text


def test_a_hook_that_is_on_is_a_weakening_each_time(tmp_path):
    rig = Rig(tmp_path, "[hooks]\ncd = {}\n")
    rig.allow()
    from dotagents import _aos

    plan = _overlay_hooks.overlay_plan(rig.overlay, _overlay_hooks.CLAUDE, rig.store)
    assert ["hook", "cd"] in plan["non_defaults"] and plan["on"]
    assert _aos.hook_plan  # the model decides; the plan above is its answer on this machine


def test_a_cached_environment_is_not_replayed_for_a_pre_env_hook(tmp_path, monkeypatch, capsys):
    from _helpers import run_cmd
    from dotagents.cli import Env

    rig = Rig(tmp_path, "[hooks]\npre-env = {}\n")
    rig.allow()
    monkeypatch.setenv("HOOK_LOG", str(rig.log))
    monkeypatch.setenv("HOOK_OUT", json.dumps({"FIRST": "one"}))
    for _ in range(2):
        assert run_cmd(Env, global_scope=True, agents_dir=rig.store, cache=True, format="dotenv") == 0
    assert len(rig.calls()) == 2
    assert "FIRST=one" in capsys.readouterr().out
