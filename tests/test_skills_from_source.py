"""An overlay's ``skills`` names skills from elsewhere, and installing it fetches them.

Fetches come only from bare Git repositories made here, reached through a made-up host that Git's
``url.<base>.insteadOf`` maps onto them: no network. Each behaviour was seen failing before it was written.
"""

import json
import logging
import os
import subprocess
from pathlib import Path

import pytest

from dotagents import _aos, _overlays, _skill_sources, _skills
from dotagents._vendor import coqui_aos
from dotagents.cli import OverlayAdd, OverlayRemove, OverlayShow, OverlaySync
from dotagents.cli import overlays as overlays_cli
from _helpers import run_cmd as _run

BASE_AGENTS = (
    "<!-- dotagents:begin -->\n# Agent Directives\n\n## Always-on rules\n"
    "- **Base rule**: keep it.\n\n## Load on demand\nNothing ships here by default.\n"
    "<!-- dotagents:end -->\n"
)
HOST = "https://skills.example.test/tools.git"


def _git(cwd, *args, check=True):
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.test",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.test")
    proc = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=str(cwd), env=env,
                          capture_output=True, text=True)
    if check and proc.returncode:
        raise AssertionError("git %s: %s" % (args, proc.stderr))
    return proc.stdout.strip()


def _skill_text(name, body="Body.\n"):
    return "---\nname: %s\ndescription: The %s skill.\n---\n%s" % (name, name, body)


class Remote:
    """A bare repository standing for ``HOST``, with a working tree to change it from."""

    def __init__(self, root: Path):
        self.work = root / "work"
        self.bare = root / "tools.git"
        self.work.mkdir(parents=True)
        _git(self.work, "init", "--quiet", "-b", "main")
        self.write("skills/review/SKILL.md", _skill_text("review"))
        self.write("skills/review/notes.txt", "notes\n")
        self.write("skills/lint/SKILL.md", _skill_text("lint"))
        self.write("skills/odd/SKILL.md", _skill_text("something-else"))
        self.commit("first")

    def write(self, rel, text):
        path = self.work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(path), "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def commit(self, message):
        _git(self.work, "add", "-A")
        _git(self.work, "commit", "--quiet", "-m", message)
        if self.bare.exists():
            _git(self.work, "push", "--quiet", str(self.bare), "main")
        else:
            _git(self.work.parent, "clone", "--quiet", "--bare", str(self.work), str(self.bare))

    def pin(self, name):
        return _aos.tree_digest(self.work / "skills" / name)


@pytest.fixture(autouse=True)
def _no_real_terminal(monkeypatch):
    """No test asks at the real terminal; one that needs an answer sets it."""
    monkeypatch.setattr(overlays_cli, "_read_tty", lambda prompt: None)


@pytest.fixture
def remote(tmp_path):
    return Remote(tmp_path / "remote")


@pytest.fixture
def fetches(monkeypatch, remote, tmp_path):
    """Every fetch the commands make, through ``remote``; the sources asked for are listed."""
    asked = []

    class Counting(_skill_sources.GitFetcher):
        def fetch(self, source):
            asked.append(source.shown())
            return super().fetch(source)

    def make(scope, cache):
        return Counting(
            tmp_path / "fetch-scratch", protocols=_skill_sources.GIT_PROTOCOLS + ":file",
            extra_config=["url.%s.insteadOf=%s" % (remote.bare.as_uri(), HOST)],
        )

    monkeypatch.setattr(overlays_cli, "_skill_fetcher", make)
    return asked


@pytest.fixture
def world(tmp_path):
    src = tmp_path / "src"
    scope_root = tmp_path / "scope"
    scope_root.mkdir()
    (scope_root / "AGENTS.md").write_text(BASE_AGENTS, encoding="utf-8")
    return src, scope_root


def _overlay(src, name, skills="", files=()):
    ov = src / name
    ov.mkdir(parents=True, exist_ok=True)
    text = 'name = "%s"\n' % name
    if skills:
        text += "skills = [%s]\n" % skills
    (ov / "overlay.cfg").write_text(text, encoding="utf-8")
    for rel, body in files:
        (ov / rel).parent.mkdir(parents=True, exist_ok=True)
        (ov / rel).write_text(body, encoding="utf-8")
    return ov


def _entry(name, path, pin=None):
    return '{ name = "%s", source = "%s#skills/%s"%s }' % (name, HOST, path, ', pin = "%s"' % pin if pin else "")


