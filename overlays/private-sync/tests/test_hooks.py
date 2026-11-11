"""private-sync's session start and sync-back are overlay hooks: declared in overlay.cfg, run by
dotagents with the event's JSON on standard input, acting on the project the harness names.

The scripts are POSIX shell, so the runs need ``sh``. The dotagents leg needs ``dotagents``
importable (it is skipped without it). Both use stand-ins that only record their arguments for
``git`` and for the overlay's ``_link`` library: nothing is cloned, linked or pushed."""
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

#: Stands in for lib/python/_link.py: records what it is asked to do.
LINK_STUB = '''\
import os

def _log(text):
    with open(os.environ["LINK_LOG"], "a") as fh:
        fh.write(text + "\\n")

def link_project(project, agents, **kw):
    _log("link-project %s" % project)

def sync_agents(agents, *, message="", project_dir=None, **kw):
    _log("sync-project %s" % project_dir)
    return 0
'''


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


def test_the_scripts_do_not_run_the_dotagents_command_line():
    # It would load every command and library the project's own .agents holds.
    for name in ("private-sync-start.sh", "private-sync-stop.sh"):
        text = (ROOT / "hooks" / name).read_text(encoding="utf-8")
        assert "dotagents_cmd" not in text and "python -m dotagents" not in text
        assert "run_link.py" in text


def _stub(directory: Path, name: str, log: Path) -> None:
    path = directory / name
    path.write_text('#!/bin/sh\necho "%s $*" >> "%s"\n' % (name, log), encoding="utf-8", newline="")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _agents_repo(tmp_path: Path) -> Path:
    agents = tmp_path / "agents-home"
    (agents / ".git").mkdir(parents=True, exist_ok=True)
    return agents


def _overlay_copy(where: Path) -> Path:
    """The overlay's hooks and library, with the library replaced by the recorder."""
    shutil.copytree(str(ROOT / "hooks"), str(where / "hooks"))
    (where / "lib" / "python").mkdir(parents=True)
    (where / "lib" / "python" / "_link.py").write_text(LINK_STUB, encoding="utf-8")
    return where


def _run_script(tmp_path, script, payload_cwd, **extra):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    _stub(bin_dir, "git", log)
    overlay = _overlay_copy(tmp_path / "overlay") if not (tmp_path / "overlay").exists() else tmp_path / "overlay"
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "AGENTS_PROJECT_ROOT")}
    env.update(PATH=str(bin_dir) + os.pathsep + os.environ["PATH"], AGENTS_HOME=str(_agents_repo(tmp_path)),
               AOS_HOOK_EVENT="session-start", AGENTS_PYTHON=sys.executable, LINK_LOG=str(log), **extra)
    payload = json.dumps({"event": "session-start", "harness": "claude-code", "cwd": str(payload_cwd)})
    done = subprocess.run([SH, str(overlay / "hooks" / script)], input=payload.encode(), env=env,
                          capture_output=True, cwd=str(tmp_path))
    assert done.returncode == 0, done.stderr
    assert done.stdout == b"", "a hook's standard output is its answer"
    return (log.read_text(encoding="utf-8") if log.exists() else ""), done.stderr.decode()


@needs_sh
@pytest.mark.parametrize("script,expect", [
    ("private-sync-start.sh", ["git -C", "link-project"]),
    ("private-sync-stop.sh", ["sync-project"]),
])
def test_the_project_is_the_one_the_harness_names(tmp_path, script, expect):
    project = tmp_path / "my project"
    project.mkdir()
    calls, _ = _run_script(tmp_path, script, tmp_path / "wherever", CLAUDE_PROJECT_DIR=str(project))
    for fragment in expect:
        assert fragment in calls
    assert " %s" % project in calls


@needs_sh
def test_agents_project_root_names_it_too(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    calls, _ = _run_script(tmp_path, "private-sync-stop.sh", tmp_path / "elsewhere", AGENTS_PROJECT_ROOT=str(project))
    assert str(project) in calls


@needs_sh
def test_a_hook_that_is_not_told_the_project_does_nothing_and_never_walks_up_from_the_cwd(tmp_path):
    # The payload's cwd is inside another project that has an .agents: it must not be adopted.
    other = tmp_path / "other-project"
    (other / ".agents").mkdir(parents=True)
    (other / "src").mkdir()
    for script in ("private-sync-start.sh", "private-sync-stop.sh"):
        calls, err = _run_script(tmp_path, script, other / "src")
        assert "link-project" not in calls and "sync-project" not in calls
        assert str(other) not in calls
        assert "did not name the project" in err


@needs_sh
def test_nothing_in_the_project_runs(tmp_path):
    project = tmp_path / "proj"
    marker = tmp_path / "PROJECT-CODE-RAN"
    plant = "open(%r, 'w').write('x')\n" % str(marker)
    (project / ".agents" / "dotagents" / "cmds").mkdir(parents=True)
    (project / ".agents" / "dotagents" / "cmds" / "link_project.py").write_text(plant, encoding="utf-8")
    (project / ".agents" / "lib").mkdir()
    (project / ".agents" / "lib" / "sitecustomize.py").write_text(plant, encoding="utf-8")
    (project / "sitecustomize.py").write_text(plant, encoding="utf-8")
    (project / "_link.py").write_text(plant, encoding="utf-8")
    for script in ("private-sync-start.sh", "private-sync-stop.sh"):
        calls, _ = _run_script(tmp_path, script, project, CLAUDE_PROJECT_DIR=str(project), PYTHONPATH=str(project))
        assert "project" in calls or str(project) in calls
    assert not marker.exists(), "something the project holds ran"


@needs_sh
def test_run_by_hand_the_scripts_use_the_current_directory(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    _stub(bin_dir, "git", log)
    overlay = _overlay_copy(tmp_path / "overlay")
    here = tmp_path / "here"
    here.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ("AOS_HOOK_EVENT", "CLAUDE_PROJECT_DIR", "AGENTS_PROJECT_ROOT")}
    env.update(PATH=str(bin_dir) + os.pathsep + os.environ["PATH"], AGENTS_HOME=str(_agents_repo(tmp_path)),
               AGENTS_PYTHON=sys.executable, LINK_LOG=str(log))
    subprocess.run([SH, str(overlay / "hooks" / "private-sync-stop.sh")], env=env, stdin=subprocess.DEVNULL,
                   capture_output=True, cwd=str(here), check=True)
    assert "sync-project %s" % here in log.read_text(encoding="utf-8")


@needs_sh
def test_dotagents_runs_them_once_allowed_and_not_before(tmp_path, monkeypatch):
    pytest.importorskip("dotagents")
    from dotagents import _hook_dispatch, _overlay_hooks

    store = tmp_path / "store"
    shutil.copytree(str(ROOT), str(store / "overlays" / "private-sync"),
                    ignore=shutil.ignore_patterns("tests", "__pycache__"))
    (store / "overlays" / "private-sync" / "lib" / "python" / "_link.py").write_text(LINK_STUB, encoding="utf-8")
    (store / "AGENTS.md").write_text("<!-- dotagents:begin -->\n# BASE\n<!-- dotagents:end -->\n", encoding="utf-8")
    (store / ".git").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    _stub(bin_dir, "git", log)
    project = tmp_path / "proj"
    (project / ".agents").mkdir(parents=True)
    monkeypatch.setenv("AGENTS_HOME", str(store))
    environ = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"], AGENTS_HOME=str(store),
                   AGENTS_PROJECT_ROOT=str(project), LINK_LOG=str(log))
    scope = _hook_dispatch.scope_for("user", store)
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
    assert "sync-project %s" % project in log.read_text(encoding="utf-8")
