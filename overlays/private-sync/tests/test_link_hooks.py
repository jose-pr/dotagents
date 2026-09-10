"""Sync-hook discovery across the flat hooks/ dir and overlay-provided
``overlays/<name>/hooks/`` dirs (the overlay-dir install model).

Moved here with the logic (D85): `_link` is this overlay's `lib/_link.py`, put on
`sys.path` by the sibling `conftest.py` -- it is no longer `dotagents._link`."""
import os

import pytest

from _link import _find_sync_hook, _run_sync_hook, store_root


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    return path


def test_no_hook_returns_none(tmp_path):
    (tmp_path / "hooks").mkdir()
    assert _find_sync_hook(tmp_path) is None


def test_flat_hook_found(tmp_path):
    hook = _touch(tmp_path / "hooks" / "sync")
    assert _find_sync_hook(tmp_path) == hook


def test_overlay_provided_hook_found(tmp_path):
    # No flat hook; an installed overlay provides one.
    hook = _touch(tmp_path / "overlays" / "private-sync" / "hooks" / "sync")
    assert _find_sync_hook(tmp_path) == hook


def test_flat_hook_wins_over_overlay(tmp_path):
    flat = _touch(tmp_path / "hooks" / "sync")
    _touch(tmp_path / "overlays" / "private-sync" / "hooks" / "sync")
    # The user's own hooks/ takes precedence over any overlay's.
    assert _find_sync_hook(tmp_path) == flat


def test_overlay_hook_alt_name(tmp_path):
    hook = _touch(tmp_path / "overlays" / "z-overlay" / "hooks" / "sync.sh")
    assert _find_sync_hook(tmp_path) == hook


# --------------------------------------------------------------------------- #
# store_root: AGENTS_STORE_DIR, else <agents_dir>/projects.
# --------------------------------------------------------------------------- #


def test_store_root_uses_agents_store_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTS_STORE_DIR", str(tmp_path / "stores"))
    assert store_root(tmp_path / "agents") == tmp_path / "stores"
    monkeypatch.delenv("AGENTS_STORE_DIR")
    assert store_root(tmp_path / "agents") == tmp_path / "agents" / "projects"


# --------------------------------------------------------------------------- #
# Sync hook env: the runner sets AGENTS_HOME / AGENTS_SYNC_MESSAGE.
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(os.name == "nt", reason="needs a POSIX-executable hook script")
def test_run_sync_hook_sets_the_env_names(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    out = tmp_path / "env.txt"
    hook = agents / "hooks" / "sync"
    hook.parent.mkdir(parents=True)
    hook.write_text(
        "#!/bin/sh\n"
        '{\n'
        '  echo "AGENTS_HOME=$AGENTS_HOME"\n'
        '  echo "AGENTS_SYNC_MESSAGE=$AGENTS_SYNC_MESSAGE"\n'
        '} > "' + str(out) + '"\n',
        encoding="utf-8",
    )
    os.chmod(hook, 0o755)

    rc = _run_sync_hook(agents, message="msg-42", dry_run=False, log=lambda *a: None)
    assert rc == 0
    env = dict(
        line.split("=", 1) for line in out.read_text().splitlines() if "=" in line
    )
    assert env["AGENTS_HOME"] == str(agents)
    assert env["AGENTS_SYNC_MESSAGE"] == "msg-42"
    assert "DOTAGENTS_AGENTS_DIR" not in env and "DOTAGENTS_SYNC_MESSAGE" not in env
