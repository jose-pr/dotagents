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
from dotagents._overlay_hooks import STATUS_PREFIX

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
    assert command["command"] == '"%s" "%s" claude-code pre-tool project --guard' % (Path(sys.executable).as_posix(), script)
    assert command["timeout"] == 240
    assert (root / "hooks" / "overlay_hook.py").is_file()
    assert "--guard" not in ours(hooks["UserPromptSubmit"], "UserPromptSubmit")[0]["command"]


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
    assert entry["command"].endswith("codex prompt user")
    assert entry["commandWindows"].startswith('& "') and entry["commandWindows"].endswith("; exit $LASTEXITCODE")


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
