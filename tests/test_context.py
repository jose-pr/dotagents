"""Characterization tests for the context generator (plan 04).

Covers: harness_loads subtraction (no double-send), on-demand inlining of both
bare and backticked refs, skills listed-not-inlined, overlay priority ordering
from the manifest, and the JSON payload shape.

Run from the repo root: ``python -m pytest tests/``.
"""

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from dotagents import _agents, _context, _overlays  # noqa: E402


# --------------------------------------------------------------------------
# Fixture: a small agents_dir (overlays + skills) and a project_root.
# --------------------------------------------------------------------------

@pytest.fixture
def layout(tmp_path):
    agents_dir = tmp_path / "agents"
    project_root = tmp_path / "proj"
    (agents_dir).mkdir()
    (project_root / ".agents").mkdir(parents=True)

    # User-level AGENTS.md points at an on-demand kb file (bare AND backticked).
    (agents_dir / "AGENTS.md").write_text(
        "# User rules\nread kb/PYTHON.md before python work.\n"
        "Also see `kb/GIT.md` for git.\n",
        encoding="utf-8",
    )
    kb = agents_dir / "kb"
    kb.mkdir()
    (kb / "PYTHON.md").write_text("PYTHON-KB-BODY", encoding="utf-8")
    (kb / "GIT.md").write_text("GIT-KB-BODY", encoding="utf-8")

    # Two overlays with CONTEXT.md and differing priority.
    ov = agents_dir / "overlays"
    ov.mkdir()
    late = ov / "zeta"
    late.mkdir()
    (late / "CONTEXT.md").write_text("ZETA-CONTEXT", encoding="utf-8")
    (late / "overlay.toml").write_text('name = "zeta"\npriority = 900\n', encoding="utf-8")
    early = ov / "alpha"
    early.mkdir()
    (early / "CONTEXT.md").write_text("ALPHA-CONTEXT", encoding="utf-8")
    (early / "overlay.toml").write_text('name = "alpha"\npriority = 100\n', encoding="utf-8")

    # A skill (must be LISTED, never inlined).
    skills = agents_dir / "skills"
    (skills / "myskill").mkdir(parents=True)
    (skills / "myskill" / "SKILL.md").write_text(
        "---\nname: myskill\ndescription: does a thing\n---\nSKILL-BODY-SECRET\n",
        encoding="utf-8",
    )

    return agents_dir, project_root


# --------------------------------------------------------------------------
# Harness-loads subtraction (no double-send)
# --------------------------------------------------------------------------

def test_harness_loads_subtracts_user_agents_md(layout, monkeypatch):
    """Claude's harness_loads has TWO entries: the absolute `~/.agents/AGENTS.md`
    and the relative `AGENTS.md` (meaning "at the project root"). This test
    covers the absolute form -- `Path.home()` is redirected to the isolated
    `agents_dir`'s parent so `~/.agents/AGENTS.md` genuinely resolves to the
    fixture's file, rather than the real machine's `~/.agents/AGENTS.md`
    (which does not exist in this tmp_path and would silently make the
    absolute-form branch a no-op, leaving only the relative form to explain a
    pass -- exactly the bug this test previously masked, see
    test_relative_harness_load_matches_project_root_only below)."""
    agents_dir, project_root = layout
    # `~/.agents/AGENTS.md` must resolve to agents_dir/AGENTS.md exactly. No
    # symlink (this machine can't create one without elevation, confirmed
    # elsewhere) -- and `agents_dir` must literally be named ".agents" for the
    # `home() / ".agents"` join in the code under test to land on it. The
    # `layout` fixture names it "agents", not ".agents", so this test builds
    # a dotted-name copy of the fixture data instead of reusing `agents_dir`.
    #
    # `monkeypatch.setattr(Path, "home", ...)` alone does NOT work here:
    # `Path.expanduser()` (what `_context.py` calls on `~/.agents/AGENTS.md`)
    # does not go through `Path.home()` -- both independently call
    # `self._flavour.gethomedir(...)`, so overriding the `home` classmethod
    # leaves `expanduser()` still reading the REAL machine home. Confirmed by
    # direct test: with only `Path.home` patched, `Path("~/.agents/AGENTS.md")
    # .expanduser()` still resolved to the real machine home, not the patched one.
    # The actual fix is the env var `gethomedir` reads -- USERPROFILE on
    # Windows, HOME on POSIX.
    fake_home = agents_dir.parent / "fakehome"
    dotagents_dir = fake_home / ".agents"
    dotagents_dir.mkdir(parents=True)
    for item in agents_dir.iterdir():
        dest = dotagents_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)
    home_var = "USERPROFILE" if os.name == "nt" else "HOME"
    monkeypatch.setenv(home_var, str(fake_home))

    claude = _agents.ClaudeAgent()
    text = _context.assemble_context(claude, dotagents_dir, project_root, global_scope=True)
    # Claude's harness already loads ~/.agents/AGENTS.md, so it must NOT be
    # re-emitted as a source block.
    assert "# User rules" not in text
    # But the overlays (never loaded by the harness) ARE emitted.
    assert "ALPHA-CONTEXT" in text
    assert "ZETA-CONTEXT" in text


