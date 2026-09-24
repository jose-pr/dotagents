"""Regression tests for the 2026-09-23 review's "Overlays, sources and skills"
findings: typed source errors, registry parsing, credential handling, the spec
grammar, manifest validation, overlay names, install records, and the
add/remove/sync lifecycle.

tmp dirs only; the git cases use a local bare repository, no network.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotagents import _overlays, _scope, _skills, _sources  # noqa: E402
from dotagents.cli import OverlayAdd, OverlayList, OverlayRemove, OverlayShow, OverlaySync  # noqa: E402

Overlay = _overlays.Overlay

BASE_AGENTS = (
    "<!-- dotagents:begin -->\n# Agent Directives\n\n## Always-on rules\n"
    "- **Base rule**: keep it.\n\n## Load on demand\nNothing ships here by default.\n"
    "<!-- dotagents:end -->\n"
)


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """No repo, store or project from the real environment leaks in."""
    for key in list(os.environ):
        if key.startswith(_sources.REPO_ENV_PREFIX):
            monkeypatch.delenv(key)
    for key in (_sources.REPO_ENV_DEFAULT, "AGENTS_PROJECT_ROOT", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "home-store"))
    monkeypatch.setenv("AGENTS_SYSTEM_ROOT", str(tmp_path / "no-system-store"))


def _run(cmd_cls, **kwargs):
    cmd = cmd_cls()
    for key, value in kwargs.items():
        setattr(cmd, key, value)
    return cmd()


def _overlay(src: Path, name: str, *, requires=(), routing=(), rules=None, files=(), skill=None, setup=None):
    ov = src / name
    ov.mkdir(parents=True, exist_ok=True)
    toml = 'name = "%s"\n' % name
    toml += "requires = [%s]\n" % ", ".join('"%s"' % r for r in requires)
    toml += "routing = [%s]\n" % ", ".join('"%s"' % r for r in routing)
    if rules is not None:
        toml += "rules = [%s]\n" % ", ".join('"%s"' % r for r in rules)
    (ov / "overlay.toml").write_text(toml, encoding="utf-8")
    for rel, body in files:
        (ov / rel).parent.mkdir(parents=True, exist_ok=True)
        (ov / rel).write_text(body, encoding="utf-8")
    if skill:
        (ov / "skills" / skill).mkdir(parents=True, exist_ok=True)
        (ov / "skills" / skill / "SKILL.md").write_text("---\nname: %s\n---\n" % skill, encoding="utf-8")
    if setup is not None:
        (ov / "setup.py").write_text(setup, encoding="utf-8")
    return ov


@pytest.fixture
def world(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    (store / "AGENTS.md").write_text(BASE_AGENTS, encoding="utf-8")
    return src, store


def _add(src, store, *names, **extra):
    kwargs = dict(
        name=list(names), repo=[str(src)] if src is not None else [], global_scope=True,
        agents_dir=store, copy=True, dry_run=False,
    )
    kwargs.update(extra)
    return _run(OverlayAdd, **kwargs)


def _sync(store, repo=None, **extra):
    kwargs = dict(
        repo=list(repo or []), global_scope=True, agents_dir=store, copy=True, dry_run=False,
    )
    kwargs.update(extra)
    return _run(OverlaySync, **kwargs)


# --------------------------------------------------------------------------
# overlays-06: a broken repo is an error, not "not found"
# --------------------------------------------------------------------------

def test_sync_with_an_unloadable_repo_fails_rather_than_skipping(world, tmp_path):
    src, store = world
    _overlay(src, "demo", files=[("kb/D.md", "d\n")])
    assert _add(src, store, "demo") == 0
    with pytest.raises(SystemExit, match="does not exist"):
        _sync(store, repo=[str(tmp_path / "no-such-repo")])


def test_a_dependency_whose_source_fails_is_an_error_not_a_skip(world, tmp_path):
    src, store = world
    top = _overlay(src, "top", requires=["dep"])
    registry = tmp_path / "reg.json"
    registry.write_text(json.dumps({"top": str(top), "dep": str(tmp_path / "gone")}), encoding="utf-8")
    with pytest.raises(SystemExit, match="does not exist"):
        _add(registry, store, "top")


def test_list_warns_per_broken_repo_and_still_lists_the_others(world, tmp_path, capsys, caplog):
    src, store = world
    _overlay(src, "listed")
    with caplog.at_level(logging.WARNING):
        _run(OverlayList, repo=[str(src), str(tmp_path / "nonexistent")], global_scope=True,
             agents_dir=store, json=True)
    assert json.loads(capsys.readouterr().out)["available"] == ["listed"]
    assert any("nonexistent" in r.getMessage() for r in caplog.records)


def test_source_errors_are_typed():
    assert issubclass(_sources.OverlayNotFound, _sources.SourceError)
    assert issubclass(_sources.SourceError, SystemExit)  # uncaught: a clean exit, no traceback


# --------------------------------------------------------------------------
# overlays-07: a malformed registry is a clean error
# --------------------------------------------------------------------------

@pytest.mark.parametrize("suffix, text, what", [
    (".json", '{"a": "x",}', "JSON"),
    (".toml", 'a = "x\n', "TOML"),
    (".yaml", "a: [x\n", "YAML"),
])
def test_malformed_registry_is_a_source_error(suffix, text, what):
    if suffix == ".toml" and sys.version_info < (3, 11):
        pytest.importorskip("tomli")
    if suffix == ".yaml":
        pytest.importorskip("yaml")
    with pytest.raises(_sources.SourceError, match="not valid %s" % what):
        _sources.parse_document(text, suffix, "reg" + suffix)


def test_malformed_store_registry_does_not_crash_list_or_add(world, capsys, caplog):
    src, store = world
    (store / "dotagents.json").write_text('{"a": "x",}', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert _run(OverlayList, repo=[], global_scope=True, agents_dir=store, json=True) == 0
    assert json.loads(capsys.readouterr().out)["available"] == []
    assert any("not valid JSON" in r.getMessage() for r in caplog.records)
    with pytest.raises(SystemExit, match="not valid JSON"):
        _add(None, store, "anything")


def test_non_utf8_registry_is_a_source_error(tmp_path):
    reg = tmp_path / "reg.json"
    reg.write_bytes(b'{"a": "\xff"}')
    with pytest.raises(_sources.SourceError, match="cannot read registry"):
        _sources.load_repo(str(reg), _sources.SourceCache(tmp_path / "cache"))


# --------------------------------------------------------------------------
# overlays-08: userinfo in an http(s) registry URL (stdlib fetch)
# --------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_stdlib_fetch_sends_userinfo_as_unredirected_basic_auth(monkeypatch):
    import base64
    import urllib.request

    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["request"] = request
        return _FakeResponse(b'{"a": "./a"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert _sources._read_url("https://alice:s3cretpw@example.invalid/reg.json") == '{"a": "./a"}'
    request = seen["request"]
    assert isinstance(request, urllib.request.Request)
    assert request.full_url == "https://example.invalid/reg.json"
    assert "s3cretpw" not in request.full_url
    # Unredirected: a redirect to another host never carries it.
    assert request.unredirected_hdrs["Authorization"] == "Basic " + base64.b64encode(b"alice:s3cretpw").decode()
    assert "Authorization" not in request.headers


def test_stdlib_fetch_failure_is_redacted(monkeypatch):
    import urllib.request

    def fake_urlopen(request, timeout=None):
        raise ValueError("bad url https://alice:s3cretpw@example.invalid/reg.json")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(_sources.SourceError) as info:
        _sources._read_url("https://alice:s3cretpw@example.invalid/reg.json")
    assert "s3cretpw" not in str(info.value)
