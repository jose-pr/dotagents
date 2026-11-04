"""Tests for the `dotagents overlays` command surface: discovery, add/remove,
skills publish/unpublish, glob filtering, --copy fallback, source resolution,
setup scripts, and the priority-ordered merge.

Filesystem-only (tmp_path); no network. Symlink-preferred publish is exercised, but
every assertion also accepts the copy fallback so the suite passes on a Windows box
without symlink privilege.
"""

import logging
import os
from pathlib import Path

import pytest

from dotagents import _aos, _overlays, _scope, _skills


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


# --------------------------------------------------------------------------- #
# Overlay: name rules (valid / normalized / env var) and the instance surface
# --------------------------------------------------------------------------- #

Overlay = _overlays.Overlay


@pytest.mark.parametrize(
    "name",
    ["flows", "private-sync", "my_overlay", "net", "My-Overlay", "foo.bar", "v1.2"],
)
def test_valid_overlay_names(name):
    # A dot mid-name is allowed (foo.bar, v1.2); only a LEADING dot is excluded.
    assert Overlay.is_valid_name(name)


@pytest.mark.parametrize("name", [".git", ".hidden", "__pycache__", "2fast", ""])
def test_invalid_overlay_names(name):
    # Leading dot / underscore / digit are all excluded.
    assert not Overlay.is_valid_name(name)


def test_normalize_name():
    """THE canonical overlay name (install dir + env var derive from it)."""
    assert Overlay.normalize_name("My_Overlay") == "my-overlay"
    assert Overlay.normalize_name("my-overlay") == "my-overlay"
    assert Overlay.normalize_name("NET") == "net"
    # A dot normalizes to `-` like `_`: `v1.2` and `v1-2` are one overlay.
    assert Overlay.normalize_name("v1.2") == "v1-2"


def test_root_var_derives_from_normalized_name():
    """Two spellings that install to the same `overlays/<n>/` get the same var,
    and the var for an install dir is always root_var_for(dir.name)."""
    assert Overlay.root_var_for("my-overlay") == "MY_OVERLAY_OVERLAY_ROOT"
    assert Overlay.root_var_for("My_Overlay") == "MY_OVERLAY_OVERLAY_ROOT"
    assert Overlay.root_var_for("net") == "NET_OVERLAY_ROOT"
    # A dot is legal in the dir name but not in a shell variable -> `_`.
    assert Overlay.root_var_for("v1.2") == "V1_2_OVERLAY_ROOT"
    for raw in ("My_Overlay", "my-overlay"):
        installed = Overlay.normalize_name(raw)
        assert Overlay.root_var_for(raw) == Overlay.root_var_for(installed)


@pytest.mark.parametrize("name", ["flows", "my_overlay", "foo.bar", "v1.2", "a-b_c.d"])
def test_root_var_of_valid_name_is_identifier(name):
    assert Overlay.is_valid_name(name)
    assert Overlay.root_var_for(name).isidentifier()


def test_overlay_instance_surface(tmp_path):
    """`.path`/`.name`/`.normalized_name`/`.root_var`/`.is_valid`/`.manifest_path`
    are all derived from the directory; nothing is read at construction."""
    d = tmp_path / "My_Ov.v2"
    ov = Overlay(d)  # dir does not exist yet
    assert ov.path == d
    assert ov.name == "My_Ov.v2"
    assert ov.normalized_name == "my-ov-v2"
    assert ov.root_var == "MY_OV_V2_OVERLAY_ROOT"
    assert ov.is_valid
    assert ov.manifest_path == d / "overlay.cfg"
    assert ov.read_manifest()["priority"] == _overlays.DEFAULT_PRIORITY
    assert ov.priority == _overlays.DEFAULT_PRIORITY
    assert ov.setup_variant() is None
    # Files written after construction are seen (no caching).
    d.mkdir()
    (d / "overlay.cfg").write_text('name = "ov"\npriority = 7\n', encoding="utf-8")
    assert ov.priority == 7
    assert ov.sort_key == (7, "ov", "My_Ov.v2")  # priority, manifest name, dir name
    # os.PathLike: usable wherever a path is, and Overlay(Overlay) is identity.
    assert Path(ov) == d
    assert Overlay(ov) == ov
    assert not Overlay(tmp_path / ".git").is_valid


