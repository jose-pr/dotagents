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
    (agents / ".git").mkdir(parents=True, exist_ok=True)
    return agents


def _run_script(tmp_path, script, payload_cwd, **extra):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    for name in ("git", "dotagents"):
        _stub(bin_dir, name, log)
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "AGENTS_PROJECT_ROOT")}
    env.update(PATH=str(bin_dir) + os.pathsep + os.environ["PATH"], AGENTS_HOME=str(_agents_repo(tmp_path)),
               AOS_HOOK_EVENT="session-start", AGENTS_PYTHON=sys.executable, **extra)
    payload = json.dumps({"event": "session-start", "harness": "claude-code", "cwd": str(payload_cwd)})
    done = subprocess.run([SH, str(ROOT / "hooks" / script)], input=payload.encode(), env=env,
                          capture_output=True, cwd=str(tmp_path))
    assert done.returncode == 0
    assert done.stdout == b"", "a hook's standard output is its answer"
    return log.read_text(encoding="utf-8") if log.exists() else ""


@needs_sh
@pytest.mark.parametrize("script,expect", [
    ("private-sync-start.sh", ["git -C", "link-project"]),
    ("private-sync-stop.sh", ["sync-project"]),
])
def test_the_project_is_the_directory_holding_agents_above_the_payloads_cwd(tmp_path, script, expect):
    project = tmp_path / "my project"
    (project / ".agents").mkdir(parents=True)
    (project / "src" / "deep").mkdir(parents=True)
    calls = _run_script(tmp_path, script, project / "src" / "deep")
    for fragment in expect:
        assert fragment in calls
    assert " %s" % project in calls and "deep" not in calls


@needs_sh
def test_the_harnesss_own_project_wins_over_the_payload(tmp_path):
    project, elsewhere = tmp_path / "proj", tmp_path / "elsewhere"
    (project / ".agents").mkdir(parents=True)
    elsewhere.mkdir()
    calls = _run_script(tmp_path, "private-sync-stop.sh", elsewhere, CLAUDE_PROJECT_DIR=str(project))
    assert str(project) in calls and "elsewhere" not in calls
    calls = _run_script(tmp_path, "private-sync-stop.sh", elsewhere, AGENTS_PROJECT_ROOT=str(project))
    assert str(project) in calls.splitlines()[-1]


@needs_sh
def test_a_payload_cwd_with_no_project_above_it_is_not_taken_as_the_project(tmp_path):
    nowhere = tmp_path / "nowhere" / "x"
    nowhere.mkdir(parents=True)
    calls = _run_script(tmp_path, "private-sync-stop.sh", nowhere)
    assert str(nowhere) not in calls, "the agent's own working directory is not the project"


@needs_sh
def test_the_payload_is_read_by_python_not_by_a_pattern(tmp_path):
    project = tmp_path / 'a "quoted" \\ proj'
    (project / ".agents").mkdir(parents=True)
    calls = _run_script(tmp_path, "private-sync-stop.sh", project)
    assert str(project) in calls


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
    (project / ".agents").mkdir(parents=True)
    (project / "src").mkdir()
    monkeypatch.setenv("AGENTS_HOME", str(store))
    environ = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"], AGENTS_HOME=str(store))
    scope = Scope("user", store)
    overlay = next(o for o in scope.installed_overlays if o.name == "private-sync")
    native = json.dumps({"session_id": "s", "cwd": str(project / "src"), "source": "startup"}).encode()

    stdout, stderr = _hook_dispatch.dispatch("claude-code", "session-start", native, scope, "user", environ)
    assert not log.exists(), "off until allowed"
    assert "hook-not-allowed private-sync" in stderr

    _overlay_hooks.allow(store, "private-sync", overlay.tree_digest(), overlay.store)
    _hook_dispatch.dispatch("claude-code", "session-start", native, scope, "user", environ)
    assert "link-project %s" % project in log.read_text(encoding="utf-8")

    log.unlink()
    turn = json.dumps({"session_id": "s", "cwd": str(project / "src")}).encode()
    _hook_dispatch.dispatch("claude-code", "turn-end", turn, scope, "user", environ)
    assert "sync-project" in log.read_text(encoding="utf-8")
