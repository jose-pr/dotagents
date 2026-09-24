#!/usr/bin/env python3
"""Codex PreToolUse hook: inject dotagents env into Bash tool calls.

Codex has no env-persistence mechanism: its SessionStart hook can only add
"plain text on stdout ... as extra developer context"
(learn.chatgpt.com/docs/hooks), and a `shell_environment_policy.set` snapshot
in config.toml would be static and global (dotagents no longer writes one).
This hook is the env: it works the same way the Claude PowerShell one does:
`PreToolUse` supports
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

Native Windows: Codex runs the shell tool through PowerShell there, which
cannot parse the POSIX prefix below -- every command in the session failed.
Until a PowerShell form is verified against Codex's own Windows tool input,
the hook passes commands through unchanged (no env) instead of breaking them;
SessionStart still delivers the context. `DOTAGENTS_HOOK_SHELL=posix|windows`
overrides the platform check (tool-internal, for tests).

`permissionDecision: "allow"` is required, not a shortcut: Codex applies
`updatedInput` only together with it and asks for approval through its
separate PermissionRequest event.

Fails safe on any error: never lets a hook bug break the user's tool call.
"""

import json
import os
import sys


def _windows_shell() -> bool:
    forced = os.environ.get("DOTAGENTS_HOOK_SHELL", "").lower()
    if forced in ("posix", "windows"):
        return forced == "windows"
    return os.name == "nt"


def main() -> int:
    if _windows_shell():
        return 0
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

    # Finds `dotagents` the way SESSION_START_COMMAND does: the project's
    # wrapper, else the store's (`$AGENTS_HOME` when set), else PATH -- by
    # path, never by splicing the bins onto PATH, which `env --diff` would
    # then report as a change. Inside `"$( ... )"` the command substitution is
    # parsed afresh, so the inner quotes are plain `"`.
    prefix = (
        'if [ -z "$AGENTS_RUNTIME_SET" ]; then export AGENTS_RUNTIME_SET=1; '
        'd="$PWD/.agents/bin/dotagents"; [ -f "$d" ] || d="${AGENTS_HOME:-$HOME/.agents}/bin/dotagents"; '
        '[ -f "$d" ] || d=dotagents; '
        'eval "$("$d" env --diff --format export 2>/dev/null)"; fi; '
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
