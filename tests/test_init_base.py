"""`init` renders the base AGENTS.md block from `_overlay/dotagents/` for THIS
store (its first line names the file's actual path) and lays down nothing
beside it: no `dotagents/` dir, no design log, no CLAUDE.md, no store-root
README. The recomposes keep the rendered path."""
import logging

import pytest

from dotagents.cli._common import BASE_ROOT, _apply_base, base_agents_text
from dotagents.cli.overlays import OverlayAdd


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "user"))
    monkeypatch.chdir(tmp_path)


def _log():
    lg = logging.getLogger("test-init-base")
    lg.addHandler(logging.NullHandler())
    return lg


def test_base_overlay_is_the_template_the_bundled_cmds_and_the_hooks():
    shipped = sorted(p.relative_to(BASE_ROOT).as_posix() for p in BASE_ROOT.rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts)
    # The block templates (user store, project store) are the only markdown:
    # no design log, no store docs.
    assert [p for p in shipped if p.endswith(".md")] == [
        "dotagents/templates/AGENTS.md", "dotagents/templates/PROJECT.md"]
    others = [p for p in shipped if not p.endswith(".md")]
    assert others and all(
        p.startswith(("dotagents/cmds/", "dotagents/hooks/")) and p.endswith(".py")
        for p in others
    ), others


def test_block_closes_findings_without_a_design_log(tmp_path):
    text = base_agents_text(BASE_ROOT, tmp_path / ".agents")
    assert "DECISIONS" not in text and "design log" not in text
    assert "dotagents findings done -g <name>" in text


def test_block_names_the_actual_agents_md(tmp_path):
    store = tmp_path / "some" / "where" / ".agents"
    text = base_agents_text(BASE_ROOT, store)
    expected = (store.resolve() / "AGENTS.md").as_posix()
    assert "Startup: annotate that you read `%s`." % expected in text
    assert "{{" not in text and "~/.agents/AGENTS.md" not in text
    # A --from base laid out the old way (AGENTS.md at its root) still works.
    old = tmp_path / "oldbase"
    old.mkdir()
    (old / "AGENTS.md").write_text("<!-- dotagents:begin -->\nread `{{AGENTS_MD}}`\n<!-- dotagents:end -->\n", encoding="utf-8")
    assert "read `%s`" % expected in base_agents_text(old, store)


def test_init_writes_the_rendered_block_only(tmp_path):
    store = tmp_path / "user"
    # codex: its write_base_config touches the store only (Claude's would also
    # write the include into the real ~/.claude).
    _apply_base(BASE_ROOT, store, force=False, dry_run=False, logger=_log(), agents=["codex"])
    agents_md = (store / "AGENTS.md").read_text(encoding="utf-8")
    assert "annotate that you read `%s`" % (store.resolve() / "AGENTS.md").as_posix() in agents_md
    assert "~/.agents/AGENTS.md" not in agents_md
    # No dotagents/ until its owner adds a command module; no store-root
    # README, no skeleton CLAUDE.md (nothing read it).
    assert sorted(p.name for p in store.iterdir()) == ["AGENTS.md"]
    # A project store names ITS file, not the user store's.
    project = tmp_path / "proj" / ".agents"
    _apply_base(BASE_ROOT, project, force=False, dry_run=False, logger=_log(), agents=["codex"])
    text = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert "annotate that you read `%s`" % (project.resolve() / "AGENTS.md").as_posix() in text


def test_reinit_leaves_an_existing_dotagents_dir_alone(tmp_path):
    # A store laid down by an older init keeps its files: init neither
    # rewrites nor deletes what it no longer ships.
    store = tmp_path / "user"
    legacy = {
        store / "dotagents" / "DECISIONS.md": "# my decisions\n",
        store / "dotagents" / "README.md": "old notes\n",
        store / "dotagents" / "cmds" / "mine.py": "# my command\n",
    }
    for path, text in legacy.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _apply_base(BASE_ROOT, store, force=False, dry_run=False, logger=_log(), agents=["codex"])
    for path, text in legacy.items():
        assert path.read_text(encoding="utf-8") == text
    assert sorted(p.name for p in (store / "dotagents").iterdir()) == ["DECISIONS.md", "README.md", "cmds"]


def _add_tiny(store, tmp_path, routing="- Tiny -> $TINY_OVERLAY_ROOT/kb/T.md"):
    src = tmp_path / "src" / "tiny"
    src.mkdir(parents=True, exist_ok=True)
    (src / "overlay.toml").write_text('name = "tiny"\nrouting = ["%s"]\n' % routing, encoding="utf-8")
    cmd = OverlayAdd()
    cmd.name, cmd.repo, cmd.global_scope, cmd.agents_dir, cmd.copy, cmd.dry_run = (
        ["tiny"], [str(tmp_path / "src")], True, store, True, False)
    assert cmd() == 0


