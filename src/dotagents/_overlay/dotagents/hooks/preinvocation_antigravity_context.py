#!/usr/bin/env python3
"""Antigravity PreInvocation hook: inject dotagents context once per session.

Why this exists: Antigravity's hooks (antigravity.google/docs/hooks) have no
SessionStart-equivalent event -- only five events exist: PreToolUse,
PostToolUse, PreInvocation, PostInvocation, Stop. PreInvocation is the closest
analog, but it "fires before the model is called" -- confirmed via its
`invocationNum` input field ("the current model invocation") -- meaning EVERY
model turn, not once per session. Naively injecting the assembled context on
every invocation would re-send it every turn: real token and latency cost, and
NOT what SessionStart hooks do elsewhere.

Fix: `invocationNum` is 0-indexed and explicitly documented as "the first
invocation is 0". Only inject on invocationNum == 0. Every later turn this
script sees a nonzero invocationNum and exits with no output -- cheap, no
`dotagents` spawn, no output means "no decision" per the docs' output-field
semantics (mirrors PreToolUse's own no-op contract elsewhere in this file set).

Output shape is DIFFERENT from Claude/Codex's PreToolUse: no
`hookSpecificOutput` wrapper. PreInvocation's documented output is a bare
`{"injectSteps": [...]}` object. Each step can be `toolCall`, `userMessage`,
or `ephemeralMessage` (a string, documented only as "a transient system
message", no further semantics specified). `ephemeralMessage` is used here --
it is the only one of the three actually intended for injected TEXT content;
`toolCall` triggers tool execution and `userMessage` impersonates the user,
neither of which fits assembled reference context.

No env mechanism exists for Antigravity at all (no PreToolUse rewrite
capability -- confirmed allow/deny/ask only, no `updatedInput`-equivalent
found anywhere in the docs), so unlike the Claude/Codex PreToolUse hooks, this
one is CONTEXT ONLY. There is no live-env-injection story for Antigravity
today.

No `shell`/`commandWindows`-equivalent field is documented for Antigravity
hook commands (checked directly, absent) -- this script must therefore be
runnable via a bare `python <path>` invocation with no shell features assumed
on either platform, and must not rely on shell quoting/redirection anywhere.

`workspacePaths` (a Common Input Field: "Absolute directory paths representing
the user's mounted workspaces") is used to find AGENTS.md, since Antigravity's
own project-level convention is `.agents/rules/`, not a bare root file --
see AntigravityAgent's write_context/harness_loads for the full picture.

Fails safe on any error: never lets a hook bug break the session.
"""

import json
import subprocess
import sys
from pathlib import Path


def _find_dotagents() -> "str | None":
    """Locate the `dotagents` command the same way the Bash/PowerShell hooks
    do -- prefer the scope's own `.agents/bin`, fall back to PATH.

    On Windows this MUST prefer `dotagents.cmd` over the bare `dotagents` sh
    script: `subprocess.run([path, ...])` invokes the file directly via
    `CreateProcess`, which cannot run a shebang script the way a POSIX shell
    can -- confirmed live: `WinError 193 "%1 is not a valid Win32
    application"` when pointed at the bare `dotagents` wrapper on this
    machine. `dotagents.cmd` is the file Windows' own process launcher
    actually understands.
    """
    import os
    import shutil

    names = ("dotagents.cmd", "dotagents") if os.name == "nt" else ("dotagents",)
    for base in (Path(".agents") / "bin", Path.home() / ".agents" / "bin"):
        for name in names:
            candidate = base / name
            if candidate.is_file():
                return str(candidate)
    return shutil.which("dotagents")


def main() -> int:
    try:
        hook_input = json.load(sys.stdin)
    except Exception:
        return 0

    if hook_input.get("invocationNum", 0) != 0:
        return 0  # every turn after the first: no-op, no `dotagents` spawn

    dotagents = _find_dotagents()
    if not dotagents:
        return 0  # nothing to inject if the command isn't resolvable

    try:
        proc = subprocess.run(
            [dotagents, "context", "--agents", "antigravity"],
            capture_output=True, timeout=25,
        )  # bytes, NOT text=True -- see below
    except Exception:
        return 0

    # `text=True` would decode the child's stdout using the platform default
    # encoding (cp1252 on this Windows machine), not UTF-8 -- confirmed live:
    # every em-dash in real assembled context came out as mojibake
    # ("â€”" instead of "—"). The same class of bug D90 already fixed
    # for `dotagents context`'s OWN stdout write; this is the same failure one
    # layer up, decoding that command's output back in a hook that reads it.
    context_text = proc.stdout.decode("utf-8", errors="replace").strip()
    if not context_text:
        return 0

    # Deliberately NOT ensure_ascii=False: the default (True) escapes non-ASCII
    # as \uXXXX, so this print() is always pure-ASCII output regardless of the
    # console's codepage -- confirmed directly. Do not "fix" this to
    # ensure_ascii=False without also routing through a UTF-8-safe stdout
    # writer (see _write_stdout in cli/context.py, added after a live cp1252
    # crash) -- that combination is exactly what broke elsewhere.
    print(json.dumps({"injectSteps": [{"ephemeralMessage": context_text}]}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Never let a hook bug break the session.
        sys.exit(0)