def _add(src, scope_root, *names, yes=True, **extra):
    return _run(OverlayAdd, name=list(names), repo=[str(src)], global_scope=True, agents_dir=scope_root,
                copy=True, dry_run=False, yes=yes, **extra)


def _remove(scope_root, *names, **extra):
    return _run(OverlayRemove, name=list(names), global_scope=True, agents_dir=scope_root, dry_run=False, **extra)


def _sync(src, scope_root, **extra):
    return _run(OverlaySync, repo=[str(src)], global_scope=True, agents_dir=scope_root, copy=True,
                dry_run=False, **extra)


def _store(scope_root):
    return _skill_sources.FetchedSkills(scope_root / "overlays")


def _no_terminal(monkeypatch, answer=None):
    monkeypatch.setattr(overlays_cli, "_read_tty", lambda prompt: answer)


# --------------------------------------------------------------------------
# installing at the pin
# --------------------------------------------------------------------------

def test_an_overlay_naming_a_skill_by_source_installs_it_at_its_pin(world, remote, fetches):
    src, scope_root = world
    _overlay(src, "team", _entry("review", "review", remote.pin("review")))
    assert _add(src, scope_root, "team") == 0
    delivered = scope_root / "skills" / "review"
    assert (delivered / "SKILL.md").read_text(encoding="utf-8") == _skill_text("review")
    assert (delivered / "notes.txt").is_file()
    record = _store(scope_root).records()["review"]
    assert record["digest"] == remote.pin("review") == record["pin"]
    assert record["source"] == "%s#skills/review" % HOST
    assert record["overlays"] == ["team"] and not record["chosen"]
    assert fetches == ["%s#skills/review" % HOST]


def test_a_wrong_pin_refuses_the_skill_and_delivers_nothing_of_it(world, remote, fetches):
    src, scope_root = world
    wrong = _aos.sha256_digest(b"not the skill")
    _overlay(src, "team", _entry("review", "review", wrong))
    with pytest.raises(SystemExit, match="not delivered.*pin-mismatch"):
        _add(src, scope_root, "team")
    assert not (scope_root / "skills" / "review").exists()
    assert _store(scope_root).records() == {}
    assert not (scope_root / "overlays" / ".dotagents-skills" / "review").exists()
    assert (scope_root / "overlays" / "team").is_dir()  # the overlay applies where the consumer continues


def test_a_fetched_skill_naming_another_skill_is_refused(world, remote, fetches):
    src, scope_root = world
    _overlay(src, "team", _entry("odd", "odd", remote.pin("odd")))
    with pytest.raises(SystemExit, match="skill-name-mismatch"):
        _add(src, scope_root, "team")
    assert not (scope_root / "skills" / "odd").exists()


def test_a_catalog_names_the_source_of_a_name(world, remote, fetches, tmp_path):
    src, scope_root = world
    catalog = tmp_path / "catalog.toml"
    catalog.write_text('[skills]\nlint = { source = "%s#skills/lint", pin = "%s" }\n'
                       % (HOST, remote.pin("lint")), encoding="utf-8")
    _overlay(src, "team", '"lint"')
    assert _add(src, scope_root, "team", skill_catalog=["main=%s" % catalog]) == 0
    assert (scope_root / "skills" / "lint" / "SKILL.md").is_file()
    assert _store(scope_root).records()["lint"]["catalog"] == "main"


# --------------------------------------------------------------------------
# nothing is fetched before the yes
# --------------------------------------------------------------------------

def test_nothing_is_fetched_or_installed_before_the_yes(world, remote, fetches, monkeypatch):
    src, scope_root = world
    _overlay(src, "team", _entry("review", "review", remote.pin("review")))
    _no_terminal(monkeypatch)
    with pytest.raises(SystemExit, match="not accepted"):
        _add(src, scope_root, "team", yes=False)
    assert fetches == []
    assert not (scope_root / "overlays" / "team").exists() and not (scope_root / "skills").exists()
    _no_terminal(monkeypatch, "no")
    with pytest.raises(SystemExit):
        _add(src, scope_root, "team", yes=False)
    assert fetches == []


