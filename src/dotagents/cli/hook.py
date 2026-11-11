"""`dotagents hook` -- what a harness's own hook runs for overlay hooks."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from dotagents.cli._common import DotAgentsArgs, _write_stdout


class Hook(DotAgentsArgs):
    """Fire the overlay hooks of one event for a harness and answer it in its own words.

    Not for typing: `init` writes it into a harness's hook configuration, and the harness runs it
    with its payload on standard input. A hook's output never reaches the harness; the answer is
    built here from the output that was accepted, and it can deny, ask or block, never approve.
    With `--guard`, a failure of this program itself answers with a refusal where the event can
    be refused, since a hook of the event is a guard. Exit status is 0."""

    _parsername_ = "hook"
    # A harness's hook entry point, not a tool for an agent to call.
    _mcp_ = False

    event: str = ""
    "The AOS event: session-start, session-end, prompt, turn-end, cd, pre-compact, pre-tool, post-tool."
    ("event",)

    harness: str = ""
    "The harness id the payload comes from: claude-code or codex."
    ("--harness",)

    level: str = "user"
    "Whose overlays run: `user` (the user and system stores) or `project` (the project's store)."
    ("--level",)

    guard: bool = False
    "A hook of this event refuses on failure: answer with a refusal if this program fails."
    ("--guard",)

    def __call__(self) -> int:
        from dotagents import _hook_dispatch, _overlay_hooks, _scope

        harness = _overlay_hooks.HARNESSES.get(self.harness)
        if harness is None or self.event not in harness.native or self.level not in ("user", "project"):
            sys.stderr.write("error: hook needs --harness claude-code|codex, an event that harness has, "
                             "and --level user|project\n")
            return 2
        try:
            stdin = sys.stdin.buffer.read(_hook_dispatch.MAX_NATIVE + 1)
        except (OSError, ValueError):
            stdin = b""
        try:
            scope = _scope.Scope.of(agents_dir=_scope.resolve_user_store(self.agents_dir),
                                    project_root=_scope.project_root_default())
            out, err = _hook_dispatch.dispatch(self.harness, self.event, stdin, scope, self.level)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - a guard fails closed
            err = "dotagents hook: %s failed (%s)\n" % (self.event, type(exc).__name__)
            out = ""
            if self.guard:
                out = _hook_dispatch.refusal_answer(harness, self.event, "an overlay hook guard could not run")
        if err:
            sys.stderr.write(err if err.endswith("\n") else err + "\n")
        if out:
            _write_stdout(out)
        return 0
