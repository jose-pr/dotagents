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
import time
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


#: Characters that end a line for some reader (a shell, Python's ``splitlines``, a terminal), or end
#: a string: a value holding one is not written into an env file.
LINE_SEPARATORS = "\0\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"


def env_file_safe(changes: "dict[str, Optional[str]]") -> bool:
    return all(value is None or not any(c in LINE_SEPARATORS for c in value) for value in changes.values())


def write_env_block(path: "str | os.PathLike[str]", label: str, changes: "dict[str, Optional[str]]") -> None:
    """Put ``changes`` into a Claude Code env file as the one block ``label`` owns: names are
    validated identifiers and every value is single-quoted, so no value is read as shell syntax.
    The file is split at line feeds only, and a value holding any line separator is refused
    (:func:`env_file_safe`), so a value cannot close its own block or start another."""
    if not env_file_safe(changes):
        raise ValueError("a value holds a line separator")
    from dotagents._fs import write_text_lf

    begin, end = ": dotagents-hook-env-begin %s" % label, ": dotagents-hook-env-end %s" % label
    kept: "list[str]" = []
    skipping = False
    try:
        existing = Path(path).read_text(encoding="utf-8").split("\n")
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
    notes += ["env-sensitive %s" % name[1] if isinstance(name, list) else str(name)
              for name in result["non_defaults"]]
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
        if env_file_safe(result["env"]):
            write_env_block(env_file, event, result["env"])
        else:
            notes.append("hook-not-applied env (a value holds a line separator)")
    message = "dotagents: " + "; ".join(notes) if notes else None
    if message and not plain:
        out["systemMessage"] = message
    stderr = "\n".join(notes)
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
              event: "Optional[str]" = None, keep_tree: bool = False) -> "list[tuple[Any, dict[str, Any]]]":
    """``(overlay, plan)`` of the overlays at ``level`` (``user``: the user and system stores,
    ``project``: the project's) that have a hook, in composition order. With ``event``, only
    overlays that have a hook for it are planned, so no tree is hashed for the rest.

    Every installed overlay counts, not only the active ones: an overlay that is refused (a guard
    whose hooks are not allowed at its digest, say) still has its guards, and they fail instead of
    vanishing."""
    from dotagents import _activate
    from dotagents._env import _ordered

    chosen = []
    for overlay in _ordered(scope.installed_overlays):
        where = "user" if overlay.store is None else scope.store_level(overlay.store)
        if (where == "project") != (level == "project"):
            continue
        entries, _ = _aos.hooks_of(overlay.document(), _activate.files(overlay), _aos.current_os(),
                                   sorted(_activate.interpreters()))
        if not any(event is None or entry["event"] == event for entry in entries):
            continue
        chosen.append(overlay)
    return [(overlay, _overlay_hooks.overlay_plan(overlay, harness, scope.user_root, keep_tree))
            for overlay in chosen]


def hook_environment(scope: Any, overlay: Any, environ: "dict[str, str]") -> "Optional[dict[str, str]]":
    """The environment of ``overlay``'s own processes for a hook: the base the session was started
    from (:func:`dotagents._overlay_hooks.session_base`), the roots, this overlay's declarations --
    and the user store's Python, never a project's venv."""
    from dotagents import _env, _pyvenv
    from dotagents._scope import Scope

    base = _overlay_hooks.session_base(environ)
    user_scope = Scope.of(agents_dir=scope.user_root, global_scope=True)
    # The venv is chosen from the user store before any declaration or env script runs, and the
    # interpreters those scripts need are found on a PATH with no store's bin in it.
    return _env.overlay_process_env(
        scope, overlay, base_env=base, python_scope=user_scope,
        base_path=_overlay_hooks.clean_path(base.get("PATH"), scope.user_root,
                                            _overlay_hooks.project_roots(base)))


def _python() -> str:
    import sys

    return sys.executable


