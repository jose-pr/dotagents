"""Scope resolution in the `env` / `context` COMMAND classes (not the library).

The library layer (`_env.get_environment`, `_context.assemble_context*`) has
always taken its two roots as explicit arguments, and `tests/test_env.py` /
`tests/test_context.py` pass tmp dirs straight in -- which is exactly why the
commands themselves could hardcode `Path.cwd()` / `Path.home() / ".agents"` for
this long without a test noticing (finding `env_context_ignore_scope_vars`).

These tests exercise the CLI classes end-to-end instead: they set
`$AGENTS_HOME` / `$AGENTS_PROJECT_ROOT`, chdir somewhere unrelated, run the
command, and assert the assembled output came from the configured roots
(D58/D79/D80). Every one of them fails on the pre-fix code.

tmp dirs only, no network. HOME/USERPROFILE are NEVER exported (a real
`~/.agents` must stay untouched) -- the user store is redirected with
`$AGENTS_HOME` and the project root with `$AGENTS_PROJECT_ROOT`, which is the
whole point of the fix.

Run from the repo root: ``python -m pytest tests/``.
"""

import json
import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from dotagents.cli._common import resolve_user_store  # noqa: E402
from dotagents.cli.context import Context  # noqa: E402
from dotagents.cli.env import Env  # noqa: E402


SCOPE_VARS = (
    "AGENTS_HOME",
    "DOTAGENTS_AGENTS_DIR",
    "AGENTS_PROJECT_ROOT",
    "CLAUDE_PROJECT_DIR",
)


def _py_emit(mapping):
    """An `env.py` body that unconditionally emits `mapping` as JSON changes."""
    return "import json\nprint(json.dumps(%r))\n" % (mapping,)


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """A user store + a project root + an unrelated cwd, with every scope var
    cleared so each test opts into exactly the ones it is about."""
    store = tmp_path / "store"
    project = tmp_path / "proj"
    elsewhere = tmp_path / "elsewhere"
    (project / ".agents").mkdir(parents=True)
    store.mkdir()
    elsewhere.mkdir()
    for var in SCOPE_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(elsewhere)
    return store, project, elsewhere


def _run_env(**fields):
    command = Env()
    command.format = "json"
    for key, value in fields.items():
        setattr(command, key, value)
    return command


def _env_json(command, capsys):
    assert command() == 0
    return json.loads(capsys.readouterr().out)


def _context_json(command, tmp_path, name="ctx.json"):
    out = tmp_path / name
    command.out = str(out)
    assert command() == 0
    return json.loads(out.read_text(encoding="utf-8"))


def _run_context(**fields):
    command = Context()
    command.format = "json"
    # Pin the agent so the assembled payload never depends on which harness the
    # test run happens to sit inside (`resolve_active_agent` reads os.environ).
    command.agents = ["claude"]
    for key, value in fields.items():
        setattr(command, key, value)
    return command


# --------------------------------------------------------------------------
# resolve_user_store: the shared precedence chain.
# --------------------------------------------------------------------------

