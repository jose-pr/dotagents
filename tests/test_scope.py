"""Scope resolution: project-by-default, -g for user, and the
``AGENTS_PROJECT_ROOT`` / agent-native project-root env-var precedence."""
import os
from pathlib import Path

import pytest

from dotagents._scope import project_root_default, resolve_scope

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
