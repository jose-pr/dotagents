"""The PowerShell-tool env loader is opt-in (`init --powershell-env-hook`).

It rewrites each PowerShell tool call through PreToolUse `updatedInput`, which
Claude Code documents only together with `permissionDecision: "allow"` (skip
the prompt) or `"ask"` (prompt every call). Wired by default it silently
auto-approved every PowerShell call after `init -g` on Windows.
"""

import json
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


def test_the_opted_in_loader_auto_approves_and_says_so():
    # Pinned so the auto-approve cannot change silently: the flag's help and
    # the docs state it.
    assert 'permissionDecision="allow"' in ClaudeAgent.PRETOOLUSE_POWERSHELL_COMMAND


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
