"""Regressions for the scope-resolution and context-assembly fixes.

tmp dirs only. The user store is redirected with `$AGENTS_HOME`, the system
store with `$AGENTS_SYSTEM_ROOT` (a path that does not exist), and the project
root with `$AGENTS_PROJECT_ROOT` or a chdir; the session's own pins are cleared
first so a hooked shell cannot leak the real checkout into a test.
"""

import json
import logging
import os
import pytest

from dotagents import _agents, _context, _env, _scope
from dotagents._fs import write_text_lf
from dotagents._scope import Scope


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    for var in ("AGENTS_PROJECT_ROOT", "CLAUDE_PROJECT_DIR", "AGENTS_HOME", "AGENTS_HARNESS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AGENTS_SYSTEM_ROOT", str(tmp_path / "no-system-store"))


# --------------------------------------------------------------------------
# scope-context-02: a session in the home directory walks the user store once
# --------------------------------------------------------------------------

@pytest.fixture
def home_store(tmp_path, monkeypatch):
    """A fake home whose `.agents` is the user store, with one overlay."""
    home = tmp_path / "home"
    store = home / ".agents"
    ov = store / "overlays" / "ov"
    ov.mkdir(parents=True)
    write_text_lf(store / "AGENTS.md", "STORE-RULES\n")
    write_text_lf(ov / "CONTEXT.md", "OV-CONTEXT\n")
    counter = (
        "import json, os\n"
        "print(json.dumps({'COUNT': str(int(os.environ.get('COUNT', '0')) + 1)}))\n"
    )
    write_text_lf(store / "env.py", counter)
    write_text_lf(ov / "env.py", counter)
    monkeypatch.setenv("AGENTS_HOME", str(store))
    return home, store


def test_home_project_is_the_user_scope(home_store):
    home, store = home_store
    scope = Scope.of(agents_dir=store, project_root=home)
    assert scope.level == "user"
    assert scope.agents_root == store
    assert scope.stores == [store]
    # The project-root level is still walked (a `~/AGENTS.local.md`).
    assert scope.project_root == home
    write_text_lf(home / "AGENTS.local.md", "HOME-LOCAL\n")
    levels = [lvl for lvl, _p, _r in scope.paths({"default": "AGENTS.md", "project-root": "AGENTS.local.md"})]
    assert levels == ["user", "project-root"]


def test_home_project_runs_each_env_file_once(home_store):
    home, store = home_store
    scope = Scope.of(agents_dir=store, project_root=home)
    files = [(lvl, p.name) for lvl, p, _r in _env.resolve_env_files(scope)]
    assert files == [("ov", "env.py"), ("user", "env.py")]
    base = {k: v for k, v in os.environ.items() if k != "COUNT"}
    changes = _env.get_environment(scope, base_env=base, explicit="gemini")
    assert changes["COUNT"] == "2"


def test_home_project_emits_each_context_file_once(home_store):
    home, store = home_store
    scope = Scope.of(agents_dir=store, project_root=home)
    text = _context.assemble_context(_agents.GeminiAgent(), scope)
    assert text.count("STORE-RULES") == 1
    assert text.count("OV-CONTEXT") == 1


def test_init_in_home_targets_the_user_scope(home_store, monkeypatch):
    """`cd ~ && dotagents init` wrote the PROJECT block into the user store."""
    home, store = home_store
    monkeypatch.chdir(home)
    scope = _scope.resolve_scope(False)
    assert scope.global_scope and scope.agents_root == store


def test_a_store_that_appears_twice_is_walked_once(tmp_path):
    system = tmp_path / "etc" / "agents"
    system.mkdir(parents=True)
    user = tmp_path / "u"
    scope = Scope("project", system, user_root=user, system_root=system)
    assert scope.stores == [system, user]


# --------------------------------------------------------------------------
# scope-context-03: a pin the cwd has left is reported by the writing commands
# --------------------------------------------------------------------------

