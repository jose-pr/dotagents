"""Tests for the VCS-host abstraction (overlays/release/tools/hosts.py).

No network: every subprocess call to `gh`/`gitlabq` is mocked. Run from the repo
root with `python -m pytest overlays/release/tests/`.
"""

import json
import sys
from pathlib import Path

import pytest

# Make tools/ importable regardless of cwd.
TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import hosts  # noqa: E402
from hosts import GitHubHost, GitLabHost, ReleaseHost, _parse_remote_url  # noqa: E402


# --------------------------------------------------------------------------
# Remote URL parsing
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    # scp-like SSH
    ("git@github.com:owner/repo.git", ("github.com", "owner/repo")),
    ("git@github.com:owner/repo", ("github.com", "owner/repo")),
    ("git@gitlab.com:group/sub/project.git", ("gitlab.com", "group/sub/project")),
    ("git@gitlab.example.org:team/app.git", ("gitlab.example.org", "team/app")),
    # ssh://
    ("ssh://git@github.com/owner/repo.git", ("github.com", "owner/repo")),
    ("ssh://git@gitlab.example.org:2222/team/app.git", ("gitlab.example.org", "team/app")),
    # https://
    ("https://github.com/owner/repo.git", ("github.com", "owner/repo")),
    ("https://github.com/owner/repo", ("github.com", "owner/repo")),
    ("https://gitlab.com/group/sub/project.git", ("gitlab.com", "group/sub/project")),
    ("https://ghe.corp.example/owner/repo.git", ("ghe.corp.example", "owner/repo")),
    # trailing slash / creds in URL
    ("https://user@github.com/owner/repo/", ("github.com", "owner/repo")),
])
def test_parse_remote_url(url, expected):
    assert _parse_remote_url(url) == expected


def test_parse_remote_url_unparseable():
    assert _parse_remote_url("not a url") is None
    assert _parse_remote_url("") is None


# --------------------------------------------------------------------------
# Host detection
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "git@github.com:owner/repo.git",
    "https://github.com/owner/repo.git",
    "ssh://git@github.com/owner/repo.git",
    "https://ghe.corp.example/owner/repo.git",   # GHE-ish, host contains "github"? no
    "git@github.enterprise.example:owner/repo.git",
])
def test_github_detect_positive(url):
    # ghe.corp.example does NOT contain "github", so assert per-host below instead.
    if "github" in _parse_remote_url(url)[0].lower():
        assert GitHubHost.detect(url) is True


@pytest.mark.parametrize("url", [
    "git@github.com:owner/repo.git",
    "https://github.com/owner/repo",
    "ssh://git@github.com/owner/repo.git",
    "git@github.enterprise.example:owner/repo.git",
])
def test_github_detect_true(url):
    assert GitHubHost.detect(url) is True


@pytest.mark.parametrize("url", [
    "git@gitlab.com:group/project.git",
    "https://gitlab.com/group/project",
    "ssh://git@gitlab.example.org:2222/team/app.git",
    "git@gitlab.example.org:team/app.git",
])
def test_gitlab_detect_true(url):
    assert GitLabHost.detect(url) is True


def test_detect_cross_negative():
    assert GitHubHost.detect("git@gitlab.com:group/project.git") is False
    assert GitLabHost.detect("git@github.com:owner/repo.git") is False
    assert GitHubHost.detect("garbage") is False
    assert GitLabHost.detect("garbage") is False


# --------------------------------------------------------------------------
# from_remote factory picks the right impl
# --------------------------------------------------------------------------

def test_from_remote_github():
    h = ReleaseHost.from_remote("git@github.com:owner/repo.git")
    assert isinstance(h, GitHubHost)
    assert h.name == "github"
    assert h.parse_remote() == ("github.com", "owner/repo")


def test_from_remote_gitlab():
    h = ReleaseHost.from_remote("https://gitlab.com/group/project.git")
    assert isinstance(h, GitLabHost)
    assert h.name == "gitlab"
    assert h.host == "gitlab.com"
    assert h.project == "group/project"


def test_from_remote_self_hosted_gitlab():
    h = ReleaseHost.from_remote("ssh://git@gitlab.example.org:2222/team/app.git")
    assert isinstance(h, GitLabHost)
    assert h.host == "gitlab.example.org"


def test_from_remote_unknown_returns_none():
    assert ReleaseHost.from_remote("git@bitbucket.org:owner/repo.git") is None
    assert ReleaseHost.from_remote(None) is None
    assert ReleaseHost.from_remote("") is None


# --------------------------------------------------------------------------
# monitor_ci — uniform shape, mocked subprocess (no network)
# --------------------------------------------------------------------------

class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _assert_uniform(result):
    assert isinstance(result, dict)
    assert "status" in result and "message" in result
    assert result["status"] in ("success", "failed", "unknown", "error")
    assert isinstance(result["message"], str) and result["message"]


def test_github_monitor_ci_gh_missing(monkeypatch):
    monkeypatch.setattr(hosts, "which", lambda name: None)
    h = GitHubHost("git@github.com:owner/repo.git")
    result = h.monitor_ci("v1.2.3")
    _assert_uniform(result)
    assert result["status"] == "unknown"
    assert "gh" in result["message"].lower()


