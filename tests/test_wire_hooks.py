"""`wire_hooks`: the skills link + per-agent hook-config merge.

Claude gets both halves (env via `$CLAUDE_ENV_FILE` + context via stdout) plus the
skills link; Codex gets the context half into `hooks.json` (no env-file equivalent
exists); every other adapter stays a no-op.

Tests pass an explicit `config_root` under tmp_path. tests/conftest.py also
points the home at a tmp dir for every test, so a call that omits it still
lands in tmp -- `test_default_config_root_follows_the_isolated_home` pins that
-- and its session guard fails the run if the real `~/.claude` or `~/.codex`
changed.
"""

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from _shell import BASH
from dotagents._fs import write_text_lf

from dotagents._agents import AntigravityAgent, ClaudeAgent, CodexAgent


def _toml_load(text):
    """Parse TOML, skipping the test where no parser is available.

    `tomllib` is stdlib only on 3.11+; this package's floor is 3.9, so on older
    interpreters the parse-back assertions skip rather than fail.
    """
    try:
        import tomllib
    except ImportError:  # pragma: no cover -- 3.9/3.10 without tomli
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            pytest.skip("no TOML parser available (needs Python 3.11+ or tomli)")
    return tomllib.loads(text)


def _scope_with_skills(tmp_path):
    """A `<scope>/.agents` dir carrying one publishable skill."""
    dest = tmp_path / "agents"
    skill = dest / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo-skill\n---\nbody\n", encoding="utf-8")
    return dest


def _settings(root):
    """The settings file `wire_hooks` writes under `root`.

    An explicit `config_root` is treated as project scope, so it is the gitignored
    `settings.local.json`; only the real `~/.claude` gets `settings.json`.
    """
    return Path(root) / "settings.local.json"


def _hooks_of(settings_path):
    return json.loads(Path(settings_path).read_text(encoding="utf-8"))["hooks"]


def _hooks_of_settings(root):
    """The `hooks` object from the settings file under `root`."""
    return _hooks_of(_settings(root))


def _commands(hook_list):
    return [h["command"] for entry in hook_list for h in entry["hooks"]]


def test_wires_both_hooks(tmp_path):
    dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
    ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

    hooks = _hooks_of_settings(root)
    assert ClaudeAgent.SESSION_START_COMMAND in _commands(hooks["SessionStart"])
    assert ClaudeAgent.CWD_CHANGED_COMMAND in _commands(hooks["CwdChanged"])


def test_session_start_persists_env_via_claude_env_file(tmp_path):
    """CLAUDE_ENV_FILE is real and documented; Claude sources it before each Bash
    command, so this is how `dotagents env` reaches the session."""
    dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
    ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

    cmd = _commands(_hooks_of_settings(root)["SessionStart"])[0]
    assert '"$d" env --diff --format export' in cmd
    assert '"$d" context' in cmd


def test_hook_finds_dotagents_by_path_not_by_splicing_path():
    """The hook must find `dotagents` without a global install.

    `init` populates `<scope>/bin/`; the hook runs the project's wrapper, else
    the store's, else whatever `dotagents` is on PATH. It invokes it by path:
    splicing both bins onto PATH made `env --diff` see them as a change and
    persist them into the session, so a later `cd` kept the first project's
    bin ahead of everything.
    """
    project = '"$PWD/.agents/bin/dotagents"'
    store = '"${AGENTS_HOME:-$HOME/.agents}/bin/dotagents"'
    for cmd in (ClaudeAgent.SESSION_START_COMMAND,):
        assert project in cmd and store in cmd
        assert cmd.index(project) < cmd.index(store), "project scope wins over the user store"
        assert "d=dotagents;" in cmd, "falls back to a dotagents on PATH"
        assert "PATH=" not in cmd


def test_env_hook_appends_and_is_guarded():
    """Two documented requirements, both load-bearing.

    `>` would discard variables other hooks wrote to the same file; an unguarded
    redirect with CLAUDE_ENV_FILE unset would create a file named "".
    """
    cmd = ClaudeAgent.SESSION_START_COMMAND
    assert '>> "$CLAUDE_ENV_FILE"' in cmd, "must append, never truncate"
    assert ">>" in cmd and not _has_truncating_redirect(cmd)
    assert '[ -n "$CLAUDE_ENV_FILE" ]' in cmd, "must guard against an unset var"
    assert cmd.index('"$d" env') < cmd.index('"$d" context'), (
        "env must be written before context runs"
    )


def _has_truncating_redirect(cmd: str) -> bool:
    """True if `cmd` contains a single-`>` redirect (as opposed to `>>`)."""
    return any(
        ch == ">" and cmd[i - 1] != ">" and (i + 1 >= len(cmd) or cmd[i + 1] != ">")
        for i, ch in enumerate(cmd)
    )


def test_second_run_is_a_noop(tmp_path):
    """`init` is re-run often -- it must not accumulate hooks or rewrite the file."""
    dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
    agent = ClaudeAgent()
    agent.wire_hooks(dest, dry_run=False, logger=None, config_root=root)
    first = _settings(root).read_text(encoding="utf-8")

    agent.wire_hooks(dest, dry_run=False, logger=None, config_root=root)
    assert _settings(root).read_text(encoding="utf-8") == first

    hooks = _hooks_of_settings(root)
    # No duplicate entries either way (the per-platform count is pinned in
    # TestDualShellSessionHooks).
    for event in ("SessionStart", "CwdChanged"):
        entries = [json.dumps(e, sort_keys=True) for e in hooks[event]]
        assert len(entries) == len(set(entries)), event