def test_overlay_discover(tmp_path):
    overlays = tmp_path / "overlays"
    for d in ("flows", "my_overlay", ".git", "__pycache__"):
        (overlays / d).mkdir(parents=True)
    (overlays / "README.md").write_text("x", encoding="utf-8")  # a file, not a dir
    found = Overlay.discover(overlays)
    assert [o.name for o in found] == ["flows", "my_overlay"]
    assert all(isinstance(o, Overlay) for o in found)
    assert Overlay.discover(tmp_path / "absent") == []


def make_source(root: Path):
    """A source dir holding two overlays: `py-demo` (routing + a skill) and
    `plain` (files only, no manifest)."""
    src = root / "src_overlays"
    # py-demo: manifest with routing, a kb file, and a skill.
    py = src / "py-demo"
    (py / "kb").mkdir(parents=True)
    (py / "kb" / "PY.md").write_text("py kb\n", encoding="utf-8")
    (py / "overlay.cfg").write_text(
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

def test_source_resolution_explicit_repo(tmp_path):
    src = make_source(tmp_path)
    source = _scope.resolve_source([str(src)])
    assert sorted(n for n in source.available() if n in ("plain", "py-demo")) == ["plain", "py-demo"]
    assert source.overlay_dir("py-demo") == src / "py-demo"


def test_source_resolution_env_default_repo(tmp_path, monkeypatch):
    src = make_source(tmp_path)
    monkeypatch.setenv("AGENTS_OVERLAYS_REPO", str(src))
    source = _scope.resolve_source(None)
    assert "py-demo" in source.available()


def test_source_resolution_keyed_env_repo_beats_the_default(tmp_path, monkeypatch):
    src = make_source(tmp_path)
    other = tmp_path / "other"
    (other / "py-demo").mkdir(parents=True)
    (other / "py-demo" / "overlay.cfg").write_text('name = "py-demo"\n', encoding="utf-8")
    monkeypatch.setenv("AGENTS_OVERLAYS_REPO", str(src))
    monkeypatch.setenv("AGENTS_OVERLAYS_REPO_A", str(other))
    assert _scope.resolve_source(None).overlay_dir("py-demo") == other / "py-demo"


def test_source_resolution_no_bundled_errors(tmp_path, monkeypatch):
    # main ships no bundled overlays/ (they live on the `repo` branch, D77),
    # and this test env packages none. With no repo anywhere (conftest clears
    # every AGENTS_OVERLAYS_REPO* var), resolve_source must fail with a clear
    # "no overlay source" error, not silently pick nothing.
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "empty-store"))
    with pytest.raises(SystemExit, match="no overlay source"):
        _scope.resolve_source(None)


def test_source_unknown_name_errors(tmp_path):
    src = make_source(tmp_path)
    source = _scope.resolve_source([str(src)])
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
    assert Overlay.discover(scope.overlay_root) == []
    (scope.overlay_root / "alpha").mkdir(parents=True)
    (scope.overlay_root / "beta").mkdir()
    (scope.overlay_root / ".hidden").mkdir()
    assert [o.name for o in Overlay.discover(scope.overlay_root)] == ["alpha", "beta"]


def test_installed_is_user_plus_project_with_project_shadowing(tmp_path):
    """The ONE two-scope discovery: user store first, then the project's; a
    same-named project overlay replaces the store's copy; no project store
    (`-g`) = the user store alone."""
    store = tmp_path / "store"
    proj = tmp_path / "proj" / ".agents"
    for d in ("store/overlays/common", "store/overlays/only-user",
              "proj/.agents/overlays/common", "proj/.agents/overlays/only-proj"):
        (tmp_path / d).mkdir(parents=True)
    both = Overlay.installed(store, proj)
    assert [(o.name, o.store) for o in both] == [
        ("only-user", store), ("common", proj), ("only-proj", proj),
    ]
    assert [o.path for o in both if o.name == "common"] == [proj / "overlays" / "common"]
    assert [(o.name, o.store) for o in Overlay.installed(store)] == [
        ("common", store), ("only-user", store),
    ]
    # Variadic: any number of stores, a None is skipped, the LAST wins a name.
    third = tmp_path / "third"
    (third / "overlays" / "common").mkdir(parents=True)
    names = [(o.name, o.store) for o in Overlay.installed(store, None, proj, third)]
    assert names == [("only-user", store), ("only-proj", proj), ("common", third)]
    assert Overlay.installed() == []


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
    copied, skipped, _lines = Overlay(src / "py-demo").install_to(dest, False)
    assert (dest / "kb" / "PY.md").is_file()
    assert (dest / "overlay.cfg").is_file()
    assert copied >= 2 and skipped == 0


