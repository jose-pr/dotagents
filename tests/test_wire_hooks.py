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
    assert len(hooks["SessionStart"]) == 1
    assert len(hooks["CwdChanged"]) == 1


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


class TestCodexHooks:
    """Codex's hook JSON is structurally identical to Claude's, so `_hooks`
    merges it unchanged. Context half only -- Codex has no CLAUDE_ENV_FILE
    equivalent, so writing an env hook would mean inventing a mechanism."""

    def test_wires_session_start_into_hooks_json(self, tmp_path):
        dest, root = tmp_path / "agents", tmp_path / "codex"
        dest.mkdir()
        CodexAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

        data = json.loads((root / "hooks.json").read_text(encoding="utf-8"))
        assert _commands(data["hooks"]["SessionStart"]) == [
            CodexAgent.SESSION_START_COMMAND
        ]
        assert "dotagents context" in CodexAgent.SESSION_START_COMMAND

    def test_no_env_hook(self, tmp_path):
        """Codex hooks only *receive* env vars; nothing persists exports back."""
        dest, root = tmp_path / "agents", tmp_path / "codex"
        dest.mkdir()
        CodexAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)
        assert "ENV_FILE" not in (root / "hooks.json").read_text(encoding="utf-8")

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


def test_never_touches_real_home(tmp_path):
    """Guard: a stray default must never let a test write to the user's ~/.claude."""
    real = Path.home() / ".claude" / "settings.json"
    before = real.read_text(encoding="utf-8") if real.is_file() else None

    dest, root = _scope_with_skills(tmp_path), tmp_path / "claude"
    ClaudeAgent().wire_hooks(dest, dry_run=False, logger=None, config_root=root)

    after = real.read_text(encoding="utf-8") if real.is_file() else None
    assert after == before, "the real ~/.claude/settings.json was modified"
