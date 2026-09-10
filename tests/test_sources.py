"""Overlay repos beyond one directory: specs (`<location>[@ref][#path]`), git
checkouts, registries (JSON/TOML/YAML mapping names to specs, from a file, a
URL or a git file), the repo precedence (--repo, then $AGENTS_OVERLAYS_REPO_<KEY>,
$AGENTS_OVERLAYS_REPO, the project store's dotagents.*, the user store's, the
bundled dir) and the lazy composite the overlay commands use.

Git tests build real repositories in tmp_path with the `git` on PATH; they skip
when there is none. No network.
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

from dotagents import _scope, _sources  # noqa: E402
from dotagents._sources import CompositeSource, DirRepo, GitCache, RegistryRepo, Spec, parse_spec  # noqa: E402

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="needs git on PATH")


@pytest.fixture(autouse=True)
def _no_ambient_repos(monkeypatch):
    for k in list(os.environ):
        if k.startswith(_sources.REPO_ENV_PREFIX):
            monkeypatch.delenv(k)
    monkeypatch.delenv(_sources.REPO_ENV_DEFAULT, raising=False)


# --------------------------------------------------------------------------
# Spec grammar
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("https://github.com/org/x.git", Spec("git", "https://github.com/org/x.git")),
    ("https://github.com/org/x.git@main", Spec("git", "https://github.com/org/x.git", "main")),
    ("https://github.com/org/x.git@v1.2#overlays/net", Spec("git", "https://github.com/org/x.git", "v1.2", "overlays/net")),
    ("https://github.com/org/x.git#overlays/net/", Spec("git", "https://github.com/org/x.git", None, "overlays/net")),
    ("git@github.com:org/x.git", Spec("git", "git@github.com:org/x.git")),
    ("git@github.com:org/x.git@0123abc", Spec("git", "git@github.com:org/x.git", "0123abc")),
    ("git@github.com:org/x", Spec("git", "git@github.com:org/x")),
    ("ssh://git@host/org/x@dev", Spec("git", "ssh://git@host/org/x", "dev")),
    ("git+https://host/org/x@main#a/b", Spec("git", "https://host/org/x", "main", "a/b")),
    ("https://tok@host/org/x.git@main", Spec("git", "https://tok@host/org/x.git", "main")),
    ("https://example.com/registry.json", Spec("url", "https://example.com/registry.json")),
    ("/srv/overlays", Spec("dir", "/srv/overlays")),
    ("C:\\repos\\overlays#net", Spec("dir", "C:\\repos\\overlays", None, "net")),
    ("~/overlays/net", Spec("dir", "~/overlays/net")),
])
def test_parse_spec(text, expected):
    assert parse_spec(text) == expected


def test_spec_display_and_messages_never_carry_userinfo():
    spec = parse_spec("https://user:s3cret@host/org/x.git@main#p")
    assert spec.display() == "https://host/org/x.git@main#p"
    assert "s3cret" not in repr(spec)
    assert _sources.redact("clone https://a:b@h/x.git failed") == "clone https://h/x.git failed"


def test_empty_spec_is_an_error():
    with pytest.raises(ValueError):
        parse_spec("   ")


# --------------------------------------------------------------------------
# A git repository with overlays in it
# --------------------------------------------------------------------------

def _git(*args, cwd):
    subprocess.run([GIT, *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path):
    """A git repo where the ROOT is one overlay (`whole`) and `overlays/inner`
    is another; `main` and a `v1` tag differ from a `dev` branch."""
    if GIT is None:
        pytest.skip("needs git on PATH")
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-q", "-b", "main", cwd=work)
    _git("config", "user.email", "t@example.invalid", cwd=work)
    _git("config", "user.name", "t", cwd=work)
    _write(work / "overlay.toml", 'name = "whole"\nrequires = []\nrouting = []\n')
    _write(work / "kb" / "WHOLE.md", "main\n")
    _write(work / "overlays" / "inner" / "overlay.toml", 'name = "inner"\nrequires = []\nrouting = []\n')
    _write(work / "overlays" / "inner" / "kb" / "INNER.md", "main\n")
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "main", cwd=work)
    _git("tag", "v1", cwd=work)
    main_sha = subprocess.run([GIT, "rev-parse", "HEAD"], cwd=str(work), capture_output=True, text=True, check=True).stdout.strip()
    _git("checkout", "-q", "-b", "dev", cwd=work)
    _write(work / "kb" / "WHOLE.md", "dev\n")
    _write(work / "overlays" / "inner" / "kb" / "INNER.md", "dev\n")
    _git("commit", "-q", "-am", "dev", cwd=work)
    _git("checkout", "-q", "main", cwd=work)
    bare = tmp_path / "remote.git"
    _git("clone", "-q", "--bare", str(work), str(bare), cwd=tmp_path)
    return {"url": str(bare), "work": work, "main": main_sha, "bare": bare}


@needs_git
def test_git_checkout_default_branch_tag_and_commit(repo, tmp_path):
    cache = GitCache(tmp_path / "cache")
    root = cache.checkout(parse_spec(repo["url"]))
    assert (root / "kb" / "WHOLE.md").read_text() == "main\n"
    dev = cache.checkout(parse_spec(repo["url"] + "@dev"))
    assert dev != root, "one checkout per repository AND ref"
    assert (dev / "kb" / "WHOLE.md").read_text() == "dev\n"
    assert (root / "kb" / "WHOLE.md").read_text() == "main\n", "the default-branch checkout is untouched"
    assert (cache.checkout(parse_spec(repo["url"] + "@v1")) / "kb" / "WHOLE.md").read_text() == "main\n"
    assert (cache.checkout(parse_spec(repo["url"] + "@" + repo["main"][:10])) / "kb" / "WHOLE.md").read_text() == "main\n"
    with pytest.raises(SystemExit) as exc:
        cache.checkout(parse_spec(repo["url"] + "@nope"))
    assert "no branch, tag or commit 'nope'" in str(exc.value)


@needs_git
def test_git_checkout_follows_the_remote_after_a_push(repo, tmp_path):
    GitCache(tmp_path / "cache").checkout(parse_spec(repo["url"] + "@main"))
    _write(repo["work"] / "kb" / "WHOLE.md", "main-2\n")
    _git("commit", "-q", "-am", "main-2", cwd=repo["work"])
    _git("push", "-q", str(repo["bare"]), "main", cwd=repo["work"])
    root = GitCache(tmp_path / "cache").checkout(parse_spec(repo["url"] + "@main"))  # a new process fetches
    assert (root / "kb" / "WHOLE.md").read_text() == "main-2\n"


@needs_git
def test_git_repo_as_a_whole_overlay_and_as_a_directory_of_overlays(repo, tmp_path):
    cache = GitCache(tmp_path / "cache")
    # A registry entry with no path: the whole repository is the overlay.
    reg = RegistryRepo("r", {"whole": repo["url"] + "@main", "inner": repo["url"] + "@dev#overlays/inner"}, cache)
    assert (reg.overlay_dir("whole") / "overlay.toml").is_file()
    assert (reg.overlay_dir("inner") / "kb" / "INNER.md").read_text() == "dev\n"
    # A repo spec with a path to a directory: a directory of overlays.
    d = _sources.load_repo(repo["url"] + "@main#overlays", cache)
    assert isinstance(d, DirRepo) and d.available() == ["inner"] and d.has("inner")
    with pytest.raises(SystemExit) as exc:
        _sources.load_repo(repo["url"] + "#overlays/missing", cache)
    assert "has no 'overlays/missing'" in str(exc.value)


@needs_git
def test_registry_from_a_git_file(repo, tmp_path):
    _write(repo["work"] / "registry.json", json.dumps({"inner": repo["url"] + "#overlays/inner"}))
    _git("add", "registry.json", cwd=repo["work"])
    _git("commit", "-q", "-m", "registry", cwd=repo["work"])
    _git("push", "-q", str(repo["bare"]), "main", cwd=repo["work"])
    reg = _sources.load_repo(repo["url"] + "@main#registry.json", GitCache(tmp_path / "cache"))
    assert isinstance(reg, RegistryRepo) and reg.available() == ["inner"]
    assert (reg.overlay_dir("inner") / "kb" / "INNER.md").read_text() == "main\n"


# --------------------------------------------------------------------------
# Local repos: a directory is a directory of overlays, a file is a registry
# --------------------------------------------------------------------------

def test_a_directory_repo_looks_up_the_overlay_by_name(tmp_path):
    _write(tmp_path / "src" / "one" / "overlay.toml", 'name = "one"\n')
    _write(tmp_path / "src" / "my_two" / "overlay.toml", 'name = "my-two"\n')
    d = _sources.load_repo(str(tmp_path / "src"), GitCache(tmp_path / "cache"))
    assert isinstance(d, DirRepo) and sorted(d.available()) == ["my_two", "one"]
    assert d.overlay_dir("one") == tmp_path / "src" / "one"
    assert d.overlay_dir("my-two") == tmp_path / "src" / "my_two", "normalized-name match"
    assert not d.has("nope")
    with pytest.raises(SystemExit) as exc:
        d.overlay_dir("nope")
    assert "not found in source" in str(exc.value)
    with pytest.raises(SystemExit):
        _sources.load_repo(str(tmp_path / "missing"), GitCache(tmp_path / "cache"))


def test_registry_documents_and_entry_forms(tmp_path):
    cache = GitCache(tmp_path / "cache")
    _write(tmp_path / "one" / "overlay.toml", 'name = "one"\n')
    _write(tmp_path / "many" / "two" / "overlay.toml", 'name = "two"\n')
    _write(tmp_path / "r.json", json.dumps({
        "one": str(tmp_path / "one"),            # the overlay itself
        "two": str(tmp_path / "many"),           # a directory of overlays holding two/
        "two-alias": str(tmp_path / "many") + "#two",
        "web": "https://example.invalid/x",
    }))
    reg = _sources.load_repo(str(tmp_path / "r.json"), cache)
    assert isinstance(reg, RegistryRepo)
    assert reg.overlay_dir("one") == tmp_path / "one"
    assert reg.overlay_dir("two") == tmp_path / "many" / "two"
    assert reg.overlay_dir("two_alias") == tmp_path / "many" / "two", "aliases match by normalized name"
    with pytest.raises(SystemExit) as exc:
        reg.overlay_dir("web")
    assert "can only be a registry" in str(exc.value)
    _write(tmp_path / "r.toml", '[overlays]\na = "/srv/a"\n')
    assert _sources.load_repo(str(tmp_path / "r.toml"), cache).entries == {"a": "/srv/a"}
    pytest.importorskip("yaml")
    _write(tmp_path / "r.yaml", "overlays:\n  a: /srv/a\n")
    assert _sources.load_repo(str(tmp_path / "r.yaml"), cache).entries == {"a": "/srv/a"}
    _write(tmp_path / "bad.json", json.dumps({"a": 1}))
    with pytest.raises(SystemExit) as exc:
        _sources.load_repo(str(tmp_path / "bad.json"), cache)
    assert "must be a source string" in str(exc.value)
    _write(tmp_path / "r.ini", "x")
    with pytest.raises(SystemExit):
        _sources.load_repo(str(tmp_path / "r.ini"), cache)


def test_env_repos_are_ordered_by_key_then_the_default():
    env = {"AGENTS_OVERLAYS_REPO_B": "/b", "AGENTS_OVERLAYS_REPO_A": "/a", "AGENTS_OVERLAYS_REPO_EMPTY": " ",
           "AGENTS_OVERLAYS_REPO": "/default", "OTHER": "x"}
    assert _sources.env_repos(env) == ["/a", "/b", "/default"]
    assert _sources.env_repos({"AGENTS_OVERLAYS_REPO": " "}) == []


def test_registry_files_first_suffix_per_store(tmp_path):
    _write(tmp_path / "p" / "dotagents.toml", "")
    _write(tmp_path / "p" / "dotagents.json", "{}")
    _write(tmp_path / "u" / "dotagents.yaml", "")
    assert _sources.registry_files(tmp_path / "p", None, tmp_path / "u", tmp_path / "none") == [
        tmp_path / "p" / "dotagents.json", tmp_path / "u" / "dotagents.yaml",
    ]


# --------------------------------------------------------------------------
# Precedence: the first repo that offers a name wins; repos load lazily
# --------------------------------------------------------------------------

def _repo_dir(tmp_path, who):
    root = tmp_path / who
    _write(root / "shared" / "overlay.toml", 'name = "shared"\n')
    _write(root / ("only_%s" % who) / "overlay.toml", 'name = "only_%s"\n' % who)
    return root


def test_resolve_precedence(tmp_path, monkeypatch):
    for who in ("repo", "repo2", "env", "envdefault", "proj", "user", "bundled"):
        _repo_dir(tmp_path, who)
    _write(tmp_path / "env.json", json.dumps({"shared": str(tmp_path / "env" / "shared"), "only_env": str(tmp_path / "env" / "only_env")}))
    proj, user = tmp_path / "proj_store", tmp_path / "user_store"
    _write(proj / "dotagents.json", json.dumps({"shared": str(tmp_path / "proj" / "shared"), "only_proj": str(tmp_path / "proj" / "only_proj")}))
    _write(user / "dotagents.toml", 'shared = "%s"\nonly_user = "%s"\n' % (
        str(tmp_path / "user" / "shared").replace("\\", "/"), str(tmp_path / "user" / "only_user").replace("\\", "/")))
    monkeypatch.setenv("AGENTS_OVERLAYS_REPO_X", str(tmp_path / "env.json"))
    monkeypatch.setenv("AGENTS_OVERLAYS_REPO", str(tmp_path / "envdefault"))

    def resolve(specs):
        return _sources.resolve(specs, cache_root=tmp_path / "cache", stores=[proj, user], bundled=tmp_path / "bundled")

    src = resolve([str(tmp_path / "repo"), str(tmp_path / "repo2")])
    assert isinstance(src, CompositeSource)
    assert src.overlay_dir("shared") == tmp_path / "repo" / "shared"
    for who in ("repo", "repo2", "env", "envdefault", "proj", "user", "bundled"):
        assert src.overlay_dir("only_" + who) == tmp_path / who / ("only_" + who), who
    names = src.available()
    assert names == ["only_repo", "shared"] + ["only_" + w for w in ("repo2", "env", "envdefault", "proj", "user", "bundled")], (
        "the first repo's names (sorted) come first, then each later repo's new ones")
    assert resolve([]).overlay_dir("shared") == tmp_path / "env" / "shared"
    monkeypatch.delenv("AGENTS_OVERLAYS_REPO_X")
    assert resolve([]).overlay_dir("shared") == tmp_path / "envdefault" / "shared"
    monkeypatch.delenv("AGENTS_OVERLAYS_REPO")
    assert resolve([]).overlay_dir("shared") == tmp_path / "proj" / "shared"
    (proj / "dotagents.json").unlink()
    assert resolve([]).overlay_dir("shared") == tmp_path / "user" / "shared"
    (user / "dotagents.toml").unlink()
    assert resolve([]).overlay_dir("shared") == tmp_path / "bundled" / "shared"
    with pytest.raises(SystemExit, match="no overlay source"):
        _sources.resolve([], cache_root=tmp_path / "cache", stores=[None, None], bundled=None)
    with pytest.raises(SystemExit) as exc:
        resolve([]).overlay_dir("nowhere")
    assert "not found in source" in str(exc.value)


def test_repos_load_lazily_in_order(tmp_path, monkeypatch):
    """A repo late in the list is never loaded (cloned) for a name an earlier one
    has -- and a broken late repo does not break an early hit."""
    _repo_dir(tmp_path, "first")
    src = _sources.resolve([str(tmp_path / "first"), str(tmp_path / "does-not-exist")],
                           cache_root=tmp_path / "cache", stores=[None, None], bundled=None)
    assert src.overlay_dir("shared") == tmp_path / "first" / "shared"
    assert list(src._loaded) == [0]
    with pytest.raises(SystemExit):
        src.overlay_dir("nowhere")  # now the second repo is needed, and it is broken


def test_resolve_source_keeps_its_contract(tmp_path):
    """`_scope.resolve_source([<dir>])` is a repo list with that dir first."""
    _repo_dir(tmp_path, "dir")
    source = _scope.resolve_source([str(tmp_path / "dir")])
    assert "shared" in source.available() and source.overlay_dir("shared") == tmp_path / "dir" / "shared"
    assert str(tmp_path / "dir") in source.root
    assert _scope.OverlaySource(tmp_path / "dir").available() == sorted(_scope.OverlaySource(tmp_path / "dir").available())


# --------------------------------------------------------------------------
# Through the CLI: add from a git registry entry, then sync follows the remote
# --------------------------------------------------------------------------

def _run(cmd_cls, **kwargs):
    cmd = cmd_cls()
    for k, v in kwargs.items():
        setattr(cmd, k, v)
    return cmd()


@needs_git
def test_add_and_sync_from_a_git_registry(repo, tmp_path, monkeypatch):
    from dotagents.cli.overlays import OverlayAdd, OverlaySync

    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("AGENTS_HOME", str(store))
    _write(store / "dotagents.json", json.dumps({
        "whole": repo["url"] + "@main",
        "inner": repo["url"] + "@dev#overlays/inner",
    }))
    (store / "AGENTS.md").write_text("<!-- dotagents:begin -->\n## Load on demand\n<!-- dotagents:end -->\n", encoding="utf-8")
    common = dict(global_scope=True, agents_dir=str(store), repo=[], copy=False, no_setup=True, dry_run=False)
    assert _run(OverlayAdd, name=["whole", "inner"], no_requires=False, **common) == 0
    assert (store / "overlays" / "whole" / "kb" / "WHOLE.md").read_text() == "main\n"
    assert (store / "overlays" / "inner" / "kb" / "INNER.md").read_text() == "dev\n"
    assert (store / ".cache" / "overlays").is_dir(), "the checkout cache lives beside the store's content"

    _write(repo["work"] / "kb" / "WHOLE.md", "main-3\n")
    _git("commit", "-q", "-am", "main-3", cwd=repo["work"])
    _git("push", "-q", str(repo["bare"]), "main", cwd=repo["work"])
    assert _run(OverlaySync, pattern=None, overwrite=True, **common) == 0
    assert (store / "overlays" / "whole" / "kb" / "WHOLE.md").read_text() == "main-3\n"