def test_reinstall_makes_the_install_match_the_source(tmp_path):
    src = make_source(tmp_path)
    dest = tmp_path / "dest" / "py-demo"
    backups = tmp_path / "backup"
    Overlay(src / "py-demo").install_to(dest, False)
    # A hand edit is replaced by the source's file, the edit backed up first.
    (dest / "kb" / "PY.md").write_text("EDITED\n", encoding="utf-8")
    result = Overlay(src / "py-demo").install_to(dest, False, backup_root=backups)
    assert (dest / "kb" / "PY.md").read_text(encoding="utf-8") == (src / "py-demo" / "kb" / "PY.md").read_text(encoding="utf-8")
    assert result.replaced == ["kb/PY.md"] and (backups / "kb" / "PY.md").read_text(encoding="utf-8") == "EDITED\n"
    assert result.written == 1


def test_overlay_files_are_its_members_manifest_included(tmp_path):
    """AOS: the manifest is a member like any other file; caches are not."""
    src = make_source(tmp_path)
    ov = Overlay(src / "py-demo")
    (ov.path / "__pycache__").mkdir()
    (ov.path / "__pycache__" / "x.pyc").write_bytes(b"")
    names = {p.relative_to(ov.path).as_posix() for p in ov.files()}
    assert "kb/PY.md" in names
    assert "overlay.cfg" in names
    assert not any("__pycache__" in n for n in names)


def test_recompose_merges_overlay_routing_into_agents_md(tmp_path):
    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    agents_md = scope.agents_root / "AGENTS.md"
    agents_md.write_text("# mine\n\n" + BASE_AGENTS + "\ntrailing note\n", encoding="utf-8")
    changed = _overlays.recompose_overlay_block(
        agents_md, BASE_AGENTS, [src / "py-demo"], False, logger()
    )
    assert changed
    text = agents_md.read_text(encoding="utf-8")
    assert "~/.agents/kb/PY.md" in text
    # Content outside the managed block is untouched; block still terminated.
    assert text.startswith("# mine\n\n") and text.endswith("\ntrailing note\n")
    assert text.count(END) == 1
    # A second run over the same set is a no-op: nothing is appended twice.
    assert not _overlays.recompose_overlay_block(
        agents_md, BASE_AGENTS, [src / "py-demo"], False, logger()
    )


def test_recompose_is_a_noop_when_no_overlay_contributes(tmp_path):
    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    agents_md = scope.agents_root / "AGENTS.md"
    assert _overlays.recompose_overlay_block(
        agents_md, BASE_AGENTS, [src / "plain"], False, logger()
    ) is False
    assert agents_md.read_text(encoding="utf-8") == BASE_AGENTS


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


def test_unpublish_keeps_a_user_edited_copy(tmp_path):
    """`_paths_match` compared file NAMES only, so a copy the user had edited
    (same file set, different bytes) was deleted as "ours"."""
    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    _skills.publish_overlay_skills(src / "py-demo", scope.shared_skills_dir, copy=True)
    target = scope.shared_skills_dir / "py-lint" / "SKILL.md"
    target.write_text(target.read_text(encoding="utf-8") + "\nMY EDIT\n", encoding="utf-8")
    removed = _skills.remove_overlay_skills(src / "py-demo", scope.shared_skills_dir, logger=logger())
    assert removed == 0
    assert "MY EDIT" in target.read_text(encoding="utf-8")


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


def test_publish_keeps_a_skill_the_user_placed(tmp_path):
    """A conflicting target used to be force-replaced (rmtree'd): a skill the
    user wrote under the same name was deleted without a backup."""
    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    mine = scope.shared_skills_dir / "py-lint"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("MY OWN SKILL\n", encoding="utf-8")
    n = _skills.publish_overlay_skills(src / "py-demo", scope.shared_skills_dir, copy=True, logger=logger())
    assert n == 0
    assert (mine / "SKILL.md").read_text(encoding="utf-8") == "MY OWN SKILL\n"


def test_resync_keeps_a_locally_edited_copy_unless_overwrite(tmp_path):
    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    _skills.publish_overlay_skills(src / "py-demo", scope.shared_skills_dir, copy=True)
    target = scope.shared_skills_dir / "py-lint"
    (target / "notes.md").write_text("MY NOTES\n", encoding="utf-8")
    assert _skills.resync_overlay_skills(src / "py-demo", scope.shared_skills_dir, copy=True, logger=logger()) == 0
    assert (target / "notes.md").is_file()
    assert _skills.resync_overlay_skills(
        src / "py-demo", scope.shared_skills_dir, copy=True, overwrite=True, logger=logger()) == 1
    assert not (target / "notes.md").exists()


