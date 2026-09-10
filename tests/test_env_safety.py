"""Env-chain safety: which files run, how a failed source is reported, and what
bash itself adds to a sourced file's output (review 2026-09-09, findings 1.6 and
the env/scope items).

tmp dirs only, no network. Run from repo root: ``python -m pytest tests/``.
"""

import os
import shutil
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from dotagents import _env  # noqa: E402
from dotagents._scope import Scope  # noqa: E402
from dotagents import _scope  # noqa: E402

HAVE_BASH = shutil.which("bash") is not None


def _py_emit(mapping):
    return "import json\nprint(json.dumps(%r))\n" % (mapping,)


@pytest.fixture
def roots(tmp_path):
    agents_dir = tmp_path / "agents"
    project_root = tmp_path / "proj"
    (project_root / ".agents").mkdir(parents=True)
    agents_dir.mkdir()
    return agents_dir, project_root


def _run(agents_dir, project_root, global_scope=False, **kw):
    return _env.get_environment(
        Scope.of(agents_dir=agents_dir, project_root=project_root, global_scope=global_scope),
        base_env={"PATH": "/usr/bin"}, **kw,
    )


# --------------------------------------------------------------------------
# 1.6: a checkout's own top-level env.py / env is never executed or sourced.
# --------------------------------------------------------------------------

def test_project_root_env_py_is_never_executed(roots):
    """Every session start runs the chain, so `<repo>/env.py` was code
    execution from any cloned checkout. Only `<repo>/.agents/env.py` runs."""
    agents_dir, project_root = roots
    (project_root / "env.py").write_text(_py_emit({"PWNED": "1"}), encoding="utf-8")
    (project_root / ".agents" / "env.py").write_text(
        _py_emit({"FROM_DOT_AGENTS": "1"}), encoding="utf-8"
    )
    env = _run(agents_dir, project_root)
    assert "PWNED" not in env
    assert env["FROM_DOT_AGENTS"] == "1"


def test_project_root_local_env_still_resolves(roots):
    """`local.env` / `pre.local.env` at the project root stay: they are the
    user's own gitignored files, not something a checkout ships."""
    agents_dir, project_root = roots
    (project_root / "local.env").write_text("", encoding="utf-8")
    (project_root / "pre.local.env").write_text("", encoding="utf-8")
    (project_root / "env").write_text("", encoding="utf-8")
    (project_root / "pre.env").write_text("", encoding="utf-8")
    resolved = _env.resolve_env_files(scope=Scope.of(agents_dir=agents_dir, project_root=project_root, global_scope=False))
    names = [(lvl, p.name) for lvl, p, _ in resolved]
    assert ("project-root", "local.env") in names
    assert ("project-root", "pre.local.env") in names
    assert ("project-root", "env") not in names
    assert ("project-root", "pre.env") not in names


def test_directory_named_env_is_not_an_env_file(roots):
    """`env/` is a common virtualenv name; a directory is never "sourced"."""
    agents_dir, project_root = roots
    (project_root / ".agents" / "env").mkdir()
    (agents_dir / "env.py").mkdir()
    resolved = _env.resolve_env_files(scope=Scope.of(agents_dir=agents_dir, project_root=project_root, global_scope=False))
    assert resolved == []


# --------------------------------------------------------------------------
# A failed `source` is reported as a failure (rc != 0), not as an empty success,
# and bash's own vars are never reported as the file's changes.
# --------------------------------------------------------------------------

@pytest.mark.skipif(not HAVE_BASH, reason="needs bash")
def test_failed_source_contributes_nothing(roots, caplog):
    """A syntax error mid-file: the old `source F; env -0` list ran `env -0`
    regardless, returned rc 0 and reported the assignments before the error."""
    agents_dir, project_root = roots
    (agents_dir / "env").write_text(
        "export BEFORE=1\nif [ ; then\nexport AFTER=1\n", encoding="utf-8"
    )
    import logging

    with caplog.at_level(logging.WARNING, logger="t"):
        env = _run(agents_dir, project_root, logger=logging.getLogger("t"))
    assert "BEFORE" not in env and "AFTER" not in env
    assert any("source failed" in r.getMessage() for r in caplog.records)


@pytest.mark.skipif(not HAVE_BASH, reason="needs bash")
def test_bash_own_vars_are_not_reported(roots):
    """Git Bash from a minimal env adds PWD (as `/c/...`), SHLVL, MSYSTEM;
    emitted into a PowerShell session they are simply wrong."""
    agents_dir, project_root = roots
    (agents_dir / "env").write_text("export ONLY=me\n", encoding="utf-8")
    env = _run(agents_dir, project_root)
    assert env["ONLY"] == "me"
    for bash_own in ("PWD", "OLDPWD", "SHLVL", "MSYSTEM", "_"):
        assert bash_own not in env


