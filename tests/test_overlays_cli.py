"""`dotagents overlays` command behaviour fixed in the 2026-09-09 review:
name normalization on add/remove, remove's un-merge, skills published from the
INSTALLED copy, `sync --copy` / `--overwrite`, dry-run setup reporting, up-front
validation, `requires`, `show`, the manifest reader's comment handling, and
the umbrella's no-subcommand exit -- plus the failure modes found since:
confinement of `requires`/`rules`, broken repos and registries, removing a
linked overlay, and what `sync` takes from where. Those still open in the
product are strict xfails naming the review issue.

tmp dirs only, no network.
"""

import json
import logging
import os
from pathlib import Path

import pytest

from dotagents import _overlays, _scope, cli
from dotagents.cli import OverlayAdd, OverlayRemove, OverlayShow, OverlaySync

BASE_AGENTS = (
    "<!-- dotagents:begin -->\n# Agent Directives\n\n## Always-on rules\n"
    "- **Base rule**: keep it.\n\n## Load on demand\nNothing ships here by default.\n"
    "<!-- dotagents:end -->\n"
)


def _run(cmd_cls, **kwargs):
    cmd = cmd_cls()
    for k, v in kwargs.items():
        setattr(cmd, k, v)
    return cmd()


def _logger():
    lg = logging.getLogger("test-overlays-cli")
    lg.addHandler(logging.NullHandler())
    return lg


def _overlay(src: Path, name: str, *, routing=None, requires=(), skill=None, setup=False, files=()):
    ov = src / name
    ov.mkdir(parents=True, exist_ok=True)
    toml = 'name = "%s"\n' % name
    toml += 'description = "the %s overlay"  # trailing comment\n' % name
    toml += "requires = [%s]\n" % ", ".join('"%s"' % r for r in requires)
    toml += "routing = [%s] # single-line array with a trailing comment\n" % (
        ", ".join('"%s"' % r for r in (routing or []))
    )
    (ov / "overlay.toml").write_text(toml, encoding="utf-8")
    for rel, body in files:
        (ov / rel).parent.mkdir(parents=True, exist_ok=True)
        (ov / rel).write_text(body, encoding="utf-8")
    if skill:
        (ov / "skills" / skill).mkdir(parents=True)
        (ov / "skills" / skill / "SKILL.md").write_text("---\nname: %s\n---\n" % skill, encoding="utf-8")
    if setup:
        (ov / "setup.py").write_text("print('setup ran')\n", encoding="utf-8")
    return ov


@pytest.fixture
def world(tmp_path):
    src = tmp_path / "src"
    scope_root = tmp_path / "scope"
    scope_root.mkdir()
    (scope_root / "AGENTS.md").write_text(BASE_AGENTS, encoding="utf-8")
    return src, scope_root


def _add(src, scope_root, *names, **extra):
    return _run(
        OverlayAdd, name=list(names), repo=[str(src)], global_scope=True,
        agents_dir=scope_root, copy=True, dry_run=False, **extra,
    )


# --------------------------------------------------------------------------
# add / remove name handling
# --------------------------------------------------------------------------

def test_add_installs_a_source_dir_spelled_with_underscores(world):
    """`add` normalizes to `my-overlay` and then looked THAT up in the source,
    so a perfectly valid `my_overlay` source dir could never be installed."""
    src, scope_root = world
    _overlay(src, "my_overlay", routing=["- Route -> ~/.agents/kb/X.md"])
    assert _add(src, scope_root, "my_overlay") == 0
    assert (scope_root / "overlays" / "my-overlay" / "overlay.toml").is_file()
    assert _add(src, scope_root, "My_Overlay") == 0  # same overlay, no second dir
    assert sorted(p.name for p in (scope_root / "overlays").iterdir()) == ["my-overlay"]