def test_resync_refreshes_an_unedited_copy_when_the_source_changes(tmp_path):
    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    _skills.publish_overlay_skills(src / "py-demo", scope.shared_skills_dir, copy=True)
    skill_md = src / "py-demo" / "skills" / "py-lint" / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\nUPSTREAM CHANGE\n", encoding="utf-8")
    assert _skills.resync_overlay_skills(src / "py-demo", scope.shared_skills_dir, copy=True, logger=logger()) == 1
    target_md = scope.shared_skills_dir / "py-lint" / "SKILL.md"
    assert "UPSTREAM CHANGE" in target_md.read_text(encoding="utf-8")
    # The record follows the refresh: a second edit-free sync is a no-op.
    assert _skills.resync_overlay_skills(src / "py-demo", scope.shared_skills_dir, copy=True, logger=logger()) == 0


def test_overlay_files_skip_vcs_metadata_and_caches(tmp_path):
    """A whole-repository overlay (a git spec with no #path) copied its clone's
    .git into the store."""
    ov = tmp_path / "whole"
    for rel in ("overlay.cfg", "kb/X.md", "references/.gitignore", ".git/HEAD",
                ".git/objects/ab/cdef", "sub/.git", "__pycache__/m.cpython-314.pyc",
                "lib/m.pyc", ".mypy_cache/x.json", "node_modules/p/index.js"):
        p = ov / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n", encoding="utf-8")
    got = [p.relative_to(ov).as_posix() for p in Overlay(ov).files()]
    assert got == ["kb/X.md", "overlay.cfg", "references/.gitignore"]


def test_remove_deletes_read_only_files(tmp_path):
    """Read-only files (git objects on Windows) made `overlays remove` fail
    half-way, leaving a half-deleted overlay."""
    import stat
    from dotagents.cli import OverlayAdd, OverlayRemove

    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    assert _run(OverlayAdd, name=["py-demo"], repo=[str(src)], global_scope=True,
                agents_dir=scope.agents_root, copy=True, dry_run=False) == 0
    ro = scope.overlay_root / "py-demo" / "kb" / "PY.md"
    os.chmod(str(ro), stat.S_IREAD)
    assert _run(OverlayRemove, name=["py-demo"], global_scope=True,
                agents_dir=scope.agents_root, dry_run=False) == 0
    assert not (scope.overlay_root / "py-demo").exists()


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
        OverlayAdd, name=["py-demo"], repo=[str(src)], global_scope=True,
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
    agents_md = scope.agents_root / "AGENTS.md"
    before = agents_md.read_bytes()
    rc = _run(
        OverlayAdd, name=["py-demo"], repo=[str(src)], global_scope=True,
        agents_dir=scope.agents_root, copy=True, dry_run=True,
    )
    assert rc == 0
    assert not (scope.overlay_root / "py-demo").exists()
    assert not (scope.shared_skills_dir / "py-lint").exists()
    assert agents_md.read_bytes() == before, "a dry run must not recompose the block"


def test_cmd_sync_glob_filter(tmp_path):
    from dotagents.cli import OverlayAdd, OverlaySync

    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    _run(OverlayAdd, name=["py-demo", "plain"], repo=[str(src)], global_scope=True,
         agents_dir=scope.agents_root, copy=True, dry_run=False)
    # A new file upstream in BOTH overlays; a glob matching only py-demo must
    # bring it to py-demo alone.
    for name in ("py-demo", "plain"):
        (src / name / "NEW.md").write_text("new\n", encoding="utf-8")
    rc = _run(OverlaySync, pattern="py*", repo=[str(src)], global_scope=True,
              agents_dir=scope.agents_root, copy=True, dry_run=False)
    assert rc == 0
    assert (scope.overlay_root / "py-demo" / "NEW.md").is_file()
    assert not (scope.overlay_root / "plain" / "NEW.md").exists()


# --------------------------------------------------------------------------- #
# Overlay setup scripts
# --------------------------------------------------------------------------- #

