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
# Overlay setup scripts (plan 06)
# --------------------------------------------------------------------------- #

def _add_setup_overlay(src: Path, name: str, body: str):
    """Add an overlay `name` under source `src` shipping a `setup.py` with `body`.

    A `setup.py` runs under the current interpreter cross-platform (no `sh`
    needed), so these tests are Windows-safe. `body` is Python appended after a
    small preamble exposing the marker path helpers."""
    o = src / name
    o.mkdir(parents=True)
    (o / "file.md").write_text("f\n", encoding="utf-8")
    preamble = (
        "import os, sys\n"
        "agents = os.environ['DOTAGENTS_AGENTS_DIR']\n"
        "overlay = os.environ.get('DOTAGENTS_OVERLAY_DIR', os.getcwd())\n"
    )
    (o / "setup.py").write_text(preamble + body, encoding="utf-8")
    return o


def test_setup_runs_on_add(tmp_path):
    from dotagents.cli import OverlayAdd

    src = tmp_path / "src_overlays"
    # setup writes a marker inside the store, proving it ran with the right env.
    _add_setup_overlay(
        src, "with-setup",
        "open(os.path.join(agents, 'SETUP_RAN'), 'w').write('ok')\n",
    )
    scope = make_scope(tmp_path)
    rc = _run(OverlayAdd, name=["with-setup"], source=str(src), global_scope=True,
              agents_dir=scope.agents_root, copy=True, dry_run=False)
    assert rc == 0
    assert (scope.agents_root / "SETUP_RAN").is_file()


def test_setup_cwd_is_installed_overlay_dir(tmp_path):
    from dotagents.cli import OverlayAdd

    src = tmp_path / "src_overlays"
    # The marker lands via a *relative* path -> proves cwd is the installed dir,
    # and its content is DOTAGENTS_OVERLAY_DIR -> proves that env var is set.
    _add_setup_overlay(
        src, "cwd-demo",
        "open('MARKER', 'w').write(overlay)\n",
    )
    scope = make_scope(tmp_path)
    _run(OverlayAdd, name=["cwd-demo"], source=str(src), global_scope=True,
         agents_dir=scope.agents_root, copy=True, dry_run=False)
    marker = scope.overlay_root / "cwd-demo" / "MARKER"
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8") == str(scope.overlay_root / "cwd-demo")


def test_add_without_setup_is_fine(tmp_path):
    from dotagents.cli import OverlayAdd

    src = make_source(tmp_path)  # py-demo / plain ship no setup script
    scope = make_scope(tmp_path)
    rc = _run(OverlayAdd, name=["plain"], source=str(src), global_scope=True,
              agents_dir=scope.agents_root, copy=True, dry_run=False)
    assert rc == 0
    assert (scope.overlay_root / "plain" / "note.md").is_file()


def test_no_setup_flag_skips(tmp_path):
    from dotagents.cli import OverlayAdd

    src = tmp_path / "src_overlays"
    _add_setup_overlay(
        src, "with-setup",
        "open(os.path.join(agents, 'SETUP_RAN'), 'w').write('ok')\n",
    )
    scope = make_scope(tmp_path)
    rc = _run(OverlayAdd, name=["with-setup"], source=str(src), global_scope=True,
              agents_dir=scope.agents_root, copy=True, dry_run=False, no_setup=True)
    assert rc == 0
    # Overlay installed, but the setup marker must NOT exist.
    assert (scope.overlay_root / "with-setup" / "file.md").is_file()
    assert not (scope.agents_root / "SETUP_RAN").exists()


def test_setup_nonzero_exit_surfaces_error(tmp_path):
    from dotagents.cli import OverlayAdd

    src = tmp_path / "src_overlays"
    _add_setup_overlay(src, "bad-setup", "sys.exit(3)\n")
    scope = make_scope(tmp_path)
    with pytest.raises(SystemExit):
        _run(OverlayAdd, name=["bad-setup"], source=str(src), global_scope=True,
             agents_dir=scope.agents_root, copy=True, dry_run=False)


def test_setup_dry_run_does_not_run(tmp_path):
    from dotagents.cli import OverlayAdd

    src = tmp_path / "src_overlays"
    _add_setup_overlay(
        src, "with-setup",
        "open(os.path.join(agents, 'SETUP_RAN'), 'w').write('ok')\n",
    )
    scope = make_scope(tmp_path)
    _run(OverlayAdd, name=["with-setup"], source=str(src), global_scope=True,
         agents_dir=scope.agents_root, copy=True, dry_run=True)
    assert not (scope.agents_root / "SETUP_RAN").exists()


def test_setup_runs_on_sync(tmp_path):
    from dotagents.cli import OverlayAdd, OverlaySync

    src = tmp_path / "src_overlays"
    # Idempotent-style setup: append a line each run so we can count invocations.
    _add_setup_overlay(
        src, "with-setup",
        "open(os.path.join(agents, 'RUNS'), 'a').write('x')\n",
    )
    scope = make_scope(tmp_path)
    _run(OverlayAdd, name=["with-setup"], source=str(src), global_scope=True,
         agents_dir=scope.agents_root, copy=True, dry_run=False)
    _run(OverlaySync, pattern="with-setup", source=str(src), global_scope=True,
         agents_dir=scope.agents_root, copy=True, dry_run=False)
    # Ran once on add + once on sync.
    assert (scope.agents_root / "RUNS").read_text(encoding="utf-8") == "xx"




# --------------------------------------------------------------------------- #
# Priority-ordered merge (plan 02 / D68)
# --------------------------------------------------------------------------- #