def test_preserves_unrelated_keys_and_foreign_hooks(tmp_path):
    dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
    root.mkdir()
    foreign = {"hooks": [{"type": "command", "command": "echo mine"}]}
    _settings(root).write_text(
        json.dumps({"model": "opus", "hooks": {"SessionStart": [foreign]}}), encoding="utf-8"
    )

    ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

    data = json.loads(_settings(root).read_text(encoding="utf-8"))
    assert data["model"] == "opus", "unrelated settings must survive"
    cmds = _commands(data["hooks"]["SessionStart"])
    assert "echo mine" in cmds, "a hook we did not write must survive"
    assert ClaudeAgent.SESSION_START_COMMAND in cmds


def test_dry_run_writes_nothing(tmp_path):
    dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
    ClaudeAgent().wire_hooks(dest, dry_run=True, logger=None, config_root=root)
    assert not _settings(root).exists()
    assert not (root / "skills").exists()


def test_skills_are_linked_or_copied(tmp_path):
    """symlink where the OS allows, copy otherwise -- either way, reachable."""
    dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
    ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
    assert (root / "skills" / "demo-skill" / "SKILL.md").is_file()


def test_skills_are_linked_per_skill_into_an_existing_skills_dir(tmp_path):
    """A user who already has `~/.claude/skills` (everyone with their own
    skills) got "skills not linked: conflict" on every init, because the WHOLE
    directory was linked and never forced. Per-skill: theirs stay, ours land."""
    dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
    mine = root / "skills" / "my-own"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("mine\n", encoding="utf-8")
    ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
    assert (root / "skills" / "demo-skill" / "SKILL.md").is_file()
    assert (mine / "SKILL.md").read_text(encoding="utf-8") == "mine\n"


def test_absent_skills_dir_is_tolerated(tmp_path):
    """True of every overlay today: none ships skills yet."""
    dest, root = tmp_path / "agents", tmp_path / "claude"
    dest.mkdir()
    ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
    assert not (root / "skills").exists()
    assert _settings(root).is_file(), "hooks still wired without skills"