def test_remove_normalizes_and_unmerges(world):
    """`add My_Ov` + `remove My_Ov` said "not installed"; and the rules an
    overlay merged into AGENTS.md now leave with it (recompose over the rest)."""
    src, scope_root = world
    _overlay(src, "one", routing=["- ONE-ROUTE -> ~/.agents/kb/ONE.md"])
    _overlay(src, "two", routing=["- TWO-ROUTE -> ~/.agents/kb/TWO.md"])
    _add(src, scope_root, "one", "two")
    agents_md = (scope_root / "AGENTS.md").read_text(encoding="utf-8")
    assert "ONE-ROUTE" in agents_md and "TWO-ROUTE" in agents_md

    assert _run(OverlayRemove, name=["ONE"], global_scope=True, agents_dir=scope_root, dry_run=False) == 0
    assert not (scope_root / "overlays" / "one").exists()
    agents_md = (scope_root / "AGENTS.md").read_text(encoding="utf-8")
    assert "ONE-ROUTE" not in agents_md
    assert "TWO-ROUTE" in agents_md
    # Recomposed from the PRISTINE base block, like add/sync do.
    assert "## Always-on rules" in agents_md and "<!-- dotagents:end -->" in agents_md


def test_add_validates_every_name_before_touching_anything(world):
    src, scope_root = world
    _overlay(src, "good", setup=True)
    with pytest.raises(SystemExit):
        _add(src, scope_root, "good", "nope")
    assert not (scope_root / "overlays").exists(), "nothing may be installed on a bad request"
    with pytest.raises(SystemExit, match="not a valid overlay name"):
        _add(src, scope_root, "good", "2fast")


# --------------------------------------------------------------------------
# skills come from the installed copy
# --------------------------------------------------------------------------

def test_skills_are_published_from_the_installed_copy(world, tmp_path):
    src, scope_root = world
    _overlay(src, "sk", skill="my-skill")
    _run(OverlayAdd, name=["sk"], repo=[str(src)], global_scope=True,
         agents_dir=scope_root, copy=False, dry_run=False)
    target = scope_root / "skills" / "my-skill"
    assert (target / "SKILL.md").is_file()
    if os.path.islink(str(target)):
        resolved = Path(os.path.realpath(str(target)))
        assert (scope_root / "overlays" / "sk").resolve() in resolved.parents, (
            "a symlink into the SOURCE dies with a temporary checkout and can never "
            "be matched by remove"
        )
    # And remove can match it.
    _run(OverlayRemove, name=["sk"], global_scope=True, agents_dir=scope_root, dry_run=False)
    assert not os.path.lexists(str(target))


# --------------------------------------------------------------------------
# sync: --copy honoured, --overwrite updates changed files
# --------------------------------------------------------------------------

def test_sync_copy_and_overwrite(world):
    src, scope_root = world
    _overlay(src, "up", skill="s1", files=[("kb/UP.md", "v1\n")])
    _add(src, scope_root, "up")
    installed = scope_root / "overlays" / "up" / "kb" / "UP.md"
    assert installed.read_text(encoding="utf-8") == "v1\n"

    (src / "up" / "kb" / "UP.md").write_text("v2\n", encoding="utf-8")
    # Plain sync never clobbers.
    _run(OverlaySync, pattern=None, repo=[str(src)], global_scope=True,
         agents_dir=scope_root, copy=True, overwrite=False, dry_run=False)
    assert installed.read_text(encoding="utf-8") == "v1\n"
    # --overwrite replaces the changed file.
    _run(OverlaySync, pattern=None, repo=[str(src)], global_scope=True,
         agents_dir=scope_root, copy=True, overwrite=True, dry_run=False)
    assert installed.read_text(encoding="utf-8") == "v2\n"

    # --copy: a skill removed from the shared dir is republished as a COPY.
    import shutil

    shutil.rmtree(str(scope_root / "skills" / "s1"))
    _run(OverlaySync, pattern=None, repo=[str(src)], global_scope=True,
         agents_dir=scope_root, copy=True, overwrite=False, dry_run=False)
    assert (scope_root / "skills" / "s1").is_dir()
    assert not os.path.islink(str(scope_root / "skills" / "s1"))


