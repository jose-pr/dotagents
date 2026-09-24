"""Regression tests for the 2026-09-23 review's skills, adapter and hook-cost
findings: Claude's per-skill links are refreshed and pruned, `overlays
add/sync/remove` keep a wired Claude skills dir current, project-scope skill
links warn when git would commit them, pi gets a pointer to a project store,
and the env loaders reuse a cached diff while the inputs are unchanged.

tmp dirs only (tests/conftest.py isolates HOME and the session variables).
"""

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dotagents import _agents, _skills
from dotagents._fs import write_text_lf
from dotagents.cli import OverlayAdd, OverlayRemove, OverlaySync

ClaudeAgent = _agents.ClaudeAgent

BASE_AGENTS = (
    "<!-- dotagents:begin -->\n# Agent Directives\n\n## Always-on rules\n"
    "- **Base rule**: keep it.\n\n## Load on demand\nNothing ships here by default.\n"
    "<!-- dotagents:end -->\n"
)


def _log():
    return logging.getLogger("test_fix_skills_adapters")


def _skill(root: Path, name: str, body: str = "v1") -> Path:
    write_text_lf(root / name / "SKILL.md", "---\nname: %s\n---\n%s\n" % (name, body))
    return root / name


def _no_links(monkeypatch):
    """A box with neither symlinks nor junctions: everything is a copy."""
    def refuse(*_a, **_k):
        raise OSError(1314, "A required privilege is not held by the client")

    monkeypatch.setattr(os, "symlink", refuse)
    monkeypatch.setattr(_skills, "_create_junction", refuse)


def _body(path: Path) -> str:
    return (path / "SKILL.md").read_text(encoding="utf-8")


def _link(store: Path, claude: Path, *, dry_run: bool = False) -> None:
    """What `init` runs: `wire_hooks` links the store's skills first."""
    ClaudeAgent().wire_hooks(store, dry_run=dry_run, logger=_log(), config_root=claude)


# --------------------------------------------------------------------------
# overlays-24: Claude's copied skills are refreshed, removed skills pruned
# --------------------------------------------------------------------------

def test_a_copied_skill_is_refreshed_when_the_store_changes(tmp_path, monkeypatch):
    _no_links(monkeypatch)
    store, claude = tmp_path / "store", tmp_path / "claude"
    _skill(store / "skills", "demo", "v1")
    _link(store, claude)
    target = claude / "skills" / "demo"
    assert target.is_dir() and not _skills._is_link(target)
    _skill(store / "skills", "demo", "v2")
    _link(store, claude)
    assert "v2" in _body(target), "an unedited copy follows the store"


def test_wire_hooks_refreshes_a_stale_copy_too(tmp_path, monkeypatch):
    _no_links(monkeypatch)
    store, claude = tmp_path / "store", tmp_path / "claude"
    _skill(store / "skills", "demo", "v1")
    ClaudeAgent().wire_hooks(store, dry_run=False, logger=_log(), config_root=claude)
    _skill(store / "skills", "demo", "v2")
    ClaudeAgent().wire_hooks(store, dry_run=False, logger=_log(), config_root=claude)
    assert "v2" in _body(claude / "skills" / "demo")


def test_a_copy_edited_in_place_is_kept(tmp_path, monkeypatch, caplog):
    _no_links(monkeypatch)
    store, claude = tmp_path / "store", tmp_path / "claude"
    _skill(store / "skills", "demo", "v1")
    _link(store, claude)
    write_text_lf(claude / "skills" / "demo" / "SKILL.md", "mine now\n")
    _skill(store / "skills", "demo", "v2")
    with caplog.at_level(logging.WARNING):
        _link(store, claude)
    assert _body(claude / "skills" / "demo") == "mine now\n"
    assert any("edited since it was copied" in r.getMessage() for r in caplog.records)


def test_a_same_named_user_skill_is_never_replaced(tmp_path):
    store, claude = tmp_path / "store", tmp_path / "claude"
    _skill(store / "skills", "demo", "store")
    write_text_lf(claude / "skills" / "demo" / "SKILL.md", "the user's\n")
    _link(store, claude)
    assert _body(claude / "skills" / "demo") == "the user's\n"
    # ... and it is not removed once the store's skill goes away either.
    shutil.rmtree(str(store / "skills" / "demo"))
    _link(store, claude)
    assert _body(claude / "skills" / "demo") == "the user's\n"


@pytest.mark.parametrize("links", [True, False], ids=["link", "copy"])
def test_a_skill_the_store_dropped_is_removed(tmp_path, monkeypatch, links):
    if not links:
        _no_links(monkeypatch)
    store, claude = tmp_path / "store", tmp_path / "claude"
    _skill(store / "skills", "demo")
    _skill(store / "skills", "keep")
    _link(store, claude)
    assert (claude / "skills" / "demo" / "SKILL.md").is_file()
    shutil.rmtree(str(store / "skills" / "demo"))
    _link(store, claude)
    assert not os.path.lexists(str(claude / "skills" / "demo")), "no stale copy, no dangling link"
    assert (claude / "skills" / "keep" / "SKILL.md").is_file()


