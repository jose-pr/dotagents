"""Scope resolution: project-by-default, -g for user, and the
``AGENTS_PROJECT_ROOT`` / agent-native project-root env-var precedence."""
import os
from pathlib import Path

import pytest

from dotagents._scope import (
    Scope,
    discover_overlays,
    is_valid_overlay_name,
    normalize_overlay_name,
    project_root_default,
    resolve_scope,
)

_ROOT_VARS = ("AGENTS_PROJECT_ROOT", "CLAUDE_PROJECT_DIR")


@pytest.fixture(autouse=True)
def _clear_root_vars(monkeypatch):
    for v in _ROOT_VARS:
        monkeypatch.delenv(v, raising=False)


def test_default_scope_is_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    scope = resolve_scope(False)
    assert scope.level == "project"
    assert scope.agents_root == tmp_path / ".agents"


def test_global_scope_is_user(tmp_path):
    scope = resolve_scope(True, agents_dir=tmp_path)
    assert scope.level == "user"
    assert scope.agents_root == tmp_path


def test_project_root_defaults_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert project_root_default() == tmp_path


def test_agents_project_root_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTS_PROJECT_ROOT", str(tmp_path))
    assert project_root_default() == tmp_path
    # ...and drives the project scope's .agents location.
    assert resolve_scope(False).agents_root == tmp_path / ".agents"


def test_claude_project_dir_used_as_fallback(tmp_path, monkeypatch):
    # No AGENTS_PROJECT_ROOT; the agent-native var is picked up.
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert project_root_default() == tmp_path


def test_agents_root_beats_claude_project_dir(tmp_path, monkeypatch):
    claude = tmp_path / "claude"
    agents = tmp_path / "agents"
    claude.mkdir()
    agents.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(claude))
    monkeypatch.setenv("AGENTS_PROJECT_ROOT", str(agents))
    assert project_root_default() == agents


def test_global_scope_ignores_project_root_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTS_PROJECT_ROOT", str(tmp_path / "proj"))
    scope = resolve_scope(True, agents_dir=tmp_path / "store")
    assert scope.level == "user"
    assert scope.agents_root == tmp_path / "store"


# --------------------------------------------------------------------------
# Overlay-name rule (D84): a dir under overlays/ is an overlay iff its name is
# valid; names normalize lowercase-dash. One shared rule, mirrored by the
# get_file_paths overlay gate.
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    ["flows", "private-sync", "my_overlay", "net", "My-Overlay", "foo.bar", "v1.2"],
)
def test_valid_overlay_names(name):
    # A dot mid-name is allowed (foo.bar, v1.2); only a LEADING dot is excluded.
    assert is_valid_overlay_name(name)


@pytest.mark.parametrize("name", [".git", ".hidden", "__pycache__", "2fast", ""])
def test_invalid_overlay_names(name):
    # Leading dot / underscore / digit are all excluded.
    assert not is_valid_overlay_name(name)


def test_normalize_overlay_name():
    assert normalize_overlay_name("My_Overlay") == "my-overlay"
    assert normalize_overlay_name("my-overlay") == "my-overlay"
    assert normalize_overlay_name("NET") == "net"


def test_discover_overlays_includes_valid_excludes_junk(tmp_path):
    overlays = tmp_path / "overlays"
    for d in ("flows", "my_overlay", ".git", "__pycache__"):
        (overlays / d).mkdir(parents=True)
    (overlays / "README.md").write_text("x", encoding="utf-8")  # a file, not a dir
    scope = Scope("user", tmp_path)
    found = discover_overlays(scope)
    assert "flows" in found
    assert "my_overlay" in found
    assert ".git" not in found
    assert "__pycache__" not in found
    assert "README.md" not in found  # a file (valid NAME, but not a dir)


def test_discover_overlays_follows_symlink_to_dir(tmp_path):
    # A symlink pointing at a directory IS a valid overlay (is_dir follows links).
    target = tmp_path / "real_overlay_target"
    target.mkdir()
    overlays = tmp_path / "overlays"
    overlays.mkdir()
    try:
        (overlays / "linked").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation not permitted (Windows without privilege)")
    scope = Scope("user", tmp_path)
    assert "linked" in discover_overlays(scope)
