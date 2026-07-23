"""Characterization tests for the context generator (plan 04).

Covers: harness_loads subtraction (no double-send), on-demand inlining of both
bare and backticked refs, skills listed-not-inlined, overlay priority ordering
from the manifest, and the JSON payload shape.

Run from the repo root: ``python -m pytest tests/``.
"""

import json
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

def test_harness_loads_subtracts_user_agents_md(layout):
    agents_dir, project_root = layout
    claude = _agents.ClaudeAgent()
    text = _context.assemble_context(claude, agents_dir, project_root, global_scope=True)
    # Claude's harness already loads ~/.agents/AGENTS.md and per-dir AGENTS.md, so
    # the user AGENTS.md at agents_dir must NOT be re-emitted as a source block.
    assert "# User rules" not in text
    # But the overlays (never loaded by the harness) ARE emitted.
    assert "ALPHA-CONTEXT" in text
    assert "ZETA-CONTEXT" in text


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