# --------------------------------------------------------------------------
# dry-run reports the setup script; requires; show; no-subcommand
# --------------------------------------------------------------------------

def test_dry_run_add_reports_the_setup_script(world, caplog):
    src, scope_root = world
    _overlay(src, "withsetup", setup=True)
    with caplog.at_level(logging.INFO):
        _run(OverlayAdd, name=["withsetup"], repo=[str(src)], global_scope=True,
             agents_dir=scope_root, copy=True, dry_run=True)
    assert any("would run setup.py" in r.getMessage() for r in caplog.records)
    assert not (scope_root / "overlays").exists()


def test_add_installs_requires_first(world, caplog):
    src, scope_root = world
    _overlay(src, "base")
    _overlay(src, "mid", requires=["base"])
    _overlay(src, "top", requires=["mid", "ghost"])
    with caplog.at_level(logging.WARNING):
        assert _add(src, scope_root, "top") == 0
    installed = sorted(p.name for p in (scope_root / "overlays").iterdir())
    assert installed == ["base", "mid", "top"]
    assert any("ghost" in r.getMessage() for r in caplog.records)
    # --no-requires installs only what was asked for.
    _run(OverlayRemove, name=["base", "mid", "top"], global_scope=True, agents_dir=scope_root, dry_run=False)
    assert _add(src, scope_root, "top", no_requires=True) == 0
    assert sorted(p.name for p in (scope_root / "overlays").iterdir()) == ["top"]


def test_an_installed_requirement_is_satisfied_not_redone(world, caplog):
    """`add mid` when `base` (its requirement) is already installed: base is
    neither copied again nor set up again. `add base` itself still is."""
    src, scope_root = world
    _overlay(src, "base", setup=True)
    _overlay(src, "mid", requires=["base"])
    assert _add(src, scope_root, "base") == 0
    with caplog.at_level(logging.INFO):
        assert _add(src, scope_root, "mid") == 0
    msgs = [r.getMessage() for r in caplog.records]
    assert any("requires base: already installed" in m for m in msgs)
    assert not any("installing it too" in m for m in msgs)
    assert not any("running setup for base" in m for m in msgs)
    assert sorted(p.name for p in (scope_root / "overlays").iterdir()) == ["base", "mid"]
    # Asked for by name, the installed overlay is set up again as always.
    caplog.clear()
    with caplog.at_level(logging.INFO):
        assert _add(src, scope_root, "base") == 0
    assert any("running setup for base" in m for m in (r.getMessage() for r in caplog.records))


def test_a_requirement_in_the_user_store_satisfies_a_project_add(world, tmp_path, monkeypatch, caplog):
    """Adding to a project: a requirement installed in the USER store is in
    the project's walk already, so it is not installed into the project too."""
    src, user_store = world
    _overlay(src, "base", setup=True)
    _overlay(src, "mid", requires=["base"])
    assert _add(src, user_store, "base") == 0
    project_store = tmp_path / "proj" / ".agents"
    project_store.mkdir(parents=True)
    (project_store / "AGENTS.md").write_text(BASE_AGENTS, encoding="utf-8")
    monkeypatch.setenv("AGENTS_HOME", str(user_store))
    monkeypatch.delenv("AGENTS_PROJECT_ROOT", raising=False)
    with caplog.at_level(logging.INFO):
        rc = _run(
            OverlayAdd, name=["mid"], repo=[str(src)], global_scope=False,
            agents_dir=project_store, copy=True, dry_run=False,
        )
    assert rc == 0
    assert any("requires base: already installed" in r.getMessage() for r in caplog.records)
    assert sorted(p.name for p in (project_store / "overlays").iterdir()) == ["mid"]


def test_requires_cycle_is_an_error(world):
    src, scope_root = world
    _overlay(src, "a", requires=["b"])
    _overlay(src, "b", requires=["a"])
    with pytest.raises(SystemExit, match="cycle"):
        _add(src, scope_root, "a")


