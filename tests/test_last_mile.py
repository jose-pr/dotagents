"""The last mile: does the config `init` writes actually reach a harness?

Review 2026-09-09 (finding 1.5): `init` wrote `<store>/CLAUDE.md`, which Claude
Code never reads, and `context` subtracted the store's AGENTS.md as "already
loaded" on a static assumption. These tests pin the fix -- the include `init`
writes where Claude reads it, the live include-based subtraction, and
`--write-agent` landing in the harness's real file as a managed block -- plus
the managed-block merge rules it relies on.

tmp dirs only. tests/conftest.py points HOME/USERPROFILE at a tmp dir and
clears AGENTS_HOME for every test; the `home` fixture below additionally
places that home under this test's tmp_path, with a `.agents` store in it.
"""

import json
import logging
import os
from pathlib import Path

import pytest

from dotagents import _agents, _hooks, _merge
from dotagents._fs import write_text_lf

BASE = "<!-- dotagents:begin -->\nBASE RULES\n<!-- dotagents:end -->\n"


@pytest.fixture
def home(tmp_path, monkeypatch):
    fake = tmp_path / "home"
    (fake / ".agents").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE" if os.name == "nt" else "HOME", str(fake))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake))
    return fake


def _log():
    lg = logging.getLogger("test-last-mile")
    lg.addHandler(logging.NullHandler())
    return lg


# --------------------------------------------------------------------------
# init writes the include where Claude reads it
# --------------------------------------------------------------------------

def test_user_scope_init_writes_the_claude_include(home, tmp_path):
    store = home / ".agents"
    _agents.ClaudeAgent().write_base_config(
        store, tmp_path, BASE, force=False, dry_run=False, logger=_log()
    )
    entry = home / ".claude" / "CLAUDE.md"
    assert entry.is_file()
    assert "@../.agents/AGENTS.md" in entry.read_text(encoding="utf-8")


def test_user_scope_include_is_skipped_when_hand_written(home, tmp_path):
    """The hand-wired setup this repo's own machine carries must be left alone."""
    store = home / ".agents"
    entry = home / ".claude" / "CLAUDE.md"
    write_text_lf(entry, "@../.agents/AGENTS.md\n\n# my own rules\n")
    before = entry.read_text(encoding="utf-8")
    _agents.ClaudeAgent().write_base_config(
        store, tmp_path, BASE, force=False, dry_run=False, logger=_log()
    )
    assert entry.read_text(encoding="utf-8") == before


def test_project_scope_init_writes_the_project_include(home, tmp_path):
    project = tmp_path / "proj"
    dest = project / ".agents"
    dest.mkdir(parents=True)
    _agents.ClaudeAgent().write_base_config(
        dest, tmp_path, BASE, force=False, dry_run=False, logger=_log()
    )
    entry = project / ".claude" / "CLAUDE.md"
    assert entry.is_file()
    assert "@../.agents/AGENTS.md" in entry.read_text(encoding="utf-8")
    assert not (home / ".claude").exists(), "project scope must not touch the user config"


def test_custom_store_is_still_the_user_scope(home, tmp_path, monkeypatch):
    """`init -g` with `$AGENTS_HOME` elsewhere: the include and the hooks go to
    `~/.claude`, not to `<store-parent>/.claude/settings.local.json`."""
    store = tmp_path / "elsewhere" / "agents"
    store.mkdir(parents=True)
    monkeypatch.setenv("AGENTS_HOME", str(store))
    agent = _agents.ClaudeAgent()
    agent.write_base_config(store, tmp_path, BASE, force=False, dry_run=False, logger=_log())
    entry = home / ".claude" / "CLAUDE.md"
    assert entry.is_file()
    assert "@" + (store / "AGENTS.md").as_posix() in entry.read_text(encoding="utf-8")
    agent.wire_hooks(store, dry_run=False, logger=_log())
    assert (home / ".claude" / "settings.json").is_file()
    assert not (store.parent / ".claude").exists()


def test_dry_run_writes_no_include(home, tmp_path):
    _agents.ClaudeAgent().write_base_config(
        home / ".agents", tmp_path, BASE, force=False, dry_run=True, logger=_log()
    )
    assert not (home / ".claude").exists()


# --------------------------------------------------------------------------
# loaded_paths follows real includes
# --------------------------------------------------------------------------

