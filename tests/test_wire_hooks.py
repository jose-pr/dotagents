"""`wire_hooks`: the skills link + per-agent hook-config merge.

Claude gets both halves (env via `$CLAUDE_ENV_FILE` + context via stdout) plus the
skills link; Codex gets the context half into `hooks.json` (no env-file equivalent
exists); every other adapter stays a no-op.

Every test redirects `config_root` at a tmp_path -- nothing here may touch the
real `~/.claude` or `~/.codex`. `test_never_touches_real_home` asserts that.

Run: ``PYTHONPATH=src python -m pytest tests/test_wire_hooks.py``
"""

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotagents._agents import ClaudeAgent, CodexAgent  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Redirect `Path.home()` for every test in this file to a throwaway dir.

    Backstop against any test in this module that forgets to pass
    `config_root=` and would otherwise land in the real `~/.claude`.
    `test_never_touches_real_home` is the primary check; this fixture ensures
    any call that forgets it
    -- present or added later -- still lands in an isolated directory instead of
    the real `~/.agents/hooks/`, because `Path.home()` itself no longer resolves
    there for the duration of the test.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))


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
    assert "dotagents env --diff --format export" in cmd
    assert "dotagents context" in cmd


def test_hook_prefixes_scope_bin_on_path():
    """The hook must find `dotagents` without a global install.

    `init` populates `<scope>/bin/`; prefixing it here is what makes the hook
    self-sufficient. Without it the hook is one `command not found` from silently
    delivering nothing -- which is exactly what happened on the dev box.
    """
    for cmd in (ClaudeAgent.SESSION_START_COMMAND, CodexAgent.SESSION_START_COMMAND):
        assert ".agents/bin" in cmd
        assert "$HOME/.agents/bin" in cmd
        assert "$PATH" in cmd, "must PREPEND, not replace, the inherited PATH"
        assert cmd.index(".agents/bin") < cmd.index("$HOME/.agents/bin"), (
            "project scope should win over the user store"
        )


