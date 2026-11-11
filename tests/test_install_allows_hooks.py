"""Installing an overlay allows its hooks: `overlays add` and `overlays sync` record the allowance at
the digest they install and say which hooks are on; `--no-hooks` leaves them off; a revoke survives a
sync; an install an agent asked for through the MCP tool allows nothing."""

from __future__ import annotations

import json
import logging
import os

import pytest

from _helpers import run_cmd
from _hook_helpers import HOOK_PY, store_env, write
from dotagents import _hook_dispatch, _overlay_hooks
from dotagents._scope import Scope
from dotagents.cli.overlays import OverlayAdd, OverlayRevokeHooks, OverlaySync

BASE = "<!-- dotagents:begin -->\n# BASE\n<!-- dotagents:end -->\n"
PROMPT = json.dumps({"session_id": "s", "cwd": "/", "prompt": "x"}).encode()


@pytest.fixture(autouse=True)
def _user_store(tmp_path, monkeypatch):
    store_env(tmp_path, monkeypatch)
    monkeypatch.setattr(_overlay_hooks, "_MCP_STRICT", [])
    monkeypatch.delenv("DOTAGENTS_MCP", raising=False)


class World(object):
    def __init__(self, tmp_path):
        self.store = tmp_path / "store"
        self.source = tmp_path / "source"
        self.log = tmp_path / "hook.log"
        write(self.store / "AGENTS.md", BASE)
        self.publish("# v1\n")

    def publish(self, note: str) -> None:
        write(self.source / "guard" / "overlay.cfg", "[hooks]\nprompt = {}\n")
        write(self.source / "guard" / "hook.py", HOOK_PY + note)

    def add(self, *, no_hooks=False) -> None:
        run_cmd(OverlayAdd, name=["guard"], repo=[str(self.source)], global_scope=True, agents_dir=self.store,
                copy=True, dry_run=False, no_hooks=no_hooks)

    def sync(self, *, no_hooks=False) -> None:
        run_cmd(OverlaySync, repo=[str(self.source)], global_scope=True, agents_dir=self.store, copy=True,
                dry_run=False, no_hooks=no_hooks)

    def revoke(self) -> None:
        run_cmd(OverlayRevokeHooks, name="guard", global_scope=True, agents_dir=self.store)

    @property
    def overlay(self):
        return next(o for o in Scope("user", self.store).installed_overlays if o.name == "guard")

    def allowed(self):
        return _overlay_hooks.allowed_digest(self.store, "guard", self.store)

    def fires(self) -> bool:
        self.log.unlink() if self.log.exists() else None
        scope = _hook_dispatch.scope_for("user", self.store)
        environ = dict(os.environ, HOOK_LOG=str(self.log))
        _hook_dispatch.dispatch("claude-code", "prompt", PROMPT, scope, "user", environ)
        return self.log.exists()


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def test_add_allows_the_hooks_at_the_digest_installed_and_they_fire(world, caplog):
    with caplog.at_level(logging.INFO):
        world.add()
    assert world.allowed() == world.overlay.tree_digest()
    assert "allowed its hooks, now on: " in caplog.text and "prompt (hook.py)" in caplog.text
    assert world.fires()


def test_no_hooks_installs_without_allowing_and_a_sync_does_not_allow_them_later(world, caplog):
    with caplog.at_level(logging.WARNING):
        world.add(no_hooks=True)
    assert world.allowed() is None and not world.fires()
    assert "its hooks are off (--no-hooks)" in caplog.text
    world.sync()
    assert world.allowed() is None, "declining at install is remembered"
    # A fresh add is the person choosing again.
    world.add()
    assert world.allowed() == world.overlay.tree_digest() and world.fires()


def test_a_sync_to_a_new_digest_keeps_the_hooks_on(world):
    world.add()
    first = world.allowed()
    world.publish("# v2, changed upstream\n")
    world.sync()
    assert world.allowed() == world.overlay.tree_digest() != first
    assert world.fires()


def test_sync_with_no_hooks_leaves_a_changed_overlay_off(world):
    world.add()
    world.publish("# v2\n")
    world.sync(no_hooks=True)
    assert world.allowed() != world.overlay.tree_digest() and not world.fires()


def test_a_revoke_survives_a_sync_until_a_fresh_add(world, caplog):
    world.add()
    world.revoke()
    assert not world.fires()
    world.publish("# v2\n")
    with caplog.at_level(logging.WARNING):
        world.sync()
    assert world.allowed() is None and not world.fires()
    assert "they were revoked and a sync keeps them off" in caplog.text
    world.add()
    assert world.allowed() == world.overlay.tree_digest() and world.fires()


def test_allow_hooks_after_a_revoke_lets_a_sync_allow_again(world):
    world.add()
    world.revoke()
    _overlay_hooks.allow(world.store, "guard", world.overlay.tree_digest(), world.store)
    assert not _overlay_hooks.is_revoked(world.store, "guard", world.store)
    world.publish("# v2\n")
    world.sync()
    assert world.fires()


