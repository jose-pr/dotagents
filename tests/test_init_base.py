"""`init` lays the base overlay down from `_overlay/dotagents/`: the AGENTS.md
block is rendered for THIS store (its first line names the file's actual
path), the README lands in `<store>/dotagents/`, and no CLAUDE.md or
store-root README is written. The recomposes keep the rendered path."""
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotagents.cli._common import BASE_ROOT, _apply_base, base_agents_text  # noqa: E402
from dotagents.cli.overlays import OverlayAdd  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    for v in ("AGENTS_HOME", "AGENTS_PROJECT_ROOT"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "user"))
    monkeypatch.chdir(tmp_path)


def _log():
    lg = logging.getLogger("test-init-base")
    lg.addHandler(logging.NullHandler())
    return lg


def test_base_overlay_is_the_dotagents_dir_only():
    shipped = sorted(p.relative_to(BASE_ROOT).as_posix() for p in BASE_ROOT.rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts)
    assert "AGENTS.md" not in shipped and "CLAUDE.md" not in shipped and "README.md" not in shipped
    assert "dotagents/templates/AGENTS.md" in shipped
    assert "dotagents/AGENTS.md" in shipped and "dotagents/README.md" in shipped
    # The notes are notes, not a block: no managed markers, no placeholder.
    notes = (BASE_ROOT / "dotagents" / "AGENTS.md").read_text(encoding="utf-8")
    assert "<!-- dotagents:begin -->" not in notes, "notes, not a managed block"


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


def test_init_writes_the_rendered_block_and_the_dotagents_readme(tmp_path):
    store = tmp_path / "user"
    # codex: its write_base_config touches the store only (Claude's would also
    # write the include into the real ~/.claude).
    _apply_base(BASE_ROOT, store, force=False, dry_run=False, logger=_log(), agents=["codex"])
    agents_md = (store / "AGENTS.md").read_text(encoding="utf-8")
    assert "annotate that you read `%s`" % (store.resolve() / "AGENTS.md").as_posix() in agents_md
    assert "~/.agents/AGENTS.md" not in agents_md
    assert (store / "dotagents" / "README.md").is_file()
    assert (store / "dotagents" / "AGENTS.md").is_file(), "the directory's own notes"
    assert not (store / "dotagents" / "templates").exists(), "the template stays in the package"
    assert not (store / "README.md").exists(), "the README lives in dotagents/, not the store root"
    assert not (store / "CLAUDE.md").exists(), "nothing read the skeleton CLAUDE.md; it is gone"
    # A project store names ITS file, not the user store's.
    project = tmp_path / "proj" / ".agents"
    _apply_base(BASE_ROOT, project, force=False, dry_run=False, logger=_log(), agents=["codex"])
    text = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert "annotate that you read `%s`" % (project.resolve() / "AGENTS.md").as_posix() in text


def test_recompose_keeps_the_rendered_path(tmp_path):
    store = tmp_path / "user"
    _apply_base(BASE_ROOT, store, force=False, dry_run=False, logger=_log(), agents=["codex"])
    src = tmp_path / "src" / "tiny"
    src.mkdir(parents=True)
    (src / "overlay.toml").write_text('name = "tiny"\nrouting = ["- Tiny -> $TINY_OVERLAY_ROOT/kb/T.md"]\n', encoding="utf-8")
    cmd = OverlayAdd()
    cmd.name, cmd.repo, cmd.global_scope, cmd.agents_dir, cmd.copy, cmd.dry_run = (
        ["tiny"], [str(tmp_path / "src")], True, store, True, False)
    assert cmd() == 0
    text = (store / "AGENTS.md").read_text(encoding="utf-8")
    assert "annotate that you read `%s`" % (store.resolve() / "AGENTS.md").as_posix() in text
    assert "{{" not in text and "TINY_OVERLAY_ROOT" in text
