"""`dotagents path`, the setup-script timeout, and CONTEXT.md frontmatter being
dropped from the assembled context (overlay.toml is the manifest).

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
    (ov / "overlay.toml").write_text('name = "legacy"\n', encoding="utf-8")
    (ov / "CONTEXT.md").write_text(
        "---\npriority: 900\nscope: [runtime]\ndependencies: [gitlab]\n---\n# Legacy rules\nBODY\n",
        encoding="utf-8",
    )
    scope = Scope.of(agents_dir=store, project_root=tmp_path / "proj", global_scope=True)
    text = _context.assemble_context(_agents.CodexAgent(), scope)
    assert "# Legacy rules\nBODY" in text
    assert "priority: 900" not in text and "dependencies:" not in text


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
        (ov / "lib").mkdir(parents=True)
        (ov / "overlay.toml").write_text('name = "%s"\n' % name, encoding="utf-8")
    (store / "lib").mkdir()
    monkeypatch.setenv("AGENTS_HOME", str(store))
    monkeypatch.delenv("AGENTS_PROJECT_ROOT", raising=False)
    return store


def test_path_lists_the_bins_highest_precedence_first(tmp_path, monkeypatch, capsys):
    store = _store(tmp_path, monkeypatch)
    assert _run(PathCmd, global_scope=True, format="json") == 0
    dirs = json.loads(capsys.readouterr().out)
    assert dirs[0] == str(store / "bin")
    assert set(dirs[1:]) == {str(store / "overlays" / n / "bin") for n in ("aa", "bb")}


def test_path_lib_lists_only_existing_lib_dirs(tmp_path, monkeypatch, capsys):
    store = _store(tmp_path, monkeypatch)
    assert _run(PathCmd, global_scope=True, lib=True, format="native") == 0
    out = capsys.readouterr().out.strip().split(os.pathsep)
    assert out[0] == str(store / "lib") and len(out) == 3


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
    toml = 'name = "slow"\n'
    if manifest_timeout is not None:
        toml += "setup_timeout = %d\n" % manifest_timeout
    (ov / "overlay.toml").write_text(toml, encoding="utf-8")
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


def test_the_flag_overrides_the_manifest_and_the_default(tmp_path):
    ov = _overlays.Overlay(_slow_overlay(tmp_path / "src", seconds=0, manifest_timeout=7))
    assert ov.setup_timeout() == 7
    assert ov.setup_timeout(3) == 3 and ov.setup_timeout(0) == 0
    bare = _overlays.Overlay(tmp_path / "none")
    assert bare.setup_timeout() == _overlays.DEFAULT_SETUP_TIMEOUT == 300


def test_an_invalid_manifest_timeout_falls_back_to_the_default(tmp_path):
    ov = tmp_path / "bad"
    ov.mkdir()
    (ov / "overlay.toml").write_text('name = "bad"\nsetup_timeout = -5\n', encoding="utf-8")
    assert _overlays.Overlay(ov).setup_timeout() == _overlays.DEFAULT_SETUP_TIMEOUT
