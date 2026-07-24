"""`ClaudeAgent.wire_hooks`: the skills link + settings.json hook merge.

Every test redirects `config_root` at a tmp_path -- nothing here may touch the
real `~/.claude`. `test_never_touches_real_home` asserts that explicitly.

Run: ``PYTHONPATH=src python -m pytest tests/test_wire_hooks.py``
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotagents._agents import ClaudeAgent  # noqa: E402


def _scope_with_skills(tmp_path):
    """A `<scope>/.agents` dir carrying one publishable skill."""
    dest = tmp_path / "agents"
    skill = dest / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo-skill\n---\nbody\n", encoding="utf-8")
    return dest


def _hooks_of(settings_path):
    return json.loads(settings_path.read_text(encoding="utf-8"))["hooks"]


def _commands(hook_list):
    return [h["command"] for entry in hook_list for h in entry["hooks"]]


def test_wires_both_hooks(tmp_path):
    dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
    ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

    hooks = _hooks_of(root / "settings.json")
    assert ClaudeAgent.SESSION_START_COMMAND in _commands(hooks["SessionStart"])
    assert ClaudeAgent.CWD_CHANGED_COMMAND in _commands(hooks["CwdChanged"])


def test_no_env_hook_is_written(tmp_path):
    """CLAUDE_ENV_FILE does not exist; a hook using it would silently do nothing."""
    dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
    ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
    assert "CLAUDE_ENV_FILE" not in (root / "settings.json").read_text(encoding="utf-8")


def test_second_run_is_a_noop(tmp_path):
    """`init` is re-run often -- it must not accumulate hooks or rewrite the file."""
    dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
    agent = ClaudeAgent()
    agent.wire_hooks(dest, dry_run=False, logger=None, config_root=root)
    first = (root / "settings.json").read_text(encoding="utf-8")

    agent.wire_hooks(dest, dry_run=False, logger=None, config_root=root)
    assert (root / "settings.json").read_text(encoding="utf-8") == first

    hooks = _hooks_of(root / "settings.json")
    assert len(hooks["SessionStart"]) == 1
    assert len(hooks["CwdChanged"]) == 1


def test_preserves_unrelated_keys_and_foreign_hooks(tmp_path):
    dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
    root.mkdir()
    foreign = {"hooks": [{"type": "command", "command": "echo mine"}]}
    (root / "settings.json").write_text(
        json.dumps({"model": "opus", "hooks": {"SessionStart": [foreign]}}), encoding="utf-8"
    )

    ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

    data = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    assert data["model"] == "opus", "unrelated settings must survive"
    cmds = _commands(data["hooks"]["SessionStart"])
    assert "echo mine" in cmds, "a hook we did not write must survive"
    assert ClaudeAgent.SESSION_START_COMMAND in cmds


def test_migrates_legacy_lowercase_session_start(tmp_path):
    dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
    root.mkdir()
    (root / "settings.json").write_text(
        json.dumps({"hooks": {"session_start": []}}), encoding="utf-8"
    )

    ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

    hooks = _hooks_of(root / "settings.json")
    assert "session_start" not in hooks, "legacy key should be dropped"
    assert ClaudeAgent.SESSION_START_COMMAND in _commands(hooks["SessionStart"])


def test_dry_run_writes_nothing(tmp_path):
    dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
    ClaudeAgent().wire_hooks(dest, dry_run=True, logger=None, config_root=root)
    assert not (root / "settings.json").exists()
    assert not (root / "skills").exists()


def test_skills_are_linked_or_copied(tmp_path):
    """symlink where the OS allows, copy otherwise -- either way, reachable."""
    dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
    ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
    assert (root / "skills" / "demo-skill" / "SKILL.md").is_file()


def test_absent_skills_dir_is_tolerated(tmp_path):
    """True of every overlay today: none ships skills yet."""
    dest, root = tmp_path / "agents", tmp_path / "claude"
    dest.mkdir()
    ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
    assert not (root / "skills").exists()
    assert (root / "settings.json").is_file(), "hooks still wired without skills"


def test_never_touches_real_home(tmp_path):
    """Guard: a stray default must never let a test write to the user's ~/.claude."""
    real = Path.home() / ".claude" / "settings.json"
    before = real.read_text(encoding="utf-8") if real.is_file() else None

    dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
    ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

    after = real.read_text(encoding="utf-8") if real.is_file() else None
    assert after == before, "the real ~/.claude/settings.json was modified"