def test_show_describes_installed_then_source(world, capsys):
    src, scope_root = world
    _overlay(src, "shown", requires=["base"], routing=["- R -> ~/.agents/kb/R.md"], skill="sk", setup=True)
    _run(OverlayShow, name="shown", repo=[str(src)], global_scope=True, agents_dir=scope_root, json=True)
    info = json.loads(capsys.readouterr().out)
    assert info["where"] == "source"
    assert info["description"] == "the shown overlay"
    assert info["requires"] == ["base"] and info["skills"] == ["sk"] and info["setup"] == "setup.py"
    assert info["root_var"] == "SHOWN_OVERLAY_ROOT"
    _overlay(src, "base")
    _add(src, scope_root, "shown")
    _run(OverlayShow, name="shown", repo=[str(src)], global_scope=True, agents_dir=scope_root, json=False)
    out = capsys.readouterr().out
    assert out.startswith("shown (installed (user))")


def test_list_shows_both_scopes_unless_global(tmp_path, monkeypatch, capsys):
    from dotagents.cli import OverlayList

    store = tmp_path / "store"
    project = tmp_path / "proj"
    for d in ("store/overlays/common", "store/overlays/only-user",
              "proj/.agents/overlays/common", "proj/.agents/overlays/only-proj"):
        (tmp_path / d).mkdir(parents=True)
    monkeypatch.setenv("AGENTS_HOME", str(store))
    monkeypatch.setenv("AGENTS_PROJECT_ROOT", str(project))
    monkeypatch.chdir(project)  # a write-scope command honours the pin only from inside it
    empty_repo = tmp_path / "empty-repo"  # a real, empty source: nothing available
    empty_repo.mkdir()

    _run(OverlayList, json=False, repo=[str(empty_repo)])
    out = capsys.readouterr().out
    assert out.splitlines()[:6] == [
        "installed (project):",
        "  common",
        "  only-proj",
        "installed (user):",
        "  only-user",
        "  common  (shadowed by a more specific store's)",
    ]
    _run(OverlayList, json=True, global_scope=True, repo=[str(empty_repo)])
    data = json.loads(capsys.readouterr().out)
    assert data["scope"] == "user" and data["installed"] == ["common", "only-user"]
    assert [s["level"] for s in data["stores"]] == ["user"]


def test_system_store_is_walked_first_and_shadowed_by_user(tmp_path, monkeypatch):
    from dotagents import _scope
    from dotagents._scope import Scope

    system = tmp_path / "etc-agents"
    store = tmp_path / "store"
    for d in ("etc-agents/overlays/common/bin", "etc-agents/overlays/sys-only/bin",
              "store/overlays/common/bin"):
        (tmp_path / d).mkdir(parents=True)
    monkeypatch.setenv("AGENTS_SYSTEM_ROOT", str(system))
    # A tmp dir is user-writable; pretend it passed the administrators-only check.
    monkeypatch.setattr(_scope, "_system_root_cache", {})
    monkeypatch.setattr(_scope, "_system_root_is_safe", lambda p: (True, ""))
    scope = Scope.of(agents_dir=store)
    assert scope.stores == [system, store]
    assert [(o.name, o.store) for o in scope.overlays] == [("sys-only", system), ("common", store)]
    levels = [lvl for lvl, _p, _r in scope.paths({"default": "bin"}, include_missing=True)]
    assert levels == ["sys-only", "system", "common", "user"]


@pytest.fixture
def _system(monkeypatch):
    from dotagents import _scope

    monkeypatch.setattr(_scope, "_system_root_cache", {})
    monkeypatch.delenv("AGENTS_SYSTEM_ROOT", raising=False)
    return _scope


def test_windows_has_no_default_system_store(_system, monkeypatch):
    # `/etc/agents` on Windows is drive-relative (`\etc\agents`) and any local
    # account can create it; its env.py and cmds ran in every user's session.
    monkeypatch.setattr(_system, "_is_windows", lambda: True)
    assert _system.system_root_default() is None