def test_env_hook_appends_and_is_guarded():
    """Two documented requirements, both load-bearing.

    `>` would discard variables other hooks wrote to the same file; an unguarded
    redirect with CLAUDE_ENV_FILE unset would create a file named "".
    """
    cmd = ClaudeAgent.SESSION_START_COMMAND
    assert '>> "$CLAUDE_ENV_FILE"' in cmd, "must append, never truncate"
    assert ">>" in cmd and not _has_truncating_redirect(cmd)
    assert '[ -n "$CLAUDE_ENV_FILE" ]' in cmd, "must guard against an unset var"
    assert cmd.index("dotagents env") < cmd.index("dotagents context"), (
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
    # 2 handlers per event: bash-syntax + a PowerShell-native equivalent (Windows
    # without Git Bash silently routes the bash one through PowerShell by
    # default, per hooks.md's `shell` field docs -- a hard parse error).
    assert len(hooks["SessionStart"]) == 2
    assert len(hooks["CwdChanged"]) == 2


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


def test_migrates_legacy_lowercase_session_start(tmp_path):
    dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
    root.mkdir()
    _settings(root).write_text(
        json.dumps({"hooks": {"session_start": []}}), encoding="utf-8"
    )

    ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

    hooks = _hooks_of_settings(root)
    assert "session_start" not in hooks, "legacy key should be dropped"
    assert ClaudeAgent.SESSION_START_COMMAND in _commands(hooks["SessionStart"])


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

    def test_both_shell_variants_present(self, tmp_path):
        dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
        ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

        hooks = _hooks_of_settings(root)
        for event in ("SessionStart", "CwdChanged"):
            assert len(hooks[event]) == 2, "%s must carry both shell variants" % event
            shells = {e["hooks"][0].get("shell") for e in hooks[event]}
            assert shells == {None, "powershell"}, (
                "one handler must be default-shell (bash), the other explicit powershell"
            )

    def test_powershell_variants_are_context_only_not_env(self, tmp_path):
        """The PowerShell SessionStart variant must NOT try to write
        $CLAUDE_ENV_FILE -- that variable's documented effect is "subsequent
        BASH commands" regardless of which shell wrote it, so a write from here
        would feed nothing. The env gap for PowerShell tool calls is covered
        separately by PRETOOLUSE_POWERSHELL_COMMAND."""
        dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
        ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

        hooks = _hooks_of_settings(root)
        ps_session_start = next(
            e for e in hooks["SessionStart"] if e["hooks"][0].get("shell") == "powershell"
        )
        assert "CLAUDE_ENV_FILE" not in ps_session_start["hooks"][0]["command"]
        assert "dotagents.cmd" in ps_session_start["hooks"][0]["command"]
        assert ps_session_start["hooks"][0]["command"].strip().endswith("context")

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

    def test_windows_only(self, tmp_path):
        """The gate itself (`os.name == "nt"`) can't safely be monkeypatched --
        `pathlib.Path()` dispatches on the REAL `os.name` to choose
        `WindowsPath`/`PosixPath`, so patching it mid-test corrupts every
        subsequent `Path()` call, pytest's own included. Inspect source instead.
        """
        import inspect

        src = inspect.getsource(ClaudeAgent.wire_hooks)
        assert 'os.name == "nt"' in src, (
            "the PowerShell PreToolUse wiring must stay gated to Windows"
        )

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
        assert "\\.agents\\bin\\dotagents.cmd" in cmd

    def test_command_is_valid_powershell_syntax(self):
        """Parses the exact production string with PowerShell's own tokenizer --
        catches a syntax error without needing a live hook invocation."""
        import subprocess

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
        if proc.returncode != 0 and "powershell" in (proc.stderr or "").lower():
            import pytest
            pytest.skip("no PowerShell available on this host")
        assert proc.stdout.strip() == "OK", proc.stdout + proc.stderr


class TestCodexHooks:
    """Codex's hook JSON is structurally identical to Claude's, so `_hooks`
    merges it unchanged. `SessionStart` is context-only, matching Claude's
    Codex-side gap -- no CLAUDE_ENV_FILE equivalent exists there. The LIVE env
    half is covered separately, below, by a `PreToolUse` hook using the same
    `updatedInput.command` rewrite mechanism Codex's own docs confirm exists
    (learn.chatgpt.com/docs/hooks, "To rewrite a supported tool call without
    blocking") -- structurally the same JSON shape as Claude's PowerShell hook."""

    def test_wires_session_start_into_hooks_json(self, tmp_path):
        dest, root = tmp_path / "agents", tmp_path / "codex"
        dest.mkdir()
        CodexAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

        data = json.loads((root / "hooks.json").read_text(encoding="utf-8"))
        assert _commands(data["hooks"]["SessionStart"]) == [
            CodexAgent.SESSION_START_COMMAND
        ]
        assert "dotagents context" in CodexAgent.SESSION_START_COMMAND

    def test_session_start_has_no_env_write(self, tmp_path):
        """SessionStart itself still carries no CLAUDE_ENV_FILE-style write --
        that mechanism does not exist for Codex at any hook event. The env half
        is PreToolUse's job, checked in TestCodexPreToolUse below."""
        dest, root = tmp_path / "agents", tmp_path / "codex"
        dest.mkdir()
        CodexAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        assert "ENV_FILE" not in CodexAgent.SESSION_START_COMMAND

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
        assert "python3" in hook["command"]
        assert str(script) in hook["command"] or script.as_posix() in hook["command"]
        # commandWindows uses `python`, not `python3` -- on Windows `python3` is
        # commonly a Microsoft Store app-execution-alias stub that silently
        # no-ops; verified directly on the dev machine (exit code 49, "Python
        # was not found... install from the Microsoft Store").
        assert hook["commandWindows"].startswith("python ")
        assert "python3" not in hook["commandWindows"]

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
        assert "dotagents env --diff --format export" in cmd

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


class TestCodexEnvBlock:
    """Codex has no per-session env mechanism, so the env is a static managed
    block in `config.toml` (`shell_environment_policy.set`), refreshed by `init`."""

    ENV = {"AGENTS_HOME": "/home/u/.agents", "AGENTS_PROJECT_ROOT": "/repo"}

    def test_writes_shell_environment_policy(self, tmp_path):
        root = tmp_path / "codex"
        CodexAgent().write_env_block(self.ENV, dry_run=False, logger=None, config_root=root)

        text = (root / "config.toml").read_text(encoding="utf-8")
        assert "[shell_environment_policy]" in text
        assert 'AGENTS_HOME = "/home/u/.agents"' in text
        assert "# dotagents:begin" in text and "# dotagents:end" in text

    def test_appends_so_toml_tables_do_not_swallow_user_keys(self, tmp_path):
        """The hazard that dictates append-not-prepend: a `[table]` header captures
        every key line after it, so a prepended block would pull the user's
        top-level keys into `[shell_environment_policy]`."""
        root = tmp_path / "codex"
        root.mkdir()
        cfg = root / "config.toml"
        cfg.write_text('model = "gpt-5"\napproval_policy = "on-request"\n', encoding="utf-8")

        CodexAgent().write_env_block(self.ENV, dry_run=False, logger=None, config_root=root)

        text = cfg.read_text(encoding="utf-8")
        assert text.index('model = "gpt-5"') < text.index("[shell_environment_policy]"), (
            "user's top-level keys must precede our table header"
        )
        parsed = _toml_load(text)
        assert parsed["model"] == "gpt-5", "user key must stay top-level, not be swallowed"
        assert parsed["shell_environment_policy"]["set"]["AGENTS_HOME"] == "/home/u/.agents"

    def test_output_is_valid_toml(self, tmp_path):
        root = tmp_path / "codex"
        CodexAgent().write_env_block(self.ENV, dry_run=False, logger=None, config_root=root)
        parsed = _toml_load((root / "config.toml").read_text(encoding="utf-8"))
        assert parsed["shell_environment_policy"]["set"] == self.ENV

    def test_values_with_quotes_and_backslashes_are_escaped(self, tmp_path):
        """Windows paths are full of backslashes; an unescaped one is invalid TOML."""
        root = tmp_path / "codex"
        env = {"AGENTS_HOME": r"D:\workspace\.agents", "ODD": 'a"b'}
        CodexAgent().write_env_block(env, dry_run=False, logger=None, config_root=root)
        parsed = _toml_load((root / "config.toml").read_text(encoding="utf-8"))
        assert parsed["shell_environment_policy"]["set"] == env

    def test_refresh_replaces_block_and_is_idempotent(self, tmp_path):
        root = tmp_path / "codex"
        agent = CodexAgent()
        agent.write_env_block(self.ENV, dry_run=False, logger=None, config_root=root)
        agent.write_env_block(self.ENV, dry_run=False, logger=None, config_root=root)
        text = (root / "config.toml").read_text(encoding="utf-8")
        assert text.count("[shell_environment_policy]") == 1, "must refresh, not append twice"

        agent.write_env_block({"AGENTS_HOME": "/new"}, dry_run=False, logger=None, config_root=root)
        parsed = _toml_load((root / "config.toml").read_text(encoding="utf-8"))
        assert parsed["shell_environment_policy"]["set"] == {"AGENTS_HOME": "/new"}

    def test_preserves_user_content_outside_the_block(self, tmp_path):
        root = tmp_path / "codex"
        root.mkdir()
        cfg = root / "config.toml"
        cfg.write_text('model = "gpt-5"\n\n[mcp_servers.docs]\ncommand = "docs-mcp"\n', encoding="utf-8")

        agent = CodexAgent()
        agent.write_env_block(self.ENV, dry_run=False, logger=None, config_root=root)
        agent.write_env_block({"AGENTS_HOME": "/changed"}, dry_run=False, logger=None, config_root=root)

        parsed = _toml_load(cfg.read_text(encoding="utf-8"))
        assert parsed["model"] == "gpt-5"
        assert parsed["mcp_servers"]["docs"]["command"] == "docs-mcp"

    def test_keeps_identity_but_skips_path(self, tmp_path):
        """Identity belongs in a per-agent file; PATH never does.

        `set` overrides per subprocess, so a baked-in PATH would replace the
        inherited one for everything Codex spawns (and is machine-specific).
        """
        root = tmp_path / "codex"
        env = {
            "AGENTS_HOME": "/home/u/.agents",
            "AGENT": "codex",
            "AGENTS_HARNESS": "codex",
            "AGENTS_VENDOR": "openai",
            "PATH": "/a:/b:/c",
        }
        CodexAgent().write_env_block(env, dry_run=False, logger=None, config_root=root)

        written = _toml_load((root / "config.toml").read_text(encoding="utf-8"))["shell_environment_policy"]["set"]
        assert written["AGENT"] == "codex"
        assert written["AGENTS_VENDOR"] == "openai"
        assert "PATH" not in written

    def test_only_skipped_vars_writes_nothing(self, tmp_path):
        root = tmp_path / "codex"
        CodexAgent().write_env_block(
            {"PATH": "/a:/b"}, dry_run=False, logger=None, config_root=root,
        )
        assert not (root / "config.toml").exists()

    def test_identity_describes_codex_not_the_running_harness(self, tmp_path):
        """The end-to-end property: initialized FROM Claude, Codex's config must
        still say AGENT=codex. `_resolved_env` passes the adapter name as
        `explicit`, so identity is computed for the target agent."""
        from dotagents.cli._common import _resolved_env

        env = _resolved_env(tmp_path / "agents", logging.getLogger("t"), "codex")
        assert env.get("AGENT") == "codex"
        assert env.get("AGENTS_VENDOR") == "openai"

    def test_empty_env_writes_nothing(self, tmp_path):
        root = tmp_path / "codex"
        CodexAgent().write_env_block({}, dry_run=False, logger=None, config_root=root)
        assert not (root / "config.toml").exists()

    def test_dry_run_writes_nothing(self, tmp_path):
        root = tmp_path / "codex"
        CodexAgent().write_env_block(self.ENV, dry_run=True, logger=None, config_root=root)
        assert not (root / "config.toml").exists()

    def test_only_written_for_an_explicitly_named_agent(self, tmp_path, monkeypatch):
        """This edits the user's main config with values that go stale, so it must
        be asked for -- never triggered because Codex happened to be detected."""
        from dotagents.cli import _common
        from dotagents.cli._common import BASE_ROOT

        # The real base overlay: its AGENTS.md is marker-wrapped, which
        # `write_base_config` requires.
        src, dest = BASE_ROOT, tmp_path / "agents"
        codex_home = tmp_path / "codexhome"
        monkeypatch.setenv("CODEX_HOME", str(codex_home))
        # Pretend Codex is the running harness, so auto-detection would pick it up.
        monkeypatch.setenv("CODEX_SANDBOX", "1")

        _common._apply_base(
            src, dest, False, False, logging.getLogger("t"), agents=None, wire_hooks=True,
        )
        assert not (codex_home / "config.toml").exists(), (
            "auto-detection must not rewrite the user's config.toml"
        )

        _common._apply_base(
            src, dest, False, False, logging.getLogger("t"),
            agents=["codex"], wire_hooks=True,
        )
        assert (codex_home / "config.toml").is_file(), "--agents codex should write it"


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


def test_never_touches_real_home(tmp_path, monkeypatch):
    """Guard: a stray default must never let a test write to the user's real
    ~/.claude/settings.json.

    Undoes the module's autouse `_isolated_home` patch for the DURATION OF THE
    PRE/POST CHECKS ONLY (via `monkeypatch.undo()`, then re-applied), so
    `Path.home()` here resolves to the machine's genuine home directory --
    otherwise this test would compare the fake home's before/after state,
    which is always equal and proves nothing.
    """
    monkeypatch.undo()
    real_settings = Path.home() / ".claude" / "settings.json"
    before_settings = real_settings.read_text(encoding="utf-8") if real_settings.is_file() else None

    dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
    ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

    after_settings = real_settings.read_text(encoding="utf-8") if real_settings.is_file() else None
    assert after_settings == before_settings, "the real ~/.claude/settings.json was modified"