def test_loaded_paths_follow_includes_recursively(home, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    write_text_lf(home / ".claude" / "CLAUDE.md", "@../.agents/AGENTS.md\n")
    write_text_lf(home / ".agents" / "AGENTS.md", "@kb/MORE.md\n")
    write_text_lf(home / ".agents" / "kb" / "MORE.md", "more")
    write_text_lf(project / ".claude" / "CLAUDE.md", "@../.agents/AGENTS.md\n")
    write_text_lf(project / ".agents" / "AGENTS.md", "proj")
    loaded = _agents.ClaudeAgent().loaded_paths(project)
    for expected in (
        home / ".agents" / "AGENTS.md",
        home / ".agents" / "kb" / "MORE.md",
        project / ".agents" / "AGENTS.md",
    ):
        assert expected.resolve() in loaded


# --------------------------------------------------------------------------
# --write-agent lands in the harness's real file, as a managed block
# --------------------------------------------------------------------------

def test_claude_reads_a_root_agents_md_only_while_no_claude_file_exists(home, tmp_path, monkeypatch):
    """Claude reads AGENTS.md as a fallback. A project-scope `init` creates
    `.claude/CLAUDE.md`; after that a static "AGENTS.md" entry subtracted a
    committed root AGENTS.md that reached neither Claude nor `context`."""
    # Bound the upward walk at this test's own dir (conftest bounds it at
    # pytest's base temp dir), so only the files below decide the answer.
    monkeypatch.setattr(_agents.ClaudeAgent, "_walk_stop", tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    write_text_lf(project / "AGENTS.md", "ROOT-AGENTS-MD\n")
    claude = _agents.ClaudeAgent()
    root_md = (project / "AGENTS.md").resolve()
    assert root_md in claude.loaded_paths(project), "no CLAUDE file: Claude reads it"

    write_text_lf(project / ".claude" / "CLAUDE.md", "@../.agents/AGENTS.md\n")
    assert root_md not in claude.loaded_paths(project), "CLAUDE file present: context must carry it"

    # ...unless the CLAUDE file includes it.
    write_text_lf(project / ".claude" / "CLAUDE.md", "@../.agents/AGENTS.md\n@../AGENTS.md\n")
    assert root_md in claude.loaded_paths(project)


def test_write_context_merges_a_block_into_the_project_file(tmp_path):
    project = tmp_path / "proj"
    write_text_lf(project / "AGENTS.md", "# The project's own AGENTS.md\n")
    codex = _agents.CodexAgent()
    codex.write_context(project, "CTX ONE", force=False, dry_run=False, logger=_log())
    codex.write_context(project, "CTX TWO", force=False, dry_run=False, logger=_log())
    text = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith("# The project's own AGENTS.md")   # user content kept, first
    assert "CTX TWO" in text and "CTX ONE" not in text         # refreshed, not appended
    assert text.count(_merge.CONTEXT_BEGIN_MARKER) == 1
    assert b"\r" not in (project / "AGENTS.md").read_bytes()


@pytest.mark.parametrize("agent_cls,rel", [
    (_agents.ClaudeAgent, ".claude/CLAUDE.md"),
    (_agents.GeminiAgent, "GEMINI.md"),
    (_agents.CursorAgent, ".cursor/rules/dotagents.mdc"),
    (_agents.CopilotAgent, ".github/copilot-instructions.md"),
    (_agents.AntigravityAgent, ".agents/rules/dotagents.md"),
    (_agents.PiAgent, ".pi/APPEND_SYSTEM.md"),
])
def test_write_context_targets_per_harness(tmp_path, agent_cls, rel):
    project = tmp_path / "proj"
    project.mkdir()
    agent_cls().write_context(project, "CTX", force=False, dry_run=False, logger=_log())
    assert (project / rel).is_file()
    assert "CTX" in (project / rel).read_text(encoding="utf-8")


def test_cli_write_agent_uses_the_project_root(tmp_path, monkeypatch):
    from dotagents.cli.context import Context

    store = tmp_path / "store"
    project = tmp_path / "proj"
    (project / ".agents").mkdir(parents=True)
    write_text_lf(store / "AGENTS.md", "# STORE RULES\n")
    monkeypatch.setenv("AGENTS_HOME", str(store))
    monkeypatch.setenv("AGENTS_PROJECT_ROOT", str(project))
    cmd = Context()
    cmd.agents = ["codex"]
    cmd.write_agent = True
    assert cmd() == 0
    assert "# STORE RULES" in (project / "AGENTS.md").read_text(encoding="utf-8")
    assert (store / "AGENTS.md").read_text(encoding="utf-8") == "# STORE RULES\n", (
        "the store's AGENTS.md is a context SOURCE and must never be overwritten"
    )


def test_cli_rejects_bad_format_and_conflicting_flags():
    from dotagents.cli.context import Context

    cmd = Context()
    cmd.format = "md"
    with pytest.raises(SystemExit, match="--format"):
        cmd()
    cmd = Context()
    cmd.format = "json"
    cmd.write_agent = True
    with pytest.raises(SystemExit, match="--write-agent"):
        cmd()
    cmd = Context()
    cmd.write_agent = True
    cmd.global_scope = True
    with pytest.raises(SystemExit, match="cannot take -g"):
        cmd()


# --------------------------------------------------------------------------
# merge_block rules the above relies on
# --------------------------------------------------------------------------

def test_merge_matches_marker_lines_not_prose_mentions(tmp_path):
    """The base AGENTS.md itself says "everything between the
    `<!-- dotagents:begin -->` / `<!-- dotagents:end -->` markers is managed";
    a substring match replaced that sentence with a second block."""
    target = tmp_path / "AGENTS.md"
    write_text_lf(
        target,
        "Intro mentions `<!-- dotagents:begin -->` and `<!-- dotagents:end -->` inline.\n\n"
        "<!-- dotagents:begin -->\nOLD\n<!-- dotagents:end -->\n\nMine.\n",
    )
    assert _merge.merge_block(target, BASE) == "block-refreshed"
    text = target.read_text(encoding="utf-8")
    assert text.count("<!-- dotagents:begin -->") == 2  # the mention + the one block
    assert "BASE RULES" in text and "OLD" not in text
    assert text.startswith("Intro mentions")
    assert text.endswith("Mine.\n")


def test_merge_refuses_a_begin_without_end(tmp_path):
    target = tmp_path / "AGENTS.md"
    write_text_lf(target, "<!-- dotagents:begin -->\nhalf\n")
    with pytest.raises(SystemExit, match="no .* after it"):
        _merge.merge_block(target, BASE)


def test_merge_ignores_marker_lines_inside_code_fences(tmp_path):
    """A fenced example of the BEGIN line paired with the real END and the
    user text between them was replaced."""
    target = tmp_path / "CLAUDE.md"
    write_text_lf(
        target,
        "# my notes\n\n```md\n<!-- dotagents:begin -->\n```\n\nIMPORTANT USER TEXT\n\n"
        "<!-- dotagents:begin -->\n@old\n<!-- dotagents:end -->\n",
    )
    assert _merge.merge_block(target, BASE, append=True) == "block-refreshed"
    text = target.read_text(encoding="utf-8")
    assert "IMPORTANT USER TEXT" in text and "@old" not in text and "BASE RULES" in text
    assert text.startswith("# my notes\n\n```md\n<!-- dotagents:begin -->\n```\n")


def test_merge_sees_a_block_behind_a_utf8_bom(tmp_path):
    target = tmp_path / "AGENTS.md"
    target.write_bytes(b"\xef\xbb\xbf<!-- dotagents:begin -->\nOLD\n<!-- dotagents:end -->\n\nMine.\n")
    assert _merge.merge_block(target, BASE) == "block-refreshed"
    data = target.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    assert data.count(b"<!-- dotagents:begin -->") == 1 and b"Mine." in data


def test_force_backups_mirror_paths_and_never_overwrite(tmp_path):
    store = tmp_path / "store"
    root = _merge.timestamped_backup_root(store)
    assert _merge.timestamped_backup_root(store) == root, "one root per store per run"
    target = store / "AGENTS.md"
    write_text_lf(target, "ORIGINAL\n")
    assert _merge.merge_block(target, BASE, force=True, backup_root=root) == "replaced (--force, backed up)"
    # A second adapter force-writing other text in the same run: the backup keeps the original.
    other = BASE.replace("BASE RULES", "OTHER")
    assert _merge.merge_block(target, other, force=True, backup_root=root) == "replaced (--force)"
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == "ORIGINAL\n"
    assert _merge.merge_block(target, other, force=True, backup_root=root) == "unchanged"
    # A same-named file outside the store is mirrored elsewhere, not over it.
    outside = tmp_path / "pi" / "AGENTS.md"
    mirrored = _merge._backup_path(outside, root)
    assert mirrored != root / "AGENTS.md"
    assert mirrored.parts[-2:] == ("pi", "AGENTS.md")
    assert mirrored.relative_to(root).parts[0] == "external"


def test_merge_reports_a_markerless_base_as_a_usage_error(tmp_path):
    with pytest.raises(SystemExit, match="managed block"):
        _merge.merge_block(tmp_path / "x.md", "no markers here\n")


def test_merge_writes_lf_only(tmp_path):
    target = tmp_path / "AGENTS.md"
    _merge.merge_block(target, BASE)
    assert b"\r" not in target.read_bytes()
    write_text_lf(target, "mine\n")
    _merge.merge_block(target, BASE)
    assert b"\r" not in target.read_bytes()


def test_write_settings_is_lf_atomic_and_keeps_unicode(tmp_path):
    path = tmp_path / "settings.json"
    _hooks.write_settings(path, {"note": "café", "hooks": {}})
    raw = path.read_bytes()
    assert b"\r" not in raw and "café".encode("utf-8") in raw
    assert json.loads(raw.decode("utf-8"))["note"] == "café"
    assert not list(tmp_path.glob("settings.json.*.tmp"))


def test_unknown_explicit_agent_does_not_override_a_pinned_identity():
    env = {"AGENTS_HARNESS": "codex", "AGENT": "codex", "AGENTS_VENDOR": "openai"}
    assert _agents.stamp_identity(env, explicit="typo", root=Path("/nonexistent")) == {}
