"""Overlay sources through pathlib_next: an http(s) directory listing is a
directory of overlays (repo) or one overlay (source), a file at a URL is a
registry whose relative entries resolve beside it, file:// is local; without
the uri extra an http(s) registry still works through the stdlib and any
other scheme names the extra. A stdlib http.server serves the fixtures."""
import functools
import http.server
import json
import shutil
import sys
import tarfile
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotagents import _sources  # noqa: E402
from dotagents._sources import DirRepo, RegistryRepo, SourceCache, Spec  # noqa: E402

needs_uri = pytest.mark.skipif(_sources.uri_path_class() is None, reason="needs pathlib_next[http]")


def _tree(root: Path):
    for name in ("one", "two"):
        d = root / "overlays" / name
        (d / "kb").mkdir(parents=True)
        (d / "overlay.toml").write_text('name = "%s"\nrequires = []\nrouting = []\n' % name, encoding="utf-8")
        (d / "kb" / (name.upper() + ".md")).write_text("# %s\n" % name, encoding="utf-8")
    (root / "cfg").mkdir()
    (root / "cfg" / "reg.json").write_text(json.dumps({
        "one": "../overlays/one",            # relative to the registry's URL
        "two": "./../overlays",              # a directory of overlays, named as an entry
    }), encoding="utf-8")


@pytest.fixture()
def served(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    _tree(root)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield "http://127.0.0.1:%d" % httpd.server_address[1], root
    finally:
        httpd.shutdown()
        httpd.server_close()


@needs_uri
def test_http_directory_listing_is_a_directory_of_overlays(served, tmp_path):
    url, root = served
    cache = SourceCache(tmp_path / "cache")
    repo = _sources.load_repo(url + "/overlays", cache)
    assert isinstance(repo, DirRepo) and sorted(repo.available()) == ["one", "two"]
    one = repo.overlay_dir("one")
    assert (one / "overlay.toml").is_file() and (one / "kb" / "ONE.md").read_text(encoding="utf-8") == "# one\n"
    assert str(one).startswith(str(tmp_path / "cache" / "uri")), "materialized into the cache"
    # Fetched once per process: a second load reuses the copy.
    assert _sources.load_repo(url + "/overlays", cache).overlay_dir("one") == one


@needs_uri
def test_http_registry_resolves_relative_entries_beside_it(served, tmp_path):
    url, root = served
    cache = SourceCache(tmp_path / "cache")
    reg = _sources.load_repo(url + "/cfg/reg.json", cache)
    assert isinstance(reg, RegistryRepo) and reg.base == Spec("url", url + "/cfg/reg.json")
    assert (reg.overlay_dir("one") / "kb" / "ONE.md").is_file()
    assert (reg.overlay_dir("two") / "overlay.toml").read_text(encoding="utf-8").startswith('name = "two"')


@needs_uri
def test_a_missing_url_is_an_error_naming_it(served, tmp_path):
    url, root = served
    with pytest.raises(SystemExit) as exc:
        _sources.load_repo(url + "/nope", SourceCache(tmp_path / "cache"))
    assert "/nope" in str(exc.value)


def test_file_url_is_the_local_path(tmp_path):
    root = tmp_path / "local"
    root.mkdir()
    _tree(root)
    repo = _sources.load_repo((root / "overlays").as_uri(), SourceCache(tmp_path / "cache"))
    assert isinstance(repo, DirRepo)
    assert repo.overlay_dir("one").resolve() == (root / "overlays" / "one").resolve(), "no copy"


def test_without_the_uri_extra_http_registries_still_work(served, tmp_path, monkeypatch):
    url, root = served
    monkeypatch.setattr(_sources, "uri_path_class", lambda: None)
    cache = SourceCache(tmp_path / "cache")
    reg = _sources.load_repo(url + "/cfg/reg.json", cache)
    assert isinstance(reg, RegistryRepo) and sorted(reg.available()) == ["one", "two"]
    # ... but a directory over http, or another scheme, names what is missing.
    with pytest.raises(SystemExit, match=r"uri extra"):
        reg.overlay_dir("one")
    with pytest.raises(SystemExit, match=r"sftp://.*uri extra"):
        _sources.load_repo("sftp://h/overlays", cache)


@needs_uri
def test_cli_add_from_an_http_repo(served, tmp_path, monkeypatch):
    from dotagents.cli.overlays import OverlayAdd

    url, root = served
    store = tmp_path / "store"
    store.mkdir()
    (store / "AGENTS.md").write_text("<!-- dotagents:begin -->\n# x\n## Always-on rules\n## Load on demand\n<!-- dotagents:end -->\n", encoding="utf-8")
    monkeypatch.setenv("AGENTS_HOME", str(store))
    monkeypatch.delenv("AGENTS_PROJECT_ROOT", raising=False)
    cmd = OverlayAdd()
    cmd.name, cmd.repo, cmd.global_scope, cmd.agents_dir, cmd.copy, cmd.dry_run = (
        ["one"], [url + "/overlays"], True, store, True, False)
    assert cmd() == 0
    assert (store / "overlays" / "one" / "kb" / "ONE.md").is_file()


@pytest.fixture()
def archives(tmp_path):
    """A zip (shutil) and a tar (plain member names) of the fixture tree."""
    tree = tmp_path / "tree"
    tree.mkdir()
    _tree(tree)
    www = tmp_path / "arch"
    www.mkdir()
    shutil.make_archive(str(www / "o"), "zip", tree)
    with tarfile.open(www / "o.tar.gz", "w:gz") as tar:
        for f in sorted(tree.rglob("*")):
            tar.add(f, arcname=f.relative_to(tree).as_posix())
    return www


@needs_uri
@pytest.mark.parametrize("scheme,archive", [("zip", "o.zip"), ("archive", "o.zip"), ("tar", "o.tar.gz")])
def test_an_archive_is_a_directory_of_overlays(archives, tmp_path, scheme, archive):
    """`zip:<archive-uri>!/<inner>` (Java-style): the inner path is the
    collection; `archive:` auto-detects the format."""
    spec = "%s:%s!/overlays" % (scheme, (archives / archive).as_uri())
    repo = _sources.load_repo(spec, SourceCache(tmp_path / "cache"))
    assert isinstance(repo, DirRepo) and sorted(repo.available()) == ["one", "two"]
    assert (repo.overlay_dir("one") / "kb" / "ONE.md").read_text(encoding="utf-8") == "# one\n"


@needs_uri
def test_a_zip_at_a_url_is_read_straight_off_http(archives, tmp_path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(archives))
    handler.log_message = lambda *a: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        spec = "zip:http://127.0.0.1:%d/o.zip!/overlays" % httpd.server_address[1]
        repo = _sources.load_repo(spec, SourceCache(tmp_path / "cache"))
        assert sorted(repo.available()) == ["one", "two"]
        assert (repo.overlay_dir("two") / "overlay.toml").is_file()
    finally:
        httpd.shutdown()
        httpd.server_close()