def test_changed_env_survives_non_utf8_bytes():
    out = _env._changed_env(b"OK=1\0BAD=\xff\xfe\0", {})
    assert out["OK"] == "1"
    assert "BAD" in out  # kept via surrogateescape, not a crash


# --------------------------------------------------------------------------
# resolve_scope honours the configurable store for -g and --agents-dir.
# --------------------------------------------------------------------------

def test_global_scope_uses_agents_home(tmp_path, monkeypatch):
    """`init -g` / `overlays ... -g` landed in the literal ~/.agents even when
    the session had pinned $AGENTS_HOME (the very var `env` emits)."""
    pinned = tmp_path / "pinned"
    monkeypatch.setenv("AGENTS_HOME", str(pinned))
    assert _scope.resolve_scope(True).agents_root == pinned
    # An explicit --agents-dir still wins.
    assert _scope.resolve_scope(True, agents_dir=tmp_path / "x").agents_root == tmp_path / "x"


def test_agents_dir_overrides_the_project_store(tmp_path):
    """`--agents-dir` is documented as the store override for EITHER scope; it
    used to be silently ignored without -g."""
    scope = _scope.resolve_scope(False, agents_dir=tmp_path / "store", project_root=tmp_path / "p")
    assert scope.level == "project"
    assert scope.agents_root == tmp_path / "store"


def test_project_scope_overlays_are_walked(roots):
    """`overlays add` installs into the project scope by default; its bin/env/
    root var were invisible to the chain (only the store's overlays were walked)."""
    agents_dir, project_root = roots
    pov = project_root / ".agents" / "overlays" / "projov"
    (pov / "bin").mkdir(parents=True)
    (pov / "env.py").write_text(_py_emit({"FROM_PROJECT_OVERLAY": "1"}), encoding="utf-8")
    env = _run(agents_dir, project_root)
    assert env["FROM_PROJECT_OVERLAY"] == "1"
    assert env["PROJOV_OVERLAY_ROOT"] == str(pov)
    assert str(pov / "bin") in env["PATH"].split(os.pathsep)
    # -g drops them with the rest of the project tier.
    env_g = _run(agents_dir, project_root, global_scope=True)
    assert "FROM_PROJECT_OVERLAY" not in env_g and "PROJOV_OVERLAY_ROOT" not in env_g


def test_project_overlay_shadows_a_same_named_store_overlay(roots):
    """One name, two scopes = ONE overlay, the project's: its root var, bin and
    env.py are the only ones in play; the store's copy contributes nothing."""
    agents_dir, project_root = roots
    store = agents_dir / "overlays" / "same"
    proj = project_root / ".agents" / "overlays" / "same"
    for ov, tag in ((store, "store"), (proj, "project")):
        (ov / "bin").mkdir(parents=True)
        (ov / "env.py").write_text(_py_emit({"WHICH": tag, tag.upper(): "1"}), encoding="utf-8")
    env = _run(agents_dir, project_root)
    assert env["SAME_OVERLAY_ROOT"] == str(proj)
    assert env["WHICH"] == "project" and "STORE" not in env
    path = env["PATH"].split(os.pathsep)
    assert str(proj / "bin") in path and str(store / "bin") not in path
    # -g: no project scope, so the store's copy is back.
    env_g = _run(agents_dir, project_root, global_scope=True)
    assert env_g["SAME_OVERLAY_ROOT"] == str(store) and env_g["WHICH"] == "store"
    # A session that already pinned the STORE's root (the SessionStart env)
    # gets re-pointed at the project's copy, not left stale.
    env_pinned = _env.get_environment(
        scope=Scope.of(agents_dir=agents_dir, project_root=project_root, global_scope=False),
        base_env={"PATH": "/usr/bin", "SAME_OVERLAY_ROOT": str(store)},
    )
    assert env_pinned["SAME_OVERLAY_ROOT"] == str(proj)


def test_level_names_are_not_valid_overlay_names():
    from dotagents._overlays import Overlay

    for reserved in ("user", "project", "system", "project-root", "default", "overlay", "User"):
        assert not Overlay.is_valid_name(reserved)
    assert Overlay.is_valid_name("users")


def test_source_available_applies_the_overlay_name_rule(tmp_path):
    src = tmp_path / "src"
    for name in ("good", "__pycache__", ".hidden", "2fast"):
        (src / name).mkdir(parents=True)
    assert _scope.OverlaySource(src).available() == ["good"]
