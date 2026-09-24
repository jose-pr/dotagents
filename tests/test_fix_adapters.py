"""Adapter fixes from the 2026-09-23 review: adapters are told the scope,
Claude honours CLAUDE_CONFIG_DIR, `init` writes the store's AGENTS.md itself,
global harness configs are left alone by a project init, Cursor's rule file,
Claude's import parsing, identity from runtime signals only, include dedupe.

tmp dirs only; the user's home and every harness config dir are redirected.
"""

import logging
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotagents import _agents, _merge  # noqa: E402
from dotagents.cli._common import BASE_ROOT, _apply_base  # noqa: E402

BASE = "<!-- dotagents:begin -->\nBASE RULES\n<!-- dotagents:end -->\n"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    for var in ("AGENTS_HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "AGENTS_HARNESS",
                "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX", "GEMINI_CLI"):
        monkeypatch.delenv(var, raising=False)
    return home


def _log():
    return logging.getLogger("test-fix-adapters")


def test_claude_writes_where_claude_config_dir_points(tmp_path, monkeypatch):
    """Claude moves user settings to $CLAUDE_CONFIG_DIR; the include and hooks
    went to ~/.claude, where it never looked."""
    cfg = tmp_path / "claudecfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    agent = _agents.ClaudeAgent()
    agent.scope_level = "user"
    agent.write_base_config(tmp_path / "store", tmp_path, BASE, force=False, dry_run=False, logger=None)
    assert (cfg / "CLAUDE.md").is_file()
    assert (cfg / "CLAUDE.md").resolve() in agent.loaded_paths(tmp_path / "proj")


def test_the_told_scope_wins_over_agents_home(tmp_path):
    """`init -g --agents-dir X` (X not $AGENTS_HOME): Claude guessed project
    scope and wrote into X/../.claude, which Claude never reads."""
    agent = _agents.ClaudeAgent()
    agent.scope_level = "user"
    store = tmp_path / "custom" / "store"
    agent.write_base_config(store, tmp_path, BASE, force=False, dry_run=False, logger=None)
    assert (Path.home() / ".claude" / "CLAUDE.md").is_file()
    assert not (store.parent / ".claude").exists()


@pytest.mark.parametrize("cls", [_agents.CodexAgent, _agents.AntigravityAgent])
def test_a_project_init_leaves_global_harness_configs_alone(tmp_path, cls):
    agent = cls()
    agent.scope_level = "project"
    dest = tmp_path / "proj" / ".agents"
    dest.mkdir(parents=True)
    agent.wire_hooks(dest, dry_run=False, logger=None)
    assert not (Path.home() / ".codex").exists()
    assert not (Path.home() / ".gemini").exists()


def test_init_writes_the_store_agents_md_whatever_agents_are_named(tmp_path):
    """`--agents gemini,cursor,copilot` produced a store with no AGENTS.md, and
    harness files inside the store where no harness looks."""
    store = tmp_path / "store"
    _apply_base(BASE_ROOT, store, False, False, _log(), agents=["gemini", "cursor", "copilot"],
                scope_level="user")
    assert sorted(p.name for p in store.iterdir()) == ["AGENTS.md"]
    gemini = (Path.home() / ".gemini" / "GEMINI.md").read_text(encoding="utf-8")
    assert "@" + (store.resolve() / "AGENTS.md").as_posix() in gemini


def test_an_unknown_agent_name_is_a_usage_error(tmp_path):
    with pytest.raises(SystemExit, match="unknown agent"):
        _apply_base(BASE_ROOT, tmp_path / "store", False, False, _log(), agents=["claud"])
    assert not (tmp_path / "store").exists()


def test_cursor_context_goes_to_an_always_applied_rule(tmp_path):
    project = tmp_path / "proj"
    _agents.CursorAgent().write_context(project, "CTX", force=False, dry_run=False, logger=None)
    text = (project / ".cursor" / "rules" / "dotagents.mdc").read_text(encoding="utf-8")
    assert text.startswith("---\n") and "alwaysApply: true" in text and "CTX" in text
    assert "AGENTS.md" in _agents.CursorAgent.harness_loads
    assert "AGENTS.md" in _agents.CopilotAgent.harness_loads


def test_claude_imports_are_parsed_the_way_claude_reads_them(tmp_path, monkeypatch):
    monkeypatch.setattr(_agents.ClaudeAgent, "_walk_stop", tmp_path)
    project = tmp_path / "parent" / "proj"
    project.mkdir(parents=True)
    for name in ("inline.md", "listed.md", "fenced.md", "span.md", "ancestor.md"):
        (tmp_path / name).write_text("x\n", encoding="utf-8")
    (project / "CLAUDE.md").write_text(
        "See @../../inline.md for details.\n"
        "- @../../listed.md\n"
        "```\n@../../fenced.md\n```\n"
        "`@../../span.md`\n",
        encoding="utf-8",
    )
    (tmp_path / "parent" / "CLAUDE.md").write_text("@../ancestor.md\n", encoding="utf-8")
    loaded = _agents.ClaudeAgent().loaded_paths(project)
    names = {p.name for p in loaded}
    assert {"inline.md", "listed.md", "ancestor.md"} <= names
    assert "fenced.md" not in names and "span.md" not in names


def test_identity_is_not_stamped_in_a_plain_shell(tmp_path):
    """A repo AGENTS.md made `env` stamp AGENT=codex into any shell."""
    (tmp_path / "AGENTS.md").write_text("x", encoding="utf-8")
    assert _agents.stamp_identity({}, root=tmp_path) == {}
    assert _agents.stamp_identity({"CLAUDECODE": "1"}, root=tmp_path)["AGENT"] == "claude-code"


def test_an_include_spelled_differently_is_not_added_twice(tmp_path):
    entry = tmp_path / "home" / ".claude" / "CLAUDE.md"
    entry.parent.mkdir(parents=True)
    entry.write_text("@~/.agents/AGENTS.md\n", encoding="utf-8")
    assert _merge.merge_include_line(entry, "@../.agents/AGENTS.md") == "skipped (present)"


def test_project_include_in_a_tracked_claude_file_warns(tmp_path, caplog):
    project = tmp_path / "proj"
    (project / ".agents").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(project)], check=True, capture_output=True)
    agent = _agents.ClaudeAgent()
    agent.scope_level, agent.project_root = "project", project
    with caplog.at_level(logging.WARNING):
        agent.write_base_config(project / ".agents", tmp_path, BASE, force=False, dry_run=False, logger=_log())
    assert "not gitignored" in caplog.text


def test_antigravity_hook_runs_the_absolute_interpreter(tmp_path):
    import json

    agent = _agents.AntigravityAgent()
    root = tmp_path / "gemcfg"
    agent.wire_hooks(tmp_path / "store", dry_run=False, logger=None, config_root=root)
    data = json.loads((root / "hooks.json").read_text(encoding="utf-8"))
    command = data["dotagents"]["PreInvocation"][0]["hooks"][0]["command"]
    assert Path(sys.executable).as_posix() in command
