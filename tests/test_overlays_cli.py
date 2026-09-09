"""`dotagents overlays` command behaviour fixed in the 2026-09-09 review:
name normalization on add/remove, remove's un-merge, skills published from the
INSTALLED copy, `sync --copy` / `--overwrite`, dry-run setup reporting, up-front
validation, `requires`, `show`, the manifest reader's comment handling, and
the umbrella's no-subcommand exit.

tmp dirs only, no network.
"""

import json
import logging
import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from dotagents import _overlays, _scope, cli  # noqa: E402
from dotagents.cli import OverlayAdd, OverlayRemove, OverlayShow, OverlaySync  # noqa: E402

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
        OverlayAdd, name=list(names), source=str(src), global_scope=True,
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
    _run(OverlayAdd, name=["sk"], source=str(src), global_scope=True,
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
    _run(OverlaySync, pattern=None, source=str(src), global_scope=True,
         agents_dir=scope_root, copy=True, overwrite=False, dry_run=False)
    assert installed.read_text(encoding="utf-8") == "v1\n"
    # --overwrite replaces the changed file.
    _run(OverlaySync, pattern=None, source=str(src), global_scope=True,
         agents_dir=scope_root, copy=True, overwrite=True, dry_run=False)
    assert installed.read_text(encoding="utf-8") == "v2\n"

    # --copy: a skill removed from the shared dir is republished as a COPY.
    import shutil

    shutil.rmtree(str(scope_root / "skills" / "s1"))
    _run(OverlaySync, pattern=None, source=str(src), global_scope=True,
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
        _run(OverlayAdd, name=["withsetup"], source=str(src), global_scope=True,
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


def test_requires_cycle_is_an_error(world):
    src, scope_root = world
    _overlay(src, "a", requires=["b"])
    _overlay(src, "b", requires=["a"])
    with pytest.raises(SystemExit, match="cycle"):
        _add(src, scope_root, "a")


def test_show_describes_installed_then_source(world, capsys):
    src, scope_root = world
    _overlay(src, "shown", requires=["base"], routing=["- R -> ~/.agents/kb/R.md"], skill="sk", setup=True)
    _run(OverlayShow, name="shown", source=str(src), global_scope=True, agents_dir=scope_root, json=True)
    info = json.loads(capsys.readouterr().out)
    assert info["where"] == "source"
    assert info["description"] == "the shown overlay"
    assert info["requires"] == ["base"] and info["skills"] == ["sk"] and info["setup"] == "setup.py"
    assert info["root_var"] == "SHOWN_OVERLAY_ROOT"
    _overlay(src, "base")
    _add(src, scope_root, "shown")
    _run(OverlayShow, name="shown", source=str(src), global_scope=True, agents_dir=scope_root, json=False)
    out = capsys.readouterr().out
    assert out.startswith("shown (installed)")


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


def test_findings_add_rejects_a_name_taken_by_frontmatter(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "findings_mod", SRC / "dotagents" / "_overlay" / "dotagents" / "cmds" / "findings.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    root = tmp_path / "findings"
    root.mkdir()
    (root / "foo.md").write_text("---\nname: bar\ndescription: hand note\n---\nbody\n", encoding="utf-8")
    store = mod.FindingsStore(root)
    with pytest.raises(SystemExit, match="already exists"):
        store.add("Something", name="bar")