def test_writing_outside_the_pinned_root_warns(tmp_path, monkeypatch, caplog):
    proj_a, proj_b = tmp_path / "projA", tmp_path / "projB"
    (proj_a / "sub").mkdir(parents=True)
    proj_b.mkdir()
    monkeypatch.setenv("AGENTS_PROJECT_ROOT", str(proj_a))

    monkeypatch.chdir(proj_a / "sub")
    with caplog.at_level(logging.WARNING, logger="dotagents"):
        assert _scope.resolve_scope(False).agents_root == proj_a / ".agents"
    assert not [r for r in caplog.records if "outside the pinned" in r.getMessage()]

    # cwd outside the pin: the project the cwd is in, not the pinned one.
    (proj_b / ".git").mkdir()
    (proj_b / "sub").mkdir()
    monkeypatch.chdir(proj_b / "sub")
    with caplog.at_level(logging.WARNING, logger="dotagents"):
        assert _scope.resolve_scope(False).agents_root == proj_b / ".agents"
    warned = [r.getMessage() for r in caplog.records if "outside the pinned" in r.getMessage()]
    assert warned and str(proj_a) in warned[0] and "$AGENTS_PROJECT_ROOT" in warned[0]

    # A fresh directory (no .git, no .agents) is its own project.
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    monkeypatch.chdir(fresh)
    assert _scope.resolve_scope(False).agents_root == fresh / ".agents"

    # An explicit store is the target, so the pin is not worth a warning.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="dotagents"):
        _scope.resolve_scope(False, agents_dir=proj_b / ".agents")
    assert not [r for r in caplog.records if "outside the pinned" in r.getMessage()]


# --------------------------------------------------------------------------
# Context assembly fixtures
# --------------------------------------------------------------------------

@pytest.fixture
def ctx(tmp_path, monkeypatch):
    """A user store with a `py` overlay, and a project with its own store."""
    store = tmp_path / "store"
    project = tmp_path / "proj"
    ov = store / "overlays" / "py"
    (project / ".agents").mkdir(parents=True)
    ov.mkdir(parents=True)
    monkeypatch.setenv("AGENTS_HOME", str(store))
    monkeypatch.setenv("AGENTS_PROJECT_ROOT", str(project))
    monkeypatch.chdir(project)
    return store, project, ov


def _gemini(scope, **kw):
    return _context.assemble_context(_agents.GeminiAgent(), scope, **kw)


def _cli(**fields):
    from dotagents.cli.context import Context

    command = Context()
    command.agents = ["gemini"]
    for key, value in fields.items():
        setattr(command, key, value)
    return command


# --------------------------------------------------------------------------
# scope-context-05: --inline resolves each source's refs against its own dir
# --------------------------------------------------------------------------

def test_inline_resolves_overlay_variable_refs_and_each_sources_own_dir(ctx):
    store, project, ov = ctx
    write_text_lf(store / "AGENTS.md", "- Python work -> $PY_OVERLAY_ROOT/kb/PYTHON.md\n")
    write_text_lf(store / "kb" / "PYTHON.md", "STORE-KB-SHADOWS-OVERLAY\n")
    write_text_lf(ov / "CONTEXT.md", "Read kb/PYTHON.md, and `${PY_OVERLAY_ROOT}/kb/STYLE.md`.\n")
    write_text_lf(ov / "kb" / "PYTHON.md", "PY-KB-BODY\n")
    write_text_lf(ov / "kb" / "STYLE.md", "PY-STYLE-BODY\n")
    write_text_lf(project / ".agents" / "AGENTS.md", "Before a release read kb/RELEASE.md.\n")
    write_text_lf(project / ".agents" / "kb" / "RELEASE.md", "PROJECT-KB-BODY\n")

    scope = Scope.of(agents_dir=store, project_root=project)
    text = _gemini(scope, inline=True)
    inlined = text.split("## On-Demand Files (Inlined)", 1)[1]
    assert inlined.count("PY-KB-BODY") == 1          # via $VAR and via the overlay's own kb/
    assert "PY-STYLE-BODY" in inlined                 # ${VAR} form
    assert "STORE-KB-SHADOWS-OVERLAY" not in inlined  # the store's same-named file
    assert "PROJECT-KB-BODY" in inlined               # the project store is a search root
    assert "### PYTHON_OVERLAY_ROOT" not in inlined


