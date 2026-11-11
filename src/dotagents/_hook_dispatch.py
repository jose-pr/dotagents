"""The program a harness's own hook runs for overlay hooks (``dotagents hook``).

The harness gives it its native payload on standard input; it fires the overlays' hooks of that
event (:mod:`dotagents._overlay_hooks`) and answers the harness in the harness's own words.
**Nothing a hook printed reaches the harness**: the answer is built here from the output
:func:`dotagents._aos.read_hook_output` accepted, and the only decisions it can state are
``deny``, ``ask`` and ``block`` -- never an approval, never a rewritten input, never a reply that
lets a stop or a tool call through that the harness would not have let through.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any, Optional

from dotagents import _aos, _overlay_hooks

#: The most of the harness's payload read; a payload is a hook's input, and 8 MiB covers a tool
#: input larger than the 1 MiB a hook is given (which then is not run).
MAX_NATIVE = 8 * 1024 * 1024

_CLAUDE_KINDS = {
    "Bash": "execute", "PowerShell": "execute", "Read": "read", "NotebookRead": "read",
    "Write": "edit", "Edit": "edit", "MultiEdit": "edit", "NotebookEdit": "edit",
    "Glob": "search", "Grep": "search", "WebFetch": "fetch", "WebSearch": "fetch",
    "TodoWrite": "think", "ExitPlanMode": "switch_mode",
}
_CODEX_KINDS = {
    "exec_command": "execute", "shell": "execute", "local_shell": "execute", "container.exec": "execute",
    "apply_patch": "edit", "web_search": "fetch",
}


def tool_kind(harness: "_overlay_hooks.Harness", name: str) -> str:
    """The ACP kind of a harness's tool; a tool it does not place is ``other``, which runs every
    hook of its event, so an unknown tool never slips past a guard."""
    table = _CODEX_KINDS if harness.id == "codex" else _CLAUDE_KINDS
    return table.get(name, "other")


def facts_of(harness: "_overlay_hooks.Harness", event: str, native: "dict[str, Any]") -> "dict[str, Any]":
    """The model's ``known`` facts for an event, from the harness's payload."""
    cwd = native.get("new_cwd") if event == "cd" and isinstance(native.get("new_cwd"), str) else native.get("cwd")
    facts: "dict[str, Any]" = {
        "harness": harness.id,
        "cwd": os.path.abspath(cwd) if isinstance(cwd, str) and cwd else os.getcwd(),
        "session": _overlay_hooks.session_id(harness.id, str(native.get("session_id", ""))),
    }
    if event == "session-start":
        facts["reason"] = harness.reasons.get(str(native.get("source", "")), "new")
    elif event == "prompt":
        facts["prompt"] = native.get("prompt") if isinstance(native.get("prompt"), str) else ""
    elif event == "cd":
        previous = native.get("old_cwd")
        facts["previous"] = os.path.abspath(previous) if isinstance(previous, str) and previous else facts["cwd"]
    elif event == "pre-compact":
        facts["trigger"] = "manual" if native.get("trigger") == "manual" else "auto"
    elif event in ("pre-tool", "post-tool"):
        name = native.get("tool_name") if isinstance(native.get("tool_name"), str) else ""
        facts["tool"] = {"kind": tool_kind(harness, name), "name": name, "input": native.get("tool_input")}
        if event == "post-tool":
            facts["status"] = "completed"
    return facts


# --------------------------------------------------------------------------- #
# The answer.
# --------------------------------------------------------------------------- #


def context_text(parts: "list[list[str]]") -> str:
    """The context hooks added, each part inside a marker the text cannot imitate: the marker holds
    a nonce chosen after the hooks ran."""
    nonce = secrets.token_hex(8)
    return "\n\n".join(
        "[context from the hook of overlay %s, marker %s]\n%s\n[end of marker %s]" % (name, nonce, text, nonce)
        for name, text in parts)


def _reason(fired: "_overlay_hooks.Fired") -> str:
    result = fired.result
    if result["reason"]:
        return "%s (overlay %s)" % (result["reason"], result["by"])
    failure = next((r for r in fired.reports if r[0] == "hook-failed" and r[1] == result["by"]), None)
    return "the hook of overlay %s failed%s" % (result["by"], " (%s)" % failure[3] if failure else "")


def write_env_block(path: "str | os.PathLike[str]", label: str, changes: "dict[str, Optional[str]]") -> None:
    """Put ``changes`` into a Claude Code env file as the one block ``label`` owns: names are
    validated identifiers and every value is single-quoted, so no value is read as shell syntax."""
    from dotagents._fs import write_text_lf

    begin, end = ": dotagents-hook-env-begin %s" % label, ": dotagents-hook-env-end %s" % label
    kept: "list[str]" = []
    skipping = False
    try:
        existing = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        existing = []
    for line in existing:
        if line.strip() == begin:
            skipping = True
        elif skipping and line.strip() == end:
            skipping = False
        elif not skipping:
            kept.append(line)
    lines: "list[str]" = []
    for name, value in changes.items():
        if not _aos.valid_variable_name(name):
            continue
        if value is None:
            lines.append("unset %s" % name)
        else:
            lines.append("export %s='%s'" % (name, value.replace("'", "'\\''")))
    if lines:
        kept += [begin] + lines + [end]
    write_text_lf(path, "\n".join(kept) + ("\n" if kept else ""), atomic=True)


def answer(harness: "_overlay_hooks.Harness", event: str, fired: "_overlay_hooks.Fired",
           env_file: "Optional[str]" = None) -> "tuple[str, str]":
    """``(stdout, stderr)`` for the harness. Exit status is always 0: a hook blocks by output."""
    result = fired.result
    notes = [_overlay_hooks.describe(r) for r in fired.reports
             if event == "session-start" or r[0] != "hook-not-allowed"]
    logged = ["hook %s %s wrote: %s" % (overlay, name, text.strip()) for overlay, name, text in fired.errors]
    message = "dotagents: " + "; ".join(notes) if notes else None
    decision = result["decision"]
    context = context_text(result["context"]) if result["context"] else ""
    out: "dict[str, Any]" = {}
    native = harness.native.get(event)
    plain = ""
    if decision is not None:
        reason = _reason(fired)
        if event == "pre-tool":
            out["hookSpecificOutput"] = {"hookEventName": native, "permissionDecision": decision,
                                         "permissionDecisionReason": reason}
        elif event == "pre-compact" and harness.id == "codex":
            out.update({"continue": False, "stopReason": reason})
        else:
            out.update({"decision": "block", "reason": reason})
    elif context and event == "pre-compact":
        plain = context
    elif context:
        out["hookSpecificOutput"] = {"hookEventName": native, "additionalContext": context}
    if event in ("session-start", "cd") and env_file and result["env"] and harness.id == "claude-code":
        write_env_block(env_file, event, result["env"])
    if message and not plain:
        out["systemMessage"] = message
    stderr = "\n".join(notes + logged)
    if plain:
        return plain, stderr
    return (json.dumps(out) + "\n" if out else ""), stderr


def refusal_answer(harness: "_overlay_hooks.Harness", event: str, why: str) -> str:
    """What the program says when it failed itself while a guard of the event exists."""
    native = harness.native.get(event)
    if event == "pre-tool":
        return json.dumps({"hookSpecificOutput": {"hookEventName": native, "permissionDecision": "deny",
                                                  "permissionDecisionReason": why}}) + "\n"
    if event == "pre-compact" and harness.id == "codex":
        return json.dumps({"continue": False, "stopReason": why}) + "\n"
    if event in ("prompt", "pre-compact"):
        return json.dumps({"decision": "block", "reason": why}) + "\n"
    return ""


# --------------------------------------------------------------------------- #
# The dispatch.
# --------------------------------------------------------------------------- #


def plans_for(scope: Any, harness: "_overlay_hooks.Harness", level: str,
              event: "Optional[str]" = None) -> "list[tuple[Any, dict[str, Any]]]":
    """``(overlay, plan)`` of the active overlays at ``level`` (``user``: the user and system
    stores, ``project``: the project's), in composition order. With ``event``, only overlays that
    have a hook for it are planned, so no tree is hashed for the rest."""
    from dotagents import _activate
    from dotagents._env import _ordered

    chosen = []
    for overlay in _ordered(scope.overlays):
        where = "user" if overlay.store is None else scope.store_level(overlay.store)
        if (where == "project") != (level == "project"):
            continue
        entries, _ = _aos.hooks_of(overlay.document(), _activate.files(overlay), _aos.current_os(),
                                   sorted(_activate.interpreters()))
        if not any(event is None or entry["event"] == event for entry in entries):
            continue
        chosen.append(overlay)
    return [(overlay, _overlay_hooks.overlay_plan(overlay, harness, scope.user_root)) for overlay in chosen]


def dispatch(harness_id: str, event: str, stdin: bytes, scope: Any, level: str,
             environ: "Optional[dict[str, str]]" = None) -> "tuple[str, str]":
    """Fire ``event`` of ``harness_id`` for the overlays of ``level``; ``(stdout, stderr)``."""
    environ = os.environ if environ is None else environ
    harness = _overlay_hooks.HARNESSES.get(harness_id)
    if harness is None or event not in harness.native:
        return "", "dotagents: no hook %s on harness %r\n" % (event, harness_id)
    try:
        native = json.loads(stdin[:MAX_NATIVE].decode("utf-8")) if stdin else {}
    except ValueError:
        native = {}
    if not isinstance(native, dict):
        native = {}
    facts = facts_of(harness, event, native)
    plans = plans_for(scope, harness, level, event=None if event == "session-start" else event)
    cache: "dict[str, Optional[dict[str, str]]]" = {}

    def environment(overlay: Any) -> "Optional[dict[str, str]]":
        from dotagents import _env

        if overlay.path not in cache:
            cache[overlay.path] = _env.overlay_process_env(scope, overlay, base_env=dict(environ))
        return cache[overlay.path]

    fired = _overlay_hooks.fire(event, facts, plans, harness, environment=environment)
    if event == "session-start":
        fired.reports.extend(_overlay_hooks.unsupported_reports(plans))
    return answer(harness, event, fired, environ.get("CLAUDE_ENV_FILE"))