def test_resolve_user_store_precedence(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit"
    from_new = tmp_path / "from-new"
    from_legacy = tmp_path / "from-legacy"

    monkeypatch.setenv("AGENTS_HOME", str(from_new))
    monkeypatch.setenv("DOTAGENTS_AGENTS_DIR", str(from_legacy))
    # 1. an explicit --agents-dir beats both vars.
    assert resolve_user_store(explicit) == explicit
    # 2. AGENTS_HOME beats the legacy name.
    assert resolve_user_store(None) == from_new
    # 3. the legacy name still works alone (one more release, D80).
    monkeypatch.delenv("AGENTS_HOME")
    assert resolve_user_store(None) == from_legacy
    # 4. neither set -> ~/.agents (computed, never touched).
    monkeypatch.delenv("DOTAGENTS_AGENTS_DIR")
    assert resolve_user_store(None) == Path.home() / ".agents"


def test_resolve_user_store_expands_a_tilde(monkeypatch):
    monkeypatch.setenv("AGENTS_HOME", os.path.join("~", "elsewhere"))
    assert resolve_user_store(None) == Path.home() / "elsewhere"


# --------------------------------------------------------------------------
# `dotagents env`
# --------------------------------------------------------------------------

def test_env_reads_the_store_from_agents_home(roots, monkeypatch, capsys):
    """The user store is `$AGENTS_HOME`, not `~/.agents`: a store env.py there
    must be executed and its vars must land in the output."""
    store, project, _ = roots
    (store / "env.py").write_text(_py_emit({"FROM_STORE": "yes"}), encoding="utf-8")
    monkeypatch.setenv("AGENTS_HOME", str(store))
    monkeypatch.setenv("AGENTS_PROJECT_ROOT", str(project))

    env = _env_json(_run_env(), capsys)
    assert env["FROM_STORE"] == "yes"


def test_env_pins_the_project_root_from_the_env_var(roots, monkeypatch, capsys):
    """Regression for the live bug: the SessionStart hook runs from wherever the
    session started, so with `$AGENTS_PROJECT_ROOT` pinned the project tier must
    resolve against THAT root, not the cwd."""
    store, project, elsewhere = roots
    (project / ".agents" / "env.py").write_text(
        _py_emit({"FROM_PROJECT": "yes"}), encoding="utf-8"
    )
    # A decoy under the cwd: picked up only by the old cwd-rooted behavior.
    (elsewhere / ".agents").mkdir()
    (elsewhere / ".agents" / "env.py").write_text(
        _py_emit({"FROM_CWD": "yes"}), encoding="utf-8"
    )
    monkeypatch.setenv("AGENTS_HOME", str(store))
    monkeypatch.setenv("AGENTS_PROJECT_ROOT", str(project))

    env = _env_json(_run_env(), capsys)
    assert env["FROM_PROJECT"] == "yes"
    assert "FROM_CWD" not in env


def test_env_falls_back_to_the_harness_project_var(roots, monkeypatch, capsys):
    """`$CLAUDE_PROJECT_DIR` is the documented fallback (`project_root_default`),
    so a Claude Code hook needs no dotagents-specific var set."""
    store, project, _ = roots
    (project / ".agents" / "env.py").write_text(
        _py_emit({"FROM_PROJECT": "yes"}), encoding="utf-8"
    )
    monkeypatch.setenv("AGENTS_HOME", str(store))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

    env = _env_json(_run_env(), capsys)
    assert env["FROM_PROJECT"] == "yes"


def test_env_agents_dir_flag_beats_the_env_var(roots, tmp_path, monkeypatch, capsys):
    store, project, _ = roots
    override = tmp_path / "override-store"
    override.mkdir()
    (store / "env.py").write_text(_py_emit({"FROM_STORE": "yes"}), encoding="utf-8")
    (override / "env.py").write_text(
        _py_emit({"FROM_OVERRIDE": "yes"}), encoding="utf-8"
    )
    monkeypatch.setenv("AGENTS_HOME", str(store))
    monkeypatch.setenv("AGENTS_PROJECT_ROOT", str(project))

    env = _env_json(_run_env(agents_dir=override), capsys)
    assert env["FROM_OVERRIDE"] == "yes"
    assert "FROM_STORE" not in env


def test_env_global_scope_skips_project_files_but_keeps_the_store(
    roots, monkeypatch, capsys
):
    """`-g` here means "skip the project tier", NOT "resolve a different store"
    (plan design Q1) -- the store's own env.py must still be evaluated."""
    store, project, _ = roots
    (store / "env.py").write_text(_py_emit({"FROM_STORE": "yes"}), encoding="utf-8")
    (project / ".agents" / "env.py").write_text(
        _py_emit({"FROM_PROJECT": "yes"}), encoding="utf-8"
    )
    monkeypatch.setenv("AGENTS_HOME", str(store))
    monkeypatch.setenv("AGENTS_PROJECT_ROOT", str(project))

    env = _env_json(_run_env(global_scope=True), capsys)
    assert env["FROM_STORE"] == "yes"
    assert "FROM_PROJECT" not in env


def test_env_emits_the_resolved_roots(roots, monkeypatch, capsys):
    """The two vars `env` emits are the two roots it resolved -- otherwise a
    subprocess reading them would disagree with the parent that wrote them."""
    store, project, _ = roots
    monkeypatch.setenv("AGENTS_HOME", str(store))
    monkeypatch.setenv("AGENTS_PROJECT_ROOT", str(project))

    env = _env_json(_run_env(), capsys)
    assert Path(env["AGENTS_HOME"]) == store
    assert Path(env["AGENTS_PROJECT_ROOT"]) == project


# --------------------------------------------------------------------------
# `dotagents context`
# --------------------------------------------------------------------------

def test_context_reads_the_store_from_agents_home(roots, tmp_path, monkeypatch):
    store, project, _ = roots
    (store / "AGENTS.md").write_text("# store rules\n", encoding="utf-8")
    monkeypatch.setenv("AGENTS_HOME", str(store))
    monkeypatch.setenv("AGENTS_PROJECT_ROOT", str(project))

    payload = _context_json(_run_context(), tmp_path)
    assert str(store / "AGENTS.md") in payload["sources"]
    assert "store rules" in payload["context"]


def test_context_pins_the_project_root_from_the_env_var(roots, tmp_path, monkeypatch):
    """The SessionStart hook's live case: run from a subdirectory, assemble the
    PINNED project's context -- not whatever `.agents` sits under the cwd."""
    store, project, elsewhere = roots
    (project / ".agents" / "AGENTS.md").write_text("# project rules\n", encoding="utf-8")
    (elsewhere / ".agents").mkdir()
    (elsewhere / ".agents" / "AGENTS.md").write_text("# cwd rules\n", encoding="utf-8")
    monkeypatch.setenv("AGENTS_HOME", str(store))
    monkeypatch.setenv("AGENTS_PROJECT_ROOT", str(project))

    payload = _context_json(_run_context(), tmp_path)
    assert str(project / ".agents" / "AGENTS.md") in payload["sources"]
    assert str(elsewhere / ".agents" / "AGENTS.md") not in payload["sources"]


def test_context_agents_dir_flag_beats_the_env_var(roots, tmp_path, monkeypatch):
    store, project, _ = roots
    override = tmp_path / "override-store"
    override.mkdir()
    (store / "AGENTS.md").write_text("# store rules\n", encoding="utf-8")
    (override / "AGENTS.md").write_text("# override rules\n", encoding="utf-8")
    monkeypatch.setenv("AGENTS_HOME", str(store))
    monkeypatch.setenv("AGENTS_PROJECT_ROOT", str(project))

    payload = _context_json(_run_context(agents_dir=override), tmp_path)
    assert str(override / "AGENTS.md") in payload["sources"]
    assert str(store / "AGENTS.md") not in payload["sources"]