def test_inline_never_resolves_a_user_ref_into_the_project(ctx):
    store, project, ov = ctx
    write_text_lf(store / "AGENTS.md", "see kb/ONLY-HERE.md\n")
    write_text_lf(project / "kb" / "ONLY-HERE.md", "CWD-REPO-BODY\n")
    for global_scope in (True, False):
        scope = Scope.of(agents_dir=store, project_root=project, global_scope=global_scope)
        assert "CWD-REPO-BODY" not in _gemini(scope, inline=True)


def test_var_refs_are_not_misread_as_relative_refs():
    text = "a $PY_OVERLAY_ROOT/kb/A.md b ${PY_OVERLAY_ROOT}/kb/B.md `$X/kb/C.md` $lower/kb/D.md"
    assert _context._find_md_refs(text) == []


# --------------------------------------------------------------------------
# scope-context-06: $NAME_OVERLAY_ROOT expands in handed-over output only
# --------------------------------------------------------------------------

def test_overlay_root_variables_expand_in_transient_output(ctx):
    store, project, ov = ctx
    write_text_lf(
        store / "AGENTS.md",
        "- Python -> $PY_OVERLAY_ROOT/kb/PYTHON.md and ${PY_OVERLAY_ROOT}/x.md, $NOPE_OVERLAY_ROOT/y.md\n",
    )
    scope = Scope.of(agents_dir=store, project_root=project)
    text = _gemini(scope)
    assert "%s/kb/PYTHON.md" % ov in text and "%s/x.md" % ov in text
    assert "$PY_OVERLAY_ROOT" not in text and "${PY_OVERLAY_ROOT}" not in text
    assert "$NOPE_OVERLAY_ROOT/y.md" in text  # not an installed overlay
    data = _context.assemble_context_data(_agents.GeminiAgent(), scope)
    assert "$PY_OVERLAY_ROOT" not in data["context"]


def test_write_agent_keeps_the_variable_and_stdout_expands_it(ctx, capsys):
    store, project, ov = ctx
    write_text_lf(store / "AGENTS.md", "- Python -> $PY_OVERLAY_ROOT/kb/PYTHON.md\n")
    assert _cli()() == 0
    assert "%s/kb/PYTHON.md" % ov in capsys.readouterr().out
    assert _cli(write_agent=True)() == 0
    written = (project / "GEMINI.md").read_text(encoding="utf-8")
    assert "$PY_OVERLAY_ROOT/kb/PYTHON.md" in written
    assert "%s/kb" % ov not in written


# --------------------------------------------------------------------------
# scope-context-07 / -08: skills precedence, frontmatter, and path
# --------------------------------------------------------------------------

def _skill(root, dirname, body):
    write_text_lf(root / "skills" / dirname / "SKILL.md", body)
    return root / "skills" / dirname / "SKILL.md"


def _skill_names(scope):
    return {s[0]: s[1] for s in _context._collect_skills(scope)}


def test_a_project_skill_shadows_the_users(ctx):
    store, project, ov = ctx
    _skill(store, "dup", "---\nname: dup\ndescription: USER COPY\n---\n")
    _skill(project / ".agents", "dup-dir", "---\nname: dup\ndescription: PROJECT COPY\n---\n")
    scope = Scope.of(agents_dir=store, project_root=project)
    assert _skill_names(scope) == {"dup": "PROJECT COPY"}


def test_skill_frontmatter_folds_block_scalars_and_ignores_the_body(ctx):
    store, project, ov = ctx
    _skill(store, "folded", "---\nname: folded\ndescription: >\n  line one\n  line two\n---\nname: body\n")
    _skill(store, "quoted", "---\nname: \"quoted\"\ndescription: 'a: b'\n---\n")
    _skill(store, "nofront", "# no frontmatter\nname: nofront\ndescription: x\n")
    scope = Scope.of(agents_dir=store, project_root=project, global_scope=True)
    assert _skill_names(scope) == {"folded": "line one line two", "quoted": "a: b"}


