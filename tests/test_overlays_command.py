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
    # Dots are part of the install path and are kept.
    assert Overlay.normalize_name("v1.2") == "v1.2"


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
    assert ov.normalized_name == "my-ov.v2"
    assert ov.root_var == "MY_OV_V2_OVERLAY_ROOT"
    assert ov.is_valid
    assert ov.manifest_path == d / "overlay.toml"
    assert ov.read_manifest()["priority"] == _overlays.DEFAULT_PRIORITY
    assert ov.priority == _overlays.DEFAULT_PRIORITY
    assert ov.find_setup_script() is None
    # Files written after construction are seen (no caching).
    d.mkdir()
    (d / "overlay.toml").write_text('name = "ov"\npriority = 7\n', encoding="utf-8")
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

def test_source_resolution_explicit_repo(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTS_OVERLAYS_REPO", raising=False)
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
    (other / "py-demo" / "overlay.toml").write_text('name = "py-demo"\n', encoding="utf-8")
    monkeypatch.setenv("AGENTS_OVERLAYS_REPO", str(src))
    monkeypatch.setenv("AGENTS_OVERLAYS_REPO_A", str(other))
    assert _scope.resolve_source(None).overlay_dir("py-demo") == other / "py-demo"


def test_source_resolution_no_bundled_errors(tmp_path, monkeypatch):
    # main ships no bundled overlays/ (they live on the `overlays` branch, D77),
    # and this test env packages none. With no repo anywhere, resolve_source
    # must fail with a clear "no overlay source" error, not silently pick nothing.
    monkeypatch.delenv("AGENTS_OVERLAYS_REPO", raising=False)
    for k in list(__import__("os").environ):
        if k.startswith("AGENTS_OVERLAYS_REPO_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "empty-store"))
    if _scope.bundled_overlays_root() is not None:
        pytest.skip("this build/checkout bundles overlays; default-source error N/A")
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
    assert (dest / "overlay.toml").is_file()
    assert copied >= 2 and skipped == 0


def test_install_overlay_dir_no_clobber(tmp_path):
    src = make_source(tmp_path)
    dest = tmp_path / "dest" / "py-demo"
    Overlay(src / "py-demo").install_to(dest, False)
    # Hand-edit an installed file; re-install must not clobber it.
    (dest / "kb" / "PY.md").write_text("EDITED\n", encoding="utf-8")
    copied, skipped, _ = Overlay(src / "py-demo").install_to(dest, False)
    assert (dest / "kb" / "PY.md").read_text(encoding="utf-8") == "EDITED\n"
    assert copied == 0 and skipped >= 1


def test_overlay_files_excludes_manifest_and_caches(tmp_path):
    src = make_source(tmp_path)
    ov = Overlay(src / "py-demo")
    (ov.path / "__pycache__").mkdir()
    (ov.path / "__pycache__" / "x.pyc").write_bytes(b"")
    names = {p.relative_to(ov.path).as_posix() for p in ov.files()}
    assert "kb/PY.md" in names
    assert "overlay.toml" not in names
    assert not any("__pycache__" in n for n in names)


def test_merge_overlay_rules_into_agents_md(tmp_path):
    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    agents_md = scope.agents_root / "AGENTS.md"
    changed = Overlay(src / "py-demo").merge_rules_into(agents_md, False, logger())
    assert changed
    text = agents_md.read_text(encoding="utf-8")
    assert "~/.agents/kb/PY.md" in text
    # Content outside the managed block is untouched; block still terminated.
    assert text.count(END) == 1


def test_merge_overlay_rules_noop_when_no_contributions(tmp_path):
    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    agents_md = scope.agents_root / "AGENTS.md"
    assert Overlay(src / "plain").merge_rules_into(agents_md, False, logger()) is False


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
    _run(
        OverlayAdd, name=["py-demo"], repo=[str(src)], global_scope=True,
        agents_dir=scope.agents_root, copy=True, dry_run=True,
    )
    assert not (scope.overlay_root / "py-demo").exists()
    assert not (scope.shared_skills_dir / "py-lint").exists() if scope.shared_skills_dir.exists() else True


def test_cmd_sync_glob_filter(tmp_path):
    from dotagents.cli import OverlayAdd, OverlaySync

    src = make_source(tmp_path)
    scope = make_scope(tmp_path)
    _run(OverlayAdd, name=["py-demo", "plain"], repo=[str(src)], global_scope=True,
         agents_dir=scope.agents_root, copy=True, dry_run=False)
    # A glob that matches only py-demo; a run that touches only it must succeed.
    rc = _run(OverlaySync, pattern="py*", repo=[str(src)], global_scope=True,
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
        "agents = os.environ['AGENTS_HOME']\n"
        "overlay = os.environ.get('AGENTS_OVERLAY_DIR', os.getcwd())\n"
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
    rc = _run(OverlayAdd, name=["with-setup"], repo=[str(src)], global_scope=True,
              agents_dir=scope.agents_root, copy=True, dry_run=False)
    assert rc == 0
    assert (scope.agents_root / "SETUP_RAN").is_file()


def test_setup_env_carries_only_the_agents_names(tmp_path):
    # The runner emits AGENTS_HOME / AGENTS_OVERLAY_DIR and nothing under the
    # retired DOTAGENTS_* prefix.
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
    # The marker lands via a *relative* path -> proves cwd is the installed dir,
    # and its content is AGENTS_OVERLAY_DIR -> proves that env var is set.
    _add_setup_overlay(
        src, "cwd-demo",
        "open('MARKER', 'w').write(overlay)\n",
    )
    scope = make_scope(tmp_path)
    _run(OverlayAdd, name=["cwd-demo"], repo=[str(src)], global_scope=True,
         agents_dir=scope.agents_root, copy=True, dry_run=False)
    marker = scope.overlay_root / "cwd-demo" / "MARKER"
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8") == str(scope.overlay_root / "cwd-demo")


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


def test_setup_runs_on_sync(tmp_path):
    from dotagents.cli import OverlayAdd, OverlaySync

    src = tmp_path / "src_overlays"
    # Idempotent-style setup: append a line each run so we can count invocations.
    _add_setup_overlay(
        src, "with-setup",
        "open(os.path.join(agents, 'RUNS'), 'a').write('x')\n",
    )
    scope = make_scope(tmp_path)
    _run(OverlayAdd, name=["with-setup"], repo=[str(src)], global_scope=True,
         agents_dir=scope.agents_root, copy=True, dry_run=False)
    _run(OverlaySync, pattern="with-setup", repo=[str(src)], global_scope=True,
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
    """Every shipped overlay.toml parses unchanged and reports default priority
    (none declares `priority` today) -- the field is optional and backward-compatible.

    The example overlays moved to the `overlays` branch (D77), so a plain main
    checkout has none to parse here; skip when the dir is absent. This still guards
    the manifests on the overlays branch (or any checkout that has an overlays/ dir),
    while the tmp-fixture tests above cover read_manifest's parsing on main."""
    repo_overlays = Path(__file__).resolve().parents[1] / "overlays"
    if not repo_overlays.is_dir():
        pytest.skip("no bundled overlays/ (they live on the `overlays` branch, D77)")
    # Either layout: the branch checked out AT `overlays/` (CI: `overlays-src/`,
    # manifests at `overlays/<name>/`), or a clone of the branch placed under it
    # (a dev box: `overlays/overlays/<name>/`).
    manifests = sorted(repo_overlays.glob("*/overlay.toml")) or sorted(
        repo_overlays.glob("overlays/*/overlay.toml")
    )
    assert manifests, "expected bundled overlays to exist"
    for manifest in manifests:
        parsed = Overlay(manifest.parent).read_manifest()
        assert isinstance(parsed["priority"], int)
        # None of the shipped overlays sets priority yet -> all default.
        assert parsed["priority"] == _overlays.DEFAULT_PRIORITY
