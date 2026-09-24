"""Regressions for the scope-resolution and context-assembly review fixes
(2026-09-23 review, "Scope resolution and context assembly").

tmp dirs only. The user store is redirected with `$AGENTS_HOME`, the system
store with `$AGENTS_SYSTEM_ROOT` (a path that does not exist), and the project
root with `$AGENTS_PROJECT_ROOT` or a chdir; the session's own pins are cleared
first so a hooked shell cannot leak the real checkout into a test.
"""

import json
import logging
import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from dotagents import _agents, _context, _env, _scope  # noqa: E402
from dotagents._fs import write_text_lf  # noqa: E402
from dotagents._scope import Scope  # noqa: E402


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

    monkeypatch.chdir(proj_b)
    with caplog.at_level(logging.WARNING, logger="dotagents"):
        _scope.resolve_scope(False)
    warned = [r.getMessage() for r in caplog.records if "outside the pinned" in r.getMessage()]
    assert warned and str(proj_a) in warned[0] and "$AGENTS_PROJECT_ROOT" in warned[0]

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
