#!/usr/bin/env python3
"""Codex SessionStart hook: print `dotagents context --agents codex`.

Codex adds a SessionStart hook's plain stdout "as extra developer context"
(learn.chatgpt.com/docs/hooks). A Python script rather than a shell line:
Codex runs a hook command through the platform shell, and on native Windows
that is cmd.exe, which reads a POSIX `PATH="..." dotagents context` as its own
PATH builtin and runs nothing. `init` wires this script with the absolute
interpreter that ran it, so no shell syntax is involved anywhere.

`dotagents` is found the way the Antigravity hook finds it: the project's
`.agents/bin`, then the store's `bin` (`$AGENTS_HOME`, else `~/.agents`), then
PATH. `--agents codex` matters: without it `context` resolves the agent from
the hook's environment, lands on Claude, and gives Codex nothing.

Fails safe: any error prints nothing and exits 0, never blocking the session.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _find_dotagents() -> "str | None":
    names = ("dotagents.cmd", "dotagents") if os.name == "nt" else ("dotagents",)
    store = Path(os.environ.get("AGENTS_HOME") or (Path.home() / ".agents"))
    for base in (Path.cwd() / ".agents" / "bin", store / "bin"):
        for name in names:
            candidate = base / name
            if candidate.is_file():
                return str(candidate)
    return shutil.which("dotagents")


def main() -> int:
    exe = _find_dotagents()
    if not exe:
        return 0
    try:
        res = subprocess.run(
            [exe, "context", "--agents", "codex"],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    if res.returncode == 0 and res.stdout:
        sys.stdout.buffer.write(res.stdout)
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
