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
from _helpers import run_cmd as _run

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
    with pytest.raises(SystemExit, match="sync failed for: demo"):
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


# --------------------------------------------------------------------------
# helpers: a local bare git repository (no network)
# --------------------------------------------------------------------------

def _git(*args, cwd=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, check=True,
                   capture_output=True, text=True, env=env)


def _bare_repo(tmp_path: Path, name: str, files: dict) -> Path:
    """A bare repo ``<tmp>/<name>.git`` whose ``main`` holds ``files``."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    work = tmp_path / ("%s-work" % name)
    work.mkdir()
    _git("init", "-q", "-b", "main", cwd=work)
    for rel, body in files.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(body, encoding="utf-8")
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "init", cwd=work)
    bare = tmp_path / ("%s.git" % name)
    _git("clone", "-q", "--bare", str(work), str(bare))
    return bare


# --------------------------------------------------------------------------
# overlays-16: paths are not re-parsed; `@` belongs to a non-git location
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("/srv/overlays@v2", _sources.Spec("dir", "/srv/overlays@v2")),
    ("sftp://user@host", _sources.Spec("url", "sftp://user@host")),
    ("https://h/cfg@x/reg.json", _sources.Spec("url", "https://h/cfg@x/reg.json")),
    # git keeps its ref
    ("/srv/x.git@v2", _sources.Spec("git", "/srv/x.git", "v2")),
    ("ssh://git@host/org/x@dev", _sources.Spec("git", "ssh://git@host/org/x", "dev")),
])
def test_only_a_git_location_has_a_ref(text, expected):
    assert _sources.parse_spec(text) == expected


def test_a_store_path_with_a_hash_still_finds_its_registry(tmp_path):
    store = tmp_path / "proj#1" / "store"
    store.mkdir(parents=True)
    (store / "AGENTS.md").write_text(BASE_AGENTS, encoding="utf-8")
    _overlay(store.parent / "ovs", "x", files=[("kb/X.md", "x\n")])
    (store / "dotagents.json").write_text(json.dumps({"x": "../ovs/x"}), encoding="utf-8")
    assert _add(None, store, "x") == 0
    assert (store / "overlays" / "x" / "kb" / "X.md").is_file()


# --------------------------------------------------------------------------
# overlays-17: relative git entries and non-http URL registries
# --------------------------------------------------------------------------

def test_relative_git_entries_resolve_beside_the_registry():
    rr = _sources.resolve_relative
    parse = _sources.parse_spec
    Spec = _sources.Spec
    local = rr(parse("../shared.git@v2"), Spec("dir", os.path.join(os.sep, "srv", "reg")), origin="r", key="k")
    assert local == Spec("git", os.path.normpath(os.path.join(os.sep, "srv", "shared.git")), "v2", None)
    # Inside a git registry: like a relative submodule URL.
    base = Spec("git", "https://h/org/a.git", "main", "overlays")
    assert rr(parse("../b.git@v1#kb"), base, origin="r", key="k") == Spec("git", "https://h/org/b.git", "v1", "kb")
    scp = Spec("git", "git@h:org/a.git", None, None)
    assert rr(parse("../b.git"), scp, origin="r", key="k") == Spec("git", "git@h:org/b.git", None, None)
    # Absolute and scp-like entries are left alone.
    assert rr(parse("git@h:org/c.git"), base, origin="r", key="k") == parse("git@h:org/c.git")


def test_relative_entries_of_non_http_url_registries_join_the_path():
    rr = _sources.resolve_relative
    parse = _sources.parse_spec
    Spec = _sources.Spec
    assert rr(parse("../one"), Spec("url", "s3://bucket/cfg/reg.json"), origin="r", key="k") == Spec(
        "url", "s3://bucket/one")
    assert rr(parse("./one"), Spec("url", "zip:file:///a.zip!/cfg/reg.json"), origin="r", key="k") == Spec(
        "url", "zip:file:///a.zip!/cfg/one")
    assert rr(parse("./x"), Spec("url", "https://h/cfg/r.json"), origin="r", key="k") == Spec(
        "url", "https://h/cfg/x")


def test_a_relative_git_entry_is_cloned_beside_the_registry_not_the_cwd(tmp_path, monkeypatch):
    _bare_repo(tmp_path, "shared", {"overlay.toml": 'name = "shared"\n', "kb/S.md": "s\n"})
    reg_dir = tmp_path / "reg"
    reg_dir.mkdir()
    (reg_dir / "reg.json").write_text(json.dumps({"shared": "../shared.git@main"}), encoding="utf-8")
    elsewhere = tmp_path / "a" / "b" / "c"  # `../shared.git` from here is not there
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)
    store = tmp_path / "store"
    store.mkdir()
    (store / "AGENTS.md").write_text(BASE_AGENTS, encoding="utf-8")
    assert _add(reg_dir / "reg.json", store, "shared") == 0
    assert (store / "overlays" / "shared" / "kb" / "S.md").is_file()


# --------------------------------------------------------------------------
# overlays-18: the stdlib http fetch honours #path
# --------------------------------------------------------------------------

def test_stdlib_http_registry_honours_the_path(monkeypatch, tmp_path):
    fetched = []
    monkeypatch.setattr(_sources, "uri_path_class", lambda: None)
    monkeypatch.setattr(_sources, "_read_url", lambda url: fetched.append(url) or '{"a": "./a"}')
    repo = _sources.load_repo("https://h/cfg#reg.json", _sources.SourceCache(tmp_path / "cache"))
    assert fetched == ["https://h/cfg/reg.json"]
    assert repo.base == _sources.Spec("url", "https://h/cfg/reg.json")
    assert repo.available() == ["a"]


# --------------------------------------------------------------------------
# overlays-09: redaction covers query tokens, cache names and .git/config
# --------------------------------------------------------------------------

def test_redact_masks_query_values_and_a_raw_at_in_the_password():
    assert "abc123" not in _sources.redact("fetched https://h/r.json?access_token=abc123&x=1")
    assert _sources.redact("https://u:p@ss@host/x.git") == "https://host/x.git"
    assert _sources.redact("clone https://a:b@h/x.git failed") == "clone https://h/x.git failed"


def test_cache_dir_names_never_carry_userinfo(tmp_path):
    cache = _sources.SourceCache(tmp_path / "cache")
    assert "s3cret" not in cache.repo_dir("https://alice:s3cret@host").name
    assert "s3cret" not in cache._slug("https://alice:s3cret@host", None)
    # ...while two credentials still get two cache dirs (the hash is of the full URL).
    assert cache.repo_dir("https://a:1@host/x.git") != cache.repo_dir("https://a:2@host/x.git")


def test_a_credentialed_clone_keeps_the_credential_out_of_git_config(tmp_path, monkeypatch):
    calls = []

    def fake_git(self, args, cwd, *, check=True):
        calls.append(list(args))
        if args[0] == "clone":
            (Path(args[-1]) / ".git").mkdir(parents=True)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(_sources.SourceCache, "_git", fake_git)
    url = "https://alice:s3cret@example.invalid/org/x.git"
    cache = _sources.SourceCache(tmp_path / "cache")
    cache.checkout(_sources.parse_spec(url + "@main"))
    assert ["remote", "set-url", "origin", "https://example.invalid/org/x.git"] in calls
    # A later process fetches from the full URL into origin's tracking refs.
    calls.clear()
    _sources.SourceCache(tmp_path / "cache").checkout(_sources.parse_spec(url + "@main"))
    fetch = [c for c in calls if c[0] == "fetch"][0]
    assert url in fetch and "+refs/heads/*:refs/remotes/origin/*" in fetch
    assert "origin" not in fetch[fetch.index("--"):]


# --------------------------------------------------------------------------
# overlays-15: the source cache is never tracked by a store kept in git
# --------------------------------------------------------------------------

def test_the_source_cache_root_ignores_everything_in_it(tmp_path):
    bare = _bare_repo(tmp_path, "whole", {"overlay.toml": 'name = "whole"\n'})
    cache_root = tmp_path / "store" / ".cache" / "overlays"
    _sources.SourceCache(cache_root).checkout(_sources.parse_spec(str(bare) + "@main"))
    ignore = cache_root / ".gitignore"
    assert ignore.is_file()
    assert "*" in ignore.read_text(encoding="utf-8").splitlines()
    assert b"\r" not in ignore.read_bytes()


# --------------------------------------------------------------------------
# overlays-23: no "bundled overlays" tier probing arbitrary directories
# --------------------------------------------------------------------------

def test_a_pyz_in_the_store_never_makes_the_store_its_own_source(tmp_path, monkeypatch):
    """A .pyz kept at <store>/dotagents.pyz put the store's own overlays/ at
    `__file__`'s parents[2]: installed overlays became the "bundled" source."""
    store = tmp_path / "store"
    _overlay(store / "overlays", "mine")
    monkeypatch.setattr(_scope, "__file__", str(store / "dotagents.pyz" / "dotagents" / "_scope.py"))
    scope = _scope.Scope("user", store)
    with pytest.raises(SystemExit, match="no overlay source"):
        _scope.resolve_source(None, scope=scope)


