"""`dotagents overlays hooks / allow-hooks / revoke-hooks`: seeing what an overlay's hooks do, then
allowing them at one digest, as the user's own act."""

from __future__ import annotations

import pytest

from _helpers import run_cmd
from _hook_helpers import Rig, store_env, write
from dotagents import _overlay_hooks
from dotagents.cli import overlays
from dotagents.cli.overlays import OverlayAllowHooks, OverlayHooks, OverlayRevokeHooks

MANIFEST = "[hooks]\nprompt = {}\npre-tool = { match = [\"execute\"], on_failure = \"refuse\", sets = [\"A\"] }\ncd = {}\n"


@pytest.fixture(autouse=True)
def _user_store(tmp_path, monkeypatch):
    store_env(tmp_path, monkeypatch)
    # No test reads the real terminal.
    monkeypatch.setattr(overlays, "_read_tty", lambda prompt: None)


def fields(rig):
    return dict(global_scope=True, agents_dir=rig.store)


def test_hooks_lists_what_runs_where_and_that_it_is_off(tmp_path, capsys):
    rig = Rig(tmp_path, MANIFEST)
    assert run_cmd(OverlayHooks, name="guard", **fields(rig)) == 0
    text = capsys.readouterr().out
    assert "off: not allowed at this digest" in text
    assert "pre-tool: hook.py (python)  match execute  sets A  timeout 30s  on_failure refuse" in text
    assert "claude-code: fires natively" in text and "codex: cannot fire" in text  # cd
    assert "post-tool" not in text
    assert "outside the harness's sandbox" in text


def test_allowing_is_two_steps_and_only_at_the_shown_digest(tmp_path, capsys, monkeypatch):
    rig = Rig(tmp_path, MANIFEST)
    assert run_cmd(OverlayAllowHooks, name="guard", **fields(rig)) == 0
    assert "run again with --digest %s" % rig.digest() in capsys.readouterr().out
    assert _overlay_hooks.allowed_digest(rig.store, "guard") is None
    assert run_cmd(OverlayAllowHooks, name="guard", digest="sha256:" + "0" * 64, **fields(rig)) == 2
    assert _overlay_hooks.allowed_digest(rig.store, "guard") is None
    monkeypatch.setattr(overlays, "_read_tty", lambda prompt: "allow guard")
    assert run_cmd(OverlayAllowHooks, name="guard", digest=rig.digest(), **fields(rig)) == 0
    assert _overlay_hooks.allowed_digest(rig.store, "guard") == rig.digest()
    write(rig.root / "hook.py", "# changed\n")
    run_cmd(OverlayHooks, name="guard", **fields(rig))
    assert "off: not allowed at this digest" in capsys.readouterr().out.split("allowed the hooks")[-1]


def test_revoking(tmp_path, capsys):
    rig = Rig(tmp_path, MANIFEST)
    rig.allow()
    assert run_cmd(OverlayRevokeHooks, name="guard", **fields(rig)) == 0
    assert _overlay_hooks.allowed_digest(rig.store, "guard") is None
    capsys.readouterr()
    run_cmd(OverlayRevokeHooks, name="guard", **fields(rig))
    assert "were not allowed" in capsys.readouterr().out


def test_an_allowance_in_a_projects_store_allows_nothing(tmp_path):
    # Only the user's own store holds allowances: a project can arrive with any file in it.
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n")
    project = tmp_path / "project" / ".agents"
    forged = _overlay_hooks.allowances_path(project)
    write(forged, '{"allowed": {"guard": "%s"}}' % rig.digest())
    assert _overlay_hooks.allowed_digest(rig.store, "guard") is None
    plan = _overlay_hooks.overlay_plan(rig.overlay, _overlay_hooks.CLAUDE, rig.store)
    assert plan["on"] is False


def test_hooks_without_a_name_lists_only_overlays_that_have_some(tmp_path, capsys):
    rig = Rig(tmp_path, "[env]\nA = \"b\"\n")
    (rig.root / "hook.py").unlink()
    assert run_cmd(OverlayHooks, **fields(rig)) == 0
    assert capsys.readouterr().out == "no overlay has hooks\n"


def test_installing_an_overlay_with_hooks_says_they_are_off(tmp_path, caplog):
    import logging

    from dotagents.cli import OverlayAdd

    source = tmp_path / "source"
    write(source / "withhooks" / "overlay.cfg", "[hooks]\nprompt = {}\n")
    write(source / "withhooks" / "hook.py", "")
    store = tmp_path / "store"
    write(store / "AGENTS.md", "<!-- dotagents:begin -->\n# BASE\n<!-- dotagents:end -->\n")
    with caplog.at_level(logging.WARNING):
        assert run_cmd(OverlayAdd, name=["withhooks"], repo=[str(source)], global_scope=True, agents_dir=store,
                       copy=True, dry_run=False) == 0
    assert "overlay withhooks has hooks (" in caplog.text and "off until you allow them" in caplog.text


def test_an_allowance_needs_the_user_at_their_terminal(tmp_path, monkeypatch, capsys):
    rig = Rig(tmp_path, MANIFEST)
    # No terminal to ask at (standard input, which a program can drive, does not count).
    assert run_cmd(OverlayAllowHooks, name="guard", digest=rig.digest(), **fields(rig)) == 2
    assert "no terminal" in capsys.readouterr().err
    # A wrong answer allows nothing.
    monkeypatch.setattr(overlays, "_read_tty", lambda prompt: "yes")
    assert run_cmd(OverlayAllowHooks, name="guard", digest=rig.digest(), **fields(rig)) == 2
    assert _overlay_hooks.allowed_digest(rig.store, "guard") is None
    # --no-prompt is for the user's own setup script.
    assert run_cmd(OverlayAllowHooks, name="guard", digest=rig.digest(), no_prompt=True, **fields(rig)) == 0
    assert _overlay_hooks.allowed_digest(rig.store, "guard") == rig.digest()


@pytest.mark.parametrize("marker", ["CLAUDECODE", "CODEX_SANDBOX", "AGENTS_HARNESS"])
def test_an_allowance_is_refused_inside_a_harness_session(tmp_path, monkeypatch, capsys, marker):
    rig = Rig(tmp_path, MANIFEST)
    monkeypatch.setenv(marker, "claude-code" if marker == "AGENTS_HARNESS" else "1")
    monkeypatch.setattr(overlays, "_read_tty", lambda prompt: "allow guard")
    assert run_cmd(OverlayAllowHooks, name="guard", digest=rig.digest(), no_prompt=True, **fields(rig)) == 2
    assert "session" in capsys.readouterr().err
    assert _overlay_hooks.allowed_digest(rig.store, "guard") is None


def test_allow_hooks_is_not_a_tool_an_agent_can_call():
    assert OverlayAllowHooks._mcp_ is False


def test_an_allowance_belongs_to_the_overlay_of_one_store(tmp_path):
    rig = Rig(tmp_path, "[hooks]\nprompt = {}\n")
    rig.allow()
    project_store = tmp_path / "proj" / ".agents"
    write(project_store / "overlays" / "guard" / "overlay.cfg", "[hooks]\nprompt = {}\n")
    write(project_store / "overlays" / "guard" / "hook.py", "")
    from dotagents._overlays import Overlay

    shadow = Overlay(project_store / "overlays" / "guard", project_store)
    assert _overlay_hooks.allowed_digest(rig.store, "guard", shadow.store) is None
    assert _overlay_hooks.overlay_plan(shadow, _overlay_hooks.CLAUDE, rig.store)["on"] is False
