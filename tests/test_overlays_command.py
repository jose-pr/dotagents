"""Tests for the `dotagents overlays` command surface: discovery, add/remove,
skills publish/unpublish, glob filtering, --copy fallback, source resolution, and
the `install --overlays` deprecation shim.

Filesystem-only (tmp_path); no network. Symlink-preferred publish is exercised, but
every assertion also accepts the copy fallback so the suite passes on a Windows box
without symlink privilege.
"""

import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotagents import _overlays, _scope, _skills  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

BEGIN = "<!-- dotagents:begin -->"
END = "<!-- dotagents:end -->"

BASE_AGENTS = (
    BEGIN
    + "\n# Agent Directives\n\n## Always-on rules\n- **Base rule**: keep it.\n\n"
    + "## Load on demand\nNothing ships here by default; overlays add lines here.\n"
    + END
    + "\n"
)


def make_source(root: Path):
    """A source dir holding two overlays: `py-demo` (routing + a skill) and
    `plain` (files only, no manifest)."""
    src = root / "src_overlays"
    # py-demo: manifest with routing, a kb file, and a skill.
    py = src / "py-demo"
    (py / "kb").mkdir(parents=True)
    (py / "kb" / "PY.md").write_text("py kb\n", encoding="utf-8")
    (py / "overlay.toml").write_text(
        'name = "py-demo"\nrouting = [\n  """- Py work -> ~/.agents/kb/PY.md""",\n]\n',
        encoding="utf-8",
    )
    (py / "skills" / "py-lint").mkdir(parents=True)
    (py / "skills" / "py-lint" / "SKILL.md").write_text("lint skill\n", encoding="utf-8")
    # plain: just a file, no manifest, no skills.
    plain = src / "plain"
    plain.mkdir(parents=True)
    (plain / "note.md").write_text("note\n", encoding="utf-8")
    return src


def make_scope(root: Path):
    agents_root = root / "scope_agents"
    agents_root.mkdir(parents=True)
    (agents_root / "AGENTS.md").write_text(BASE_AGENTS, encoding="utf-8")
    return _scope.Scope("user", agents_root)


def logger():
    lg = logging.getLogger("test-overlays")
    lg.addHandler(logging.NullHandler())
    return lg


# --------------------------------------------------------------------------- #
# Source resolution
# --------------------------------------------------------------------------- #

def test_source_resolution_explicit_path(tmp_path):
    src = make_source(tmp_path)
    source = _scope.resolve_source(str(src))
    assert sorted(source.available()) == ["plain", "py-demo"]
    assert source.overlay_dir("py-demo") == src / "py-demo"


def test_source_resolution_env(tmp_path, monkeypatch):
    src = make_source(tmp_path)
    monkeypatch.setenv(_scope.SOURCE_ENV, str(src))
    source = _scope.resolve_source(None)
    assert "py-demo" in source.available()


def test_source_resolution_bundled_default(tmp_path, monkeypatch):
    # No explicit source, no env -> the bundled overlays/ (repo checkout).
    monkeypatch.delenv(_scope.SOURCE_ENV, raising=False)
    source = _scope.resolve_source(None)
    # The repo's real overlays include these names.
    avail = source.available()
    assert "python" in avail and "flows" in avail


def test_source_unknown_name_errors(tmp_path):
    src = make_source(tmp_path)
    source = _scope.resolve_source(str(src))
    with pytest.raises(SystemExit):
        source.overlay_dir("nope")


# --------------------------------------------------------------------------- #
# Scope + discovery
# --------------------------------------------------------------------------- #

def test_resolve_scope_global_is_user(tmp_path):
    scope = _scope.resolve_scope(True, agents_dir=tmp_path / "a")
    assert scope.level == "user"
    assert scope.agents_root == tmp_path / "a"


def test_resolve_scope_default_is_project(tmp_path):
    scope = _scope.resolve_scope(False, project_root=tmp_path / "proj")
    assert scope.level == "project"
    assert scope.agents_root == tmp_path / "proj" / ".agents"


