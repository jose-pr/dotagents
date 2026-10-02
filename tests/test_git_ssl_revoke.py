"""`git_ssl_revoke` in `<store>/dotagents/config.toml`: git's
certificate-revocation check for overlay clones and fetches. Git for Windows'
Schannel backend fails an https clone whose CRL cannot be fetched; git with
OpenSSL (Linux) checks no CRLs. dotagents passes the setting to every git call
as `http.schannelCheckRevoke`, `best-effort` unless the store says otherwise."""
import subprocess

import pytest

from dotagents import _sources
from dotagents.cli._common import STORE_CONFIG


def _config(store, text):
    path = store / STORE_CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def argv(monkeypatch):
    seen = []

    def run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(_sources.subprocess, "run", run)
    return seen


def test_every_git_call_carries_the_setting_best_effort_by_default(tmp_path, argv):
    cache = _sources.SourceCache(tmp_path / "cache")
    cache._git(["ls-remote", "https://example.invalid/r.git"], None)
    assert argv[-1][:3] == ["git", "-c", "http.schannelCheckRevoke=best-effort"]
    _sources.SourceCache(tmp_path / "cache", ssl_revoke="false")._git(["fetch"], None)
    assert argv[-1][:3] == ["git", "-c", "http.schannelCheckRevoke=false"]


def test_the_store_config_chooses_project_before_user(tmp_path):
    user, project = tmp_path / "user", tmp_path / "project"
    assert _sources.git_ssl_revoke(project, user) == "best-effort"
    _config(user, 'git_ssl_revoke = "false"\n')
    assert _sources.git_ssl_revoke(None, user) == "false"
    _config(project, 'git_ssl_revoke = "TRUE"\n')
    assert _sources.git_ssl_revoke(project, user) == "true"


def test_an_unknown_value_names_the_file(tmp_path):
    _config(tmp_path, 'git_ssl_revoke = "maybe"\n')
    with pytest.raises(_sources.SourceError, match="git_ssl_revoke = 'maybe'.*best-effort, true, false"):
        _sources.git_ssl_revoke(tmp_path)


def test_a_revocation_failure_names_the_setting(tmp_path, monkeypatch):
    stderr = ("fatal: unable to access 'https://example.invalid/r.git/': schannel: next InitializeSecurityContext "
              "failed: CRYPT_E_NO_REVOCATION_CHECK (0x80092012) - The revocation function was unable to check "
              "revocation for the certificate.")
    monkeypatch.setattr(_sources.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 128, "", stderr))
    with pytest.raises(_sources.SourceError) as caught:
        _sources.SourceCache(tmp_path)._git(["clone", "https://example.invalid/r.git"], None)
    message = str(caught.value)
    assert "CRYPT_E_NO_REVOCATION_CHECK" in message and 'git_ssl_revoke = "false"' in message


def test_any_other_failure_has_no_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(_sources.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 128, "", "fatal: repository not found"))
    with pytest.raises(_sources.SourceError) as caught:
        _sources.SourceCache(tmp_path)._git(["clone", "https://example.invalid/r.git"], None)
    assert "git_ssl_revoke" not in str(caught.value)


@pytest.mark.skipif(not __import__("shutil").which("git"), reason="needs git on PATH")
def test_git_accepts_the_option_on_a_local_clone(tmp_path):
    """The option reaches a real git without breaking a clone (git ignores
    it off Schannel; on Windows it is honoured)."""
    work = tmp_path / "work"
    work.mkdir()
    for args in (["init", "-q", "-b", "main"], ["-c", "user.email=t@example.invalid", "-c", "user.name=t",
                                                 "commit", "-q", "--allow-empty", "-m", "x"]):
        subprocess.run(["git", *args], cwd=str(work), check=True, capture_output=True)
    cache = _sources.SourceCache(tmp_path / "cache", ssl_revoke="false")
    cache._git(["clone", "-q", str(work), str(tmp_path / "copy")], None)
    assert (tmp_path / "copy" / ".git").is_dir()