def test_reinit_keeps_the_installed_overlays_rules(tmp_path):
    # Re-running init rewrote the block from the bare template, dropping every
    # installed overlay's rules and routing until the next `overlays sync`.
    store = tmp_path / "user"
    _apply_base(BASE_ROOT, store, force=False, dry_run=False, logger=_log(), agents=["codex"])
    _add_tiny(store, tmp_path)
    after_add = (store / "AGENTS.md").read_text(encoding="utf-8")
    assert "TINY_OVERLAY_ROOT" in after_add
    _apply_base(BASE_ROOT, store, force=False, dry_run=False, logger=_log(), agents=["codex"])
    assert (store / "AGENTS.md").read_text(encoding="utf-8") == after_add


def test_routing_drops_only_the_placeholder_line(tmp_path):
    # The placeholder regex used to swallow a neighbouring line.
    store = tmp_path / "user"
    _apply_base(BASE_ROOT, store, force=False, dry_run=False, logger=_log(), agents=["codex"])
    _add_tiny(store, tmp_path)
    text = (store / "AGENTS.md").read_text(encoding="utf-8")
    assert "Read the matching file BEFORE such a task" in text
    assert "Nothing ships here by default" not in text


def test_force_keeps_the_original_when_two_adapters_write_it(tmp_path):
    # Codex and Antigravity both force-write <store>/AGENTS.md; the second
    # used to "back up" the first's output over the only copy of the original.
    store = tmp_path / "user"
    store.mkdir()
    (store / "AGENTS.md").write_text("MY PRECIOUS HAND-WRITTEN AGENTS.md\n", encoding="utf-8")
    _apply_base(BASE_ROOT, store, force=True, dry_run=False, logger=_log(), agents=["codex", "antigravity"])
    backups = list((store / "install_backup").rglob("AGENTS.md"))
    assert [p.read_text(encoding="utf-8") for p in backups] == ["MY PRECIOUS HAND-WRITTEN AGENTS.md\n"]


def _init(store, from_=None):
    from dotagents.cli.init import Init

    cmd = Init()
    cmd.dest, cmd.from_, cmd.agents, cmd.no_hooks = store, from_, ["codex"], True
    cmd.dry_run, cmd.force, cmd.bin_dir = False, False, None
    assert cmd() == 0


def test_from_base_is_recorded_and_every_writer_composes_over_it(tmp_path):
    # The overlay commands used to recompose over the bundled base, silently
    # replacing an `init --from` one; a plain re-init did the same.
    from dotagents.cli._common import STORE_CONFIG, read_store_config

    base = tmp_path / "mybase"
    (base / "dotagents" / "templates").mkdir(parents=True)
    (base / "dotagents" / "templates" / "AGENTS.md").write_text(
        "<!-- dotagents:begin -->\n# CUSTOM BASE\nread `{{AGENTS_MD}}`\n\n"
        "## Load on demand\nNothing ships here by default.\n<!-- dotagents:end -->\n",
        encoding="utf-8",
    )
    store = tmp_path / "user"
    _init(store, from_=str(base))
    assert read_store_config(store) == {"base": str(base.resolve())}

    _add_tiny(store, tmp_path)
    text = (store / "AGENTS.md").read_text(encoding="utf-8")
    assert "# CUSTOM BASE" in text and "TINY_OVERLAY_ROOT" in text

    _init(store)  # no --from: the recorded base, not the bundled one
    assert (store / "AGENTS.md").read_text(encoding="utf-8") == text

    plain = tmp_path / "plain"
    _init(plain)
    assert not (plain / STORE_CONFIG).exists() and not (plain / "dotagents").exists()


def test_dry_run_writes_nothing(tmp_path):
    store = tmp_path / "user"
    _apply_base(BASE_ROOT, store, force=False, dry_run=True, logger=_log(), agents=["codex"])
    assert not store.exists()


def test_recompose_keeps_the_rendered_path(tmp_path):
    store = tmp_path / "user"
    _apply_base(BASE_ROOT, store, force=False, dry_run=False, logger=_log(), agents=["codex"])
    _add_tiny(store, tmp_path)
    text = (store / "AGENTS.md").read_text(encoding="utf-8")
    assert "annotate that you read `%s`" % (store.resolve() / "AGENTS.md").as_posix() in text
    assert "{{" not in text and "TINY_OVERLAY_ROOT" in text


def test_project_scope_gets_the_minimal_project_block(tmp_path):
    """A project init copied the whole global block (Permissions, Read-local,
    Global-config misses) into the project store; every session read both, so
    the always-on rules were loaded twice."""
    project = tmp_path / "proj" / ".agents"
    _apply_base(BASE_ROOT, project, force=False, dry_run=False, logger=_log(),
                agents=["codex"], project=True)
    text = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert "# Project agent directives" in text
    assert "**Permissions**" not in text and "Global-config misses" not in text
    assert "annotate that you read `%s`" % (project.resolve() / "AGENTS.md").as_posix() in text


def test_overlays_add_creates_the_block_when_agents_md_is_missing(tmp_path):
    # An `overlays add` into a store without AGENTS.md installed the files and
    # merged the routing nowhere.
    store = tmp_path / "user"
    store.mkdir()
    _add_tiny(store, tmp_path)
    text = (store / "AGENTS.md").read_text(encoding="utf-8")
    assert "<!-- dotagents:begin -->" in text and "TINY_OVERLAY_ROOT" in text
