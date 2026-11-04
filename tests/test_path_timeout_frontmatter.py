"""`dotagents path`, the setup timeout, and front matter being dropped from
an overlay's declared context files.

tmp dirs only; the conftest isolates the home and the stores.
"""

import json
import logging
import os
import time

import pytest

from dotagents import _agents, _context, _overlays
from dotagents._scope import Scope
from dotagents.cli import OverlayAdd, PathCmd

from _helpers import run_cmd as _run

BASE_AGENTS = "<!-- dotagents:begin -->\n# BASE\n## Load on demand\n<!-- dotagents:end -->\n"


# --------------------------------------------------------------------------- #
# CONTEXT.md frontmatter
# --------------------------------------------------------------------------- #


def test_context_md_frontmatter_is_not_context(tmp_path):
    store = tmp_path / "store"
    ov = store / "overlays" / "legacy"
    ov.mkdir(parents=True)
    (ov / "overlay.cfg").write_text('name = "legacy"\ncontext = ["CONTEXT.md"]\n', encoding="utf-8")
    (ov / "CONTEXT.md").write_text(
        "---\npriority: 900\nscope: [runtime]\ndependencies: [gitlab]\n---\n# Legacy rules\nBODY\n",
        encoding="utf-8",
    )
    scope = Scope.of(agents_dir=store, project_root=tmp_path / "proj", global_scope=True)
    text = _context.assemble_context(_agents.CodexAgent(), scope)
    assert "# Legacy rules\nBODY" in text
    assert "priority: 900" not in text and "dependencies:" not in text


def test_context_md_is_context_by_default_and_context_lists_the_files(tmp_path):
    """The root's CONTEXT.md is an overlay's context; an empty `context`
    turns nothing off, `[defaults] context = false` does."""
    store = tmp_path / "store"
    ov = store / "overlays" / "plain"
    ov.mkdir(parents=True)
    (ov / "CONTEXT.md").write_text("DEFAULT-CONTEXT\n", encoding="utf-8")
    (store / "AGENTS.md").write_text("base\n", encoding="utf-8")
    scope = Scope.of(agents_dir=store, project_root=tmp_path / "proj", global_scope=True)
    assert "DEFAULT-CONTEXT" in _context.assemble_context(_agents.CodexAgent(), scope)
    (ov / "overlay.cfg").write_text("context = []\n", encoding="utf-8")
    assert "DEFAULT-CONTEXT" in _context.assemble_context(_agents.CodexAgent(), scope)
    (ov / "overlay.cfg").write_text("[defaults]\ncontext = false\n", encoding="utf-8")
    assert "DEFAULT-CONTEXT" not in _context.assemble_context(_agents.CodexAgent(), scope)


def test_only_a_closed_leading_block_is_frontmatter():
    strip = _context._strip_frontmatter
    assert strip("---\na: 1\n---\nbody\n") == "body\n"
    assert strip("﻿---\na: 1\n...\nbody\n") == "body\n"
    assert strip("---\nnever closed\n") == "---\nnever closed\n"
    assert strip("text\n---\na: 1\n---\n") == "text\n---\na: 1\n---\n"


# --------------------------------------------------------------------------- #
# dotagents path
# --------------------------------------------------------------------------- #


def _store(tmp_path, monkeypatch):
    store = tmp_path / "store"
    for name in ("aa", "bb"):
        ov = store / "overlays" / name
        (ov / "lib" / "python").mkdir(parents=True)
        (ov / "lib" / "python" / "m.py").write_text("", encoding="utf-8")
        (ov / "bin").mkdir()
        (ov / "bin" / "tool").write_text("", encoding="utf-8")
        (ov / "overlay.cfg").write_text('name = "%s"\n' % name, encoding="utf-8")
    # `aa` keeps the defaults (its bin/ and lib/python/); `bb` turns both off.
    with open(str(store / "overlays" / "bb" / "overlay.cfg"), "a", encoding="utf-8") as fh:
        fh.write('[defaults]\nbin = false\nlib = false\n')
    (store / "lib").mkdir()
    monkeypatch.setenv("AGENTS_HOME", str(store))
    monkeypatch.delenv("AGENTS_PROJECT_ROOT", raising=False)
    return store


