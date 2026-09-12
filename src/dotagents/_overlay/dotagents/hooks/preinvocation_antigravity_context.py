#!/usr/bin/env python3
"""Antigravity PreInvocation hook: inject dotagents context once per session.

Antigravity's hooks (antigravity.google/docs/hooks) have five events --
PreToolUse, PostToolUse, PreInvocation, PostInvocation, Stop -- and no
SessionStart equivalent. PreInvocation fires before EVERY model call, so this
script injects only when `invocationNum` (0-indexed) is 0 and exits with no
output on every later turn: no `dotagents` spawn, and no output means "no
decision" per the docs.

Output is a bare `{"injectSteps": [...]}` object, not Claude/Codex's
`hookSpecificOutput` wrapper. Of the three step types, `ephemeralMessage` ("a
transient system message") is the one for injected text; `toolCall` executes
a tool and `userMessage` impersonates the user.

Context only: Antigravity's PreToolUse is allow/deny/ask with no
`updatedInput`, so there is no env-injection path like the Claude/Codex
PreToolUse hooks.

No `shell`/`commandWindows` field exists for Antigravity hook commands, so
this script must run under a bare `python <path>` on either platform and
must not rely on shell quoting or redirection.

`workspacePaths` (a Common Input Field: the user's mounted workspace
directories) pins the project root: the first existing entry becomes the cwd
and `AGENTS_PROJECT_ROOT` of the `dotagents context` spawn, so the
workspace's `.agents/` is what gets assembled rather than the hook process's
undocumented cwd. Antigravity's project-level convention is `.agents/rules/`
-- see AntigravityAgent's context_target/harness_loads.

Fails safe on any error: never lets a hook bug break the session.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


def _find_dotagents() -> "str | None":
    """Locate the `dotagents` command the same way the Bash/PowerShell hooks
    do -- prefer the scope's own `.agents/bin`, fall back to PATH.

    On Windows this MUST prefer `dotagents.cmd` over the bare `dotagents` sh
    script: `subprocess.run([path, ...])` goes through `CreateProcess`, which
    cannot run a shebang script (`WinError 193 "%1 is not a valid Win32
    application"`).
    """
    import shutil

    names = ("dotagents.cmd", "dotagents") if os.name == "nt" else ("dotagents",)
    store = Path(os.environ.get("AGENTS_HOME") or (Path.home() / ".agents"))
    for base in (Path(".agents") / "bin", store / "bin"):
        for name in names:
            candidate = base / name
            if candidate.is_file():
                return str(candidate)
    return shutil.which("dotagents")


def _workspace_root(hook_input) -> "str | None":
    """The first existing `workspacePaths` entry, or ``None``: it becomes
    `AGENTS_PROJECT_ROOT` (and the cwd) for the `dotagents context` spawn, so
    the project's `.agents/` is resolved rather than the hook process's
    undocumented cwd."""
    paths = hook_input.get("workspacePaths")
    if isinstance(paths, list):
        for p in paths:
            if isinstance(p, str) and p and os.path.isdir(p):
                return p
    return None


def main() -> int:

    try:
        hook_input = json.load(sys.stdin)
    except Exception:
        return 0

    if hook_input.get("invocationNum", 0) != 0:
        return 0  # every turn after the first: no-op, no `dotagents` spawn

    root = _workspace_root(hook_input)
    if root:
        os.chdir(root)
    dotagents = _find_dotagents()
    if not dotagents:
        return 0  # nothing to inject if the command isn't resolvable

    env = dict(os.environ)
    if root:
        env["AGENTS_PROJECT_ROOT"] = root
    try:
        proc = subprocess.run(
            [dotagents, "context", "--agents", "antigravity"],
            capture_output=True, timeout=25, env=env,
        )  # bytes, NOT text=True -- see below
    except Exception:
        return 0
    if proc.returncode != 0:
        return 0  # a failed assembly must not inject a partial payload

    # Decoded explicitly as UTF-8: `text=True` would use the platform default
    # encoding (cp1252 on Windows) and turn every non-ASCII character in the
    # context into mojibake.
    context_text = proc.stdout.decode("utf-8", errors="replace").strip()
    if not context_text:
        return 0

    # Deliberately NOT ensure_ascii=False: the default escapes non-ASCII as
    # \uXXXX, so this print() is pure ASCII whatever the console's codepage.
    # ensure_ascii=False would need a UTF-8-safe stdout writer (see
    # _write_stdout in cli/context.py) or it crashes on a cp1252 console.
    print(json.dumps({"injectSteps": [{"ephemeralMessage": context_text}]}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Never let a hook bug break the session.
        sys.exit(0)