def _add_setup_overlay(src: Path, name: str, body: str):
    """Add an overlay `name` under source `src` shipping a `setup.py` with `body`.

    The manifest declares it (`[[setup]]`, interpreter `python`): a file named
    setup.py means nothing by its name. It runs under the current interpreter
    cross-platform (no `sh` needed), so these tests are Windows-safe. `body`
    is Python appended after a small preamble exposing the marker path helpers."""
    o = src / name
    o.mkdir(parents=True)
    (o / "file.md").write_text("f\n", encoding="utf-8")
    (o / "overlay.cfg").write_text('[[setup]]\ninterpreter = "python"\npath = "setup.py"\n', encoding="utf-8")
    preamble = (
        "import os, sys\n"
        "agents = os.environ['AGENTS_HOME']\n"
        "overlay = os.environ['AOS_OVERLAY_ROOT']\n"
        "state = os.environ['AOS_OVERLAY_STATE']\n"
    )
    (o / "setup.py").write_text(preamble + body, encoding="utf-8")
    return o


def test_the_default_setup_is_the_first_runnable_setup_script(tmp_path):
    """No `setup` declared: the root's first `setup.<ext>` this OS can run
    (.py before .ps1, .bash, .sh, .js) runs, on every add;
    `[defaults] setup = false` turns it off."""
    from dotagents.cli import OverlayAdd

    src = tmp_path / "src_overlays"
    o = src / "plain"
    o.mkdir(parents=True)
    (o / "setup.py").write_text("open(%r, 'a').write('py')\n" % str(tmp_path / "RAN"), encoding="utf-8")
    (o / "setup.sh").write_text("echo sh >> %s\n" % (tmp_path / "RAN").as_posix(), encoding="utf-8")
    scope = make_scope(tmp_path)

    def add():
        return _run(OverlayAdd, name=["plain"], repo=[str(src)], global_scope=True,
                    agents_dir=scope.agents_root, copy=True, dry_run=False)

    assert add() == 0 and add() == 0
    assert (tmp_path / "RAN").read_text(encoding="utf-8") == "pypy", "install, then sync"
    (o / "overlay.cfg").write_text("[defaults]\nsetup = false\n", encoding="utf-8")
    assert add() == 0
    assert (tmp_path / "RAN").read_text(encoding="utf-8") == "pypy", "turned off"


def test_setup_runs_on_add(tmp_path):
    from dotagents.cli import OverlayAdd

    src = tmp_path / "src_overlays"
    # setup writes a marker inside the store, proving it ran with the right env.
    _add_setup_overlay(
        src, "with-setup",
        "open(os.path.join(agents, 'SETUP_RAN'), 'w').write('ok')\n",
    )
    scope = make_scope(tmp_path)
    rc = _run(OverlayAdd, name=["with-setup"], repo=[str(src)], global_scope=True,
              agents_dir=scope.agents_root, copy=True, dry_run=False)
    assert rc == 0
    assert (scope.agents_root / "SETUP_RAN").is_file()


def test_setup_env_carries_only_the_agents_names(tmp_path):
    # The runner emits AGENTS_HOME and the AOS_OVERLAY_* names, and nothing
    # under the retired DOTAGENTS_* prefix.
    from dotagents.cli import OverlayAdd

    src = tmp_path / "src_overlays"
    _add_setup_overlay(
        src, "dual-env",
        "assert os.environ['AGENTS_HOME'] == agents\n"
        "assert not [k for k in os.environ if k.startswith('DOTAGENTS_')]\n"
        "open(os.path.join(agents, 'DUAL_OK'), 'w').write('ok')\n",
    )
    scope = make_scope(tmp_path)
    rc = _run(OverlayAdd, name=["dual-env"], repo=[str(src)], global_scope=True,
              agents_dir=scope.agents_root, copy=True, dry_run=False)
    assert rc == 0
    assert (scope.agents_root / "DUAL_OK").is_file()


def test_setup_cwd_is_installed_overlay_dir(tmp_path):
    from dotagents.cli import OverlayAdd

    src = tmp_path / "src_overlays"
    # The cwd is the installed root (AOS_OVERLAY_ROOT); what setup produces
    # goes into AOS_OVERLAY_STATE, outside the root, which keeps its digest.
    _add_setup_overlay(
        src, "cwd-demo",
        "open(os.path.join(state, 'MARKER'), 'w').write(os.getcwd() + '|' + overlay)\n",
    )
    scope = make_scope(tmp_path)
    _run(OverlayAdd, name=["cwd-demo"], repo=[str(src)], global_scope=True,
         agents_dir=scope.agents_root, copy=True, dry_run=False)
    installed = Overlay(scope.overlay_root / "cwd-demo")
    marker = installed.state_dir() / "MARKER"
    assert marker.is_file()
    cwd, root = marker.read_text(encoding="utf-8").split("|")
    assert Path(cwd) == Path(root) == scope.overlay_root / "cwd-demo"
    assert installed.tree_digest() == Overlay(src / "cwd-demo").tree_digest()