def test_system_store_must_be_absolute_present_and_admin_only(_system, monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("AGENTS_SYSTEM_ROOT", "relative/agents")
    assert _system.system_root_default() is None
    assert "not an absolute path" in caplog.text

    monkeypatch.setenv("AGENTS_SYSTEM_ROOT", str(tmp_path / "absent"))
    assert _system.system_root_default() is None, "a missing root contributes nothing, not even bin/"

    monkeypatch.setenv("AGENTS_SYSTEM_ROOT", str(tmp_path))
    monkeypatch.setattr(_system, "_system_root_is_safe", lambda p: (False, "a user can write it"))
    assert _system.system_root_default() is None
    assert "a user can write it" in caplog.text


def test_a_user_writable_dir_is_not_a_safe_system_store(tmp_path):
    # The real check, on whichever OS runs the suite: a tmp dir belongs to the
    # current (non-root, or non-admin-only) user.
    from dotagents._scope import _system_root_is_safe

    ok, reason = _system_root_is_safe(tmp_path)
    assert not ok and reason


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL check")
def test_an_admin_only_windows_dir_is_a_safe_system_store(monkeypatch):
    # The ACL logic itself, from a neutral environment. What a PowerShell 7
    # parent does to the check is the next test's subject, so the result here
    # does not depend on which shell started pytest.
    from dotagents._scope import _system_root_is_safe

    monkeypatch.delenv("PSModulePath", raising=False)
    ok, reason = _system_root_is_safe(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
    assert ok, reason


def _pwsh7_modules():
    import shutil
    import subprocess

    pwsh = shutil.which("pwsh")
    if pwsh is None:
        return None
    try:
        home = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", "$PSHOME"],
                              capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    modules = Path(home) / "Modules" if home else None
    return modules if modules is not None and modules.is_dir() else None


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL check")
@pytest.mark.xfail(
    strict=True,
    reason="open: _system_root_is_safe spawns Windows PowerShell with the PSModulePath a "
           "PowerShell 7 parent exports, so Get-Acl fails to load and every system store "
           "is rejected when dotagents runs from pwsh",
)
def test_the_acl_check_survives_a_powershell_7_parent(monkeypatch):
    from dotagents._scope import _system_root_is_safe

    modules = _pwsh7_modules()
    if modules is None:
        pytest.skip("needs PowerShell 7 (pwsh)")
    inherited = os.environ.get("PSModulePath", "")
    monkeypatch.setenv("PSModulePath", os.pathsep.join(p for p in (str(modules), inherited) if p))
    ok, reason = _system_root_is_safe(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
    assert ok, reason


def test_umbrella_without_subcommand_exits_2():
    assert cli.Overlays()() == 2
    assert cli.Dotagents()() == 2


# --------------------------------------------------------------------------
# manifest reader
# --------------------------------------------------------------------------

def test_manifest_reader_handles_comments_indentation_and_quotes(tmp_path):
    ov = tmp_path / "ov"
    ov.mkdir()
    (ov / "overlay.toml").write_text(
        'name = "ov" # the name\n'
        "priority = 5 # low\n"
        'routing = ["a #not-a-comment"] # trailing\n'
        "rules = [\n"
        '    "r1.md",\n'
        "    'r2.md',\n"
        "  ]\n"
        'requires = ["""multi\nline"""]\n',
        encoding="utf-8",
    )
    m = _overlays.Overlay(ov).read_manifest()
    assert m["priority"] == 5
    assert m["routing"] == ["a #not-a-comment"], "a trailing comment must not swallow the next array"
    assert m["rules"] == ["r1.md", "r2.md"]
    assert m["requires"] == ["multi\nline"]


def test_compose_block_explains_overlay_root_vars_once(tmp_path):
    """Routing lines point at `$<NAME>_OVERLAY_ROOT/...` (what `dotagents env`
    exports), not `~/.agents/...`; the block says so once, above them."""
    from dotagents.cli import _compose_block
    from dotagents.cli._common import OVERLAY_ROOT_NOTE

    a = _overlay(tmp_path, "a", routing=["- Plan -> $A_OVERLAY_ROOT/flows/PLAN.md"])
    b = _overlay(tmp_path, "b", routing=["- Rust -> $B_OVERLAY_ROOT/kb/RUST.md"])
    out = _compose_block(BASE_AGENTS, [a, b], _logger())
    assert out.count(OVERLAY_ROOT_NOTE) == 1
    assert out.index(OVERLAY_ROOT_NOTE) < out.index("$A_OVERLAY_ROOT")
    # No overlay-root vars -> no note.
    c = _overlay(tmp_path, "c", routing=["- Plain -> `dotagents foo`"])
    assert OVERLAY_ROOT_NOTE not in _compose_block(BASE_AGENTS, [c], _logger())


def test_compose_block_without_load_on_demand_heading_keeps_rules(tmp_path):
    from dotagents.cli import _compose_block

    ov = tmp_path / "ov"
    ov.mkdir()
    (ov / "rules.md").write_text("- **Rule**: keep me.\n", encoding="utf-8")
    (ov / "overlay.toml").write_text('name = "ov"\nrules = ["rules.md"]\n', encoding="utf-8")
    base = "<!-- dotagents:begin -->\n# Directives\n<!-- dotagents:end -->\n"
    out = _compose_block(base, [ov], _logger())
    assert "- **Rule**: keep me." in out
    assert out.index("keep me.") < out.index("<!-- dotagents:end -->")


def test_scratch_dir_is_one_dir_removed_at_exit():
    from dotagents.cli._common import _scratch_dir

    a = _scratch_dir()
    assert a == _scratch_dir() and a.is_dir()
    assert a.name.startswith("dotagents-")
    # ...and gone once its process exits, contents and all. In a child, so
    # this process's own dir survives for the tests after this one.
    import subprocess
    import sys

    code = ("from dotagents.cli._common import _scratch_dir; d = _scratch_dir(); "
            "(d / 'sub').mkdir(); (d / 'sub' / 'f').write_text('x'); print(d)")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    child = Path(proc.stdout.strip())
    assert child.name.startswith("dotagents-") and child != a
    assert not child.exists()


# --------------------------------------------------------------------------
# failure modes: confinement, broken repos, bad registries, removal, sync
# --------------------------------------------------------------------------

def _open(issue, why):
    """A regression test for a confirmed, still-open product bug: strict, so
    the fix turns it into an XPASS failure and the marker has to go."""
    return pytest.mark.xfail(strict=True, reason="open (review 2026-09-23 %s): %s" % (issue, why))


@_open("overlays-05", "requires names are not validated")
def test_requires_cannot_install_outside_the_overlays_dir(world, tmp_path):
    src, scope_root = world
    _overlay(tmp_path, "escape")  # beside the source, reachable as ../escape
    _overlay(src, "top", requires=["../escape", "user"])
    _add(src, scope_root, "top")
    assert not (scope_root / "escape").exists(), "a requires entry installed outside overlays/"
    assert sorted(p.name for p in (scope_root / "overlays").iterdir()) == ["top"]


@_open("overlays-05", "rules paths are not confined to the overlay")
def test_rules_cannot_merge_a_file_outside_the_overlay(world, tmp_path):
    src, scope_root = world
    secret = tmp_path / "secret.md"
    secret.write_text("- **Secret**: must not be merged.\n", encoding="utf-8")
    top = _overlay(src, "top")
    manifest = top / "overlay.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + "rules = [%s, %s]\n" % (json.dumps(str(secret)), json.dumps("../../../secret.md")),
        encoding="utf-8",
    )
    _add(src, scope_root, "top")
    assert "Secret" not in (scope_root / "AGENTS.md").read_text(encoding="utf-8")


@_open("overlays-07", "a registry parse error escapes as a traceback")
@pytest.mark.parametrize("name, text", [
    ("dotagents.json", '{"a": "x",}'),
    ("dotagents.toml", 'a = "x\n'),
])
def test_a_malformed_store_registry_is_a_clean_error(world, name, text):
    """One trailing comma in the store's registry turned every overlays
    command into a traceback."""
    src, scope_root = world
    (scope_root / name).write_text(text, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        _run(OverlayShow, name="nope", repo=[], global_scope=True, agents_dir=scope_root, json=True)
    assert name in str(exc.value)


@_open("overlays-06", "repo failures are swallowed as not-found")
def test_a_broken_repo_fails_the_add_instead_of_dropping_a_requirement(world, tmp_path):
    """A requirement looked up in a repo that FAILS (here: missing) is an
    error, not the documented warning for a requirement no repo offers."""
    src, scope_root = world
    _overlay(src, "top", requires=["dep"])
    with pytest.raises(SystemExit):
        _run(OverlayAdd, name=["top"], repo=[str(src), str(tmp_path / "missing-repo")],
             global_scope=True, agents_dir=scope_root, copy=True, dry_run=False)


@_open("overlays-06", "repo failures are swallowed as not-found")
def test_sync_with_a_mistyped_repo_is_an_error(world, tmp_path):
    src, scope_root = world
    _overlay(src, "one")
    _add(src, scope_root, "one")
    with pytest.raises(SystemExit):
        _run(OverlaySync, pattern=None, repo=[str(tmp_path / "typo")], global_scope=True,
             agents_dir=scope_root, copy=True, overwrite=False, dry_run=False)


def _link_dir(link, target):
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        if os.name != "nt":
            raise
    import _winapi  # a junction needs no privilege on Windows

    _winapi.CreateJunction(str(target), str(link))


def test_remove_unlinks_a_symlinked_overlay_and_keeps_its_target(world, tmp_path):
    src, scope_root = world
    target = _overlay(tmp_path / "dev", "myov", routing=["- MYOV-ROUTE -> x"])
    (scope_root / "overlays").mkdir()
    try:
        _link_dir(scope_root / "overlays" / "myov", target)
    except OSError:
        pytest.skip("cannot create a directory link here")
    assert _run(OverlayRemove, name=["myov"], global_scope=True, agents_dir=scope_root, dry_run=False) == 0
    assert not os.path.lexists(str(scope_root / "overlays" / "myov"))
    assert (target / "overlay.toml").is_file(), "the link's target is the user's, not ours to delete"


@_open("overlays-03", "sync records no provenance")
def test_sync_keeps_the_repo_an_overlay_was_added_from(world, tmp_path, monkeypatch):
    """`add --repo private` then a plain `sync` with another repo configured:
    the overlay must not take files from that other repo."""
    src, scope_root = world
    _overlay(src, "python", files=[("kb/PY.md", "private\n")])
    _add(src, scope_root, "python")
    public = tmp_path / "public"
    _overlay(public, "python", files=[("kb/PY.md", "public\n"), ("bin/pytool", "echo public\n")])
    monkeypatch.setenv("AGENTS_OVERLAYS_REPO", str(public))
    _run(OverlaySync, pattern=None, repo=[], global_scope=True, agents_dir=scope_root,
         copy=True, overwrite=True, dry_run=False)
    installed = scope_root / "overlays" / "python"
    assert (installed / "kb" / "PY.md").read_text(encoding="utf-8") == "private\n"
    assert not (installed / "bin" / "pytool").exists()


@_open("overlays-12", "sync never prunes files deleted upstream")
def test_sync_overwrite_drops_a_file_removed_upstream(world):
    """An `env.py` renamed upstream kept running from the store, because its
    presence alone activates it."""
    src, scope_root = world
    _overlay(src, "demo", files=[("env.py", "print('{}')\n")])
    _add(src, scope_root, "demo")
    (src / "demo" / "env.py").rename(src / "demo" / "pre.env.py")
    _run(OverlaySync, pattern=None, repo=[str(src)], global_scope=True, agents_dir=scope_root,
         copy=True, overwrite=True, dry_run=False)
    installed = scope_root / "overlays" / "demo"
    assert (installed / "pre.env.py").is_file()
    assert not (installed / "env.py").exists()