class TestDualShellSessionHooks:
    """hooks.md's `shell` field docs: "Defaults to bash, or to powershell on
    Windows when Git Bash isn't installed." SESSION_START_COMMAND/
    CWD_CHANGED_COMMAND are bash syntax with no `shell` set -- on a Windows
    machine without Git Bash, Claude Code runs them THROUGH POWERSHELL by
    default, which is a hard parse error (verified: `if [ -n ... ]; then` fed
    to `powershell -Command` raises "Missing '(' after 'if'"). So a second,
    PowerShell-native handler is registered on each event; every handler in a
    matched group fires unconditionally (hooks.md), so exactly one of the two
    succeeds per machine depending on which interpreter is present."""

    @pytest.mark.parametrize("windows, per_event", [(True, 2), (False, 1)])
    def test_handlers_per_platform(self, tmp_path, monkeypatch, windows, per_event):
        """Windows: bash + PowerShell handler per event and the PreToolUse
        loader. POSIX: the bash handler only -- Claude Code there runs a
        `shell: powershell` entry THROUGH BASH (a syntax error every session,
        measured in WSL). Both branches run on every OS via the `_is_windows`
        seam (`os.name` itself cannot be patched: `pathlib.Path()` dispatches
        on it)."""
        monkeypatch.setattr(ClaudeAgent, "_is_windows", staticmethod(lambda: windows))
        dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
        ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

        hooks = _hooks_of_settings(root)
        for event in ("SessionStart", "CwdChanged"):
            assert len(hooks[event]) == per_event, event
            shells = {e["hooks"][0].get("shell") for e in hooks[event]}
            assert shells == ({None, "powershell"} if windows else {None}), event
        assert "PreToolUse" not in hooks  # the PowerShell env loader is opt-in

    def test_posix_retracts_powershell_entries_written_earlier(self, tmp_path, monkeypatch):
        """The upgrade path: an install written while both variants were
        registered everywhere keeps failing every session until the stale
        `shell: powershell` entries are REMOVED, not merely no longer written.
        Foreign hooks sharing the event stay."""
        dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
        monkeypatch.setattr(ClaudeAgent, "_is_windows", staticmethod(lambda: True))
        ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        data = json.loads(_settings(root).read_text(encoding="utf-8"))
        data["hooks"]["SessionStart"].append({"hooks": [{"type": "command", "command": "echo mine"}]})
        write_text_lf(_settings(root), json.dumps(data, indent=2) + "\n")

        monkeypatch.setattr(ClaudeAgent, "_is_windows", staticmethod(lambda: False))
        ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        hooks = _hooks_of_settings(root)
        for event in ("SessionStart", "CwdChanged"):
            assert not [e for e in hooks[event] if e["hooks"][0].get("shell") == "powershell"], event
        assert "PreToolUse" not in hooks
        assert "echo mine" in _commands(hooks["SessionStart"]), "a foreign hook survives the retraction"
        assert len(hooks["SessionStart"]) == 2 and len(hooks["CwdChanged"]) == 1
        # ...and the second POSIX run is a no-op.
        before = _settings(root).read_text(encoding="utf-8")
        ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        assert _settings(root).read_text(encoding="utf-8") == before

    def test_the_gate_is_the_real_os_name(self):
        """The seam's default must stay `os.name` -- "is pwsh installed" would
        reintroduce the POSIX failure, since it is Claude Code's handling of
        the `shell` field, not the presence of a binary, that differs."""
        assert ClaudeAgent._is_windows() is (os.name == "nt")

    def test_powershell_variants_are_context_only_not_env(self):
        """The PowerShell SessionStart variant must NOT try to write
        $CLAUDE_ENV_FILE -- that variable's documented effect is "subsequent
        BASH commands" regardless of which shell wrote it, so a write from here
        would feed nothing. The env gap for PowerShell tool calls is covered
        separately by PRETOOLUSE_POWERSHELL_COMMAND."""
        cmd = ClaudeAgent.SESSION_START_COMMAND_POWERSHELL
        assert "CLAUDE_ENV_FILE" not in cmd
        assert "dotagents.cmd" in cmd
        assert cmd.strip().endswith("& $d context } }")

    def test_powershell_variants_only_run_when_bash_is_absent(self, tmp_path):
        """Both handlers fire on every session (hooks.md); on a Windows box that
        has BOTH Git Bash and PowerShell, both succeeded and the same context
        was injected twice (two identical 100 KB payloads, measured). The
        PowerShell variant selects itself: no Git Bash, or it does nothing.
        "No Git Bash" is decided the way Claude decides which shell runs hooks
        (CLAUDE_CODE_GIT_BASH_PATH, the Git install dirs, git's own tree) --
        not `Get-Command bash`, which finds the WSL launcher stub."""
        for cmd in (
            ClaudeAgent.SESSION_START_COMMAND_POWERSHELL,
            ClaudeAgent.CWD_CHANGED_COMMAND_POWERSHELL,
        ):
            assert cmd.startswith(ClaudeAgent._PS_NO_BASH)
            assert "Get-Command bash" not in cmd
            assert "CLAUDE_CODE_GIT_BASH_PATH" in cmd
            assert cmd.rstrip().endswith("}")

    def test_cwd_changed_repins_the_project_root(self):
        """The SessionStart pin is only-if-unset; a `cd` into another project
        must re-pin AGENTS_PROJECT_ROOT or every later command keeps the first
        project's root."""
        cmd = ClaudeAgent.CWD_CHANGED_COMMAND
        assert "export AGENTS_PROJECT_ROOT=" in cmd
        assert '>> "$CLAUDE_ENV_FILE"' in cmd
        assert "[ -d .agents ]" in cmd, "only a directory that IS a project re-pins"
        assert "pwd -W" in cmd, "Git Bash needs the Windows-native form for a Windows Python"

    @pytest.mark.skipif(BASH is None, reason="needs a working bash")
    def test_cwd_changed_command_runs_and_pins(self, tmp_path):
        import subprocess

        (tmp_path / ".agents").mkdir()
        (tmp_path / "AGENTS.md").write_text("ROOT-AGENTS\n", encoding="utf-8")
        env_file = tmp_path / "env.sh"
        env_file.write_text("", encoding="utf-8")
        proc = subprocess.run(
            [BASH, "-c", ClaudeAgent.CWD_CHANGED_COMMAND], cwd=str(tmp_path),
            env={**os.environ, "CLAUDE_ENV_FILE": str(env_file)}, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "ROOT-AGENTS" in proc.stdout
        pinned = env_file.read_text(encoding="utf-8")
        assert pinned.startswith("export AGENTS_PROJECT_ROOT=")  # %q-quoted
        assert tmp_path.name in pinned

    def test_idempotent_no_duplication_across_shell_variants(self, tmp_path):
        dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
        agent = ClaudeAgent()
        agent.wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        first = _settings(root).read_text(encoding="utf-8")

        agent.wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        assert _settings(root).read_text(encoding="utf-8") == first


class TestPowerShellPreToolUse:
    """The $CLAUDE_ENV_FILE mechanism is Bash-tool-only (every hooks.md mention
    says "subsequent Bash commands"; verified live that $env:CLAUDE_ENV_FILE is
    empty inside a PowerShell tool call). This closes that gap independently via
    a PreToolUse hook that injects a guarded env-loader into PowerShell tool
    calls specifically, using `updatedInput` rather than trying to persist state
    across the hook's own (separately-spawned, non-persistent) process.

    Deliberately an INLINE `-Command`, never a `.ps1` file: a script file is
    subject to PowerShell's execution policy (RemoteSigned/AllSigned/Restricted)
    and dotagents has no code-signing certificate. Verified directly that the
    inline form runs successfully even under `Set-ExecutionPolicy -Scope
    Process Restricted`, which blocks every `.ps1` file outright.
    """

    @pytest.fixture(autouse=True)
    def _as_windows(self, monkeypatch):
        """PreToolUse wiring is Windows-only; exercise it on every OS through
        the `_is_windows` seam (the real gate stays `os.name`, see
        TestDualShellSessionHooks.test_the_gate_is_the_real_os_name)."""
        monkeypatch.setattr(ClaudeAgent, "_is_windows", staticmethod(lambda: True))
        monkeypatch.setattr(ClaudeAgent, "powershell_env_hook", True)  # opt-in

    def test_wires_pretooluse_inline_no_file(self, tmp_path):
        dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"

        ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

        hooks = _hooks_of_settings(root)
        assert "PreToolUse" in hooks
        assert len(hooks["PreToolUse"]) == 1
        entry = hooks["PreToolUse"][0]["hooks"][0]
        assert entry["shell"] == "powershell"
        assert entry["command"] == ClaudeAgent.PRETOOLUSE_POWERSHELL_COMMAND
        assert "-File" not in entry["command"], "must be inline, not a script file reference"
        assert ".ps1" not in entry["command"]

    def test_idempotent(self, tmp_path):
        dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
        agent = ClaudeAgent()

        agent.wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        first = _settings(root).read_text(encoding="utf-8")

        agent.wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        assert _settings(root).read_text(encoding="utf-8") == first
        hooks = _hooks_of_settings(root)
        assert len(hooks["PreToolUse"]) == 1, "must not accumulate duplicate entries"

    def test_dry_run_writes_nothing(self, tmp_path):
        dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
        ClaudeAgent().wire_hooks(dest, dry_run=True, logger=None, config_root=root)
        assert not _settings(root).exists()

    def test_command_has_no_backspace_corruption(self):
        """A bare `\\b` inside a normal Python string literal silently becomes a
        backspace character (\\x08), not the two characters `\\`+`b` -- would
        corrupt the emitted `\\.agents\\bin\\...` path. Caught once already by
        testing a draft of this exact command through a real PowerShell spawn;
        pinned here so a future edit that reintroduces a non-raw string literal
        containing `\\b` fails fast instead of silently shipping broken."""
        cmd = ClaudeAgent.PRETOOLUSE_POWERSHELL_COMMAND
        assert chr(8) not in cmd, "backspace character found -- a \\b literal was not raw-stringed"
        assert '\\.agents"' in cmd and "\\bin\\dotagents.cmd" in cmd

    def test_loader_uses_the_diff_and_the_configurable_store(self):
        """Each PowerShell tool call is a fresh process, so the loader runs on
        every call -- the change set, not a re-assignment of the whole env."""
        cmd = ClaudeAgent.PRETOOLUSE_POWERSHELL_COMMAND
        assert "env --diff --format powershell" in cmd
        assert "$env:AGENTS_HOME" in cmd

    def test_command_is_valid_powershell_syntax(self):
        """Parses the exact production string with PowerShell's own tokenizer --
        catches a syntax error without needing a live hook invocation.

        Skips (never fails) when `powershell` isn't on this host at all --
        confirmed on Linux CI runners, `subprocess.run` raises `FileNotFoundError`
        for a missing executable rather than returning a non-zero exit code, so
        that must be caught explicitly, not inferred from `proc.returncode`."""
        import shutil
        import subprocess

        import pytest

        if shutil.which("powershell") is None:
            pytest.skip("no PowerShell available on this host")

        proc = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "$e=$null; [System.Management.Automation.PSParser]::Tokenize("
                "[Console]::In.ReadToEnd(), [ref]$e) | Out-Null; "
                "if ($e.Count -eq 0) { 'OK' } else { $e | ForEach-Object { $_.Message } }",
            ],
            input=ClaudeAgent.PRETOOLUSE_POWERSHELL_COMMAND,
            capture_output=True, text=True,
        )
        assert proc.stdout.strip() == "OK", proc.stdout + proc.stderr


