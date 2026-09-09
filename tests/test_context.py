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
from dotagents._scope import Scope  # noqa: E402


def S(agents_dir, project_root, global_scope=False):
    """The walk's scope from the old (agents_dir, project_root, global_scope) triple."""
    return Scope.of(agents_dir=agents_dir, project_root=project_root, global_scope=global_scope)


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
    # Priority order is the REVERSE of alphabetical order on purpose: with
    # alpha=100/zeta=900 the old test passed even while priority sorting was
    # silently broken (alphabetical happened to give the same answer).
    early = ov / "zeta"
    early.mkdir()
    (early / "CONTEXT.md").write_text("ZETA-CONTEXT root=<ZETA_OVERLAY_ROOT>", encoding="utf-8")
    (early / "overlay.toml").write_text('name = "zeta"\npriority = 100\n', encoding="utf-8")
    late = ov / "alpha"
    late.mkdir()
    (late / "CONTEXT.md").write_text("ALPHA-CONTEXT", encoding="utf-8")
    (late / "overlay.toml").write_text('name = "alpha"\npriority = 900\n', encoding="utf-8")

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
    """Claude loads the store's AGENTS.md only through an `@` include in
    `~/.claude/CLAUDE.md` -- so `context` subtracts it exactly when that
    include is really there (`ClaudeAgent.loaded_paths` reads the entry files),
    not on the old static assumption. `Path.home()` is redirected to a fake
    home carrying both the store and the include, so `~/.claude/CLAUDE.md`
    genuinely resolves to the fixture's file, rather than the real machine's."""
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
    monkeypatch.delenv("AGENTS_HOME", raising=False)

    claude = _agents.ClaudeAgent()

    # Fresh install, no include yet: NOTHING loads the store's AGENTS.md, so
    # `context` must emit it (the old static assumption dropped it here, and
    # the base rules never reached a session -- review 2026-09-09, 1.5).
    text = _context.assemble_context(claude, S(dotagents_dir, project_root, True))
    assert "# User rules" in text

    # With the include `init` writes, the harness loads it -> subtracted.
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / "CLAUDE.md").write_text("@../.agents/AGENTS.md\n", encoding="utf-8")
    text = _context.assemble_context(claude, S(dotagents_dir, project_root, True))
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

    text = _context.assemble_context(codex, S(agents_dir, project_root, True))
    assert "# User rules" in text, (
        "a same-named file OUTSIDE project_root must not be wrongly suppressed"
    )

    # Now put a real AGENTS.md AT project_root -- THAT one must be suppressed.
    (project_root / "AGENTS.md").write_text("# Project root rules\n", encoding="utf-8")
    text2 = _context.assemble_context(codex, S(agents_dir, project_root, True))
    assert "# Project root rules" not in text2, "the actual project-root file IS the harness load"
    assert "# User rules" in text2, "the unrelated same-named file is still not suppressed"


def test_non_claude_agent_keeps_agents_md(layout):
    agents_dir, project_root = layout
    # Gemini's harness_loads is GEMINI.md only, so the user AGENTS.md is NOT
    # subtracted for Gemini -- it appears.
    gemini = _agents.GeminiAgent()
    text = _context.assemble_context(gemini, S(agents_dir, project_root, True))
    assert "# User rules" in text


# --------------------------------------------------------------------------
# On-demand inlining (bare + backticked)
# --------------------------------------------------------------------------

def test_inlines_bare_and_backticked_refs(layout):
    agents_dir, project_root = layout
    gemini = _agents.GeminiAgent()  # keeps AGENTS.md so the refs are present
    text = _context.assemble_context(gemini, S(agents_dir, project_root, True), inline=True)
    assert "PYTHON-KB-BODY" in text   # bare "read kb/PYTHON.md"
    assert "GIT-KB-BODY" in text      # backticked `kb/GIT.md`
    assert "On-Demand Files (Inlined)" in text


def test_inlining_is_opt_in(layout):
    """The base rule is "read the matching file BEFORE such a task, never
    preemptively"; inlining every mention made a 100 KB SessionStart payload."""
    agents_dir, project_root = layout
    gemini = _agents.GeminiAgent()
    text = _context.assemble_context(gemini, S(agents_dir, project_root, True))
    assert "read kb/PYTHON.md" in text          # the pointer is still there
    assert "PYTHON-KB-BODY" not in text         # the body is not
    assert "On-Demand Files (Inlined)" not in text