def test_github_monitor_ci_success(monkeypatch):
    monkeypatch.setattr(hosts, "which", lambda name: "/usr/bin/gh")
    runs = [{
        "databaseId": 42,
        "status": "completed",
        "conclusion": "success",
        "url": "https://github.com/owner/repo/actions/runs/42",
    }]
    monkeypatch.setattr(
        GitHubHost, "_run",
        staticmethod(lambda cmd, timeout=None: FakeProc(stdout=json.dumps(runs))),
    )
    h = GitHubHost("git@github.com:owner/repo.git")
    result = h.monitor_ci("v1.2.3")
    _assert_uniform(result)
    assert result["status"] == "success"


def test_github_monitor_ci_failure(monkeypatch):
    monkeypatch.setattr(hosts, "which", lambda name: "/usr/bin/gh")
    runs = [{
        "databaseId": 7,
        "status": "completed",
        "conclusion": "failure",
        "url": "https://github.com/owner/repo/actions/runs/7",
    }]
    monkeypatch.setattr(
        GitHubHost, "_run",
        staticmethod(lambda cmd, timeout=None: FakeProc(stdout=json.dumps(runs))),
    )
    h = GitHubHost("git@github.com:owner/repo.git")
    result = h.monitor_ci("v1.2.3")
    _assert_uniform(result)
    assert result["status"] == "failed"


def test_github_monitor_ci_no_runs(monkeypatch):
    monkeypatch.setattr(hosts, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        GitHubHost, "_run",
        staticmethod(lambda cmd, timeout=None: FakeProc(stdout="[]")),
    )
    h = GitHubHost("git@github.com:owner/repo.git")
    result = h.monitor_ci("v9.9.9")
    _assert_uniform(result)
    assert result["status"] == "unknown"


def test_github_monitor_ci_list_error(monkeypatch):
    monkeypatch.setattr(hosts, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        GitHubHost, "_run",
        staticmethod(lambda cmd, timeout=None: FakeProc(returncode=1, stderr="boom")),
    )
    h = GitHubHost("git@github.com:owner/repo.git")
    result = h.monitor_ci("v1.2.3")
    _assert_uniform(result)
    assert result["status"] == "error"


def test_gitlab_monitor_ci_tool_missing(monkeypatch):
    monkeypatch.delenv("GITLABQ", raising=False)
    monkeypatch.setattr(hosts, "which", lambda name: None)
    h = GitLabHost("git@gitlab.com:group/project.git")
    result = h.monitor_ci("v1.2.3")
    _assert_uniform(result)
    assert result["status"] == "unknown"
    assert "unavailable" in result["message"].lower()
    # Must not leak any private overlay/infra name.
    assert "internal" not in result["message"].lower()


def test_gitlab_monitor_ci_success(monkeypatch):
    monkeypatch.delenv("GITLABQ", raising=False)
    monkeypatch.setattr(hosts, "which", lambda name: "/usr/bin/gitlabq")
    payload = {"status": "success", "pipeline": {"web_url": "https://gitlab.com/p/1"}}
    monkeypatch.setattr(
        hosts.subprocess, "run",
        lambda *a, **k: FakeProc(stdout=json.dumps(payload)),
    )
    h = GitLabHost("git@gitlab.com:group/project.git")
    result = h.monitor_ci("v1.2.3")
    _assert_uniform(result)
    assert result["status"] == "success"


def test_gitlab_monitor_ci_failed(monkeypatch):
    monkeypatch.delenv("GITLABQ", raising=False)
    monkeypatch.setattr(hosts, "which", lambda name: "/usr/bin/gitlabq")
    payload = {
        "status": "failed",
        "pipeline": {"web_url": "https://gitlab.com/p/2"},
        "failed_jobs": ["build"],
    }
    monkeypatch.setattr(
        hosts.subprocess, "run",
        lambda *a, **k: FakeProc(stdout=json.dumps(payload)),
    )
    h = GitLabHost("git@gitlab.com:group/project.git")
    result = h.monitor_ci("v1.2.3")
    _assert_uniform(result)
    assert result["status"] == "failed"
    assert result["failed_jobs"] == ["build"]


def test_gitlab_monitor_ci_nonjson(monkeypatch):
    monkeypatch.delenv("GITLABQ", raising=False)
    monkeypatch.setattr(hosts, "which", lambda name: "/usr/bin/gitlabq")
    monkeypatch.setattr(
        hosts.subprocess, "run",
        lambda *a, **k: FakeProc(stdout="not json"),
    )
    h = GitLabHost("git@gitlab.com:group/project.git")
    result = h.monitor_ci("v1.2.3")
    _assert_uniform(result)
    assert result["status"] == "error"


# --------------------------------------------------------------------------
# Uniform shape across BOTH hosts in their unavailable-tool paths
# --------------------------------------------------------------------------

def test_monitor_ci_shape_uniform_across_hosts(monkeypatch):
    monkeypatch.delenv("GITLABQ", raising=False)
    monkeypatch.setattr(hosts, "which", lambda name: None)
    gh = ReleaseHost.from_remote("git@github.com:owner/repo.git")
    gl = ReleaseHost.from_remote("git@gitlab.com:group/project.git")
    r1 = gh.monitor_ci("v1.0.0")
    r2 = gl.monitor_ci("v1.0.0")
    for r in (r1, r2):
        _assert_uniform(r)
    assert set(["status", "message"]).issubset(r1)
    assert set(["status", "message"]).issubset(r2)


# --------------------------------------------------------------------------
# push default
# --------------------------------------------------------------------------

def test_push_default_calls_git():
    calls = []
    h = GitHubHost("git@github.com:owner/repo.git")
    h.push(lambda *a: calls.append(a), "v1.2.3")
    assert calls == [("push",), ("push", "origin", "v1.2.3")]