def test_a_dangling_link_from_before_the_record_is_pruned(tmp_path):
    store, claude = tmp_path / "store", tmp_path / "claude"
    gone = _skill(store / "skills", "gone")
    (claude / "skills").mkdir(parents=True)
    try:
        os.symlink(str(gone), str(claude / "skills" / "gone"), target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need privileges here")
    shutil.rmtree(str(gone))
    _link(store, claude)
    assert not os.path.lexists(str(claude / "skills" / "gone"))


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows fallback")
def test_a_junction_is_tried_before_a_copy(tmp_path, monkeypatch):
    def refuse(*_a, **_k):
        raise OSError(1314, "A required privilege is not held by the client")

    monkeypatch.setattr(os, "symlink", refuse)
    store, claude = tmp_path / "store", tmp_path / "claude"
    _skill(store / "skills", "demo", "v1")
    _link(store, claude)
    target = claude / "skills" / "demo"
    assert _skills._is_junction(target)
    _skill(store / "skills", "demo", "v2")
    assert "v2" in _body(target), "a junction is always current"
    shutil.rmtree(str(store / "skills" / "demo"))
    _link(store, claude)
    assert not os.path.lexists(str(target))


def test_dry_run_links_nothing(tmp_path):
    store, claude = tmp_path / "store", tmp_path / "claude"
    _skill(store / "skills", "demo")
    _link(store, claude, dry_run=True)
    assert not (claude / "skills").exists()


# --------------------------------------------------------------------------
# overlays-25: overlays add / sync / remove keep a wired Claude current
# --------------------------------------------------------------------------

def _run(cmd_cls, **kwargs):
    cmd = cmd_cls()
    for key, value in kwargs.items():
        setattr(cmd, key, value)
    return cmd()


def _overlay(src: Path, name: str, skill=None) -> Path:
    ov = src / name
    write_text_lf(ov / "overlay.toml", 'name = "%s"\n' % name)
    if skill:
        _skill(ov / "skills", skill)
    return ov


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A user store `init` wired into Claude (via $CLAUDE_CONFIG_DIR)."""
    for key in ("AGENTS_OVERLAYS_REPO", "AGENTS_PROJECT_ROOT", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(key, raising=False)
    store = tmp_path / "store"
    write_text_lf(store / "AGENTS.md", BASE_AGENTS)
    monkeypatch.setenv("AGENTS_HOME", str(store))
    claude = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    agent = ClaudeAgent()
    agent.scope_level = "user"
    agent.write_base_config(store, tmp_path, BASE_AGENTS, force=False, dry_run=False, logger=None)
    src = tmp_path / "src"
    src.mkdir()
    return src, store, claude


def _opts(src, store, **extra):
    kwargs = dict(repo=[str(src)], global_scope=True, agents_dir=store, copy=False, dry_run=False)
    kwargs.update(extra)
    return kwargs


def test_add_links_a_new_skill_into_a_wired_claude(wired):
    src, store, claude = wired
    _overlay(src, "withskill", skill="hello")
    assert _run(OverlayAdd, name=["withskill"], no_setup=True, no_requires=False, **_opts(src, store)) == 0
    assert (claude / "skills" / "hello" / "SKILL.md").is_file()


def test_remove_prunes_the_claude_link(wired):
    src, store, claude = wired
    _overlay(src, "withskill", skill="hello")
    _run(OverlayAdd, name=["withskill"], no_setup=True, no_requires=False, **_opts(src, store))
    ClaudeAgent().wire_hooks(store, dry_run=False, logger=_log())  # linked either way
    assert (claude / "skills" / "hello" / "SKILL.md").is_file()
    _run(OverlayRemove, name=["withskill"], force=False, global_scope=True, agents_dir=store, dry_run=False)
    assert not os.path.lexists(str(claude / "skills" / "hello")), "no dangling link left behind"


def test_sync_links_a_skill_the_overlay_gained(wired):
    src, store, claude = wired
    _overlay(src, "grows")
    _run(OverlayAdd, name=["grows"], no_setup=True, no_requires=False, **_opts(src, store))
    _skill(src / "grows" / "skills", "later")
    assert _run(OverlaySync, pattern=None, overwrite=False, prune=False, no_setup=True,
                **_opts(src, store)) == 0
    assert (claude / "skills" / "later" / "SKILL.md").is_file()


def test_an_unwired_claude_is_left_alone(tmp_path, monkeypatch, caplog):
    store = tmp_path / "store"
    write_text_lf(store / "AGENTS.md", BASE_AGENTS)
    monkeypatch.setenv("AGENTS_HOME", str(store))
    claude = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    src = tmp_path / "src"
    _overlay(src, "withskill", skill="hello")
    with caplog.at_level(logging.INFO):
        _run(OverlayAdd, name=["withskill"], no_setup=True, no_requires=False, **_opts(src, store))
    assert not (claude / "skills").exists()
    assert any("re-run `dotagents init" in r.getMessage() for r in caplog.records)


def test_project_scope_links_into_the_project_claude_dir(tmp_path, monkeypatch):
    for key in ("AGENTS_OVERLAYS_REPO", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "user-store"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "user-claude"))
    project = tmp_path / "proj"
    store = project / ".agents"
    write_text_lf(store / "AGENTS.md", BASE_AGENTS)
    monkeypatch.setenv("AGENTS_PROJECT_ROOT", str(project))
    monkeypatch.chdir(project)
    agent = ClaudeAgent()
    agent.scope_level, agent.project_root = "project", project
    agent.write_base_config(store, tmp_path, BASE_AGENTS, force=False, dry_run=False, logger=None)
    src = tmp_path / "src"
    _overlay(src, "withskill", skill="hello")
    _run(OverlayAdd, name=["withskill"], no_setup=True, no_requires=False,
         **_opts(src, store, global_scope=False))
    assert (project / ".claude" / "skills" / "hello" / "SKILL.md").is_file()
    assert not (tmp_path / "user-claude" / "skills").exists(), "the user's config is not touched"


# --------------------------------------------------------------------------
# adapters-12: project-scope skill links warn when git would commit them
# --------------------------------------------------------------------------

def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("ignored", [False, True])
def test_project_skill_links_warn_when_not_gitignored(tmp_path, caplog, ignored):
    project = tmp_path / "proj"
    project.mkdir()
    _git("init", "-q", cwd=project)
    if ignored:
        write_text_lf(project / ".gitignore", ".claude/skills/\n")
    store = project / ".agents"
    _skill(store / "skills", "hello")
    agent = ClaudeAgent()
    agent.scope_level, agent.project_root = "project", project
    with caplog.at_level(logging.WARNING):
        agent.wire_hooks(store, dry_run=False, logger=_log())  # what project-scope `init` runs
    assert (project / ".claude" / "skills" / "hello" / "SKILL.md").is_file()
    warned = [r.getMessage() for r in caplog.records if "not gitignored" in r.getMessage()]
    assert bool(warned) is not ignored
    if warned:
        assert "hello" in warned[0]


# --------------------------------------------------------------------------
# adapters-13: pi is pointed at a project store's AGENTS.md
# --------------------------------------------------------------------------

def _pi_project(tmp_path):
    project = tmp_path / "proj"
    store = project / ".agents"
    write_text_lf(store / "AGENTS.md", BASE_AGENTS)
    agent = _agents.PiAgent()
    agent.scope_level, agent.project_root = "project", project
    return agent, project, store


def test_pi_gets_a_pointer_to_the_project_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi"))
    agent, project, store = _pi_project(tmp_path)
    agent.write_base_config(store, tmp_path, BASE_AGENTS, force=False, dry_run=False, logger=_log())
    text = (project / ".pi" / "APPEND_SYSTEM.md").read_text(encoding="utf-8")
    assert "`.agents/AGENTS.md`" in text, "relative to the project root: no machine path"
    assert "Base rule" not in text, "a pointer, never a copy"
    assert not (tmp_path / "pi").exists(), "the user's pi config is not touched"
    agent.write_base_config(store, tmp_path, BASE_AGENTS, force=True, dry_run=False, logger=_log())
    assert (project / ".pi" / "APPEND_SYSTEM.md").read_text(encoding="utf-8") == text, "idempotent"


def test_pi_pointer_leaves_a_context_block_alone(tmp_path):
    """`launch pi` on Windows writes the assembled context -- the store's
    AGENTS.md, markers and all -- into the same file first."""
    from dotagents import _merge

    agent, project, store = _pi_project(tmp_path)
    agent.write_context(project, BASE_AGENTS, force=False, dry_run=False, logger=_log())
    before = (project / ".pi" / "APPEND_SYSTEM.md").read_text(encoding="utf-8")
    agent.write_base_config(store, tmp_path, BASE_AGENTS, force=False, dry_run=False, logger=_log())
    after = (project / ".pi" / "APPEND_SYSTEM.md").read_text(encoding="utf-8")
    span = _merge.find_block(after, _merge.CONTEXT_BEGIN_MARKER, _merge.CONTEXT_END_MARKER)
    assert after[span[0]:span[1]] in before, "the context block is intact"
    assert "Base rule" in after and "`.agents/AGENTS.md`" in after