def test_path_lists_the_bins_highest_precedence_first(tmp_path, monkeypatch, capsys):
    store = _store(tmp_path, monkeypatch)
    assert _run(PathCmd, global_scope=True, format="json") == 0
    dirs = json.loads(capsys.readouterr().out)
    assert dirs == [str(store / "bin"), str(store / "overlays" / "aa" / "bin")]


def test_path_lib_lists_only_existing_lib_dirs(tmp_path, monkeypatch, capsys):
    store = _store(tmp_path, monkeypatch)
    assert _run(PathCmd, global_scope=True, lib=True, format="native") == 0
    out = capsys.readouterr().out.strip().split(os.pathsep)
    assert out == [str(store / "lib"), str(store / "overlays" / "aa" / "lib" / "python")]


def test_path_posix_is_colon_joined_msys_form_on_windows(tmp_path, monkeypatch, capsys):
    _store(tmp_path, monkeypatch)
    assert _run(PathCmd, global_scope=True, format="posix") == 0
    out = capsys.readouterr().out.strip()
    assert ";" not in out and "\\" not in out
    if os.name == "nt":
        assert out.startswith("/") and ":" in out


def test_path_rejects_an_unknown_format(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match="--format"):
        _run(PathCmd, global_scope=True, format="yaml")


# --------------------------------------------------------------------------- #
# setup timeout
# --------------------------------------------------------------------------- #


def _slow_overlay(src, *, seconds, manifest_timeout=None):
    ov = src / "slow"
    ov.mkdir(parents=True)
    toml = 'name = "slow"\n[[setup]]\ninterpreter = "python"\npath = "setup.py"\n'
    if manifest_timeout is not None:
        toml += "timeout = %d\n" % manifest_timeout
    (ov / "overlay.cfg").write_text(toml, encoding="utf-8")
    (ov / "setup.py").write_text("import time\ntime.sleep(%s)\n" % seconds, encoding="utf-8")
    return ov


def _add(src, store, **extra):
    store.mkdir(exist_ok=True)
    (store / "AGENTS.md").write_text(BASE_AGENTS, encoding="utf-8")
    return _run(OverlayAdd, name=["slow"], repo=[str(src)], global_scope=True,
                agents_dir=store, copy=True, dry_run=False, **extra)


def test_the_manifest_setup_timeout_stops_a_hung_script(tmp_path, caplog):
    src = tmp_path / "src"
    _slow_overlay(src, seconds=30, manifest_timeout=1)
    started = time.monotonic()
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit, match="exit 124"):
        _add(src, tmp_path / "store")
    assert time.monotonic() - started < 20
    assert any("did not finish in 1s" in r.getMessage() for r in caplog.records)
    assert not (tmp_path / "store" / "overlays" / "slow").exists(), "rolled back"


def test_setup_timeout_zero_means_no_limit(tmp_path):
    src = tmp_path / "src"
    _slow_overlay(src, seconds=1.5, manifest_timeout=1)
    assert _add(src, tmp_path / "store", setup_timeout=0) == 0


def test_the_variant_declares_the_timeout_and_300_is_the_default(tmp_path):
    ov = _overlays.Overlay(_slow_overlay(tmp_path / "src", seconds=0, manifest_timeout=7))
    assert ov.setup_variant()["timeout"] == 7
    assert _overlays.DEFAULT_SETUP_TIMEOUT == 300


def test_an_invalid_timeout_refuses_the_manifest(tmp_path):
    ov = tmp_path / "bad"
    ov.mkdir()
    (ov / "overlay.cfg").write_text(
        'name = "bad"\n[[setup]]\ninterpreter = "python"\npath = "setup.py"\ntimeout = -5\n', encoding="utf-8")
    overlay = _overlays.Overlay(ov)
    assert overlay.setup_variant() is None
    assert any("/setup/0/timeout" in p for p in overlay.manifest_problems())
