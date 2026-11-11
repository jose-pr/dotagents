"""Wiring a harness's own hooks to `dotagents hook` for the overlay hooks installed in a store:
one entry per event an overlay has a hook for, none for an event nobody uses, nothing for a store
whose overlays have no hooks, and an entry that survives a second `init` once."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

from _hook_helpers import Rig, write
from dotagents._agents import ClaudeAgent, CodexAgent
from dotagents import _overlay_hooks
from dotagents._overlay_hooks import STATUS_PREFIX
from dotagents._scope import resolve_user_store

LOG = logging.getLogger("wiring-test")


def settings(root: Path, name: str = "settings.local.json") -> dict:
    return json.loads((root / name).read_text(encoding="utf-8"))


def ours(entries, native):
    return [h for e in entries for h in e["hooks"] if h.get("statusMessage") == "dotagents: " + STATUS_PREFIX + native]


def test_claude_gets_an_entry_for_each_event_an_overlay_hooks(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\npre-tool = { on_failure = \"refuse\" }\nturn-end = false\n")
    root = tmp_path / "claude"
    ClaudeAgent().wire_overlay_hooks(rig.store, dry_run=False, logger=LOG, config_root=root)
    hooks = settings(root)["hooks"]
    # hook.py also gets the default events; turn-end is declared off.
    assert set(hooks) == {"SessionStart", "SessionEnd", "UserPromptSubmit", "CwdChanged", "PreCompact", "PreToolUse"}
    command = ours(hooks["PreToolUse"], "PreToolUse")[0]
    script = (root / "hooks" / "overlay_hook.py").resolve().as_posix()
    token = _overlay_hooks.level_token(rig.store, resolve_user_store())
    py = Path(sys.executable).as_posix()
    # Single quotes, so nothing in a path is expanded; the text holds no guard flag.
    assert command["command"] == "'%s' -I '%s' claude-code pre-tool %s" % (py, script, token)
    assert command["timeout"] == _overlay_hooks.entry_timeout(resolve_user_store())
    assert (root / "hooks" / "overlay_hook.py").is_file()
    record = json.loads((root / "hooks" / ("overlay_hook.%s.json" % token)).read_text(encoding="utf-8"))
    assert record["guards"] == ["pre-tool"] and record["python"] == str(Path(sys.executable).absolute())
    assert record["store"] == str(resolve_user_store()) and record["deadline"] == 150
    assert Path(record["code_root"]).exists()


def test_the_entry_text_does_not_change_when_a_guard_comes_or_goes(tmp_path):
    rig = Rig(tmp_path, "[hooks]\npre-tool = { on_failure = \"refuse\" }\n")
    root = tmp_path / "claude"
    agent = ClaudeAgent()
    agent.wire_overlay_hooks(rig.store, dry_run=False, logger=LOG, config_root=root)
    before = (root / "settings.local.json").read_bytes()
    write(rig.root / "overlay.cfg", "[hooks]\npre-tool = {}\n")
    agent.wire_overlay_hooks(rig.store, dry_run=False, logger=LOG, config_root=root)
    assert (root / "settings.local.json").read_bytes() == before
    token = _overlay_hooks.level_token(rig.store, resolve_user_store())
    record = json.loads((root / "hooks" / ("overlay_hook.%s.json" % token)).read_text(encoding="utf-8"))
    assert record["guards"] == []


def test_a_projects_entries_run_the_forwarder_of_the_users_config_only(tmp_path, monkeypatch):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n")
    user_config, project = tmp_path / "user-claude", tmp_path / "proj"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user_config))
    agent = ClaudeAgent()
    agent.scope_level, agent.project_root = "project", project
    agent.wire_overlay_hooks(rig.store, dry_run=False, logger=LOG)
    entry = ours(settings(project / ".claude")["hooks"]["UserPromptSubmit"], "UserPromptSubmit")[0]
    assert (user_config / "hooks" / "overlay_hook.py").is_file()
    assert not (project / ".claude" / "hooks").exists(), "nothing the entry runs is under the project"
    assert (user_config / "hooks" / "overlay_hook.py").resolve().as_posix() in entry["command"]


def test_the_interpreter_going_away_is_reported(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n")
    root = tmp_path / "claude"
    ClaudeAgent().wire_overlay_hooks(rig.store, dry_run=False, logger=LOG, config_root=root)
    token = _overlay_hooks.level_token(rig.store, resolve_user_store())
    assert _overlay_hooks.interpreter_problem(root, token) is None
    path = _overlay_hooks.record_path(root, token)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["python"] = str(tmp_path / "gone" / "python")
    path.write_text(json.dumps(record), encoding="utf-8")
    assert "is gone" in _overlay_hooks.interpreter_problem(root, token)


def test_a_path_with_a_quote_in_it_is_one_word_in_each_shell():
    assert _overlay_hooks.sh_quote("/a b/it's") == "'/a b/it'\\''s'"
    assert _overlay_hooks.ps_quote("C:\\a b\\it's") == "'C:\\a b\\it''s'"


def test_claude_wires_nothing_for_a_store_without_hooks_and_retracts_what_is_gone(tmp_path):
    rig = Rig(tmp_path, "[env]\nA = \"b\"\n")
    (rig.root / "hook.py").unlink()
    root = tmp_path / "claude"
    ClaudeAgent().wire_overlay_hooks(rig.store, dry_run=False, logger=LOG, config_root=root)
    assert not (root / "settings.local.json").exists() and not (root / "hooks").exists()
    write(rig.root / "overlay.cfg", "[hooks]\nprompt = { run = \"p.py\" }\n")
    write(rig.root / "p.py", "")
    foreign = {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}
    write(root / "settings.local.json", json.dumps(foreign))
    ClaudeAgent().wire_overlay_hooks(rig.store, dry_run=False, logger=LOG, config_root=root)
    after = settings(root)["hooks"]
    assert ours(after["UserPromptSubmit"], "UserPromptSubmit") and "echo mine" in json.dumps(after)
    assert "PreToolUse" not in after
    write(rig.root / "overlay.cfg", "")
    ClaudeAgent().wire_overlay_hooks(rig.store, dry_run=False, logger=LOG, config_root=root)
    final = settings(root)["hooks"]
    assert final == foreign["hooks"]


def test_wiring_twice_changes_nothing(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n")
    root = tmp_path / "claude"
    agent = ClaudeAgent()
    agent.wire_overlay_hooks(rig.store, dry_run=False, logger=LOG, config_root=root)
    first = (root / "settings.local.json").read_bytes()
    agent.wire_overlay_hooks(rig.store, dry_run=False, logger=LOG, config_root=root)
    assert (root / "settings.local.json").read_bytes() == first


def test_a_dry_run_writes_nothing(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n")
    root = tmp_path / "claude"
    ClaudeAgent().wire_overlay_hooks(rig.store, dry_run=True, logger=LOG, config_root=root)
    assert not root.exists()


def test_codex_gets_only_the_events_it_has(tmp_path):
    rig = Rig(tmp_path, "[hooks]\ncd = {}\nprompt = {}\n")
    agent = CodexAgent()
    agent.scope_level = "user"
    root = tmp_path / "codex"
    agent.wire_overlay_hooks(rig.store, dry_run=False, logger=LOG, config_root=root)
    hooks = json.loads((root / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert "CwdChanged" not in hooks
    entry = ours(hooks["UserPromptSubmit"], "UserPromptSubmit")[0]
    assert entry["command"].endswith(" codex prompt user") and entry["command"].startswith("'")
    assert " -I '" in entry["command"] and " -I '" in entry["commandWindows"]
    assert entry["commandWindows"].startswith("& '") and entry["commandWindows"].endswith("; exit $LASTEXITCODE")
    assert "--guard" not in entry["command"] + entry["commandWindows"]


def test_codex_warns_loudly_when_its_entry_has_to_change(tmp_path, monkeypatch, caplog):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n")
    agent = CodexAgent()
    agent.scope_level = "user"
    root = tmp_path / "codex"
    agent.wire_overlay_hooks(rig.store, dry_run=False, logger=LOG, config_root=root)
    with caplog.at_level(logging.WARNING):
        agent.wire_overlay_hooks(rig.store, dry_run=False, logger=LOG, config_root=root)
        assert "trust" not in caplog.text, "an unchanged entry says nothing"
        monkeypatch.setattr(sys, "executable", str(tmp_path / "other-python"))
        agent.wire_overlay_hooks(rig.store, dry_run=False, logger=LOG, config_root=root)
    assert "Codex will ask you to trust it again" in caplog.text


def test_codex_is_wired_for_the_user_store_only(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n")
    agent = CodexAgent()
    agent.scope_level = "project"
    root = tmp_path / "codex"
    agent.wire_overlay_hooks(rig.store, dry_run=False, logger=LOG, config_root=root)
    assert not root.exists()


def test_init_wires_claudes_overlay_hooks(tmp_path, monkeypatch):
    from _helpers import run_cmd
    from dotagents.cli import Init

    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n")
    config = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("AGENTS_HOME", str(rig.store))
    run_cmd(Init, global_scope=True, agents=["claude"], no_venv=True)
    assert ours(settings(config, "settings.json")["hooks"]["UserPromptSubmit"], "UserPromptSubmit")