def test_relative_harness_load_matches_project_root_only(layout):
    """Regression: a RELATIVE harness_loads entry (e.g. Codex's "AGENTS.md")
    must resolve against project_root and match by full path, not by bare
    filename anywhere in the source list. The old code did `path.name == hl`
    with no directory check, so Codex's "AGENTS.md" entry wrongly suppressed
    `~/.agents/AGENTS.md` (a user-store file Codex's harness never reads) purely
    because both files happened to be named "AGENTS.md" -- confirmed live:
    `dotagents context --agents codex` emitted an empty `sources: []` even with
    a real, non-empty ~/.agents/AGENTS.md present.

    Here, agents_dir/AGENTS.md ("# User rules") is NOT at project_root, so a
    relative harness_loads entry must NOT suppress it.
    """
    agents_dir, project_root = layout
    codex = _agents.CodexAgent()
    assert codex.harness_loads == ["AGENTS.md"]  # relative, no ~/ or / prefix

    text = _context.assemble_context(codex, agents_dir, project_root, global_scope=True)
    assert "# User rules" in text, (
        "a same-named file OUTSIDE project_root must not be wrongly suppressed"
    )

    # Now put a real AGENTS.md AT project_root -- THAT one must be suppressed.
    (project_root / "AGENTS.md").write_text("# Project root rules\n", encoding="utf-8")
    text2 = _context.assemble_context(codex, agents_dir, project_root, global_scope=True)
    assert "# Project root rules" not in text2, "the actual project-root file IS the harness load"
    assert "# User rules" in text2, "the unrelated same-named file is still not suppressed"


def test_non_claude_agent_keeps_agents_md(layout):
    agents_dir, project_root = layout
    # Gemini's harness_loads is GEMINI.md only, so the user AGENTS.md is NOT
    # subtracted for Gemini -- it appears.
    gemini = _agents.GeminiAgent()
    text = _context.assemble_context(gemini, agents_dir, project_root, global_scope=True)
    assert "# User rules" in text


# --------------------------------------------------------------------------
# On-demand inlining (bare + backticked)
# --------------------------------------------------------------------------

def test_inlines_bare_and_backticked_refs(layout):
    agents_dir, project_root = layout
    gemini = _agents.GeminiAgent()  # keeps AGENTS.md so the refs are present
    text = _context.assemble_context(gemini, agents_dir, project_root, global_scope=True)
    assert "PYTHON-KB-BODY" in text   # bare "read kb/PYTHON.md"
    assert "GIT-KB-BODY" in text      # backticked `kb/GIT.md`
    assert "On-Demand Files (Inlined)" in text


# --------------------------------------------------------------------------
# Skills: listed, not inlined
# --------------------------------------------------------------------------

def test_skills_listed_not_inlined(layout):
    agents_dir, project_root = layout
    gemini = _agents.GeminiAgent()
    text = _context.assemble_context(gemini, agents_dir, project_root, global_scope=True)
    assert "Available Skills (Opt-in)" in text
    assert "myskill" in text
    assert "does a thing" in text
    assert "SKILL-BODY-SECRET" not in text  # body never inlined


# --------------------------------------------------------------------------
# Overlay priority ordering (from the manifest)
# --------------------------------------------------------------------------

def test_overlay_priority_orders_by_manifest(layout):
    agents_dir, project_root = layout
    gemini = _agents.GeminiAgent()
    text = _context.assemble_context(gemini, agents_dir, project_root, global_scope=True)
    # alpha (priority 100) must appear before zeta (priority 900) despite alpha
    # sorting later alphabetically -- proves priority, not name, drives order.
    assert text.index("ALPHA-CONTEXT") < text.index("ZETA-CONTEXT")


def test_default_priority_is_500():
    assert _overlays.DEFAULT_PRIORITY == 500


def test_manifest_reports_priority(tmp_path):
    ov = tmp_path / "ov"
    ov.mkdir()
    (ov / "overlay.toml").write_text('name = "ov"\npriority = 42\n', encoding="utf-8")
    assert _overlays.read_manifest(ov)["priority"] == 42
    # Missing priority -> default.
    ov2 = tmp_path / "ov2"
    ov2.mkdir()
    (ov2 / "overlay.toml").write_text('name = "ov2"\n', encoding="utf-8")
    assert _overlays.read_manifest(ov2)["priority"] == _overlays.DEFAULT_PRIORITY


# --------------------------------------------------------------------------
# JSON payload shape
# --------------------------------------------------------------------------

def test_json_payload_shape(layout):
    agents_dir, project_root = layout
    gemini = _agents.GeminiAgent()
    data = _context.assemble_context_data(gemini, agents_dir, project_root, global_scope=True)
    # Round-trips as JSON.
    json.dumps(data)
    assert data["agent"] == "gemini"
    assert data["harness"] == "gemini-cli"
    assert isinstance(data["sources"], list) and data["sources"]
    assert "PYTHON-KB-BODY" in data["context"]           # inlining in the text field
    assert {"name": "myskill", "description": "does a thing"} in data["skills"]
    assert "SKILL-BODY-SECRET" not in data["context"]    # skills stay out of context


def test_json_context_excludes_skills_listing(layout):
    agents_dir, project_root = layout
    gemini = _agents.GeminiAgent()
    data = _context.assemble_context_data(gemini, agents_dir, project_root, global_scope=True)
    # The skills listing markdown heading is NOT baked into the context field
    # (skills are a separate structured field in JSON).
    assert "Available Skills (Opt-in)" not in data["context"]


def test_stdout_survives_a_cp1252_console(tmp_path, monkeypatch):
    """Regression: `dotagents context` died with UnicodeEncodeError on Windows.

    A bare `print()` encodes with the console codepage (cp1252 by default), so one
    character outside Latin-1 -- here U+2194, which really appeared in a live
    context -- aborted the command with no output. This is the SessionStart hook's
    payload, so the failure was silent and total.
    """
    import io
    import sys

    from dotagents.cli.context import _write_stdout

    raw = io.BytesIO()

    class Cp1252Stdout(io.TextIOWrapper):
        pass

    monkeypatch.setattr(
        sys, "stdout", Cp1252Stdout(raw, encoding="cp1252", errors="strict")
    )
    _write_stdout("arrows \u2194 and an emoji \U0001f600\n")

    assert "\u2194" in raw.getvalue().decode("utf-8")