def test_a_yes_at_the_terminal_covers_the_overlay_and_its_skills(world, remote, fetches, monkeypatch, caplog):
    src, scope_root = world
    _overlay(src, "team", _entry("review", "review", remote.pin("review")))
    asked = []
    monkeypatch.setattr(overlays_cli, "_read_tty", lambda prompt: asked.append(prompt) or "yes")
    with caplog.at_level(logging.INFO):
        assert _add(src, scope_root, "team", yes=False) == 0
    assert len(asked) == 1 and "team" in asked[0]
    assert "skill review (named by overlay team): fetched from %s#skills/review, pinned" % HOST in caplog.text
    assert (scope_root / "skills" / "review").is_dir()


def test_a_dry_run_shows_the_skills_and_fetches_nothing(world, remote, fetches, caplog):
    src, scope_root = world
    _overlay(src, "team", _entry("review", "review"))
    with caplog.at_level(logging.INFO):
        assert _run(OverlayAdd, name=["team"], repo=[str(src)], global_scope=True, agents_dir=scope_root,
                    copy=True, dry_run=True, yes=False) == 0
    assert "UNPINNED" in caplog.text and fetches == []


# --------------------------------------------------------------------------
# a name nothing resolves
# --------------------------------------------------------------------------

def test_a_missing_and_an_ambiguous_name_are_reported_and_the_overlay_applies(world, remote, fetches, tmp_path, caplog):
    src, scope_root = world
    first, second = tmp_path / "a.toml", tmp_path / "b.toml"
    first.write_text('[skills]\ntwice = "%s#skills/lint"\n' % HOST, encoding="utf-8")
    second.write_text('[skills]\ntwice = "%s#skills/review"\n' % HOST, encoding="utf-8")
    _overlay(src, "team", '"ghost", "twice"', files=[("skills/own/SKILL.md", _skill_text("own"))])
    with caplog.at_level(logging.INFO):
        assert _add(src, scope_root, "team", skill_catalog=["one=%s" % first, "two=%s" % second]) == 0
    assert "skill ghost named by overlay team is not installed: no catalog offers it" in caplog.text
    assert "skill twice named by overlay team is not installed: catalogs one, two all offer it" in caplog.text
    assert fetches == []
    assert (scope_root / "overlays" / "team").is_dir() and (scope_root / "skills" / "own").exists()
    assert not (scope_root / "skills" / "ghost").exists() and not (scope_root / "skills" / "twice").exists()


# --------------------------------------------------------------------------
# keeping, syncing, removing
# --------------------------------------------------------------------------

def test_sync_keeps_what_was_accepted_without_fetching_it_again(world, remote, fetches):
    src, scope_root = world
    _overlay(src, "team", _entry("review", "review", remote.pin("review")))
    _add(src, scope_root, "team")
    fetches.clear()
    assert _sync(src, scope_root, yes=False) == 0
    assert fetches == [] and (scope_root / "skills" / "review" / "SKILL.md").is_file()


def test_sync_refetches_a_kept_skill_that_was_damaged_only_with_a_yes(world, remote, fetches):
    src, scope_root = world
    _overlay(src, "team", _entry("review", "review", remote.pin("review")))
    _add(src, scope_root, "team")
    tree = _store(scope_root).tree("review")
    (tree / "notes.txt").write_text("tampered\n", encoding="utf-8")
    fetches.clear()
    assert _sync(src, scope_root, yes=False) == 0 and fetches == []
    assert _sync(src, scope_root, yes=True) == 0
    assert (tree / "notes.txt").read_text(encoding="utf-8") == "notes\n" and len(fetches) == 1


def test_a_changed_pin_is_an_update_that_needs_a_yes(world, remote, fetches, monkeypatch, caplog):
    src, scope_root = world
    _overlay(src, "team", _entry("review", "review", remote.pin("review")))
    _add(src, scope_root, "team")
    old = _store(scope_root).records()["review"]["digest"]
    remote.write("skills/review/SKILL.md", _skill_text("review", "Changed.\n"))
    remote.commit("second")
    _overlay(src, "team", _entry("review", "review", remote.pin("review")))
    fetches.clear()
    _no_terminal(monkeypatch)
    with caplog.at_level(logging.INFO):
        assert _sync(src, scope_root, yes=False) == 0
    assert fetches == [] and _store(scope_root).records()["review"]["digest"] == old
    assert "is not fetched: it is new or changed" in caplog.text
    assert _sync(src, scope_root, yes=True) == 0
    assert _store(scope_root).records()["review"]["digest"] == remote.pin("review") != old
    assert "Changed." in (scope_root / "skills" / "review" / "SKILL.md").read_text(encoding="utf-8")


