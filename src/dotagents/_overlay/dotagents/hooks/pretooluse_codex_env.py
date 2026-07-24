#!/usr/bin/env python3
"""Codex PreToolUse hook: inject dotagents env into Bash tool calls.

Why this exists: Codex has NO env-persistence mechanism at all -- unlike
Claude, which has $CLAUDE_ENV_FILE (Bash-tool-only, but real). Codex's
SessionStart hook can only add "plain text on stdout ... as extra developer
context" (learn.chatgpt.com/docs/hooks); nothing persists exports into
subsequent commands. dotagents' only other Codex-env path is a STATIC snapshot
baked into config.toml via `shell_environment_policy.set` (see
CodexAgent.write_env_block) -- frozen at whatever `dotagents init` last ran,
never live, and only written on an explicit `--agents codex`.

This hook closes the live-env gap the same way the Claude/PowerShell one does:
`PreToolUse` supports `updatedInput.command` (learn.chatgpt.com/docs/hooks,
"To rewrite a supported tool call without blocking") -- the exact same
mechanism and JSON shape as Claude Code's, confirmed structurally identical.
Matched via `"matcher": "Bash"` in hooks.json (Codex's only shell-execution
tool -- no separate PowerShell/cmd tool the way Claude has), so this script
only ever receives Bash tool_input, unlike the Claude PowerShell hook which
had to check tool_name itself against a no-matcher registration.

Guarded by AGENTS_RUNTIME_SET (same name/convention as the Claude PowerShell
hook and the precursor's original runtime.py), so the `dotagents env` spawn
only happens once per session -- the first Bash tool call pays the cost, every
later one is a cheap `[ -z "$AGENTS_RUNTIME_SET" ]` check inside its own
prepended shell snippet, re-evaluated fresh in that call's own process (each
tool call is its own process; nothing here relies on state surviving across
calls except via the guard var itself, which only works if AGENTS_RUNTIME_SET
set by one call's export is inherited by the next -- UNVERIFIED for Codex,
same open question as the Claude PowerShell hook).

Written as a file, not inlined via `python3 -c`: Codex hook scripts are
documented and shown exclusively as files (learn.chatgpt.com/docs/hooks
consistently shows `python3 ~/.codex/hooks/*.py`), and Python source has no
execution-policy/signing concern the way a PowerShell .ps1 does on Windows --
that constraint was specific to PowerShell, not this.

Fails safe on any error: never lets a hook bug break the user's tool call.
"""

import json
import os
import sys


def main() -> int:
    try:
        hook_input = json.load(sys.stdin)
    except Exception:
        return 0

    if os.environ.get("AGENTS_RUNTIME_SET"):
        return 0

    tool_input = hook_input.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    original_command = tool_input.get("command")
    if not isinstance(original_command, str) or not original_command:
        return 0

    # PATH prefix mirrors SESSION_START_COMMAND's own -- `<scope>/bin` may not
    # be on PATH yet (that is part of what this loads), so `dotagents` is
    # findable without a global install or a PATH edit by the user.
    prefix = (
        'if [ -z "$AGENTS_RUNTIME_SET" ]; then export AGENTS_RUNTIME_SET=1; '
        'eval "$(PATH=\\".agents/bin:$HOME/.agents/bin:$PATH\\" '
        'dotagents env --diff --format export 2>/dev/null)"; fi; '
    )

    updated_input = dict(tool_input)
    updated_input["command"] = prefix + original_command

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated_input,
        }
    }
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Never let a hook bug break the user's tool call.
        sys.exit(0)