def scope_for(level: str, user_store: "str | os.PathLike[str]") -> Any:
    """The scope a hook of ``level`` is looked for in. A user-level hook comes from the user and
    system stores only: no project store is in the scope, so nothing a project holds can shadow
    it, add commands or libraries to it, or choose its Python."""
    from dotagents import _scope

    if level == "user":
        return _scope.Scope.of(agents_dir=user_store, global_scope=True)
    return _scope.Scope.of(agents_dir=user_store, project_root=_scope.project_root_default())


def dispatch(harness_id: str, event: str, stdin: bytes, scope: Any, level: str,
             environ: "Optional[dict[str, str]]" = None, started: "Optional[float]" = None,
             shadow_scope: Any = None, expect_guard: bool = False) -> "tuple[str, str]":
    """Fire ``event`` of ``harness_id`` for the overlays of ``level``; ``(stdout, stderr)``.

    ``started`` is the :func:`time.monotonic` time the process started: everything from there on,
    scope building and hashing included, is inside one deadline (the user's ``deadline`` setting)."""
    environ = os.environ if environ is None else environ
    started = time.monotonic() if started is None else started
    harness = _overlay_hooks.HARNESSES.get(harness_id)
    if harness is None or event not in harness.native:
        return "", "dotagents: no hook %s on harness %r\n" % (event, harness_id)
    user_store = scope.user_root
    deadline = started + _overlay_hooks.deadline_for(user_store) - 5
    limits = _overlay_hooks.limits_for(user_store)
    native = None
    if len(stdin) <= MAX_NATIVE:
        try:
            native = json.loads(stdin.decode("utf-8")) if stdin else {}
        except ValueError:
            native = None
        if not isinstance(native, dict):
            native = None
    plans = plans_for(scope, harness, level, event=None if event == "session-start" else event,
                      keep_tree=native is not None)
    if native is None:
        # An oversize or unreadable payload is a failure of every hook of the event, and a guard
        # refuses.
        report = "hook-failed %s payload" % event
        guarded = any(entry["event"] == event and entry["on_failure"] == "refuse"
                      for _overlay, plan in plans for entry in plan["hooks"])
        return (refusal_answer(harness, event, "an overlay hook guard could not read the event") if guarded else ""), report
    if expect_guard and not any(entry["event"] == event and entry["on_failure"] == "refuse"
                                for _overlay, plan in plans for entry in plan["hooks"]):
        # The forwarder's record says this event has a guard and none was found (the overlay's
        # hooks were taken out, or the entry is stale): a guard that has gone missing is a failed one.
        return (refusal_answer(harness, event, "an overlay hook guard is missing"),
                "hook-failed %s guard-missing" % event)
    facts = facts_of(harness, event, native)
    cache: "dict[str, Optional[dict[str, str]]]" = {}

    def environment(overlay: Any) -> "Optional[dict[str, str]]":
        if overlay.path not in cache:
            cache[overlay.path] = hook_environment(scope, overlay, dict(environ))
        return cache[overlay.path]

    base = _overlay_hooks.session_base(environ)
    base_path = _overlay_hooks.clean_path(base.get("PATH"), user_store,
                                          _overlay_hooks.project_roots(base, facts["cwd"]))
    fired = _overlay_hooks.fire(event, facts, plans, harness, environment=environment, limits=limits,
                                user_store=user_store, deadline=deadline, base_path=base_path)
    _overlay_hooks.log_errors(user_store, fired.errors)
    if event == "session-start":
        fired.reports.extend(_overlay_hooks.unsupported_reports(plans))
        if shadow_scope is not None:
            try:
                fired.reports.extend(["hook-shadowed", overlay.name]
                                     for overlay in _overlay_hooks.shadowed_with_hooks(shadow_scope))
            except Exception as exc:  # noqa: BLE001 - the hooks have run; say so and carry on
                fired.reports.append(["hook-failed", "shadow-check", type(exc).__name__])
    return answer(harness, event, fired, environ.get("CLAUDE_ENV_FILE"))
