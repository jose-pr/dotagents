"""Characterization tests for the agent registry (plans 00 + 08).

Covers: env-var detection per adapter, resolve_active_agent precedence
(explicit > $AGENTS_HARNESS > detect_env > config-file detect() > claude
default), and stamp_identity producing the correct AGENTS_*/AGENT vars per
harness with no blanket-rewrite junk.

Run from the repo root: ``python -m pytest tests/``.
"""

import sys
from pathlib import Path

import pytest

# Make src/ importable regardless of cwd / install state.
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from dotagents import _agents  # noqa: E402
from dotagents._agents import (  # noqa: E402
    ClaudeAgent,
    CodexAgent,
    CopilotAgent,
    CursorAgent,
    GeminiAgent,
    resolve_active_agent,
    stamp_identity,
)

# A root guaranteed to contain no agent config files, so the config-file detect()
# fallback finds nothing and resolution reaches the claude default.
EMPTY_ROOT = Path("/__dotagents_nonexistent_root__")


# --------------------------------------------------------------------------
# Per-adapter env-var detection
# --------------------------------------------------------------------------

@pytest.mark.parametrize("env", [
    {"CLAUDECODE": "1"},
    {"CLAUDE_CODE_ENTRYPOINT": "cli"},
])
def test_claude_detect_env(env):
    assert ClaudeAgent().detect_env(env)


@pytest.mark.parametrize("env", [
    {"GEMINI_CLI": "1"},
])
def test_gemini_detect_env(env):
    assert GeminiAgent().detect_env(env)


def test_gemini_does_not_detect_on_api_key_or_invented_session():
    # GEMINI_API_KEY is a credential, GEMINI_SESSION was invented -- neither marks
    # a running Gemini CLI.
    assert not GeminiAgent().detect_env({"GEMINI_API_KEY": "x"})
    assert not GeminiAgent().detect_env({"GEMINI_SESSION": "x"})


@pytest.mark.parametrize("env", [
    {"CODEX_HOME": "/tmp/codex"},
    {"CODEX_SANDBOX": "1"},
    {"CODEX_SANDBOX_NETWORK_DISABLED": "1"},  # prefix match
])
def test_codex_detect_env(env):
    assert CodexAgent().detect_env(env)


def test_codex_no_invented_session_marker():
    assert not CodexAgent().detect_env({"CODEX_SESSION": "x"})


def test_cursor_detect_env():
    assert CursorAgent().detect_env({"CURSOR_AGENT": "1"})
    # The invented CURSOR_SESSION_ID must not detect.
    assert not CursorAgent().detect_env({"CURSOR_SESSION_ID": "x"})


def test_copilot_has_no_env_marker():
    # Copilot ships no runtime marker yet; env detection is always False and
    # detection is left to config-file detect().
    assert CopilotAgent().detect_env_vars == []
    assert not CopilotAgent().detect_env({"COPILOT_MODEL": "gpt-5"})
    assert not CopilotAgent().detect_env({"GITHUB_COPILOT": "1"})


# --------------------------------------------------------------------------
# resolve_active_agent precedence
# --------------------------------------------------------------------------

def test_precedence_explicit_wins():
    # explicit beats a conflicting env marker
    ag = resolve_active_agent({"GEMINI_CLI": "1"}, explicit="codex", root=EMPTY_ROOT)
    assert ag.name == "codex"


def test_precedence_agents_harness_stamp():
    # $AGENTS_HARNESS accepts the harness_id ...
    ag = resolve_active_agent({"AGENTS_HARNESS": "gemini-cli"}, root=EMPTY_ROOT)
    assert ag.name == "gemini"
    # ... and the short registry name.
    ag = resolve_active_agent({"AGENTS_HARNESS": "cursor"}, root=EMPTY_ROOT)
    assert ag.name == "cursor"


def test_precedence_env_detection():
    ag = resolve_active_agent({"GEMINI_CLI": "1"}, root=EMPTY_ROOT)
    assert ag.name == "gemini"


def test_precedence_harness_beats_env():
    ag = resolve_active_agent(
        {"AGENTS_HARNESS": "codex", "GEMINI_CLI": "1"}, root=EMPTY_ROOT
    )
    assert ag.name == "codex"


def test_precedence_config_file_detect_fallback(tmp_path):
    # No env markers, but a Gemini config file is present -> detect() picks Gemini.
    (tmp_path / "GEMINI.md").write_text("x", encoding="utf-8")
    ag = resolve_active_agent({}, root=tmp_path)
    assert ag.name == "gemini"


def test_precedence_default_claude():
    ag = resolve_active_agent({}, root=EMPTY_ROOT)
    assert ag.name == "claude"


def test_unknown_agents_harness_falls_through_to_default():
    ag = resolve_active_agent({"AGENTS_HARNESS": "not-a-real-harness"}, root=EMPTY_ROOT)
    assert ag.name == "claude"


# --------------------------------------------------------------------------
# stamp_identity (plan 08)
# --------------------------------------------------------------------------

def test_stamp_claude_full():
    ident = stamp_identity(
        {"CLAUDECODE": "1", "ANTHROPIC_MODEL": "claude-opus-4-8"}, root=EMPTY_ROOT
    )
    assert ident["AGENTS_HARNESS"] == "claude-code"  # harness-id, NOT "claude"
    assert ident["AGENT"] == "claude-code"           # ecosystem marker aligned
    assert ident["AGENTS_VENDOR"] == "anthropic"
    assert ident["AGENTS_MODEL"] == "claude-opus-4-8"


def test_stamp_gemini_no_model_when_unset():
    ident = stamp_identity({"GEMINI_CLI": "1"}, root=EMPTY_ROOT)
    assert ident["AGENTS_HARNESS"] == "gemini-cli"
    assert ident["AGENTS_VENDOR"] == "google"
    # No AGENTS_MODEL emitted when the source var is absent (no empty value).
    assert "AGENTS_MODEL" not in ident


def test_stamp_never_clobbers_user_set_value():
    ident = stamp_identity(
        {"CLAUDECODE": "1", "AGENTS_HARNESS": "already-set"}, root=EMPTY_ROOT
    )
    # An existing value is respected: not re-emitted.
    assert "AGENTS_HARNESS" not in ident


def test_stamp_no_blanket_rewrite_junk():
    # The dropped CLAUDE_*->AGENTS_* rewrite must not resurface.
    ident = stamp_identity(
        {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "abc"}, root=EMPTY_ROOT
    )
    assert "AGENTS_CODE_SESSION_ID" not in ident
    assert all(not k.endswith("_SESSION_ID") for k in ident)


def test_harness_ids_are_distinct_from_short_names():
    # The whole point of the bug fix: harness_id != short name where they differ.
    assert ClaudeAgent.harness_id == "claude-code" != ClaudeAgent.name
    assert GeminiAgent.harness_id == "gemini-cli" != GeminiAgent.name


def test_registry_covers_all_builtin_names():
    names = {a.name for a in _agents.get_all_agents()}
    assert names == {"claude", "gemini", "codex", "cursor", "copilot"}
