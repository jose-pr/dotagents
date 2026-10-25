"""The branch layout: a project's store is a branch of the agents repo, and
``<project>/.agents`` is a git worktree on it."""
import subprocess

import pytest

from _link import branch_layout, link_project, sync_agents

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def git(cwd, *args):
    res = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


@pytest.fixture
def repos(tmp_path, monkeypatch):
    for key, value in GIT_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("AGENTS_STORE_LAYOUT", raising=False)
    monkeypatch.delenv("AGENTS_STORE_DIR", raising=False)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    agents = tmp_path / "agents"
    subprocess.run(["git", "clone", "-q", str(origin), str(agents)], check=True)
    (agents / "AGENTS.md").write_text("global\n", encoding="utf-8")
    git(agents, "add", "-A")
    git(agents, "commit", "-q", "-m", "init")
    git(agents, "push", "-q", "-u", "origin", "main")
    project = tmp_path / "app"
    project.mkdir()
    (project / ".gitignore").write_text(".agents\n", encoding="utf-8")
    return origin, agents, project


def branch_of(path):
    return git(path, "symbolic-ref", "--short", "HEAD")


def test_a_new_project_gets_an_orphan_branch_with_ignore_rules(repos):
    _origin, agents, project = repos
    assert branch_layout(agents, "app")
    link_project(project, agents)
    assert branch_of(project / ".agents") == "app"
    assert (project / ".agents" / ".gitignore").is_file()
    # The global config is not on the project's branch.
    assert not (project / ".agents" / "AGENTS.md").exists()
    # Linking again changes nothing.
    link_project(project, agents)
    assert branch_of(project / ".agents") == "app"


def test_a_real_agents_dir_is_adopted_and_committed(repos):
    _origin, agents, project = repos
    (project / ".agents" / "plans").mkdir(parents=True)
    (project / ".agents" / "plans" / "a.md").write_text("plan\n", encoding="utf-8")
    (project / ".agents" / "scratch").mkdir()
    (project / ".agents" / "scratch" / "tmp.txt").write_text("x\n", encoding="utf-8")
    link_project(project, agents)
    worktree = project / ".agents"
    assert (worktree / "plans" / "a.md").read_text(encoding="utf-8") == "plan\n"
    assert "plans/a.md" in git(worktree, "ls-files")
    # Scratch is carried over but never committed.
    assert (worktree / "scratch" / "tmp.txt").is_file()
    assert "scratch/tmp.txt" not in git(worktree, "ls-files")
    assert not list(project.glob(".agents.bak*"))


def test_a_branch_on_origin_is_checked_out_and_tracked(repos, tmp_path):
    origin, agents, project = repos
    # Another machine pushed the project's branch.
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    git(other, "checkout", "-q", "--orphan", "app")
    git(other, "rm", "-rq", "--cached", ".")
    for child in other.iterdir():
        if child.name != ".git":
            child.unlink()
    (other / "kb.md").write_text("from elsewhere\n", encoding="utf-8")
    git(other, "add", "kb.md")
    git(other, "commit", "-q", "-m", "app store")
    git(other, "push", "-q", "origin", "app")

    link_project(project, agents)
    worktree = project / ".agents"
    assert (worktree / "kb.md").read_text(encoding="utf-8") == "from elsewhere\n"
    assert git(worktree, "rev-parse", "--abbrev-ref", "@{u}") == "origin/app"


def test_sync_commits_and_pushes_the_project_branch(repos):
    origin, agents, project = repos
    link_project(project, agents)
    (project / ".agents" / "notes.md").write_text("n\n", encoding="utf-8")
    sync_agents(agents, project_dir=project, message="sync: app")
    assert "notes.md" in git(origin, "ls-tree", "-r", "--name-only", "app")
    # The global repo's main branch does not carry the project's files.
    assert "notes.md" not in git(origin, "ls-tree", "-r", "--name-only", "main")


def test_an_existing_projects_dir_keeps_the_directory_layout(repos):
    _origin, agents, _project = repos
    (agents / "projects" / "app").mkdir(parents=True)
    assert not branch_layout(agents, "app")


def test_the_layout_can_be_forced(repos, monkeypatch):
    _origin, agents, _project = repos
    (agents / "projects" / "app").mkdir(parents=True)
    monkeypatch.setenv("AGENTS_STORE_LAYOUT", "branch")
    assert branch_layout(agents, "app")
    monkeypatch.setenv("AGENTS_STORE_LAYOUT", "dir")
    (agents / "projects" / "app").rmdir()
    assert not branch_layout(agents, "app")