def test_inlining_never_double_sends_sources_or_harness_files(layout):
    agents_dir, project_root = layout
    (agents_dir / "AGENTS.md").write_text(
        "# User rules\nsee `CLAUDE.md`, .agents/AGENTS.md and kb/GIT.md\n", encoding="utf-8"
    )
    (project_root / "CLAUDE.md").write_text("CLAUDE-ENTRY", encoding="utf-8")
    (project_root / ".agents" / "AGENTS.md").write_text("PROJECT-RULES", encoding="utf-8")
    gemini = _agents.GeminiAgent()
    text = _context.assemble_context(gemini, S(agents_dir, project_root, False), inline=True)
    assert "GIT-KB-BODY" in text
    assert text.count("PROJECT-RULES") == 1       # a source, emitted once
    assert "CLAUDE-ENTRY" not in text             # a harness entry file, never inlined


def test_overlay_placeholders_expand(layout):
    """`<NAME_OVERLAY_ROOT>` names the same dir `env` emits for the overlay --
    it never expanded before, because the resolver labels overlay entries by
    NAME and the code compared against the literal "overlay"."""
    agents_dir, project_root = layout
    gemini = _agents.GeminiAgent()
    text = _context.assemble_context(gemini, S(agents_dir, project_root, True))
    assert "<ZETA_OVERLAY_ROOT>" not in text
    assert "root=%s" % (agents_dir / "overlays" / "zeta") in text


def test_project_overlay_shadows_the_store_copy_in_context(layout):
    agents_dir, project_root = layout
    pov = project_root / ".agents" / "overlays" / "zeta"   # same name as the store's
    pov.mkdir(parents=True)
    (pov / "CONTEXT.md").write_text("ZETA-FROM-PROJECT", encoding="utf-8")
    gemini = _agents.GeminiAgent()
    text = _context.assemble_context(gemini, S(agents_dir, project_root, False))
    assert "ZETA-FROM-PROJECT" in text and "ZETA-CONTEXT" not in text
    assert "ALPHA-CONTEXT" in text  # an unshadowed store overlay still contributes


def test_project_scope_overlays_are_context_sources(layout):
    """`overlays add` installs into the PROJECT scope by default; its CONTEXT.md
    must be a source (only the store's overlays were walked before)."""
    agents_dir, project_root = layout
    pov = project_root / ".agents" / "overlays" / "projov"
    pov.mkdir(parents=True)
    (pov / "CONTEXT.md").write_text("PROJECT-OVERLAY-CONTEXT", encoding="utf-8")
    gemini = _agents.GeminiAgent()
    assert "PROJECT-OVERLAY-CONTEXT" in _context.assemble_context(gemini, S(agents_dir, project_root, False))
    assert "PROJECT-OVERLAY-CONTEXT" not in _context.assemble_context(gemini, S(agents_dir, project_root, True))


# --------------------------------------------------------------------------
# Skills: listed, not inlined
# --------------------------------------------------------------------------

def test_skills_listed_not_inlined(layout):
    agents_dir, project_root = layout
    gemini = _agents.GeminiAgent()
    text = _context.assemble_context(gemini, S(agents_dir, project_root, True))
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
    text = _context.assemble_context(gemini, S(agents_dir, project_root, True))
    # zeta (priority 100) must appear before alpha (priority 900) despite zeta
    # sorting later alphabetically -- proves priority, not name, drives order.
    assert text.index("ZETA-CONTEXT") < text.index("ALPHA-CONTEXT")


def test_default_priority_is_500():
    assert _overlays.DEFAULT_PRIORITY == 500


def test_manifest_reports_priority(tmp_path):
    ov = tmp_path / "ov"
    ov.mkdir()
    (ov / "overlay.toml").write_text('name = "ov"\npriority = 42\n', encoding="utf-8")
    assert _overlays.Overlay(ov).read_manifest()["priority"] == 42
    assert _overlays.Overlay(ov).priority == 42
    # Missing priority -> default.
    ov2 = tmp_path / "ov2"
    ov2.mkdir()
    (ov2 / "overlay.toml").write_text('name = "ov2"\n', encoding="utf-8")
    assert _overlays.Overlay(ov2).read_manifest()["priority"] == _overlays.DEFAULT_PRIORITY
    assert _overlays.Overlay(ov2).priority == _overlays.DEFAULT_PRIORITY


# --------------------------------------------------------------------------
# JSON payload shape
# --------------------------------------------------------------------------

def test_json_payload_shape(layout):
    agents_dir, project_root = layout
    gemini = _agents.GeminiAgent()
    data = _context.assemble_context_data(gemini, S(agents_dir, project_root, True), inline=True)
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
    data = _context.assemble_context_data(gemini, S(agents_dir, project_root, True))
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