class TestCodexHooks:
    """Codex's hook JSON is structurally identical to Claude's, so `_hooks`
    merges it unchanged. `SessionStart` is context-only, matching Claude's
    Codex-side gap -- no CLAUDE_ENV_FILE equivalent exists there. The LIVE env
    half is covered separately, below, by a `PreToolUse` hook using the same
    `updatedInput.command` rewrite mechanism Codex's own docs confirm exists
    (learn.chatgpt.com/docs/hooks, "To rewrite a supported tool call without
    blocking") -- structurally the same JSON shape as Claude's PowerShell hook."""

    def test_wires_session_start_as_a_script_under_the_absolute_interpreter(self, tmp_path):
        """A shell line broke on native Windows (cmd.exe read `PATH="..."` as its
        PATH builtin and ran nothing); a bare `python3` may not exist there."""
        dest, root = tmp_path / "agents", tmp_path / "codex"
        dest.mkdir()
        CodexAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

        data = json.loads((root / "hooks.json").read_text(encoding="utf-8"))
        hook = data["hooks"]["SessionStart"][0]["hooks"][0]
        command, command_windows = CodexAgent.hook_commands(root, CodexAgent.SESSION_START_HOOK_SCRIPT)
        assert hook["command"] == command and hook["commandWindows"] == command_windows
        assert Path(sys.executable).as_posix() in command
        assert (root / "hooks" / CodexAgent.SESSION_START_HOOK_SCRIPT).is_file()

    def test_session_start_script_runs_context_for_codex(self, tmp_path, monkeypatch):
        """End to end, with a stub `dotagents` on the project bin: the script
        prints what `dotagents context --agents codex` printed. Without the
        flag `context` resolved Claude and gave Codex nothing."""
        import subprocess

        dest, root = tmp_path / "agents", tmp_path / "codex"
        dest.mkdir()
        CodexAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        project = tmp_path / "proj"
        stub_bin = project / ".agents" / "bin"
        stub_bin.mkdir(parents=True)
        if os.name == "nt":
            (stub_bin / "dotagents.cmd").write_text("@echo off\r\necho STUB %*\r\n", encoding="utf-8")
        else:
            (stub_bin / "dotagents").write_text('#!/bin/sh\necho STUB "$@"\n', encoding="utf-8")
            (stub_bin / "dotagents").chmod(0o755)
        proc = subprocess.run(
            [sys.executable, str(root / "hooks" / CodexAgent.SESSION_START_HOOK_SCRIPT)],
            cwd=str(project), capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "STUB context --agents codex" in proc.stdout

    def test_session_start_has_no_env_write(self, tmp_path):
        """SessionStart itself still carries no CLAUDE_ENV_FILE-style write --
        that mechanism does not exist for Codex at any hook event. The env half
        is PreToolUse's job, checked in TestCodexPreToolUse below."""
        from dotagents.cli._common import BASE_ROOT

        script = Path(BASE_ROOT) / "dotagents" / "hooks" / CodexAgent.SESSION_START_HOOK_SCRIPT
        assert "ENV_FILE" not in script.read_text(encoding="utf-8").split('"""', 2)[2]

    def test_targets_hooks_json_not_config_toml(self, tmp_path):
        """Never rewrite the user's main TOML config."""
        dest, root = tmp_path / "agents", tmp_path / "codex"
        dest.mkdir()
        root.mkdir()
        toml = root / "config.toml"
        toml.write_text('model = "gpt-5"\n', encoding="utf-8")

        CodexAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

        assert toml.read_text(encoding="utf-8") == 'model = "gpt-5"\n'
        assert (root / "hooks.json").is_file()

    def test_idempotent(self, tmp_path):
        dest, root = tmp_path / "agents", tmp_path / "codex"
        dest.mkdir()
        agent = CodexAgent()
        agent.wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        first = (root / "hooks.json").read_text(encoding="utf-8")
        agent.wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        assert (root / "hooks.json").read_text(encoding="utf-8") == first

    def test_dry_run_writes_nothing(self, tmp_path):
        dest, root = tmp_path / "agents", tmp_path / "codex"
        dest.mkdir()
        CodexAgent().wire_hooks(dest, dry_run=True, logger=None, config_root=root)
        assert not (root / "hooks.json").exists()
        assert not (root / "hooks" / CodexAgent.PRETOOLUSE_HOOK_SCRIPT).exists()


class TestCodexPreToolUse:
    """Codex has NO env-persistence mechanism at any hook event (unlike Claude,
    which at least has $CLAUDE_ENV_FILE for the Bash tool). Closed via
    PreToolUse's documented `updatedInput.command` rewrite, matched on
    `matcher: "Bash"` (Codex's only shell tool -- no separate PowerShell/cmd
    tool, so unlike Claude's no-matcher hook this one can filter at the
    settings level instead of checking tool_name at runtime)."""

    @pytest.fixture(autouse=True)
    def _posix_shell(self, monkeypatch):
        # The script passes commands through untouched on a Windows shell;
        # these tests exercise the POSIX rewrite on every host.
        monkeypatch.setenv("DOTAGENTS_HOOK_SHELL", "posix")

    def test_windows_shell_passes_commands_through(self, tmp_path, monkeypatch):
        """Codex runs its shell tool through PowerShell on native Windows, which
        cannot parse the POSIX prefix: every command in the session failed."""
        import subprocess

        dest, root = tmp_path / "agents", tmp_path / "codex"
        dest.mkdir()
        CodexAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        monkeypatch.setenv("DOTAGENTS_HOOK_SHELL", "windows")
        proc = subprocess.run(
            [sys.executable, str(root / "hooks" / CodexAgent.PRETOOLUSE_HOOK_SCRIPT)],
            input='{"tool_name":"Bash","tool_input":{"command":"Get-ChildItem"}}',
            capture_output=True, text=True,
        )
        assert proc.returncode == 0 and proc.stdout == ""

    def test_deploys_script_and_wires_pretooluse(self, tmp_path):
        dest, root = tmp_path / "agents", tmp_path / "codex"
        dest.mkdir()

        CodexAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

        script = root / "hooks" / CodexAgent.PRETOOLUSE_HOOK_SCRIPT
        assert script.is_file()
        from dotagents.cli._common import BASE_ROOT
        package_script = Path(BASE_ROOT) / "dotagents" / "hooks" / CodexAgent.PRETOOLUSE_HOOK_SCRIPT
        assert script.read_bytes() == package_script.read_bytes()

        data = json.loads((root / "hooks.json").read_text(encoding="utf-8"))
        entries = data["hooks"]["PreToolUse"]
        assert len(entries) == 1
        assert entries[0]["matcher"] == "Bash"
        hook = entries[0]["hooks"][0]
        assert str(script) in hook["command"] or script.as_posix() in hook["command"]
        # The absolute interpreter that ran init, in both forms: on Windows a
        # bare `python3` is commonly the Microsoft Store alias stub (exit 49,
        # "Python was not found"), and a bare `python` may not exist on POSIX.
        assert Path(sys.executable).as_posix() in hook["command"]
        assert str(Path(sys.executable)) in hook["commandWindows"]

    def test_script_output_is_valid_json_and_rewrites_command(self, tmp_path):
        """The real end-to-end property: run the deployed script exactly as
        Codex would invoke it, with real stdin, and check the rewritten
        command is syntactically sound and carries the env-loader guard."""
        import subprocess
        import sys

        dest, root = tmp_path / "agents", tmp_path / "codex"
        dest.mkdir()
        CodexAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        script = root / "hooks" / CodexAgent.PRETOOLUSE_HOOK_SCRIPT

        proc = subprocess.run(
            [sys.executable, str(script)],
            input='{"tool_name":"Bash","tool_input":{"command":"echo hi"}}',
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        cmd = out["hookSpecificOutput"]["updatedInput"]["command"]
        assert cmd.endswith("echo hi")
        assert "AGENTS_RUNTIME_SET" in cmd
        assert '"$d" env --diff --format export' in cmd

    @pytest.mark.skipif(BASH is None, reason="needs a working bash")
    def test_rewritten_command_actually_runs_in_bash(self, tmp_path):
        """Execute the prefix with a stub `dotagents` in the project bin: the
        project's wrapper is the one that runs, its export lands, and the
        prefix leaves PATH as it found it."""
        import subprocess
        import sys

        dest, root = tmp_path / "agents", tmp_path / "codex"
        dest.mkdir()
        CodexAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        script = root / "hooks" / CodexAgent.PRETOOLUSE_HOOK_SCRIPT
        proc = subprocess.run(
            [sys.executable, str(script)],
            input='{"tool_name":"Bash","tool_input":{"command":"printf %s:%s \\"$FROM_STUB\\" \\"$STUB_PATH\\""}}',
            capture_output=True, text=True,
        )
        cmd = json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"]["command"]

        # The stub stands in for `dotagents env --diff`: it reports the PATH it
        # was spawned with as an export.
        project = tmp_path / "proj"
        stub_bin = project / ".agents" / "bin"
        stub_bin.mkdir(parents=True)
        write_text_lf(  # Path.write_text(newline=) needs 3.10; the package floor is 3.9
            stub_bin / "dotagents",
            "#!/bin/sh\necho \"export FROM_STUB='yes'\"\necho \"export STUB_PATH='$PATH'\"\n",
        )
        (stub_bin / "dotagents").chmod(0o755)
        run = subprocess.run(
            [BASH, "-c", cmd], cwd=str(project), capture_output=True, text=True,
            env={k: v for k, v in os.environ.items() if k != "AGENTS_RUNTIME_SET"},
        )
        assert run.returncode == 0, run.stderr
        value, path = run.stdout.split(":", 1)
        assert value == "yes", run.stdout + run.stderr
        assert '"' not in path, "PATH must not carry literal quote characters"
        assert tmp_path.name not in path, "the prefix does not splice the project bin onto PATH"

    def test_script_guard_skips_when_already_set(self, tmp_path):
        import subprocess
        import sys

        dest, root = tmp_path / "agents", tmp_path / "codex"
        dest.mkdir()
        CodexAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        script = root / "hooks" / CodexAgent.PRETOOLUSE_HOOK_SCRIPT

        import os
        env = dict(os.environ)
        env["AGENTS_RUNTIME_SET"] = "1"
        proc = subprocess.run(
            [sys.executable, str(script)],
            input='{"tool_name":"Bash","tool_input":{"command":"echo hi"}}',
            capture_output=True, text=True, env=env,
        )
        assert proc.returncode == 0
        assert proc.stdout.strip() == "", "guard set -- must emit no output (no decision)"

    def test_script_fails_safe_on_bad_input(self, tmp_path):
        import subprocess
        import sys

        dest, root = tmp_path / "agents", tmp_path / "codex"
        dest.mkdir()
        CodexAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        script = root / "hooks" / CodexAgent.PRETOOLUSE_HOOK_SCRIPT

        proc = subprocess.run(
            [sys.executable, str(script)],
            input="not json at all",
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, "must never fail the tool call on bad input"
        assert proc.stdout.strip() == ""

    def test_idempotent_no_script_rewrite_when_unchanged(self, tmp_path):
        dest, root = tmp_path / "agents", tmp_path / "codex"
        dest.mkdir()
        agent = CodexAgent()
        agent.wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        script = root / "hooks" / CodexAgent.PRETOOLUSE_HOOK_SCRIPT
        first_mtime = script.stat().st_mtime_ns

        agent.wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        assert script.stat().st_mtime_ns == first_mtime, "unchanged script must not be rewritten"


class TestCodexEnvBlockRemoval:
    """Earlier releases wrote a static `[shell_environment_policy]` snapshot into
    config.toml: one project's paths pinned into the global config, sometimes
    invalid TOML. dotagents writes none now, and `wire_hooks` removes an old one."""

    OLD_BLOCK = (
        "# dotagents:begin\n"
        "# Managed by dotagents -- edits inside this block are overwritten by\n"
        "[shell_environment_policy]\n"
        'set = {AGENTS_HOME = "/home/u/.agents", AGENTS_PROJECT_ROOT = "/repo"}\n'
        "# dotagents:end\n"
    )

    def test_removes_the_old_block_and_keeps_the_rest(self, tmp_path):
        dest, root = tmp_path / "agents", tmp_path / "codex"
        dest.mkdir()
        root.mkdir()
        cfg = root / "config.toml"
        user = 'model = "gpt-5"\n\n[mcp_servers.docs]\ncommand = "docs-mcp"\n'
        cfg.write_text(user + "\n" + self.OLD_BLOCK, encoding="utf-8")

        CodexAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

        text = cfg.read_text(encoding="utf-8")
        assert text == user
        assert "shell_environment_policy" not in _toml_load(text)

    def test_never_writes_a_block(self, tmp_path):
        dest, root = tmp_path / "agents", tmp_path / "codex"
        dest.mkdir()
        CodexAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        assert not (root / "config.toml").exists()

    def test_dry_run_keeps_the_old_block(self, tmp_path):
        dest, root = tmp_path / "agents", tmp_path / "codex"
        dest.mkdir()
        root.mkdir()
        (root / "config.toml").write_text(self.OLD_BLOCK, encoding="utf-8")
        CodexAgent().wire_hooks(dest, dry_run=True, logger=None, config_root=root)
        assert (root / "config.toml").read_text(encoding="utf-8") == self.OLD_BLOCK


def test_unsupported_adapters_are_noops(tmp_path):
    """Gemini/Cursor/Copilot have no verified hook schema -- inventing one is how
    a broken hook gets shipped. They keep the base no-op."""
    from dotagents._agents import CopilotAgent, CursorAgent, GeminiAgent

    dest, root = tmp_path / "agents", tmp_path / "cfg"
    dest.mkdir()
    for agent in (GeminiAgent(), CursorAgent(), CopilotAgent()):
        agent.wire_hooks(dest, dry_run=False, logger=None, config_root=root)
    assert not root.exists(), "a no-op adapter must not create a config dir"


def test_project_scope_writes_the_gitignored_local_settings(tmp_path):
    """A project-scope `init` must not edit the user's GLOBAL settings, and must
    use `settings.local.json` -- `settings.json` in a repo is checked into source
    control, and these hooks carry machine-specific paths."""
    project = tmp_path / "proj"
    dest = project / ".agents"
    dest.mkdir(parents=True)

    ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None)

    assert (project / ".claude" / "settings.local.json").is_file()
    assert not (project / ".claude" / "settings.json").exists(), (
        "the committed project settings file must not be touched"
    )


def test_default_config_root_follows_the_isolated_home(tmp_path):
    """No `config_root`: the user store's hooks go to `Path.home()/.claude`,
    which conftest has pointed at a tmp dir -- so a test that forgets the
    argument writes there, never into the real `~/.claude`."""
    from conftest import REAL_HOME

    home = Path.home()
    assert home != REAL_HOME and tmp_path.parent in home.parents
    real_settings = REAL_HOME / ".claude" / "settings.json"
    before = real_settings.read_bytes() if real_settings.is_file() else None

    store = home / ".agents"
    store.mkdir()
    ClaudeAgent().wire_hooks(store, dry_run=False, logger=None)

    assert (home / ".claude" / "settings.json").is_file()
    assert (real_settings.read_bytes() if real_settings.is_file() else None) == before


class TestAntigravityHooks:
    """Antigravity has no SessionStart-equivalent (five events only:
    PreToolUse/PostToolUse/PreInvocation/PostInvocation/Stop -- confirmed
    against antigravity.google/docs/hooks). PreInvocation fires every model
    turn (has invocationNum), so the hook script itself gates on
    invocationNum == 0 to behave like a real SessionStart. No env mechanism
    exists at all -- PreToolUse is allow/deny/ask only, no updatedInput
    rewrite capability (confirmed against two independent sources), so this
    is context-only, unlike Claude's and Codex's PreToolUse hooks."""

    def test_no_detection_marker_explicit_only(self):
        """No documented env-var marker exists for Antigravity (checked
        hooks/rules-workflows/getting-started/plugins docs, none found) --
        past sessions already invented and had to walk back false markers
        for other agents. detect_env_vars stays empty so detect_env() always
        returns False; --agents antigravity is required explicitly."""
        agent = AntigravityAgent()
        assert agent.detect_env_vars == []
        assert agent.detect_env({"ANYTHING": "1", "PATH": "/usr/bin"}) is False

    def test_wires_preinvocation_into_gemini_config_hooks_json(self, tmp_path):
        dest, root = tmp_path / "agents", tmp_path / "gemini_config"
        dest.mkdir()

        AntigravityAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

        data = json.loads((root / "hooks.json").read_text(encoding="utf-8"))
        # Documented shape is {"<name>": {"PreInvocation": [...]}}, NOT a bare
        # top-level "hooks" key the way Claude/Codex's schema works.
        assert "dotagents" in data
        entries = data["dotagents"]["PreInvocation"]
        assert len(entries) == 1
        hook = entries[0]["hooks"][0]
        assert "python" in hook["command"]
        assert AntigravityAgent.PREINVOCATION_HOOK_SCRIPT in hook["command"]
        # PreInvocation's matcher is documented as ignored -- no matcher key.
        assert "matcher" not in entries[0]

    def test_deploys_script(self, tmp_path):
        dest, root = tmp_path / "agents", tmp_path / "gemini_config"
        dest.mkdir()

        AntigravityAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

        script = root / "hooks" / AntigravityAgent.PREINVOCATION_HOOK_SCRIPT
        assert script.is_file()
        from dotagents.cli._common import BASE_ROOT
        package_script = Path(BASE_ROOT) / "dotagents" / "hooks" / AntigravityAgent.PREINVOCATION_HOOK_SCRIPT
        assert script.read_bytes() == package_script.read_bytes()

    def test_idempotent(self, tmp_path):
        dest, root = tmp_path / "agents", tmp_path / "gemini_config"
        dest.mkdir()
        agent = AntigravityAgent()
        agent.wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        first = (root / "hooks.json").read_text(encoding="utf-8")
        agent.wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        assert (root / "hooks.json").read_text(encoding="utf-8") == first

    def test_dry_run_writes_nothing(self, tmp_path):
        dest, root = tmp_path / "agents", tmp_path / "gemini_config"
        dest.mkdir()
        AntigravityAgent().wire_hooks(dest, dry_run=True, logger=None, config_root=root)
        assert not (root / "hooks.json").exists()
        assert not (root / "hooks" / AntigravityAgent.PREINVOCATION_HOOK_SCRIPT).exists()

    def test_script_only_injects_on_first_invocation(self, tmp_path):
        """The real property that makes this behave like SessionStart at all:
        gate on invocationNum == 0, run for real via subprocess.

        Hermetic: no `dotagents` in the cwd's `.agents/bin`, in the (tmp)
        store's `bin`, or on a PATH holding only an empty dir -- so the first
        invocation finds nothing to run and injects nothing, instead of
        spawning whatever `dotagents` the developer has installed against
        their real store."""
        import subprocess
        import sys

        dest, root = tmp_path / "agents", tmp_path / "gemini_config"
        dest.mkdir()
        AntigravityAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        script = root / "hooks" / AntigravityAgent.PREINVOCATION_HOOK_SCRIPT
        empty_bin = tmp_path / "empty-bin"
        empty_bin.mkdir()

        first = subprocess.run(
            [sys.executable, str(script)],
            input='{"invocationNum": 0}', capture_output=True, text=True,
            cwd=str(tmp_path), env=dict(os.environ, PATH=str(empty_bin)),
        )
        second = subprocess.run(
            [sys.executable, str(script)],
            input='{"invocationNum": 1}', capture_output=True, text=True,
        )
        third = subprocess.run(
            [sys.executable, str(script)],
            input='{"invocationNum": 5}', capture_output=True, text=True,
        )
        assert second.returncode == 0 and second.stdout.strip() == "", (
            "invocationNum != 0 must produce no output (no-op every later turn)"
        )
        assert third.returncode == 0 and third.stdout.strip() == ""
        assert first.returncode == 0, first.stderr
        assert first.stdout.strip() == "", "no dotagents anywhere: nothing to inject"

    def test_script_output_shape_when_it_fires(self, tmp_path, monkeypatch):
        """When it DOES have something to inject, the output must be the bare
        `{"injectSteps": [{"ephemeralMessage": ...}]}` shape -- no
        `hookSpecificOutput` wrapper, unlike Claude/Codex's PreToolUse."""
        import subprocess
        import sys

        dest, root = tmp_path / "agents", tmp_path / "gemini_config"
        dest.mkdir()
        AntigravityAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        script = root / "hooks" / AntigravityAgent.PREINVOCATION_HOOK_SCRIPT

        # Stub `dotagents` at `<cwd>/.agents/bin/`, the FIRST location
        # `_find_dotagents()` checks -- it deliberately wins over any real
        # installation on PATH or in the real ~/.agents/bin, so this must be
        # where the stub lives for the test to observe it rather than the
        # real install.
        stub_dir = tmp_path / ".agents" / "bin"
        stub_dir.mkdir(parents=True)
        stub_name = "dotagents.cmd" if os.name == "nt" else "dotagents"
        stub = stub_dir / stub_name
        if os.name == "nt":
            stub.write_text('@echo off\r\necho STUB-CONTEXT-EM\xe2\x80\x94DASH\r\n', encoding="utf-8")
        else:
            stub.write_text("#!/bin/sh\necho 'STUB-CONTEXT-EM\xe2\x80\x94DASH'\n", encoding="utf-8")
            stub.chmod(0o755)

        proc = subprocess.run(
            [sys.executable, str(script)],
            input='{"invocationNum": 0}', capture_output=True, text=True,
            cwd=str(tmp_path),
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip(), "expected the stub's output to be injected"
        out = json.loads(proc.stdout)
        assert "injectSteps" in out
        assert "hookSpecificOutput" not in out
        step = out["injectSteps"][0]
        assert set(step.keys()) == {"ephemeralMessage"}
        assert "STUB-CONTEXT-EM" in step["ephemeralMessage"]

    def test_script_fails_safe_on_bad_input(self, tmp_path):
        import subprocess
        import sys

        dest, root = tmp_path / "agents", tmp_path / "gemini_config"
        dest.mkdir()
        AntigravityAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        script = root / "hooks" / AntigravityAgent.PREINVOCATION_HOOK_SCRIPT

        proc = subprocess.run(
            [sys.executable, str(script)],
            input="not json", capture_output=True, text=True,
        )
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""


@pytest.mark.skipif(os.name != "nt", reason="PowerShell parser")
def test_powershell_hook_commands_parse():
    """The PowerShell variants are one-line strings built by concatenation; a
    stray quote only shows up when PowerShell parses them. The inner loader
    (single-quoted inside the PreToolUse command) is parsed on its own too."""
    import subprocess

    check = ("$e=$null;$null=[System.Management.Automation.Language.Parser]::"
             "ParseInput($env:CODE,[ref]$null,[ref]$e);$e.Count")
    inner = ClaudeAgent.PRETOOLUSE_POWERSHELL_COMMAND.split("$p = '", 1)[1].split("'; ", 1)[0]
    for code in (ClaudeAgent.SESSION_START_COMMAND_POWERSHELL,
                 ClaudeAgent.PRETOOLUSE_POWERSHELL_COMMAND, inner):
        res = subprocess.run(["powershell", "-NoProfile", "-Command", check],
                             capture_output=True, text=True, env=dict(os.environ, CODE=code))
        assert res.stdout.strip() == "0", (code, res.stdout, res.stderr)