def test_add_without_setup_is_fine(tmp_path):
    from dotagents.cli import OverlayAdd

    src = make_source(tmp_path)  # py-demo / plain ship no setup script
    scope = make_scope(tmp_path)
    rc = _run(OverlayAdd, name=["plain"], repo=[str(src)], global_scope=True,
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
    rc = _run(OverlayAdd, name=["with-setup"], repo=[str(src)], global_scope=True,
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
        _run(OverlayAdd, name=["bad-setup"], repo=[str(src)], global_scope=True,
             agents_dir=scope.agents_root, copy=True, dry_run=False)


def test_setup_dry_run_does_not_run(tmp_path):
    from dotagents.cli import OverlayAdd

    src = tmp_path / "src_overlays"
    _add_setup_overlay(
        src, "with-setup",
        "open(os.path.join(agents, 'SETUP_RAN'), 'w').write('ok')\n",
    )
    scope = make_scope(tmp_path)
    _run(OverlayAdd, name=["with-setup"], repo=[str(src)], global_scope=True,
         agents_dir=scope.agents_root, copy=True, dry_run=True)
    assert not (scope.agents_root / "SETUP_RAN").exists()


def test_setup_is_told_install_then_sync_on_every_sync_then_remove(tmp_path):
    """The phase is the first argument and AOS_SETUP_PHASE: install on the
    first add, sync on every sync (a new digest or not), remove before the
    copy and its state are deleted. The state directory is kept across syncs."""
    from dotagents.cli import OverlayAdd, OverlayRemove, OverlaySync

    src = tmp_path / "src_overlays"
    _add_setup_overlay(
        src, "with-setup",
        "assert sys.argv[1] == os.environ['AOS_SETUP_PHASE']\n"
        "kept = os.path.join(state, 'kept')\n"
        "with open(os.path.join(agents, 'RUNS'), 'a') as fh:\n"
        "    fh.write('%s:%s:%s,' % (sys.argv[1], os.path.isfile(kept), os.path.isfile('file.md')))\n"
        "open(kept, 'w').write('x')\n",
    )
    scope = make_scope(tmp_path)
    runs = scope.agents_root / "RUNS"
    installed = Overlay(scope.overlay_root / "with-setup")

    def sync():
        assert _run(OverlaySync, pattern="with-setup", repo=[str(src)], global_scope=True,
                    agents_dir=scope.agents_root, copy=True, dry_run=False) == 0

    assert _run(OverlayAdd, name=["with-setup"], repo=[str(src)], global_scope=True,
                agents_dir=scope.agents_root, copy=True, dry_run=False) == 0
    sync()
    (src / "with-setup" / "file.md").write_text("changed\n", encoding="utf-8")
    sync()
    assert runs.read_text(encoding="utf-8") == "install:False:True,sync:True:True,sync:True:True,"
    assert installed.read_preparation() == {"digest": installed.tree_digest(), "phase": "sync"}
    state = installed.state_dir()
    assert _run(OverlayRemove, name=["with-setup"], global_scope=True, agents_dir=scope.agents_root,
                dry_run=False) == 0
    assert runs.read_text(encoding="utf-8").endswith(",remove:True:True,")
    assert not state.exists() and not installed.preparation_path().exists()


def test_a_failed_sync_refuses_the_copy_until_setup_succeeds(tmp_path, caplog):
    from dotagents import _env
    from dotagents.cli import OverlayAdd, OverlaySync

    src = tmp_path / "src_overlays"
    _add_setup_overlay(
        src, "flaky",
        "if os.path.isfile(os.path.join(overlay, 'fail.md')):\n    sys.exit(4)\n",
    )
    (src / "flaky" / "overlay.cfg").write_text(
        '[[setup]]\ninterpreter = "python"\npath = "setup.py"\n[env]\nFLAKY = "on"\n', encoding="utf-8")
    scope = make_scope(tmp_path)
    installed = Overlay(scope.overlay_root / "flaky")

    def sync():
        return _run(OverlaySync, pattern="flaky", repo=[str(src)], global_scope=True,
                    agents_dir=scope.agents_root, copy=True, dry_run=False)

    def env():
        return _env.get_environment(scope, base_env={"PATH": ""}, logger=logging.getLogger("t"))

    assert _run(OverlayAdd, name=["flaky"], repo=[str(src)], global_scope=True,
                agents_dir=scope.agents_root, copy=True, dry_run=False) == 0
    first = installed.tree_digest()
    assert env()["FLAKY"] == "on"
    (src / "flaky" / "fail.md").write_text("x\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(SystemExit):
            sync()
        assert "FLAKY" not in env(), "the digest whose sync failed is not activated"
    assert "it was prepared for %s, not this digest" % first in caplog.text
    assert installed.read_preparation() == {"digest": first, "phase": "install"}
    (src / "flaky" / "fail.md").unlink()
    assert sync() == 0
    assert env()["FLAKY"] == "on"
    assert installed.read_preparation() == {"digest": installed.tree_digest(), "phase": "sync"}


def test_a_failed_sync_of_the_prepared_digest_keeps_it_active(tmp_path):
    from dotagents import _env
    from dotagents.cli import OverlayAdd, OverlaySync

    src = tmp_path / "src_overlays"
    _add_setup_overlay(src, "steady", "sys.exit(6 if os.path.isfile(os.path.join(agents, 'FAIL')) else 0)\n")
    (src / "steady" / "overlay.cfg").write_text(
        '[[setup]]\ninterpreter = "python"\npath = "setup.py"\n[env]\nSTEADY = "on"\n', encoding="utf-8")
    scope = make_scope(tmp_path)
    assert _run(OverlayAdd, name=["steady"], repo=[str(src)], global_scope=True,
                agents_dir=scope.agents_root, copy=True, dry_run=False) == 0
    installed = Overlay(scope.overlay_root / "steady")
    recorded = installed.read_preparation()
    (scope.agents_root / "FAIL").write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit):
        _run(OverlaySync, pattern="steady", repo=[str(src)], global_scope=True,
             agents_dir=scope.agents_root, copy=True, dry_run=False)
    assert installed.read_preparation() == recorded
    env = _env.get_environment(scope, base_env={"PATH": ""}, logger=logging.getLogger("t"))
    assert env["STEADY"] == "on", "the recorded digest stays active"


def test_a_failed_remove_phase_is_reported_and_the_removal_goes_ahead(tmp_path, caplog):
    from dotagents.cli import OverlayAdd, OverlayRemove

    src = tmp_path / "src_overlays"
    _add_setup_overlay(src, "stuck", "sys.exit(5 if sys.argv[1] == 'remove' else 0)\n")
    scope = make_scope(tmp_path)
    assert _run(OverlayAdd, name=["stuck"], repo=[str(src)], global_scope=True,
                agents_dir=scope.agents_root, copy=True, dry_run=False) == 0
    state = Overlay(scope.overlay_root / "stuck").state_dir()
    with caplog.at_level(logging.ERROR):
        assert _run(OverlayRemove, name=["stuck"], global_scope=True, agents_dir=scope.agents_root,
                    dry_run=False) == 0
    assert "failed in its remove phase (exit 5)" in caplog.text
    assert not (scope.overlay_root / "stuck").exists() and not state.exists()


def test_install_starts_from_an_empty_state_directory(tmp_path):
    """Installed with --no-setup, an overlay's state may hold what something
    else left there; its first setup is `install`, into an empty directory."""
    from dotagents.cli import OverlayAdd, OverlaySync

    src = tmp_path / "src_overlays"
    _add_setup_overlay(src, "late", "open(os.path.join(agents, 'SEEN'), 'w').write("
                                    "sys.argv[1] + ':' + ','.join(os.listdir(state)))\n")
    scope = make_scope(tmp_path)
    assert _run(OverlayAdd, name=["late"], repo=[str(src)], global_scope=True,
                agents_dir=scope.agents_root, copy=True, dry_run=False, no_setup=True) == 0
    state = Overlay(scope.overlay_root / "late").state_dir()
    state.mkdir(parents=True)
    (state / "stale").write_text("x", encoding="utf-8")
    assert _run(OverlaySync, pattern="late", repo=[str(src)], global_scope=True,
                agents_dir=scope.agents_root, copy=True, dry_run=False) == 0
    assert (scope.agents_root / "SEEN").read_text(encoding="utf-8") == "install:"


def test_remove_runs_no_setup_when_none_succeeded(tmp_path):
    from dotagents.cli import OverlayAdd, OverlayRemove

    src = tmp_path / "src_overlays"
    _add_setup_overlay(src, "never", "open(os.path.join(agents, 'RAN'), 'a').write(sys.argv[1])\n")
    scope = make_scope(tmp_path)
    assert _run(OverlayAdd, name=["never"], repo=[str(src)], global_scope=True,
                agents_dir=scope.agents_root, copy=True, dry_run=False, no_setup=True) == 0
    assert _run(OverlayRemove, name=["never"], global_scope=True, agents_dir=scope.agents_root,
                dry_run=False) == 0
    assert not (scope.agents_root / "RAN").exists()


# --------------------------------------------------------------------------- #
# Priority-ordered merge (D68)
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
    (ov / "overlay.cfg").write_text(toml, encoding="utf-8")
    return ov


def test_overlay_sort_key_default_priority_when_absent(tmp_path):
    ov = _make_rules_overlay(tmp_path, "no-prio", "NP")  # no priority key
    prio, name, dirname = Overlay(ov).sort_key
    assert prio == _overlays.DEFAULT_PRIORITY
    assert name == "no-prio" and dirname == "no-prio"


def test_sort_overlays_by_priority_orders_low_first_regardless_of_input(tmp_path):
    hi = _make_rules_overlay(tmp_path, "zeta", "HI", priority=900)   # sorts last
    lo = _make_rules_overlay(tmp_path, "alpha", "LO", priority=100)  # sorts first
    mid = _make_rules_overlay(tmp_path, "mid", "MID")               # default 500
    # Feed in a deliberately unsorted order -- plain dirs are accepted too.
    ordered = Overlay.sort_by_priority([hi, mid, lo])
    assert [o.name for o in ordered] == ["alpha", "mid", "zeta"]
    assert all(isinstance(o, Overlay) for o in ordered)


def test_sort_overlays_name_tiebreaker_on_equal_priority(tmp_path):
    b = _make_rules_overlay(tmp_path, "bravo", "B", priority=300)
    a = _make_rules_overlay(tmp_path, "alfa", "A", priority=300)
    ordered = Overlay.sort_by_priority([Overlay(b), Overlay(a)])
    assert [o.name for o in ordered] == ["alfa", "bravo"]


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

    _run(OverlayAdd, name=["alpha"], repo=[str(src)], global_scope=True,
         agents_dir=scope.agents_root, copy=True, dry_run=False)
    _run(OverlayAdd, name=["zeta"], repo=[str(src)], global_scope=True,
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

    _run(OverlayAdd, name=["zeta"], repo=[str(src)], global_scope=True,
         agents_dir=scope.agents_root, copy=True, dry_run=False)
    _run(OverlayAdd, name=["alpha"], repo=[str(src)], global_scope=True,
         agents_dir=scope.agents_root, copy=True, dry_run=False)

    text = (scope.agents_root / "AGENTS.md").read_text(encoding="utf-8")
    assert text.index("LO rule") < text.index("HI rule")
    assert text.index("LO route") < text.index("HI route")


def test_existing_bundled_overlay_manifests_still_parse(tmp_path):
    """Every shipped overlay.cfg is a valid AOS manifest (the files it names
    present) and reports default priority (none declares `priority` today).

    The example overlays live on the `repo` branch (D77), under
    `overlays/<name>/`. CI checks that branch out at `overlays-src/`, a dev box
    at `repo/` (a gitignored worktree); an older layout put it at `overlays/`.
    The first of those present is parsed; a checkout with none of them skips,
    and the tmp-fixture tests above cover read_manifest's parsing there."""
    checkout = Path(__file__).resolve().parents[1]
    candidates = [checkout / d for d in ("overlays-src/overlays", "repo/overlays", "overlays/overlays", "overlays")]
    repo_overlays = next((d for d in candidates if list(d.glob("*/overlay.cfg"))), None)
    if repo_overlays is None:
        pytest.skip("no checkout of the example overlays (overlays-src/, repo/ or overlays/)")
    manifests = sorted(repo_overlays.glob("*/overlay.cfg"))
    assert manifests, "expected bundled overlays to exist"
    for manifest in manifests:
        overlay = Overlay(manifest.parent)
        assert overlay.manifest_problems() == [], manifest
        assert _aos.validate(overlay.document(), overlay.members()) == [], manifest
        parsed = overlay.read_manifest()
        assert isinstance(parsed["priority"], int)
        # None of the shipped overlays sets priority yet -> all default.
        assert parsed["priority"] == _overlays.DEFAULT_PRIORITY
