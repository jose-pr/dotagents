"""Tests for the bundled `launch` command module
(`src/dotagents/_overlay/dotagents/cmds/launch.py`): the env is assembled and
handed to the child, the context reaches the harness the way that harness
takes it, the passthrough lands after dotagents' own flags, and the exit code
is the child's. The spawn is a module seam (`_spawn`), so nothing real runs.

Loaded by file path exactly as discovery does (not an importable package
member), like `test_findings_cmd.py`.
"""

import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

MODULE_PATH = ROOT / "src" / "dotagents" / "_overlay" / "dotagents" / "cmds" / "launch.py"

CONTEXT = "# rules\n\nRead the local AGENTS.md first.\n"


@pytest.fixture(scope="module")
def launch_mod():
    spec = importlib.util.spec_from_file_location("test_launch_module", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.modules.pop(spec.name, None)


@pytest.fixture(autouse=True)
def _isolated_scope(monkeypatch, tmp_path):
    """A fresh store and project under tmp_path; no harness markers from the
    session running the tests; and os.environ restored afterwards, because
    `launch` exports the assembled env into this process on purpose."""
    saved = dict(os.environ)
    for var in (
        "AGENTS_PROJECT_ROOT", "AGENTS_HOME", "AGENTS_HARNESS", "AGENTS_CONTEXT_FILE",
        "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "GEMINI_CLI", "CODEX_SANDBOX",
    ):
        monkeypatch.delenv(var, raising=False)
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("AGENTS_HOME", str(store))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    yield tmp_path
    os.environ.clear()
    os.environ.update(saved)


def _program(tmp_path, name="fake-harness"):
    """An executable file `shutil.which` accepts, in a dir of its own."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if os.name == "nt":
        p = bin_dir / (name + ".cmd")
        p.write_text("@echo off\r\n")
    else:
        p = bin_dir / name
        p.write_text("#!/bin/sh\n")
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


def _capture_spawn(monkeypatch, launch_mod, rc=0):
    calls = []

    def fake_spawn(argv, env):
        calls.append((list(argv), dict(env)))
        return rc

    monkeypatch.setattr(launch_mod, "_spawn", fake_spawn)
    return calls


def _context_is(monkeypatch, text):
    from dotagents import _context

    monkeypatch.setattr(_context, "assemble_context", lambda agent, scope, inline=False: text)


def _run(launch_mod, passthrough=(), **kwargs):
    cmd = launch_mod.Launch()
    cmd._passthrough_ = list(passthrough)
    for k, v in kwargs.items():
        setattr(cmd, k, v)
    return cmd()


# --------------------------------------------------------------------------- #
# Errors first
# --------------------------------------------------------------------------- #


def test_unknown_agent_is_a_usage_error(launch_mod):
    with pytest.raises(SystemExit, match="unknown agent 'nope'"):
        _run(launch_mod, agent="nope")


def test_ide_only_agent_needs_command(launch_mod, monkeypatch):
    _context_is(monkeypatch, "")
    with pytest.raises(SystemExit, match="antigravity has no command-line harness"):
        _run(launch_mod, agent="antigravity")


def test_missing_program_is_an_error_after_the_env_is_applied(launch_mod, monkeypatch):
    _context_is(monkeypatch, "")
    with pytest.raises(SystemExit, match="not found on PATH"):
        _run(launch_mod, agent="claude", command="no-such-harness-xyz")


# --------------------------------------------------------------------------- #
# The happy paths
# --------------------------------------------------------------------------- #


def test_claude_gets_the_context_as_an_appended_prompt_file(launch_mod, monkeypatch, tmp_path):
    program = _program(tmp_path)
    _context_is(monkeypatch, CONTEXT)
    calls = _capture_spawn(monkeypatch, launch_mod, rc=7)

    rc = _run(launch_mod, passthrough=["--model", "sonnet"], agent="claude", command=str(program))

    assert rc == 7, "the exit code is the harness's"
    (argv, env), = calls
    assert Path(argv[0]) == program
    assert argv[1] == "--append-system-prompt-file"
    context_file = Path(argv[2])
    assert context_file.read_text(encoding="utf-8") == CONTEXT
    assert argv[3:] == ["--model", "sonnet"], "the passthrough comes after dotagents' flags"
    # The env the child gets: identity, the scope roots, and where the context is.
    assert env["AGENTS_HARNESS"] == "claude-code"
    assert env["AGENT"] == "claude-code"
    assert env["AGENTS_HOME"] == str(tmp_path / "store")
    assert env["AGENTS_CONTEXT_FILE"] == str(context_file)
    # ... and this process has it too (exported, not only handed over).
    assert os.environ["AGENTS_HARNESS"] == "claude-code"
    assert "AGENTS_CONTEXT_FILE" not in os.environ, "the file is the child's, not a global"


def test_a_harness_without_an_append_flag_gets_its_own_file_written(
    launch_mod, monkeypatch, tmp_path
):
    program = _program(tmp_path)
    _context_is(monkeypatch, CONTEXT)
    calls = _capture_spawn(monkeypatch, launch_mod)

    rc = _run(launch_mod, agent="gemini", command=str(program))

    assert rc == 0
    (argv, env), = calls
    assert argv == [str(program)] or Path(argv[0]) == program and argv[1:] == []
    assert "--append-system-prompt-file" not in argv
    target = tmp_path / "project" / "GEMINI.md"
    text = target.read_text(encoding="utf-8")
    assert "dotagents:context" in text and CONTEXT.strip() in text
    assert env["AGENTS_HARNESS"] == "gemini-cli"
    assert Path(env["AGENTS_CONTEXT_FILE"]).read_text(encoding="utf-8") == CONTEXT


def test_no_context_is_environment_only(launch_mod, monkeypatch, tmp_path):
    program = _program(tmp_path)
    calls = _capture_spawn(monkeypatch, launch_mod)

    def boom(*a, **k):
        raise AssertionError("context must not be assembled under --no-context")

    from dotagents import _context

    monkeypatch.setattr(_context, "assemble_context", boom)

    rc = _run(launch_mod, passthrough=["-p", "hi"], agent="gemini", command=str(program), no_context=True)

    assert rc == 0
    (argv, env), = calls
    assert argv[1:] == ["-p", "hi"]
    assert not (tmp_path / "project" / "GEMINI.md").exists()
    assert "AGENTS_CONTEXT_FILE" not in env
    assert env["AGENTS_HARNESS"] == "gemini-cli"


def test_empty_context_adds_no_flag(launch_mod, monkeypatch, tmp_path):
    program = _program(tmp_path)
    _context_is(monkeypatch, "")
    calls = _capture_spawn(monkeypatch, launch_mod)

    _run(launch_mod, agent="claude", command=str(program))

    (argv, env), = calls
    assert argv[1:] == []
    assert "AGENTS_CONTEXT_FILE" not in env


def test_program_is_resolved_on_the_exported_path(launch_mod, monkeypatch, tmp_path):
    """A harness an overlay's / a scope's bin/ provides is found even when the
    caller's PATH does not have it: the lookup runs AFTER the env is applied."""
    project_bin = tmp_path / "project" / ".agents" / "bin"
    project_bin.mkdir(parents=True)
    program = _program(project_bin.parent, name="scoped-harness")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    _context_is(monkeypatch, "")
    calls = _capture_spawn(monkeypatch, launch_mod)

    _run(launch_mod, agent="claude", command="scoped-harness")

    (argv, env), = calls
    assert Path(argv[0]).resolve() == program.resolve()
    assert str(project_bin) in env["PATH"].split(os.pathsep)


def test_default_agent_is_the_active_one(launch_mod, monkeypatch, tmp_path):
    program = _program(tmp_path)
    monkeypatch.setenv("AGENTS_HARNESS", "gemini-cli")
    calls = _capture_spawn(monkeypatch, launch_mod)

    _run(launch_mod, command=str(program), no_context=True)

    (_argv, env), = calls
    assert env["AGENT"] == "gemini-cli"


def test_dry_run_prints_and_runs_nothing(launch_mod, monkeypatch, tmp_path, capsys):
    _context_is(monkeypatch, CONTEXT)

    def boom(argv, env):
        raise AssertionError("dry run must not spawn")

    monkeypatch.setattr(launch_mod, "_spawn", boom)

    rc = _run(
        launch_mod, passthrough=["--model", "sonnet"], agent="claude",
        command="no-such-harness-xyz", dry_run=True,
    )

    assert rc == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("no-such-harness-xyz --append-system-prompt-file ")
    assert out[0].endswith(" --model sonnet")
    assert out[1].startswith("env: ")
    assert "AGENTS_HARNESS" in out[1] and "AGENTS_PROJECT_ROOT" in out[1]
    assert not (tmp_path / "project" / ".claude" / "CLAUDE.md").exists()


# --------------------------------------------------------------------------- #
# The seams
# --------------------------------------------------------------------------- #


def test_spawn_keeps_waiting_through_a_keyboard_interrupt(launch_mod, monkeypatch):
    class FakeProc:
        def __init__(self):
            self.waits = 0

        def wait(self):
            self.waits += 1
            if self.waits == 1:
                raise KeyboardInterrupt
            return 130

    proc = FakeProc()
    seen = {}

    def fake_popen(argv, env):
        seen["argv"], seen["env"] = argv, env
        return proc

    monkeypatch.setattr(launch_mod.subprocess, "Popen", fake_popen)
    assert launch_mod._spawn(["x", "--y"], {"A": "1"}) == 130
    assert proc.waits == 2
    assert seen == {"argv": ["x", "--y"], "env": {"A": "1"}}


def test_describe_quotes_only_what_needs_it(launch_mod):
    assert launch_mod._describe(["claude", "--model", "a b", ""]) == 'claude --model "a b" ""'
