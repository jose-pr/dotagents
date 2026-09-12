#!/usr/bin/env python3
"""Codex PreToolUse hook: inject dotagents env into Bash tool calls.

Codex has no env-persistence mechanism: its SessionStart hook can only add
"plain text on stdout ... as extra developer context"
(learn.chatgpt.com/docs/hooks), and the `shell_environment_policy.set`
snapshot in config.toml (CodexAgent.write_env_block) is static, frozen at the
last `dotagents init --agents codex`. This hook provides the live env the
same way the Claude PowerShell one does: `PreToolUse` supports
`updatedInput.command` ("To rewrite a supported tool call without blocking"),
the same mechanism and JSON shape as Claude Code's.

Wired with `"matcher": "Bash"` in hooks.json -- Codex's only shell tool -- so
this script only ever receives Bash tool_input and needs no tool_name check.

The prepended snippet is guarded by AGENTS_RUNTIME_SET (the same name as the
Claude PowerShell hook uses): the `dotagents env` spawn happens on the first
Bash call, and every later call re-evaluates the cheap `[ -z ... ]` check in
its own process. The guard only skips the spawn if one call's export is
inherited by the next, which is not guaranteed; the snippet is correct either
way.

Shipped as a file, not inlined: Codex documents hook commands as
`python3 ~/.codex/hooks/*.py`, and a `.py` file has no execution-policy or
signing concern (that constraint is PowerShell-specific).

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
    # findable without a global install or a PATH edit by the user; the store
    # is `$AGENTS_HOME` when set. Inside `"$( ... )"` the command substitution
    # is parsed afresh, so the inner quotes must be plain `"`: a `\"` there is
    # a LITERAL quote character that ends up inside PATH.
    prefix = (
        'if [ -z "$AGENTS_RUNTIME_SET" ]; then export AGENTS_RUNTIME_SET=1; '
        'eval "$(PATH=".agents/bin:${AGENTS_HOME:-$HOME/.agents}/bin:$PATH" '
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
