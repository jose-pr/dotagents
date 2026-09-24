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
