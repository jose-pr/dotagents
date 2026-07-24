"""`_hooks` merge primitives: additive, idempotent, and crash-proof.

The settings file belongs to the user, so the properties under test are mostly
about what we DON'T do: don't duplicate on re-run, don't touch foreign hooks,
don't raise on a hand-mangled file, don't discard unrelated keys.

Run: ``PYTHONPATH=src python -m pytest tests/test_hooks.py``
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotagents import _hooks  # noqa: E402

CMD = "dotagents context"


def test_build_entry_omits_absent_optionals():
    entry = _hooks.build_hook_entry(CMD)
    assert entry == {"hooks": [{"type": "command", "command": CMD}]}
    assert "matcher" not in entry  # absent, not null


def test_build_entry_includes_matcher_and_status():
    entry = _hooks.build_hook_entry(CMD, matcher="startup", status_message="Loading")
    assert entry["matcher"] == "startup"
    assert entry["hooks"][0]["statusMessage"] == "Loading"


def test_absent_creates_entry():
    merged, changed = _hooks.merge_hook(None, CMD)
    assert changed is True
    assert merged == [_hooks.build_hook_entry(CMD)]


def test_idempotent_second_merge_is_noop():
    """The property that matters: re-running `init` must not accumulate hooks."""
    first, changed_a = _hooks.merge_hook(None, CMD, status_message="Loading")
    second, changed_b = _hooks.merge_hook(first, CMD, status_message="Loading")
    assert changed_a is True
    assert changed_b is False
    assert second == first
    assert len(second) == 1


def test_foreign_hooks_preserved_verbatim():
    foreign = {"matcher": "startup", "hooks": [{"type": "command", "command": "echo hi"}]}
    merged, changed = _hooks.merge_hook([foreign], CMD)
    assert changed is True
    assert merged[0] is foreign, "a hook we did not write must survive untouched"
    assert any(_hooks._is_ours(e, CMD) for e in merged)
    assert len(merged) == 2


def test_duplicates_of_our_command_collapse():
    dup = [_hooks.build_hook_entry(CMD), _hooks.build_hook_entry(CMD)]
    merged, changed = _hooks.merge_hook(dup, CMD)
    assert changed is True
    assert len(merged) == 1


@pytest.mark.parametrize(
    "malformed",
    [
        ["a bare string"],
        [{"no_hooks_key": True}],
        [{"hooks": "not-a-list"}],
        [None],
        [42],
    ],
    ids=["string", "dict-without-hooks", "hooks-not-list", "none", "int"],
)
def test_malformed_entries_are_dropped_never_raise(malformed):
    """A hand-edited settings file must not make `init` explode."""
    merged, changed = _hooks.merge_hook(malformed, CMD)
    assert changed is True
    assert len(merged) == 1  # only ours survives
    assert _hooks._is_ours(merged[0], CMD)


def test_non_list_existing_is_replaced():
    merged, changed = _hooks.merge_hook({"unexpected": "shape"}, CMD)
    assert changed is True
    assert merged == [_hooks.build_hook_entry(CMD)]


def test_load_settings_missing_and_empty(tmp_path):
    assert _hooks.load_settings(tmp_path / "nope.json") == {}
    empty = tmp_path / "empty.json"
    empty.write_text("   ", encoding="utf-8")
    assert _hooks.load_settings(empty) == {}


def test_load_settings_rejects_invalid_json(tmp_path):
    """Silently starting from {} would overwrite the user's file on next write."""
    bad = tmp_path / "settings.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        _hooks.load_settings(bad)
    assert str(bad) in str(exc.value)


def test_load_settings_rejects_non_object(tmp_path):
    arr = tmp_path / "settings.json"
    arr.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(SystemExit):
        _hooks.load_settings(arr)


def test_write_settings_roundtrip_and_dry_run(tmp_path):
    path = tmp_path / "nested" / "settings.json"
    _hooks.write_settings(path, {"a": 1}, dry_run=True)
    assert not path.exists(), "dry_run must not create anything"

    _hooks.write_settings(path, {"a": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_status_message_identifies_our_hook_across_command_revisions():
    """Revising the command we write must REPLACE the old hook, not sit beside it.

    Dedup by exact command string orphans the previous version: the old (often
    broken) hook keeps running alongside the new one. The statusMessage is a stable
    label we choose, so it survives a command rewrite.
    """
    old, _ = _hooks.merge_hook(None, "old-command", status_message="Loading agent context")
    merged, changed = _hooks.merge_hook(
        old, "new-command", status_message="Loading agent context"
    )

    assert changed is True
    commands = [h["command"] for e in merged for h in e["hooks"]]
    assert commands == ["new-command"], "the superseded hook must be gone"


def test_status_match_does_not_touch_a_foreign_hook():
    """Only OUR status message supersedes; a user's hook is never dropped."""
    foreign = {"hooks": [{"type": "command", "command": "mine", "statusMessage": "Mine"}]}
    merged, _ = _hooks.merge_hook([foreign], CMD, status_message="Loading agent context")

    commands = [h["command"] for e in merged for h in e["hooks"]]
    assert "mine" in commands
    assert CMD in commands