def _make_rules_overlay(src_root: Path, name: str, marker: str, priority=None):
    """An overlay contributing one always-on rule (`- **<marker>...`) and one
    routing line, each carrying `marker` so merged order is observable. `priority`
    is omitted from the manifest when None (exercises the DEFAULT_PRIORITY path)."""
    ov = src_root / name
    ov.mkdir(parents=True, exist_ok=True)
    (ov / "rules.md").write_text(
        "- **%s rule**: contributed by %s.\n" % (marker, name), encoding="utf-8"
    )
    toml = 'name = "%s"\n' % name
    if priority is not None:
        toml += "priority = %d\n" % priority
    toml += 'rules = ["rules.md"]\n'
    toml += 'routing = ["""- %s route -> ~/.agents/kb/%s.md"""]\n' % (marker, marker)
    (ov / "overlay.toml").write_text(toml, encoding="utf-8")
    return ov


def test_overlay_sort_key_default_priority_when_absent(tmp_path):
    ov = _make_rules_overlay(tmp_path, "no-prio", "NP")  # no priority key
    prio, name = _overlays.overlay_sort_key(ov)
    assert prio == _overlays.DEFAULT_PRIORITY
    assert name == "no-prio"


def test_sort_overlays_by_priority_orders_low_first_regardless_of_input(tmp_path):
    hi = _make_rules_overlay(tmp_path, "zeta", "HI", priority=900)   # sorts last
    lo = _make_rules_overlay(tmp_path, "alpha", "LO", priority=100)  # sorts first
    mid = _make_rules_overlay(tmp_path, "mid", "MID")               # default 500
    # Feed in a deliberately unsorted order.
    ordered = _overlays.sort_overlays_by_priority([hi, mid, lo])
    assert [p.name for p in ordered] == ["alpha", "mid", "zeta"]


def test_sort_overlays_name_tiebreaker_on_equal_priority(tmp_path):
    b = _make_rules_overlay(tmp_path, "bravo", "B", priority=300)
    a = _make_rules_overlay(tmp_path, "alfa", "A", priority=300)
    ordered = _overlays.sort_overlays_by_priority([b, a])
    assert [p.name for p in ordered] == ["alfa", "bravo"]


def test_compose_block_orders_multiple_overlays_by_priority(tmp_path):
    from dotagents.cli import _compose_block

    hi = _make_rules_overlay(tmp_path, "zeta", "HI", priority=900)
    lo = _make_rules_overlay(tmp_path, "alpha", "LO", priority=100)
    mid = _make_rules_overlay(tmp_path, "mid", "MID")  # default 500

    # Regardless of input order, low priority lands earlier, high later.
    for order in ([hi, lo, mid], [mid, hi, lo], [lo, mid, hi]):
        out = _compose_block(BASE_AGENTS, order, logger())
        # Rules: the LO rule must precede MID which must precede HI.
        assert out.index("LO rule") < out.index("MID rule") < out.index("HI rule")
        # Routing likewise.
        assert out.index("LO route") < out.index("MID route") < out.index("HI route")
        # Base's own rule still leads the always-on section.
        assert out.index("Base rule") < out.index("LO rule")


def test_recompose_block_positions_high_priority_last_across_adds(tmp_path):
    """Single-`add` positioning: add LO first, then HI. HI (priority 900) must land
    AFTER LO (priority 100) in the block even though it was added later -- recompose
    reorders by (priority, name), never mere append order."""
    from dotagents.cli import OverlayAdd

    src = tmp_path / "src_overlays"
    _make_rules_overlay(src, "alpha", "LO", priority=100)
    _make_rules_overlay(src, "zeta", "HI", priority=900)
    scope = make_scope(tmp_path)

    _run(OverlayAdd, name=["alpha"], source=str(src), global_scope=True,
         agents_dir=scope.agents_root, copy=True, dry_run=False)
    _run(OverlayAdd, name=["zeta"], source=str(src), global_scope=True,
         agents_dir=scope.agents_root, copy=True, dry_run=False)

    text = (scope.agents_root / "AGENTS.md").read_text(encoding="utf-8")
    assert text.index("LO rule") < text.index("HI rule")
    assert text.index("LO route") < text.index("HI route")
    assert text.count(END) == 1


def test_recompose_block_high_priority_added_first_still_sorts_last(tmp_path):
    """The reverse order: add HI first, then LO. LO must still precede HI."""
    from dotagents.cli import OverlayAdd

    src = tmp_path / "src_overlays"
    _make_rules_overlay(src, "alpha", "LO", priority=100)
    _make_rules_overlay(src, "zeta", "HI", priority=900)
    scope = make_scope(tmp_path)

    _run(OverlayAdd, name=["zeta"], source=str(src), global_scope=True,
         agents_dir=scope.agents_root, copy=True, dry_run=False)
    _run(OverlayAdd, name=["alpha"], source=str(src), global_scope=True,
         agents_dir=scope.agents_root, copy=True, dry_run=False)

    text = (scope.agents_root / "AGENTS.md").read_text(encoding="utf-8")
    assert text.index("LO rule") < text.index("HI rule")
    assert text.index("LO route") < text.index("HI route")


def test_existing_bundled_overlay_manifests_still_parse(tmp_path):
    """Every shipped overlay.toml parses unchanged and reports default priority
    (none declares `priority` today) -- the field is optional and backward-compatible."""
    repo_overlays = Path(__file__).resolve().parents[1] / "overlays"
    manifests = sorted(repo_overlays.glob("*/overlay.toml"))
    assert manifests, "expected bundled overlays to exist"
    for manifest in manifests:
        parsed = _overlays.read_manifest(manifest.parent)
        assert isinstance(parsed["priority"], int)
        # None of the shipped overlays sets priority yet -> all default.
        assert parsed["priority"] == _overlays.DEFAULT_PRIORITY
