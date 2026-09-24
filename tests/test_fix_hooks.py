"""Hook fixes from the 2026-09-23 review: the PowerShell handlers' Git Bash
gate, UTF-8 through the PowerShell pipe, the PreToolUse matcher, merging that
keeps the user's keys, symlink-safe and non-ASCII-safe wrappers.

tmp dirs only; the PowerShell tests run the real hook command strings.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


from dotagents import _hooks, _wrappers
from dotagents._agents import ClaudeAgent

WINDOWS = os.name == "nt"
POWERSHELL = shutil.which("powershell") if WINDOWS else None
SYSTEM32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")


def _bash():
    """A real POSIX bash; the WindowsApps `bash.exe` is the WSL launcher stub."""
    found = shutil.which("bash")
    if found and "WindowsApps" not in found:
        return found
    git = shutil.which("git")
    if git:
        candidate = Path(git).resolve().parents[1] / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return None


BASH = _bash()


def _ps(command, cwd, env):
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=str(cwd), env=env, capture_output=True,
    )


def _no_git_env(tmp_path):
    """An environment in which Claude Code would find no Git Bash: no override,
    empty Program Files, and no `git` on PATH."""
    empty = tmp_path / "programfiles"
    empty.mkdir(exist_ok=True)
    ours = {
        "PATH": os.pathsep.join([SYSTEM32, os.path.join(SYSTEM32, "WindowsPowerShell", "v1.0")]),
        "ProgramFiles": str(empty),
        "ProgramFiles(x86)": str(empty),
        "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"),
    }
    # os.environ's keys are upper-cased on Windows: drop them case-blind, or
    # the block carries PROGRAMFILES and ProgramFiles and either may win.
    drop = {k.upper() for k in ours} | {"CLAUDE_CODE_GIT_BASH_PATH", "AGENTS_HOME"}
    env = {k: v for k, v in os.environ.items() if k.upper() not in drop}
    env.update(ours)
    return env


def _project_with_stub(tmp_path):
    """A project whose `.agents\\bin\\dotagents.cmd` prints non-ASCII UTF-8."""
    project = tmp_path / "proj"
    bin_dir = project / ".agents" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "dotagents.cmd").write_bytes(
        ('@"%s" -c "import sys; sys.stdout.buffer.write(\'CTX-\\u00e9\\u2713\\n\'.encode())"\r\n'
         % sys.executable).encode("ascii")
    )
    return project


@pytest.mark.skipif(POWERSHELL is None, reason="Windows PowerShell only")
class TestPowerShellHandlers:
    @pytest.mark.parametrize("name", [
        "SESSION_START_COMMAND_POWERSHELL",
        "CWD_CHANGED_COMMAND_POWERSHELL",
        "PRETOOLUSE_POWERSHELL_COMMAND",
    ])
    def test_every_handler_parses(self, name, tmp_path):
        script = tmp_path / "cmd.txt"
        script.write_text(getattr(ClaudeAgent, name), encoding="utf-8")
        check = (
            "$e = $null; [void][System.Management.Automation.Language.Parser]::ParseInput("
            "[IO.File]::ReadAllText('%s'), [ref]$null, [ref]$e); $e.Count" % script
        )
        proc = _ps(check, tmp_path, dict(os.environ))
        assert proc.stdout.decode().strip() == "0", proc.stdout + proc.stderr

    @pytest.mark.skipif(
        any(Path(os.environ.get(v, "-"), "Git", "bin", "bash.exe").is_file()
            for v in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)")),
        reason="Windows resets ProgramFiles per process, so an installed Git Bash cannot be hidden",
    )
    def test_runs_when_there_is_no_git_bash(self, tmp_path):
        """hooks-06: with no Git Bash the PowerShell handler is the one that works."""
        project = _project_with_stub(tmp_path)
        proc = _ps(ClaudeAgent.SESSION_START_COMMAND_POWERSHELL, project, _no_git_env(tmp_path))
        assert proc.returncode == 0, proc.stderr
        assert b"CTX-" in proc.stdout

    def test_the_env_loader_reads_utf8(self, tmp_path):
        """env-09: the loader pipes `env` into Invoke-Expression, and PowerShell
        decoded that captured output in the OEM code page: "\u00e9\u2713"
        arrived as "\u251c\u2310\u0393\u00a3\u00f4". Runs the hook, then the
        command it rewrote, against a store whose `dotagents` prints UTF-8."""
        store = tmp_path / "store"
        (store / "bin").mkdir(parents=True)
        (store / "bin" / "dotagents.cmd").write_bytes((
            '@"%s" -c "import sys; sys.stdout.buffer.write('
            '\'$env:X = \\\'CTX-\\u00e9\\u2713\\\'\\n\'.encode())"\r\n' % sys.executable
        ).encode("ascii"))
        out = tmp_path / "out.txt"
        env = {k: v for k, v in os.environ.items() if k.upper() not in ("AGENTS_RUNTIME_SET", "AGENTS_HOME")}
        env["AGENTS_HOME"] = str(store)
        call = {"tool_name": "PowerShell",
                "tool_input": {"command": "[IO.File]::WriteAllText('%s', $env:X)" % out}}
        hook = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", ClaudeAgent.PRETOOLUSE_POWERSHELL_COMMAND],
            input=json.dumps(call).encode(), env=env, capture_output=True,
        )
        rewritten = json.loads(hook.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
        run = _ps(rewritten, tmp_path, env)
        assert run.returncode == 0, run.stderr
        assert out.read_text(encoding="utf-8") == "CTX-\u00e9\u2713"

    def test_steps_aside_when_claude_would_use_git_bash(self, tmp_path):
        """Claude Code runs hooks in Git Bash when CLAUDE_CODE_GIT_BASH_PATH
        names one -- bash is not on PATH there, which is what the old
        `Get-Command bash` gate looked for, so both handlers ran."""
        project = _project_with_stub(tmp_path)
        fake_bash = tmp_path / "bash.exe"
        fake_bash.write_bytes(b"")
        env = _no_git_env(tmp_path)
        env["CLAUDE_CODE_GIT_BASH_PATH"] = str(fake_bash)
        proc = _ps(ClaudeAgent.SESSION_START_COMMAND_POWERSHELL, project, env)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == b""

    def test_steps_aside_for_git_on_path_with_bash_beside_it(self, tmp_path):
        """A default Git install puts only `Git\\cmd` on PATH."""
        project = _project_with_stub(tmp_path)
        git = tmp_path / "Git"
        (git / "cmd").mkdir(parents=True)
        (git / "bin").mkdir()
        shutil.copy(os.path.join(SYSTEM32, "where.exe"), git / "cmd" / "git.exe")
        (git / "bin" / "bash.exe").write_bytes(b"")
        env = _no_git_env(tmp_path)
        env["PATH"] = str(git / "cmd") + os.pathsep + env["PATH"]
        proc = _ps(ClaudeAgent.SESSION_START_COMMAND_POWERSHELL, project, env)
        assert proc.stdout == b""


def test_the_powershell_env_loader_is_matched_to_powershell_calls(tmp_path, monkeypatch):
    """hooks-07: with no matcher the loader spawned PowerShell (hundreds of
    ms) before every Read, Grep and Edit."""
    monkeypatch.setattr(ClaudeAgent, "_is_windows", staticmethod(lambda: True))
    agent = ClaudeAgent()
    agent.powershell_env_hook = True
    root = tmp_path / "claude"
    store = tmp_path / "store"
    store.mkdir()
    agent.wire_hooks(store, dry_run=False, logger=None, config_root=root)
    entries = json.loads((root / "settings.local.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert [e.get("matcher") for e in entries] == ["PowerShell"]


def test_remove_hook_finds_both_label_spellings():
    """hooks-11: entries written before the namespaced label are still ours."""
    old = {"hooks": [{"type": "command", "command": "dotagents context", "statusMessage": "Loading"}]}
    new = {"hooks": [{"type": "command", "command": "x", "statusMessage": "dotagents: Loading"}]}
    foreign = {"hooks": [{"type": "command", "command": "y", "statusMessage": "Theirs"}]}
    kept, changed = _hooks.remove_hook([old, new, foreign], status_message="Loading")
    assert changed and kept == [foreign]


@pytest.mark.skipif(BASH is None, reason="needs a real bash")
def test_a_wrapper_run_through_a_symlink_finds_its_pyz(tmp_path):
    """hooks-13: `$(dirname "$0")` was the symlink's directory, so a wrapper
    linked into ~/.local/bin looked for the pyz there."""
    store = tmp_path / "store"
    store.mkdir()
    pyz = store / "dotagents.pyz"
    pyz.write_text('print("PYZ-OK")\n', encoding="utf-8")
    _wrappers.write_wrappers(store / "bin", pyz, relative=True)
    link_dir = tmp_path / "local-bin"
    link_dir.mkdir()
    try:
        os.symlink(store / "bin" / "dotagents", link_dir / "dotagents")
    except OSError:
        pytest.skip("symlinks need privileges here")
    proc = subprocess.run(
        [BASH, (link_dir / "dotagents").as_posix()],
        capture_output=True, text=True, env={**os.environ, "MSYS": "winsymlinks:nativestrict"},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "PYZ-OK"


@pytest.mark.skipif(not WINDOWS, reason="cmd.exe only")
@pytest.mark.parametrize("short_names", [True, False], ids=["8.3", "codepage"])
def test_a_cmd_wrapper_runs_an_interpreter_under_a_non_ascii_path(tmp_path, monkeypatch, short_names):
    """hooks-12: cmd reads a .cmd in the OEM code page, so a UTF-8 path to
    "C:\\Users\\Jos\u00e9\\..." was "cannot find the path". Either the 8.3
    name or, where there is none, code page 65001 around the call."""
    if not short_names:
        monkeypatch.setattr(_wrappers, "_cmd_safe", lambda p: p)
    # A venv launcher runs from anywhere that has a pyvenv.cfg one level up.
    venv = Path(sys.prefix)
    home = tmp_path / "Jos\u00e9 \u00e7a"
    (home / "Scripts").mkdir(parents=True)
    shutil.copy(sys.executable, home / "Scripts" / Path(sys.executable).name)
    shutil.copy(venv / "pyvenv.cfg", home / "pyvenv.cfg")
    python = str(home / "Scripts" / Path(sys.executable).name)
    pyz = tmp_path / "dotagents.pyz"
    pyz.write_text('import sys; print("RAN", sys.argv[1:])\n', encoding="utf-8")
    _wrappers.write_wrappers(tmp_path / "bin", pyz, python=python)
    cmd = tmp_path / "bin" / "dotagents.cmd"
    proc = subprocess.run(["cmd.exe", "/d", "/c", str(cmd), "a"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "RAN ['a']" in proc.stdout


def test_an_ascii_cmd_wrapper_is_unchanged():
    """The code-page dance is only for a line that needs it."""
    assert _wrappers._cmd_safe("C:\\Python\\python.exe") == "C:\\Python\\python.exe"
