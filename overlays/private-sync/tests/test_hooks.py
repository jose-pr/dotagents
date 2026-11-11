"""private-sync's session start and sync-back are overlay hooks: declared in overlay.cfg, run by
dotagents with the event's JSON on standard input, taking the project from its ``cwd``.

The scripts are POSIX shell, so the runs need ``sh``. The dotagents leg needs ``dotagents``
importable (it is skipped without it), and uses stand-ins for ``git`` and ``dotagents`` that only
record their arguments: nothing is cloned and nothing is pushed."""
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SH = shutil.which("sh")
needs_sh = pytest.mark.skipif(SH is None or os.name == "nt", reason="the hook scripts are POSIX shell")


def _manifest() -> dict:
    try:
        import tomllib
    except ImportError:  # pragma: no cover -- Python < 3.11
        tomllib = pytest.importorskip("tomli")
    return tomllib.loads((ROOT / "overlay.cfg").read_text(encoding="utf-8"))


def test_the_hooks_are_declared_and_their_scripts_exist():
    hooks = _manifest()["hooks"]
    assert hooks["session-start"]["run"] == "hooks/private-sync-start.sh"
    assert hooks["turn-end"]["run"] == "hooks/private-sync-stop.sh"
    for entry in hooks.values():
        assert (ROOT / entry["run"]).is_file()
        # Neither is a guard: a failed pull or push is reported and the session goes on.
        assert entry.get("on_failure", "report") == "report"
    assert not (ROOT / "hooks" / "settings.snippet.json").exists()


def _stub(directory: Path, name: str, log: Path) -> None:
    path = directory / name
    path.write_text('#!/bin/sh\necho "%s $*" >> "%s"\n' % (name, log), encoding="utf-8", newline="")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _agents_repo(tmp_path: Path) -> Path:
    agents = tmp_path / "agents-home"
    (agents / ".git").mkdir(parents=True)
    return agents


@needs_sh
@pytest.mark.parametrize("script,expect", [
    ("private-sync-start.sh", ["git -C", "link-project"]),
    ("private-sync-stop.sh", ["sync-project"]),
])
def test_the_scripts_take_the_project_from_the_payloads_cwd(tmp_path, script, expect):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for name in ("git", "dotagents"):
        _stub(bin_dir, name, log)
    project = tmp_path / "my project"
    project.mkdir()
    env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"], AGENTS_HOME=str(_agents_repo(tmp_path)),
               AOS_HOOK_EVENT="session-start", CLAUDE_PROJECT_DIR=str(tmp_path / "elsewhere"))
    payload = json.dumps({"event": "session-start", "harness": "claude-code", "cwd": str(project)})
    done = subprocess.run([SH, str(ROOT / "hooks" / script)], input=payload.encode(), env=env,
                          capture_output=True, cwd=str(ROOT))
    assert done.returncode == 0
    assert done.stdout == b"", "a hook's standard output is its answer"
    calls = log.read_text(encoding="utf-8")
    for fragment in expect:
        assert fragment in calls
    assert str(project) in calls and "elsewhere" not in calls


@needs_sh
def test_run_by_hand_the_scripts_still_use_the_projects_environment(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for name in ("git", "dotagents"):
        _stub(bin_dir, name, log)
    env = {k: v for k, v in os.environ.items() if k != "AOS_HOOK_EVENT"}
    env.update(PATH=str(bin_dir) + os.pathsep + os.environ["PATH"], AGENTS_HOME=str(_agents_repo(tmp_path)),
               CLAUDE_PROJECT_DIR=str(tmp_path / "by-hand"))
    subprocess.run([SH, str(ROOT / "hooks" / "private-sync-stop.sh")], env=env, stdin=subprocess.DEVNULL,
                   capture_output=True, cwd=str(ROOT), check=True)
    assert str(tmp_path / "by-hand") in log.read_text(encoding="utf-8")


@needs_sh
def test_dotagents_runs_them_once_allowed_and_not_before(tmp_path, monkeypatch):
    pytest.importorskip("dotagents")
    from dotagents import _hook_dispatch, _overlay_hooks
    from dotagents._scope import Scope

    store = tmp_path / "store"
    shutil.copytree(str(ROOT), str(store / "overlays" / "private-sync"),
                    ignore=shutil.ignore_patterns("tests", "__pycache__"))
    (store / "AGENTS.md").write_text("<!-- dotagents:begin -->\n# BASE\n<!-- dotagents:end -->\n", encoding="utf-8")
    (store / ".git").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for name in ("git", "dotagents"):
        _stub(bin_dir, name, log)
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.setenv("AGENTS_HOME", str(store))
    environ = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"], AGENTS_HOME=str(store))
    scope = Scope("user", store)
    overlay = next(o for o in scope.installed_overlays if o.name == "private-sync")
    native = json.dumps({"session_id": "s", "cwd": str(project), "source": "startup"}).encode()

    stdout, stderr = _hook_dispatch.dispatch("claude-code", "session-start", native, scope, "user", environ)
    assert not log.exists(), "off until allowed"
    assert "hook-not-allowed private-sync" in stderr

    _overlay_hooks.allow(store, "private-sync", overlay.tree_digest())
    _hook_dispatch.dispatch("claude-code", "session-start", native, scope, "user", environ)
    assert "link-project %s" % project in log.read_text(encoding="utf-8")

    log.unlink()
    turn = json.dumps({"session_id": "s", "cwd": str(project)}).encode()
    _hook_dispatch.dispatch("claude-code", "turn-end", turn, scope, "user", environ)
    assert "sync-project" in log.read_text(encoding="utf-8")