# --------------------------------------------------------------------------
# overlays-13: the manifest is read as TOML
# --------------------------------------------------------------------------

@pytest.fixture(params=["toml", "hand"])
def reader(request, monkeypatch):
    """Run a manifest test under tomllib/tomli AND under the hand fallback."""
    toml_module = getattr(_overlays, "_toml_module", lambda: None)
    if request.param == "toml":
        if toml_module() is None:
            pytest.skip("neither tomllib nor tomli is importable")
    else:
        monkeypatch.setattr(_overlays, "_toml_module", lambda: None, raising=False)
    return request.param


def test_manifest_escapes_underscored_ints_and_tables(tmp_path, reader):
    ov = tmp_path / "demo"
    ov.mkdir()
    (ov / "overlay.toml").write_text(
        'name = "demo"\n'
        'description = "say \\"hi\\""\n'
        'routing = ["- Run \\"dotagents env\\" first -> x", \'- Path C:\\tools -> y\', "- Tab\\there -> z"]\n'
        "priority = 1_000\n"
        "[nested]\n"
        'rules = ["nested.md"]\n'
        'name = "not-the-name"\n',
        encoding="utf-8",
    )
    manifest = Overlay(ov).read_manifest()
    assert manifest["routing"] == ['- Run "dotagents env" first -> x', "- Path C:\\tools -> y", "- Tab\there -> z"]
    assert manifest["description"] == 'say "hi"'
    assert manifest["name"] == "demo"
    assert manifest["rules"] == []  # a key inside [nested] is not a top-level key
    assert manifest["priority"] == 1000