def test_discover_overlays_by_presence(tmp_path):
    scope = make_scope(tmp_path)
    assert _scope.discover_overlays(scope) == []
    (scope.overlay_root / "alpha").mkdir(parents=True)
    (scope.overlay_root / "beta").mkdir()
    (scope.overlay_root / ".hidden").mkdir()
    assert _scope.discover_overlays(scope) == ["alpha", "beta"]


def test_glob_filter():
    names = ["python", "py-demo", "node", "rust"]
    assert _scope.filter_names(names, "py*") == ["python", "py-demo"]
    assert _scope.filter_names(names, None) == names
    assert _scope.filter_names(names, "*") == names


# --------------------------------------------------------------------------- #
# install_overlay_dir + rules merge
# --------------------------------------------------------------------------- #

def test_install_overlay_dir_copies_files_and_manifest(tmp_path):
    src = make_source(tmp_path)
    dest = tmp_path / "dest" / "py-demo"
    copied, skipped, _lines = _overlays.install_overlay_dir(src / "py-demo", dest, False)
    assert (dest / "kb" / "PY.md").is_file()
    assert (dest / "overlay.toml").is_file()
    assert copied >= 2 and skipped == 0


def test_install_overlay_dir_no_clobber(tmp_path):
    src = make_source(tmp_path)
    dest = tmp_path / "dest" / "py-demo"
    _overlays.install_overlay_dir(src / "py-demo", dest, False)
    # Hand-edit an installed file; re-install must not clobber it.
    (dest / "kb" / "PY.md").write_text("EDITED\n", encoding="utf-8")
    copied, skipped, _ = _overlays.install_overlay_dir(src / "py-demo", dest, False)
    assert (dest / "kb" / "PY.md").read_text(encoding="utf-8") == "EDITED\n"
    assert copied == 0 and skipped >= 1


def test_merge_overlay_rules_into_agents_md(tmp_path):
    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    agents_md = scope.agents_root / "AGENTS.md"
    changed = _overlays.merge_overlay_rules(agents_md, src / "py-demo", False, logger())
    assert changed
    text = agents_md.read_text(encoding="utf-8")
    assert "~/.agents/kb/PY.md" in text
    # Content outside the managed block is untouched; block still terminated.
    assert text.count(END) == 1


def test_merge_overlay_rules_noop_when_no_contributions(tmp_path):
    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    agents_md = scope.agents_root / "AGENTS.md"
    assert _overlays.merge_overlay_rules(agents_md, src / "plain", False, logger()) is False


# --------------------------------------------------------------------------- #
# Skills publish / unpublish / resync / clean
# --------------------------------------------------------------------------- #

def _is_published(target: Path) -> bool:
    return os.path.islink(str(target)) or target.is_dir()


def test_publish_overlay_skills(tmp_path):
    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    n = _skills.publish_overlay_skills(src / "py-demo", scope.shared_skills_dir, logger=logger())
    assert n == 1
    target = scope.shared_skills_dir / "py-lint"
    assert _is_published(target)
    # The skill content is reachable through the publish (symlink or copy).
    assert (target / "SKILL.md").is_file()


def test_publish_copy_fallback(tmp_path):
    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    n = _skills.publish_overlay_skills(
        src / "py-demo", scope.shared_skills_dir, copy=True, logger=logger()
    )
    assert n == 1
    target = scope.shared_skills_dir / "py-lint"
    # copy=True must never leave a symlink.
    assert not os.path.islink(str(target))
    assert target.is_dir() and (target / "SKILL.md").is_file()


def test_publish_no_skills_is_noop(tmp_path):
    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    assert _skills.publish_overlay_skills(src / "plain", scope.shared_skills_dir) == 0


def test_remove_overlay_skills_only_own(tmp_path):
    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    _skills.publish_overlay_skills(src / "py-demo", scope.shared_skills_dir, copy=True)
    # An unrelated skill placed by hand must survive removal.
    other = scope.shared_skills_dir / "user-skill"
    other.mkdir()
    (other / "x.md").write_text("x\n", encoding="utf-8")
    removed = _skills.remove_overlay_skills(
        src / "py-demo", scope.shared_skills_dir, logger=logger()
    )
    assert removed == 1
    assert not (scope.shared_skills_dir / "py-lint").exists()
    assert other.is_dir()  # untouched