def test_an_install_through_the_default_mcp_server_allows_hooks_like_the_command_line(world, monkeypatch):
    monkeypatch.setenv("DOTAGENTS_MCP", "stdio")
    world.add()
    assert world.allowed() == world.overlay.tree_digest() and world.fires()


def test_a_strict_mcp_server_installs_without_allowing_and_says_how_to_turn_them_on(world, monkeypatch, caplog):
    monkeypatch.setattr(_overlay_hooks, "_MCP_STRICT", [True])
    with caplog.at_level(logging.WARNING):
        world.add()
    assert world.allowed() is None and not world.fires()
    assert "dotagents mcp --strict" in caplog.text and "allow-hooks guard" in caplog.text
    world.sync()
    assert world.allowed() is None


def test_a_strict_mcp_sync_does_not_take_back_what_the_user_allowed(world, monkeypatch):
    world.add()
    monkeypatch.setattr(_overlay_hooks, "_MCP_STRICT", [True])
    world.sync()
    assert world.allowed() == world.overlay.tree_digest() and world.fires()
    # The command line, not strict, allows a changed overlay as before.
    monkeypatch.setattr(_overlay_hooks, "_MCP_STRICT", [])
    world.publish("# v2\n")
    world.sync()
    assert world.fires()


def test_the_users_own_setting_makes_a_server_started_by_environment_strict(world, monkeypatch):
    monkeypatch.setenv("DOTAGENTS_MCP", "stdio")
    write(_overlay_hooks.mcp_settings_path(world.store), json.dumps({"strict": True}))
    world.add()
    assert world.allowed() is None


def test_the_setting_is_read_from_the_user_store_only_and_the_command_line_is_not_affected(world, tmp_path, monkeypatch):
    # A project's own file, however it is named, decides nothing.
    project_store = tmp_path / "project" / ".agents"
    write(_overlay_hooks.mcp_settings_path(project_store), json.dumps({"strict": True}))
    write(project_store / "dotagents" / "config.toml", "strict = true\n")
    monkeypatch.setenv("DOTAGENTS_MCP", "stdio")
    world.add()
    assert world.allowed() == world.overlay.tree_digest()
    # And the user's setting does nothing for the command line: only a server serving tools is strict.
    monkeypatch.delenv("DOTAGENTS_MCP")
    write(_overlay_hooks.mcp_settings_path(world.store), json.dumps({"strict": True}))
    world.publish("# v2\n")
    world.sync()
    assert world.fires()


def test_dotagents_mcp_strict_marks_the_process(monkeypatch):
    from dotagents.cli.mcp import Mcp

    monkeypatch.setattr(_overlay_hooks, "_MCP_STRICT", [])
    served = []
    import duho.mcp

    monkeypatch.setattr(duho.mcp, "serve_running_app", lambda **kw: served.append(1) or 0)
    command = Mcp()
    command.strict = True
    assert command() == 0 and _overlay_hooks._MCP_STRICT == [True]
    monkeypatch.setattr(_overlay_hooks, "_MCP_STRICT", [])
    default = Mcp()
    assert default() == 0 and _overlay_hooks._MCP_STRICT == []


def test_an_install_made_by_0_9_2_with_hooks_off_becomes_allowed_on_the_next_sync(world):
    """0.9.2 recorded nothing at install: the hooks were off until `allow-hooks`."""
    world.add()
    _overlay_hooks.allowances_path(world.store).unlink()
    assert world.allowed() is None and not world.fires()
    world.sync()
    assert world.allowed() == world.overlay.tree_digest() and world.fires()


def test_a_0_9_2_install_the_user_revoked_stays_off(world):
    world.add()
    world.revoke()
    # Whatever the file held before the revoke was recorded, the revoke is what a sync honours.
    world.sync()
    assert not world.fires()


def test_a_dry_run_allows_nothing(world, caplog):
    with caplog.at_level(logging.INFO):
        run_cmd(OverlayAdd, name=["guard"], repo=[str(world.source)], global_scope=True, agents_dir=world.store,
                copy=True, dry_run=True, no_hooks=False)
    assert not _overlay_hooks.allowances_path(world.store).exists()


def test_an_overlay_without_hooks_records_nothing(world):
    write(world.source / "plain" / "overlay.cfg", "[env]\nA = \"b\"\n")
    run_cmd(OverlayAdd, name=["plain"], repo=[str(world.source)], global_scope=True, agents_dir=world.store,
            copy=True, dry_run=False, no_hooks=False)
    assert not _overlay_hooks.allowances_path(world.store).exists()


def test_a_project_installs_allowance_is_for_the_overlay_of_that_store(tmp_path):
    world = World(tmp_path)
    project_store = tmp_path / "project" / ".agents"
    write(project_store / "AGENTS.md", BASE)
    os.chdir(tmp_path / "project")
    run_cmd(OverlayAdd, name=["guard"], repo=[str(world.source)], global_scope=False, copy=True, dry_run=False,
            no_hooks=False)
    assert _overlay_hooks.allowed_digest(world.store, "guard", project_store) is not None
    assert _overlay_hooks.allowed_digest(world.store, "guard", world.store) is None
