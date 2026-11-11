#!/usr/bin/env python3
"""Run `dotagents hook` for a harness's own hook: the harness's payload goes in, its answer comes out.

    overlay_hook.py HARNESS EVENT LEVEL [--guard]

`init` and `overlays add/sync/remove` write this into a harness's hook configuration with the
absolute interpreter that ran them, so no shell syntax is involved in finding `dotagents`
(the project's `.agents/bin`, then the store's `bin`, then PATH).

If `dotagents` cannot be found or run and the event has a guard (`--guard`: a hook of the event
refuses on failure), this answers in the harness's words with a refusal where the event can be
refused, because a guard that fails open is missing without a word. Without a guard it stays
silent. The exit status is always 0: a hook blocks by what it says.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

#: Bounds the harness's own timeout for this entry also covers (the event's bound is 120 s).
TIMEOUT = 230


def find_dotagents():
    names = ("dotagents.cmd", "dotagents") if os.name == "nt" else ("dotagents",)
    store = Path(os.environ.get("AGENTS_HOME") or (Path.home() / ".agents"))
    for base in (Path.cwd() / ".agents" / "bin", store / "bin"):
        for name in names:
            candidate = base / name
            if candidate.is_file():
                return str(candidate)
    return shutil.which("dotagents")


def refusal(harness, event, why):
    if event == "pre-tool":
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": why}}
    if event == "pre-compact" and harness == "codex":
        return {"continue": False, "stopReason": why}
    if event in ("prompt", "pre-compact"):
        return {"decision": "block", "reason": why}
    return None


def main(argv):
    if len(argv) < 4:
        return 0
    harness, event, level = argv[1:4]
    guard = "--guard" in argv[4:]
    payload = sys.stdin.buffer.read()
    exe = find_dotagents()
    command = [exe, "hook", event, "--harness", harness, "--level", level] if exe else None
    if command and guard:
        command.append("--guard")
    answer = None
    if command:
        try:
            done = subprocess.run(command, input=payload, stdout=subprocess.PIPE, timeout=TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            done = None
        if done is not None and done.returncode == 0:
            sys.stdout.buffer.write(done.stdout)
            sys.stdout.flush()
            return 0
    if guard:
        answer = refusal(harness, event, "an overlay hook guard could not run")
    if answer is not None:
        sys.stdout.write(json.dumps(answer) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception:
        sys.exit(0)