def test_removal_keeps_a_skill_another_overlay_still_names_and_offers_the_last(world, remote, fetches, monkeypatch):
    src, scope_root = world
    entry = _entry("review", "review", remote.pin("review"))
    _overlay(src, "one", entry)
    _overlay(src, "two", entry)
    _add(src, scope_root, "one", "two")
    assert _store(scope_root).records()["review"]["overlays"] == ["one", "two"]
    assert _remove(scope_root, "one", yes=True) == 0
    assert (scope_root / "skills" / "review" / "SKILL.md").is_file()
    # Offered, not forced: no terminal and no yes keeps it.
    _no_terminal(monkeypatch)
    assert _remove(scope_root, "two", yes=False) == 0
    assert (scope_root / "skills" / "review").exists() and "review" in _store(scope_root).records()
    # Naming nothing now; a later removal with the yes takes it.
    _overlay(src, "three", "")
    _add(src, scope_root, "three")
    assert _remove(scope_root, "three", yes=True) == 0
    assert not os.path.lexists(str(scope_root / "skills" / "review")) and _store(scope_root).records() == {}
    assert not _store(scope_root).tree("review").exists()


def test_a_skill_the_person_chose_is_never_removed_with_an_overlay(world, remote, fetches):
    src, scope_root = world
    _overlay(src, "one", _entry("review", "review", remote.pin("review")))
    _add(src, scope_root, "one")
    store = _store(scope_root)
    records = store.records()
    records["review"]["chosen"] = True
    store.save(records)
    assert _remove(scope_root, "one", yes=True) == 0
    assert (scope_root / "skills" / "review" / "SKILL.md").is_file() and "review" in store.records()


def test_a_skill_placed_by_hand_is_kept_and_not_replaced(world, remote, fetches, caplog):
    src, scope_root = world
    (scope_root / "skills" / "review").mkdir(parents=True)
    (scope_root / "skills" / "review" / "SKILL.md").write_text(_skill_text("review", "Mine.\n"), encoding="utf-8")
    _overlay(src, "one", _entry("review", "review", remote.pin("review")))
    with caplog.at_level(logging.WARNING):
        _add(src, scope_root, "one")
    assert "Mine." in (scope_root / "skills" / "review" / "SKILL.md").read_text(encoding="utf-8")
    _remove(scope_root, "one", yes=True)
    assert "Mine." in (scope_root / "skills" / "review" / "SKILL.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# delivered like a bundled skill; nothing runs; hard limits
# --------------------------------------------------------------------------

def test_a_bundled_skill_is_still_published_and_a_named_one_is_not_looked_for_in_the_tree(world, remote, fetches):
    src, scope_root = world
    _overlay(src, "team", '"lint"', files=[("skills/own/SKILL.md", _skill_text("own"))])
    overlay = _overlays.Overlay(src / "team")
    assert overlay.skills() == ["own"] and [e.name for e in overlay.skills_elsewhere()] == ["lint"]
    assert _skills.harness_excluded(scope_root, "claude-code") == set()


def test_a_fetched_skill_is_listed_in_the_context_like_a_bundled_one(world, remote, fetches):
    from dotagents import _context, _scope

    src, scope_root = world
    _overlay(src, "team", _entry("review", "review", remote.pin("review")))
    _add(src, scope_root, "team")
    names = [name for name, _description, _path in _context._collect_skills(_scope.resolve_scope(
        global_scope=True, agents_dir=scope_root))]
    assert "review" in names


def test_a_source_that_is_not_git_is_not_fetched_here(tmp_path):
    fetcher = _skill_sources.GitFetcher(tmp_path)
    with pytest.raises(coqui_aos.Error):
        fetcher.fetch(coqui_aos.Source.parse("https://skills.example.test/pack/review"))


def test_a_link_in_a_skill_is_refused(tmp_path, remote):
    sha = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=str(remote.work), input="SKILL.md",
                         capture_output=True, text=True).stdout.strip()
    _git(remote.work, "update-index", "--add", "--cacheinfo", "120000,%s,skills/lint/link" % sha)
    _git(remote.work, "commit", "--quiet", "-m", "link")
    _git(remote.work, "push", "--quiet", str(remote.bare), "main")
    fetcher = _skill_sources.GitFetcher(
        tmp_path / "s", protocols=_skill_sources.GIT_PROTOCOLS + ":file",
        extra_config=["url.%s.insteadOf=%s" % (remote.bare.as_uri(), HOST)])
    with pytest.raises(coqui_aos.Error) as caught:
        fetcher.fetch(coqui_aos.Source.parse(HOST + "#skills/lint"))
    assert caught.value.class_ is coqui_aos.Class.SYMLINK