def test_skills_listing_names_each_skill_md(ctx):
    store, project, ov = ctx
    write_text_lf(store / "AGENTS.md", "rules\n")
    md = _skill(store, "some-dir", "---\nname: tool\ndescription: does it\n---\n")
    scope = Scope.of(agents_dir=store, project_root=project, global_scope=True)
    assert "- **tool** (`%s`): does it" % md in _gemini(scope)
    data = _context.assemble_context_data(_agents.GeminiAgent(), scope)
    assert data["skills"] == [{"name": "tool", "description": "does it", "path": str(md)}]


# --------------------------------------------------------------------------
# scope-context-09: context CLI agent handling and output
# --------------------------------------------------------------------------

def test_an_output_file_takes_one_agent(ctx, tmp_path):
    store, project, ov = ctx
    write_text_lf(store / "AGENTS.md", "rules\n")
    with pytest.raises(SystemExit, match="one agent"):
        _cli(agents=["gemini,codex"], out=str(tmp_path / "out.md"))()
    # JSON holds an array, so it still takes several.
    assert _cli(agents=["gemini,codex"], out=str(tmp_path / "o.json"), format="json")() == 0
    assert len(json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))) == 2


def test_no_known_agent_exits_2_and_harness_ids_resolve(ctx, capsys):
    store, project, ov = ctx
    write_text_lf(store / "AGENTS.md", "rules\n")
    assert _cli(agents=["nosuch"])() == 2
    assert _cli(agents=["gemini-cli"], format="json")() == 0
    assert json.loads(capsys.readouterr().out)["agent"] == "gemini"


def test_an_empty_assembly_gets_no_reminder_wrapper(ctx, capsys):
    assert _cli(format="system-reminder")() == 0
    assert "system-reminder" not in capsys.readouterr().out


# --------------------------------------------------------------------------
# scope-context-10: overlay sources sort by Overlay.sort_key, before stores
# --------------------------------------------------------------------------

def test_context_orders_overlays_like_the_managed_block(ctx):
    store, project, ov = ctx
    write_text_lf(store / "AGENTS.md", "STORE-RULES\n")
    overlays = (("a-dir", "zzz", 500), ("b-dir", "aaa", 500), ("late", "late", 20000))
    for dirname, name, priority in overlays:
        d = store / "overlays" / dirname
        write_text_lf(d / "overlay.toml", 'name = "%s"\npriority = %d\n' % (name, priority))
        write_text_lf(d / "CONTEXT.md", "CTX-%s\n" % dirname)
    scope = Scope.of(agents_dir=store, project_root=project, global_scope=True)
    text = _gemini(scope)
    order = [text.index(m) for m in ("CTX-b-dir", "CTX-a-dir", "CTX-late", "STORE-RULES")]
    assert order == sorted(order)


def test_discovery_reads_agents_dir_as_the_user_store_only_where_it_is_one():
    """scope-04: `launch claude -- --agents-dir x` (the harness's argument) and
    `findings list --agents-dir x` (a PROJECT store) both replaced the user
    tier of command discovery."""
    from dotagents import cli

    read = cli._agents_dir_from_argv
    assert read(["launch", "claude", "--", "--agents-dir", "zzz"]) is None
    assert read(["findings", "list", "--agents-dir", "x/.agents"]) is None
    assert read(["init", "--agents-dir=x/.agents"]) is None
    assert read(["findings", "list", "-g", "--agents-dir", "x"]) == "x"
    assert read(["-v", "context", "--agents-dir=x"]) == "x"
    assert read(["env", "--agents-dir", "x"]) == "x"
    # `env` here is --cmdspath's value, not the command.
    assert read(["--cmdspath", "env", "init", "--agents-dir", "x"]) is None
