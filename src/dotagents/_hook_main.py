"""The entry the hook forwarder runs: fire one event's overlay hooks and say what happened.

This is a fixed entry, not the `dotagents` command line: it discovers no command and adds no
library, because discovery reads `<project>/.agents/dotagents/cmds/` and the project's overlays'
`cmds/`, and what the agent can write there must not run in the program that guards the agent.
It builds a scope with no project store for a user-level hook, and the user store's Python and
interpreters are the only ones it uses.

Its answer is one JSON object, ``{"nonce": <the nonce it was given>, "answer": <what to tell the
harness, as text>}``. The forwarder accepts nothing without its own nonce back, so a program that
stands in for this one (an exit status of 0 and no output, say) is a failed guard, not "no
objection".
"""

from __future__ import annotations

import json
import sys
import time
from typing import Optional


def _option(argv: "list[str]", name: str) -> "Optional[str]":
    if name in argv:
        index = argv.index(name)
        if index + 1 < len(argv):
            return argv[index + 1]
    return None


def main(argv: "list[str]") -> int:
    """``EVENT --harness H --level user|project --agents-dir DIR --nonce N``; exit status 0 only with
    an answer."""
    started = time.monotonic()
    if not argv:
        return 2
    event, harness = argv[0], _option(argv, "--harness")
    level, store, nonce = _option(argv, "--level"), _option(argv, "--agents-dir"), _option(argv, "--nonce")
    if not (harness and level in ("user", "project") and store and nonce):
        return 2
    from dotagents import _hook_dispatch, _overlay_hooks

    if harness not in _overlay_hooks.HARNESSES or event not in _overlay_hooks.HARNESSES[harness].native:
        return 2
    stdin = sys.stdin.buffer.read(_hook_dispatch.MAX_NATIVE + 1)
    scope = _hook_dispatch.scope_for(level, store)
    shadow = _hook_dispatch.scope_for("project", store) if level == "user" else None
    out, err = _hook_dispatch.dispatch(harness, event, stdin, scope, level, started=started, shadow_scope=shadow)
    if err:
        sys.stderr.write(err if err.endswith("\n") else err + "\n")
    sys.stdout.write(json.dumps({"nonce": nonce, "answer": out}))
    sys.stdout.flush()
    return 0