def test_a_fetch_is_given_none_of_the_persons_credentials(monkeypatch, tmp_path):
    for key in ("SSH_AUTH_SOCK", "GIT_ASKPASS", "SSH_ASKPASS", "GH_TOKEN", "GIT_CONFIG_COUNT", "GIT_SSH_COMMAND"):
        monkeypatch.setenv(key, "x")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.test:3128")
    fetcher = _skill_sources.GitFetcher(tmp_path)
    env = fetcher.environment()
    assert not {"SSH_AUTH_SOCK", "GIT_ASKPASS", "SSH_ASKPASS", "GH_TOKEN", "GIT_CONFIG_COUNT"} & set(env)
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert "IdentityAgent=none" in env["GIT_SSH_COMMAND"] and "BatchMode=yes" in env["GIT_SSH_COMMAND"]
    assert env["HTTPS_PROXY"] == "http://proxy.example.test:3128"
    args = fetcher.arguments(["fetch"])
    for setting in ("credential.helper=", "http.followRedirects=false"):
        assert setting in args
    assert fetcher.protocols == "http:https:ssh:git" and env["GIT_ALLOW_PROTOCOL"] == "http:https:ssh:git"


def test_fetching_runs_nothing_from_the_source(world, remote, fetches, tmp_path):
    src, scope_root = world
    marker = tmp_path / "ran"
    remote.write("skills/review/setup.py", "open(%r, 'w').write('ran')\n" % str(marker))
    remote.write("skills/review/env.sh", "touch %s\n" % marker)
    remote.write(".gitattributes", "* filter=evil\n")
    remote.commit("scripts")
    _overlay(src, "team", _entry("review", "review", remote.pin("review")))
    assert _add(src, scope_root, "team") == 0
    assert not marker.exists()
    assert (scope_root / "skills" / "review" / "setup.py").is_file()  # a file like any other


# --------------------------------------------------------------------------
# show, and the case dotagents and canoa share
# --------------------------------------------------------------------------

def test_show_lists_the_skills_from_elsewhere_with_their_sources(world, remote, capsys):
    src, scope_root = world
    _overlay(src, "team", _entry("review", "review", remote.pin("review")), files=[("skills/own/SKILL.md", _skill_text("own"))])
    _run(OverlayShow, name="team", repo=[str(src)], global_scope=True, agents_dir=scope_root, json=True)
    info = json.loads(capsys.readouterr().out)
    assert info["skills"] == ["own"]
    assert info["skills_elsewhere"] == [
        {"name": "review", "origin": "source", "source": "%s#skills/review" % HOST, "pin": remote.pin("review")}]


CASES = json.loads((Path(__file__).parent / "skills_from_source_case.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"][:40] for c in CASES])
def test_the_same_overlay_gives_the_skills_canoa_gives(case, tmp_path):
    """The case canoa's tests hold too: one overlay's skills are the same wherever it is installed."""
    overlays = {}
    for identity, files in case["overlays"].items():
        root = tmp_path / identity
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            with open(str(root / rel), "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
        overlays[identity] = _overlays.Overlay(root)
    catalogs = []
    for item in case["catalogs"]:
        path = tmp_path / ("%s-%s" % (item["label"], item["name"]))
        path.write_text(item["text"], encoding="utf-8")
        catalogs.append("%s=%s" % (item["label"], path))
    plan = _skill_sources.plan_skills(overlays, _skill_sources.load_catalogs(catalogs))
    expect = case["expect"]
    got = coqui_aos.to_data(plan)
    assert [{k: v for k, v in coqui_aos.to_data(s).items() if v is not None} for s in plan.skills] == expect["skills"]
    assert [list(x) for x in plan.missing] == expect["missing"]
    assert [[a, b, c] for a, b, c in plan.ambiguous] == expect["ambiguous"]
    assert [list(x) for x in plan.unpinned] == expect["unpinned"]
    assert [list(x) for x in plan.shadowing] == expect["shadowing"]
    assert plan.delivered == expect["delivered"] and plan.collisions == expect["collisions"]
    assert [{"name": f.name, "overlay": f.overlay, "source": f.from_.shown(), "pin": f.pin}
            for f in plan.to_fetch] == expect["fetch"]
    assert got  # the plan is data