def test_clean_broken_syncs(tmp_path):
    shared = tmp_path / "skills"
    shared.mkdir()
    src_skill = tmp_path / "real-skill"
    src_skill.mkdir()
    link = shared / "linked"
    try:
        os.symlink(str(src_skill), str(link), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    # Delete the source -> the symlink is now broken and must be swept.
    import shutil as _sh
    _sh.rmtree(str(src_skill))
    _skills.clean_broken_syncs(shared, logger=logger())
    assert not os.path.lexists(str(link))


def test_resync_republishes_missing(tmp_path):
    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    # Nothing published yet -> resync falls back to a fresh publish.
    _skills.resync_overlay_skills(src / "py-demo", scope.shared_skills_dir, logger=logger())
    assert _is_published(scope.shared_skills_dir / "py-lint")


# --------------------------------------------------------------------------- #
# End-to-end via the Cmd classes
# --------------------------------------------------------------------------- #

def _run(cmd_cls, **kwargs):
    # duho's `_logger_` is a read-only property resolving to
    # logging.getLogger(self._parsername_); we just set the data fields and call.
    cmd = cmd_cls()
    for k, v in kwargs.items():
        setattr(cmd, k, v)
    return cmd()


def test_cmd_add_then_remove_roundtrip(tmp_path):
    from dotagents.cli import OverlayAdd, OverlayRemove

    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    rc = _run(
        OverlayAdd, name=["py-demo"], source=str(src), global_scope=True,
        agents_dir=scope.agents_root, copy=True, dry_run=False,
    )
    assert rc == 0
    assert (scope.overlay_root / "py-demo" / "kb" / "PY.md").is_file()
    assert (scope.shared_skills_dir / "py-lint").is_dir()
    assert "~/.agents/kb/PY.md" in (scope.agents_root / "AGENTS.md").read_text(encoding="utf-8")

    rc = _run(
        OverlayRemove, name=["py-demo"], global_scope=True,
        agents_dir=scope.agents_root, dry_run=False,
    )
    assert rc == 0
    assert not (scope.overlay_root / "py-demo").exists()
    assert not (scope.shared_skills_dir / "py-lint").exists()


def test_cmd_add_dry_run_writes_nothing(tmp_path):
    from dotagents.cli import OverlayAdd

    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    _run(
        OverlayAdd, name=["py-demo"], source=str(src), global_scope=True,
        agents_dir=scope.agents_root, copy=True, dry_run=True,
    )
    assert not (scope.overlay_root / "py-demo").exists()
    assert not (scope.shared_skills_dir / "py-lint").exists() if scope.shared_skills_dir.exists() else True


def test_cmd_sync_glob_filter(tmp_path):
    from dotagents.cli import OverlayAdd, OverlaySync

    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    _run(OverlayAdd, name=["py-demo", "plain"], source=str(src), global_scope=True,
         agents_dir=scope.agents_root, copy=True, dry_run=False)
    # A glob that matches only py-demo; a run that touches only it must succeed.
    rc = _run(OverlaySync, pattern="py*", source=str(src), global_scope=True,
              agents_dir=scope.agents_root, copy=True, dry_run=False)
    assert rc == 0


# --------------------------------------------------------------------------- #
# Deprecation shim: `install --overlays` warns + still applies.
# --------------------------------------------------------------------------- #

def test_install_overlays_deprecation_warns_and_applies(tmp_path, caplog):
    from dotagents.cli import Install

    # A minimal base overlay to install from.
    base = tmp_path / "base"
    base.mkdir()
    (base / "AGENTS.md").write_text(BASE_AGENTS, encoding="utf-8")
    src = make_source(tmp_path)
    dest = tmp_path / "dest"

    cmd = Install()
    cmd.from_ = str(base)
    cmd.dest = dest
    cmd.overlays = [str(src / "plain")]
    cmd.dry_run = False
    # duho's `_logger_` resolves to logging.getLogger(cmd._parsername_) == "install".
    with caplog.at_level(logging.WARNING, logger="install"):
        rc = cmd()
    assert rc == 0
    assert any("deprecated" in r.message.lower() for r in caplog.records)
    # The overlay's file still landed (flat-copy legacy behavior preserved).
    assert (dest / "note.md").is_file()
