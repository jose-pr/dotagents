"""The default ignore rules of a store kept in git.

A linked project's ``.agents/scratch/`` is ``<store>/projects/<name>/scratch/``,
so without these rules the built-in git path (``git add -A``) commits throwaway
files, caches and the machine-local overrides into the private repository.
"""
import os
import shutil
import subprocess

import pytest

from _link import (
    STORE_IGNORE_DEFAULTS,
    STORE_IGNORE_MARK,
    ensure_store_gitignore,
    sync_agents,
)


def _lines(path):
    return path.read_bytes().decode("utf-8").splitlines()


def test_a_store_without_a_gitignore_gets_the_defaults(tmp_path):
    assert ensure_store_gitignore(tmp_path) is True
    lines = _lines(tmp_path / ".gitignore")
    assert lines[0] == STORE_IGNORE_MARK
    for rule in ("scratch/", "tmp/", ".*", "!.gitignore", "!.gitkeep"):
        assert rule in lines
    assert lines[-len(STORE_IGNORE_DEFAULTS):] == list(STORE_IGNORE_DEFAULTS)
    assert b"\r" not in (tmp_path / ".gitignore").read_bytes()


def test_the_block_is_written_once_and_an_edited_one_is_left_alone(tmp_path):
    ensure_store_gitignore(tmp_path)
    path = tmp_path / ".gitignore"
    # The owner drops a rule and adds an exception; the marker line stays.
    edited = [line for line in _lines(path) if line != "tmp/"] + ["!.keepme"]
    path.write_bytes(("\n".join(edited) + "\n").encode("utf-8"))

    assert ensure_store_gitignore(tmp_path) is False
    assert _lines(path) == edited


def test_existing_rules_are_kept_and_the_block_follows_them(tmp_path):
    path = tmp_path / ".gitignore"
    path.write_bytes(b"# mine\n!scratch/keep.md\n*.bak")  # no final newline

    assert ensure_store_gitignore(tmp_path) is True
    lines = _lines(path)
    assert lines[:3] == ["# mine", "!scratch/keep.md", "*.bak"]
    assert lines[3] == "" and lines[4] == STORE_IGNORE_MARK


def test_a_crlf_file_stays_crlf(tmp_path):
    path = tmp_path / ".gitignore"
    path.write_bytes(b"*.bak\r\n")

    ensure_store_gitignore(tmp_path)
    data = path.read_bytes()
    assert data.count(b"\r\n") == data.count(b"\n")


def test_dry_run_reports_and_writes_nothing(tmp_path):
    seen = []
    assert ensure_store_gitignore(tmp_path, dry_run=True, log=lambda *a: seen.append(a)) is True
    assert seen and not (tmp_path / ".gitignore").exists()


@pytest.fixture
def git_env(tmp_path, monkeypatch):
    """A git that reads no system or user configuration and has an identity."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv("GIT_%s_NAME" % role, "Test")
        monkeypatch.setenv("GIT_%s_EMAIL" % role, "test@example.invalid")
    monkeypatch.delenv("DOTAGENTS_AGENTS_TOKEN", raising=False)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "--quiet", str(remote)], check=True)
    return remote


def _write(root, rel, text="x\n"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _tracked(agents):
    out = subprocess.run(
        ["git", "-C", str(agents), "ls-files"], capture_output=True, text=True, check=True
    )
    return set(out.stdout.split())


def test_the_first_sync_leaves_throwaway_hidden_and_local_files_out(tmp_path, git_env):
    agents = tmp_path / "agents"
    kept = [
        "AGENTS.md",
        "projects/app/AGENTS.md",
        "projects/app/plans/.gitkeep",
        "projects/app/plans/work.md",
        "overlays/demo/.dotagents-install.json",
        "overlays/demo/references/.gitignore",
    ]
    left_out = [
        "AGENTS.local.md",
        "scratch/probe.py",
        "projects/app/scratch/capture.log",
        "projects/app/tmp/out.json",
        "projects/app/.pytest_cache/state",
        "projects/app/local.env",
        "projects/app/pre.local.env",
        "projects/app/dotagents/cmds/__pycache__/x.pyc",
        ".cache/overlays/checkout/file",
    ]
    for rel in kept + left_out:
        _write(agents, rel)

    assert sync_agents(agents, remote=str(git_env), message="init", pull=False) == 0

    tracked = _tracked(agents)
    assert tracked == set(kept) | {".gitignore"}
    pushed = subprocess.run(
        ["git", "--git-dir", str(git_env), "ls-tree", "-r", "--name-only", "main"],
        capture_output=True, text=True, check=True,
    )
    assert set(pushed.stdout.split()) == tracked


def test_a_later_sync_does_not_write_the_block_again(tmp_path, git_env):
    agents = tmp_path / "agents"
    _write(agents, "AGENTS.md")
    sync_agents(agents, remote=str(git_env), message="init", pull=False)
    before = (agents / ".gitignore").read_bytes()

    _write(agents, "projects/app/plans/work.md")
    assert sync_agents(agents, message="more") == 0
    assert (agents / ".gitignore").read_bytes() == before
    assert "projects/app/plans/work.md" in _tracked(agents)
