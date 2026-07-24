"""Sync-hook discovery across the flat hooks/ dir and overlay-provided
``overlays/<name>/hooks/`` dirs (the overlay-dir install model)."""
from dotagents._link import _find_sync_hook


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