def test_an_invalid_manifest_is_a_warning_and_an_empty_manifest(tmp_path, caplog):
    if _overlays._toml_module() is None:
        pytest.skip("neither tomllib nor tomli is importable")
    ov = tmp_path / "broken"
    ov.mkdir()
    (ov / "overlay.toml").write_text('routing = ["unterminated\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        manifest = Overlay(ov).read_manifest()
    assert manifest["routing"] == [] and manifest["name"] == "broken"
    assert any("not valid TOML" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------
# overlays-05: requires / rules cannot escape the overlay or overlays/
# --------------------------------------------------------------------------

def test_invalid_requires_entries_are_dropped(tmp_path, reader):
    ov = _overlay(tmp_path, "top", requires=["../escape", "user", "ok", "Ok"])
    assert Overlay(ov).read_manifest()["requires"] == ["ok"]


def test_a_requires_path_never_installs_outside_overlays(world):
    src, store = world
    _overlay(src, "top", requires=["../escape"])
    _overlay(src / "..", "escape", files=[("kb/E.md", "e\n")])  # what `../escape` would find
    assert _add(src, store, "top") == 0
    assert not (store / "escape").exists()
    assert sorted(p.name for p in (store / "overlays").iterdir()) == ["top"]


def test_rules_outside_the_overlay_are_not_merged(world, tmp_path):
    src, store = world
    (tmp_path / "secret.md").write_text("- **Secret**: from outside\n", encoding="utf-8")
    _overlay(src, "top", rules=["../../secret.md", str(tmp_path / "secret.md")])
    assert _add(src, store, "top") == 0
    assert "Secret" not in (store / "AGENTS.md").read_text(encoding="utf-8")


def test_a_non_utf8_rules_file_is_a_warning_not_a_wedge(world):
    """overlays-11: one bad rules file crashed every later add/sync."""
    src, store = world
    bad = _overlay(src, "bad", rules=["rules.md"])
    (bad / "rules.md").write_bytes(b"- **Bad**: \xff\xfe\n")
    _overlay(src, "good", routing=["- GOOD-ROUTE -> x"])
    assert _add(src, store, "bad") == 0
    assert _add(src, store, "good") == 0
    assert "GOOD-ROUTE" in (store / "AGENTS.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# overlays-19 / -20: names that alias on Windows or on the root variable
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["python.", "net-", "my_", "con", "NUL", "com1", "lpt9", "aux.tools"])
def test_names_that_alias_on_windows_are_invalid(name):
    assert not Overlay.is_valid_name(name)


def test_removing_a_trailing_dot_name_is_refused_not_aliased(world):
    src, store = world
    _overlay(src, "python", files=[("kb/P.md", "p\n")])
    _add(src, store, "python")
    with pytest.raises(SystemExit, match="not a valid overlay name"):
        _run(OverlayRemove, name=["python."], global_scope=True, agents_dir=store, dry_run=False)
    assert (store / "overlays" / "python").is_dir()


def test_dots_normalize_so_v1_2_and_v1_dash_2_are_one_overlay():
    assert Overlay.normalize_name("v1.2") == Overlay.normalize_name("v1-2") == "v1-2"


def test_shadowing_compares_normalized_names(tmp_path):
    user, project = tmp_path / "user", tmp_path / "proj"
    (user / "overlays" / "my_overlay").mkdir(parents=True)
    (project / "overlays" / "my-overlay").mkdir(parents=True)
    assert [o.path for o in Overlay.installed(user, project)] == [project / "overlays" / "my-overlay"]


def test_discover_warns_when_two_dirs_are_one_overlay(tmp_path, caplog):
    root = tmp_path / "overlays"
    (root / "v1.2").mkdir(parents=True)
    (root / "v1-2").mkdir()
    with caplog.at_level(logging.WARNING):
        Overlay.discover(root)
    assert any("are one overlay" in r.getMessage() for r in caplog.records)


def test_a_dotted_install_from_an_older_rule_is_still_found(world):
    src, store = world
    _overlay(src, "foo.bar", files=[("kb/F.md", "f\n")])
    legacy = store / "overlays" / "foo.bar"  # what `add foo.bar` made before dots normalized
    shutil.copytree(str(src / "foo.bar"), str(legacy))
    assert _add(src, store, "foo.bar") == 0
    assert sorted(p.name for p in (store / "overlays").iterdir()) == ["foo.bar"], "re-add reuses it"
    assert _run(OverlayRemove, name=["foo.bar"], global_scope=True, agents_dir=store, dry_run=False) == 0
    assert not legacy.exists()


# --------------------------------------------------------------------------
# overlays-03: sync resolves an overlay from the repo it was installed from
# --------------------------------------------------------------------------

@pytest.fixture
def two_repos(tmp_path):
    private, public = tmp_path / "private", tmp_path / "public"
    _overlay(private, "python", files=[("kb/PY.md", "priv\n")])
    _overlay(public, "python", files=[("kb/PY.md", "pub\n"), ("bin/pytool", "#!/bin/sh\n")])
    return private, public


def test_sync_uses_the_recorded_repo_not_the_first_offering_one(world, two_repos, monkeypatch):
    _src, store = world
    private, public = two_repos
    assert _add(private, store, "python") == 0
    monkeypatch.setenv("AGENTS_OVERLAYS_REPO", str(public))
    assert _sync(store) == 0
    assert not (store / "overlays" / "python" / "bin" / "pytool").exists()
    assert _sync(store, overwrite=True) == 0
    assert (store / "overlays" / "python" / "kb" / "PY.md").read_text(encoding="utf-8") == "priv\n"


def test_the_record_holds_an_absolute_redacted_source(world, two_repos, monkeypatch):
    _src, store = world
    private, _public = two_repos
    monkeypatch.chdir(private.parent)
    assert _add(Path("private"), store, "python") == 0  # a --repo relative to the cwd
    record = Overlay(store / "overlays" / "python").read_install_record()
    assert record["source"]["kind"] == "dir"
    assert Path(record["source"]["location"]) == private
    assert record["files"]["kb/PY.md"]


def test_an_explicit_repo_replaces_the_recorded_source(world, two_repos, caplog):
    _src, store = world
    private, public = two_repos
    _add(private, store, "python")
    with caplog.at_level(logging.INFO):
        assert _sync(store, repo=[str(public)]) == 0
    assert (store / "overlays" / "python" / "bin" / "pytool").is_file()
    assert any("now from" in r.getMessage() for r in caplog.records)
    assert Path(Overlay(store / "overlays" / "python").read_install_record()["source"]["location"]) == public


def test_a_credentialed_record_matches_the_configured_repo(tmp_path):
    url = "https://alice:s3cret@example.invalid/org/x.git@main"
    record = _sources.source_record(url)
    assert record["lossy"] and "s3cret" not in json.dumps(record)
    chain = _sources.CompositeSource(["/somewhere/else", url], _sources.SourceCache(tmp_path / "c"))
    assert chain.find_repo(record) == url


# --------------------------------------------------------------------------
# overlays-10: remove unpublishes only after the delete, and always recomposes
# --------------------------------------------------------------------------

def test_a_failed_remove_keeps_skills_and_still_recomposes(world, monkeypatch):
    from dotagents.cli import overlays as overlays_cmd

    src, store = world
    _overlay(src, "one", routing=["- ONE-ROUTE -> x"])
    _overlay(src, "two", routing=["- TWO-ROUTE -> y"], skill="two-skill")
    _add(src, store, "one", "two")
    real = overlays_cmd._remove_tree

    def flaky(path):
        if Path(path).name == "two":
            raise PermissionError("[WinError 5] Access is denied")
        real(path)

    monkeypatch.setattr(overlays_cmd, "_remove_tree", flaky)
    with pytest.raises(PermissionError):
        _run(OverlayRemove, name=["one", "two"], global_scope=True, agents_dir=store, dry_run=False)
    text = (store / "AGENTS.md").read_text(encoding="utf-8")
    assert "ONE-ROUTE" not in text, "what was removed is un-merged even though a later removal failed"
    assert "TWO-ROUTE" in text
    assert (store / "skills" / "two-skill").exists(), "the overlay that was not deleted keeps its skill"


def test_remove_unlinks_a_linked_overlay_and_keeps_its_target(world, tmp_path):
    src, store = world
    dev = _overlay(tmp_path / "dev", "myov", routing=["- MYOV -> x"])
    link = store / "overlays" / "myov"
    link.parent.mkdir(parents=True)
    try:
        os.symlink(str(dev), str(link), target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("no symlink support")
        import _winapi

        _winapi.CreateJunction(str(dev), str(link))
    assert _run(OverlayRemove, name=["myov"], global_scope=True, agents_dir=store, dry_run=False) == 0
    assert not os.path.lexists(str(link))
    assert (dev / "overlay.toml").is_file()


# --------------------------------------------------------------------------
# overlays-11: add is transactional per overlay
# --------------------------------------------------------------------------

def test_a_failed_setup_rolls_back_that_overlay_and_merges_the_rest(world):
    src, store = world
    _overlay(src, "aaa", routing=["- AAA-ROUTE -> x"])
    _overlay(src, "bbb", routing=["- BBB-ROUTE -> y"], skill="bbb-skill", setup="import sys\nsys.exit(4)\n")
    _overlay(src, "ccc", routing=["- CCC-ROUTE -> z"])
    with pytest.raises(SystemExit, match="setup for overlay 'bbb' failed"):
        _add(src, store, "aaa", "bbb")
    assert sorted(p.name for p in (store / "overlays").iterdir()) == ["aaa"]
    assert not (store / "skills" / "bbb-skill").exists()
    assert "AAA-ROUTE" in (store / "AGENTS.md").read_text(encoding="utf-8")
    _add(src, store, "ccc")
    assert "BBB-ROUTE" not in (store / "AGENTS.md").read_text(encoding="utf-8")


def test_an_interrupted_fresh_install_leaves_nothing_behind(world, monkeypatch):
    src, store = world
    _overlay(src, "half", files=[("kb/A.md", "a\n"), ("kb/B.md", "b\n")])
    real = Overlay.install_to

    def interrupted(self, dest, *args, **kwargs):
        real(self, dest, *args, **kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(Overlay, "install_to", interrupted)
    with pytest.raises(KeyboardInterrupt):
        _add(src, store, "half")
    left = sorted(p.name for p in (store / "overlays").iterdir()) if (store / "overlays").exists() else []
    assert left == [], "neither a partial overlays/half nor a staging dir"


# --------------------------------------------------------------------------
# overlays-12: sync reports kept files, refreshes the manifest, prunes
# --------------------------------------------------------------------------

def test_sync_reports_a_differing_file_as_kept_not_unchanged(world, caplog):
    src, store = world
    _overlay(src, "demo", files=[("kb/D.md", "d\n")])
    _add(src, store, "demo")
    (store / "overlays" / "demo" / "kb" / "D.md").write_text("edited\n", encoding="utf-8")
    with caplog.at_level(logging.INFO):
        _sync(store, repo=[str(src)])
    assert any("kept kb/D.md" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert not any("2 unchanged" in r.getMessage() for r in caplog.records)


def test_plain_sync_refreshes_the_manifest(world):
    src, store = world
    _overlay(src, "demo")
    _add(src, store, "demo")
    _overlay(src, "demo", routing=["- NEW-ROUTE -> x"])  # upstream adds routing
    _sync(store, repo=[str(src)])
    assert "NEW-ROUTE" in (store / "AGENTS.md").read_text(encoding="utf-8")


def test_sync_prunes_files_dropped_upstream_unless_edited(world, tmp_path):
    src, store = world
    ov = _overlay(src, "demo", files=[("env.py", "OLD\n"), ("kb/K.md", "k\n")])
    _add(src, store, "demo")
    installed = store / "overlays" / "demo"
    (installed / "kb" / "K.md").write_text("edited here\n", encoding="utf-8")
    (ov / "env.py").rename(ov / "pre.env.py")
    (ov / "kb" / "K.md").unlink()
    assert _sync(store, repo=[str(src)]) == 0
    assert not (installed / "env.py").exists(), "an unmodified file the source dropped is removed"
    assert (installed / "pre.env.py").is_file()
    assert (installed / "kb" / "K.md").is_file(), "an edited one is kept"
    assert _sync(store, repo=[str(src)], prune=True) == 0
    assert not (installed / "kb" / "K.md").exists()
    backups = list((store / "install_backup").rglob("K.md"))
    assert backups and backups[0].read_text(encoding="utf-8") == "edited here\n"


def test_sync_overwrite_backs_up_what_it_replaces(world):
    src, store = world
    _overlay(src, "demo", files=[("kb/D.md", "d\n")])
    _add(src, store, "demo")
    (store / "overlays" / "demo" / "kb" / "D.md").write_text("mine\n", encoding="utf-8")
    assert _sync(store, repo=[str(src)], overwrite=True) == 0
    assert (store / "overlays" / "demo" / "kb" / "D.md").read_text(encoding="utf-8") == "d\n"
    backups = list((store / "install_backup").rglob("D.md"))
    assert backups and backups[0].read_text(encoding="utf-8") == "mine\n"


# --------------------------------------------------------------------------
# overlays-21: setup scripts see the user store as AGENTS_HOME
# --------------------------------------------------------------------------

def test_project_setup_gets_the_user_store_as_agents_home(world, tmp_path, monkeypatch):
    src, user_store = world
    out = tmp_path / "setup-env.json"
    _overlay(src, "probe", setup=(
        "import json, os\n"
        "json.dump({k: os.environ.get(k) for k in ('AGENTS_HOME', 'AGENTS_SCOPE_ROOT', 'AGENTS_SCOPE')},"
        " open(%r, 'w'))\n" % str(out)
    ))
    project_store = tmp_path / "proj" / ".agents"
    project_store.mkdir(parents=True)
    (project_store / "AGENTS.md").write_text(BASE_AGENTS, encoding="utf-8")
    monkeypatch.setenv("AGENTS_HOME", str(user_store))
    assert _run(OverlayAdd, name=["probe"], repo=[str(src)], global_scope=False,
                agents_dir=project_store, copy=True, dry_run=False) == 0
    seen = json.loads(out.read_text(encoding="utf-8"))
    assert Path(seen["AGENTS_HOME"]) == user_store
    assert Path(seen["AGENTS_SCOPE_ROOT"]) == project_store
    assert seen["AGENTS_SCOPE"] == "project"


# --------------------------------------------------------------------------
# overlays-22: reverse dependencies and new requires
# --------------------------------------------------------------------------

def test_remove_refuses_a_required_overlay_unless_forced(world):
    src, store = world
    _overlay(src, "engineering")
    _overlay(src, "python", requires=["engineering"])
    _add(src, store, "python")
    with pytest.raises(SystemExit, match="required by python"):
        _run(OverlayRemove, name=["engineering"], global_scope=True, agents_dir=store, dry_run=False)
    assert (store / "overlays" / "engineering").is_dir()
    assert _run(OverlayRemove, name=["engineering"], global_scope=True, agents_dir=store,
                dry_run=False, force=True) == 0
    assert not (store / "overlays" / "engineering").exists()
    # Removing both together is fine: nothing left requires it.


def test_sync_installs_a_requirement_added_upstream(world):
    src, store = world
    _overlay(src, "engineering", files=[("flows/PLAN.md", "p\n")])
    _overlay(src, "python")
    _add(src, store, "python")
    _overlay(src, "python", requires=["engineering"])  # upstream now requires it
    assert _sync(store, repo=[str(src)]) == 0
    assert (store / "overlays" / "engineering" / "flows" / "PLAN.md").is_file()


def test_list_and_show_flag_an_unmet_requirement(world, capsys):
    src, store = world
    _overlay(src, "python", requires=["engineering"])
    _add(src, store, "python", no_requires=True)
    _run(OverlayList, repo=[str(src)], global_scope=True, agents_dir=store, json=False)
    assert "python  (requires missing: engineering)" in capsys.readouterr().out
    _run(OverlayShow, name="python", repo=[], global_scope=True, agents_dir=store, json=True)
    assert json.loads(capsys.readouterr().out)["unmet_requires"] == ["engineering"]


# --------------------------------------------------------------------------
# overlays-25: add says how new skills reach an agent's own skills dir
# --------------------------------------------------------------------------

def test_add_says_init_links_new_skills(world, caplog):
    src, store = world
    _overlay(src, "withskill", skill="hello")
    with caplog.at_level(logging.INFO):
        _add(src, store, "withskill")
    assert any("re-run `dotagents init" in r.getMessage() and "hello" in r.getMessage()
               for r in caplog.records)


# --------------------------------------------------------------------------
# Neatness pass: dry-run wording, a missing name, list marks, source == install
# --------------------------------------------------------------------------

def test_a_dry_run_says_it_would_recompose(world, caplog):
    src, store = world
    _overlay(src, "routed", routing=["- ROUTED -> x.md"])
    with caplog.at_level(logging.INFO):
        assert _add(src, store, "routed", dry_run=True) == 0
    messages = [r.getMessage() for r in caplog.records]
    assert "would recompose overlay rules/routing in AGENTS.md" in messages
    assert not any(m.startswith("recomposed") for m in messages), messages
    assert "ROUTED" not in (store / "AGENTS.md").read_text(encoding="utf-8")


def test_a_dry_run_into_a_store_without_agents_md_says_it_would_create_it(world, caplog):
    src, store = world
    (store / "AGENTS.md").unlink()
    _overlay(src, "routed", routing=["- ROUTED -> x.md"])
    with caplog.at_level(logging.INFO):
        assert _add(src, store, "routed", dry_run=True) == 0
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("would create") and "AGENTS.md" in m for m in messages), messages
    assert not any(m.startswith("created") for m in messages), messages
    assert not (store / "AGENTS.md").exists()


@pytest.mark.parametrize("command", ["add", "remove", "show"])
def test_a_missing_overlay_name_is_a_usage_error(world, command, capsys):
    from dotagents import cli

    src, store = world
    argv = ["overlays", command, "-g", "--agents-dir", str(store)]
    if command != "remove":
        argv += ["--repo", str(src)]
    try:
        rc = cli.main(argv)
    except SystemExit as exc:
        rc = exc.code
    assert rc == 2
    assert "overlay name" in capsys.readouterr().err
    assert not (store / "overlays").exists()


def test_list_marks_an_installed_overlay_whose_source_name_differs(world, capsys):
    src, store = world
    _overlay(src, "My_Overlay")
    _add(src, store, "My_Overlay")
    assert (store / "overlays" / "my-overlay").is_dir()
    capsys.readouterr()
    _run(OverlayList, repo=[str(src)], global_scope=True, agents_dir=store, json=False)
    assert "  My_Overlay *" in capsys.readouterr().out.splitlines()


def _registry_at_the_install_dir(store):
    """The overlay's only copy authored where `add` installs it, named by a
    registry beside it -- the setup the docs' old example led to."""
    mine = store / "overlays" / "mine"
    (mine / "kb").mkdir(parents=True)
    (mine / "kb" / "MINE.md").write_text("only copy\n", encoding="utf-8")
    (store / "dotagents.json").write_text(json.dumps({"mine": "./overlays/mine"}), encoding="utf-8")
    return mine


def test_add_refuses_a_source_inside_the_install_root(world):
    _src, store = world
    mine = _registry_at_the_install_dir(store)
    with pytest.raises(SystemExit, match="inside"):
        _add(None, store, "mine")
    assert (mine / "kb" / "MINE.md").read_text(encoding="utf-8") == "only copy\n"
    assert not (mine / Overlay.INSTALL_RECORD).exists()


def test_sync_refuses_a_source_inside_the_install_root(world, caplog):
    src, store = world
    _overlay(src, "mine", files=[("kb/MINE.md", "v1\n")])
    _add(src, store, "mine")
    installed = store / "overlays" / "mine"
    with caplog.at_level(logging.ERROR):
        with pytest.raises(SystemExit, match="sync failed for: mine"):
            _sync(store, repo=[str(store / "overlays")])
    assert any("inside" in r.getMessage() for r in caplog.records)
    assert (installed / "kb" / "MINE.md").read_text(encoding="utf-8") == "v1\n"
