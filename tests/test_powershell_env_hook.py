"""The PowerShell-tool env loader is opt-in (`init --powershell-env-hook`).

It rewrites each PowerShell tool call through PreToolUse `updatedInput` and states no
permission decision: Claude Code evaluates its rules against the rewritten command and
asks, denies or runs as it would without a hook. `"allow"` would skip the prompt.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


from dotagents._agents import ClaudeAgent


@pytest.fixture(autouse=True)
def _windows(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(ClaudeAgent, "_is_windows", staticmethod(lambda: True))


def _pretooluse(root):
    path = Path(root) / "settings.local.json"
    return json.loads(path.read_text(encoding="utf-8"))["hooks"].get("PreToolUse")


def test_not_wired_by_default_and_an_old_one_is_removed(tmp_path):
    dest, root = tmp_path / "agents", tmp_path / "claude"
    dest.mkdir()
    agent = ClaudeAgent()
    agent.powershell_env_hook = True
    agent.wire_hooks(dest, dry_run=False, logger=None, config_root=root)
    assert _pretooluse(root), "opted in: wired"

    ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
    assert not _pretooluse(root), "default: an earlier release's loader is removed"


@pytest.mark.parametrize("opted_in", [False, True])
def test_the_wired_line_names_pretooluse_only_when_it_was_wired(tmp_path, caplog, opted_in):
    import logging

    dest, root = tmp_path / "agents", tmp_path / "claude"
    dest.mkdir()
    agent = ClaudeAgent()
    agent.powershell_env_hook = opted_in
    with caplog.at_level(logging.INFO, logger="t"):
        agent.wire_hooks(dest, dry_run=False, logger=logging.getLogger("t"), config_root=root)
    wired = [r.getMessage() for r in caplog.records if r.getMessage().startswith("wired ")]
    assert len(wired) == 1, wired
    assert ("PreToolUse" in wired[0]) is opted_in, wired[0]


def test_the_loader_states_no_permission_decision():
    assert "permissionDecision" not in ClaudeAgent.PRETOOLUSE_POWERSHELL_COMMAND


POWERSHELL = shutil.which("powershell")


@pytest.mark.skipif(POWERSHELL is None, reason="Windows PowerShell only")
def test_the_hook_rewrites_the_call_and_leaves_the_decision_to_the_permission_system(tmp_path):
    call = {"tool_name": "PowerShell", "tool_input": {"command": "Remove-Item x.txt", "description": "d", "timeout": 5}}
    env = {name: value for name, value in os.environ.items() if name.upper() != "AGENTS_RUNTIME_SET"}
    hook = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", ClaudeAgent.PRETOOLUSE_POWERSHELL_COMMAND],
        input=json.dumps(call).encode(), env=env, capture_output=True, cwd=tmp_path,
    )
    assert hook.returncode == 0, hook.stderr
    output = json.loads(hook.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in output, "a decision here would answer for the permission system"
    updated = output["updatedInput"]
    assert updated["command"].startswith("if (-not $env:AGENTS_RUNTIME_SET)")
    assert updated["command"].endswith("; Remove-Item x.txt")
    assert (updated["description"], updated["timeout"]) == ("d", 5), "the whole input is replaced, so the rest must survive"


def test_init_flag_reaches_the_adapter(tmp_path, monkeypatch):
    from dotagents.cli._common import BASE_ROOT, _apply_base

    seen = []
    # `init` also runs a dry-run pass first (it validates the settings files
    # before writing anything); only the real pass is counted.
    monkeypatch.setattr(
        ClaudeAgent, "wire_hooks",
        lambda self, dest, **kw: kw["dry_run"] or seen.append(self.powershell_env_hook),
    )
    monkeypatch.setattr(ClaudeAgent, "write_base_config", lambda self, *a, **kw: None)
    import logging

    for flag in (False, True):
        _apply_base(BASE_ROOT, tmp_path / "s", False, False, logging.getLogger("t"),
                    agents=["claude"], wire_hooks=True, powershell_env_hook=flag)
    assert seen == [False, True]
