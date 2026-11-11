"""Firing overlay hooks (AOS "Hooks"): which overlay hooks a session has, whether the user
allowed them at the overlay's digest, running them, and what of their output applies on a harness.

:mod:`dotagents._aos` is the model (events, payloads, outputs, the combination); this module
gives it a filesystem, the user's allowances, a process and a harness. A hook is the overlay's
code: it runs outside the harness's sandbox with the user's access, so each step that starts one
is explicit and bounded -- allowed at one digest, argv as a list, the limits of :data:`LIMITS`,
nothing of the output trusted before :func:`dotagents._aos.read_hook_output` has read it, and no
output able to grant anything.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from dotagents import _aos

#: The consumer's bounds on every hook (:data:`dotagents._aos.DEFAULT_HOOK_LIMITS`).
LIMITS = dict(_aos.DEFAULT_HOOK_LIMITS)

#: A consumer's own harness id for a process no harness started.
NO_HARNESS = "dotagents"

# --------------------------------------------------------------------------- #
# What each harness lets dotagents fire and apply.
# --------------------------------------------------------------------------- #


class Harness(object):
    """What dotagents can do for hooks on one harness: the harness's own hook for each event it
    has, and the outputs it can apply there (``refuse`` and ``block`` are the event's own
    refusal, ``deny`` and ``ask`` a call's). ``pre-env`` is never native: dotagents fires it
    when it builds the environment, on every harness."""

    def __init__(self, ident: str, native: "dict[str, str]", applies: "dict[str, tuple[str, ...]]",
                 reasons: "dict[str, str]", *, anywhere: bool = False):
        self.id = ident
        #: No harness is known (a plain shell): nothing is said to be unfireable.
        self.anywhere = anywhere
        self.native = dict(native)
        self.applies = {event: frozenset(items) for event, items in applies.items()}
        self.applies["pre-env"] = frozenset(("env", "refuse"))
        #: The harness's name for a session start's source, to the reason sent.
        self.reasons = dict(reasons)

    @property
    def fires(self) -> "list[str]":
        if self.anywhere:
            return list(_aos.HOOK_EVENTS)
        return [event for event in _aos.HOOK_EVENTS if event == "pre-env" or event in self.native]

    def event_of(self, native: str) -> "Optional[str]":
        for event, name in self.native.items():
            if name == native:
                return event
        return None


#: Claude Code: every event has a native hook (measured, Claude Code 2.1.318). A session start
#: cannot be refused; variables reach only Bash, through the env file; a call's variables would
#: need the rewrite of a command, which is not done, so they are reported.
CLAUDE = Harness(
    "claude-code",
    {"session-start": "SessionStart", "session-end": "SessionEnd", "prompt": "UserPromptSubmit",
     "turn-end": "Stop", "cd": "CwdChanged", "pre-compact": "PreCompact", "pre-tool": "PreToolUse",
     "post-tool": "PostToolUse"},
    {"session-start": ("context", "env"), "session-end": (), "prompt": ("context", "block"),
     "turn-end": (), "cd": ("env",), "pre-compact": ("context", "block"),
     "pre-tool": ("context", "deny", "ask"), "post-tool": ("context",)},
    {"startup": "new", "resume": "resume", "clear": "clear", "compact": "compact"},
)

#: Codex: no working-directory event; a session start adds context and cannot be refused; a
#: compaction can be stopped but takes no context; a call can be denied, not sent to the user
#: (measured, Codex 0.166.0).
CODEX = Harness(
    "codex",
    {"session-start": "SessionStart", "session-end": "SessionEnd", "prompt": "UserPromptSubmit",
     "turn-end": "Stop", "pre-compact": "PreCompact", "pre-tool": "PreToolUse", "post-tool": "PostToolUse"},
    {"session-start": ("context",), "session-end": (), "prompt": ("context", "block"), "turn-end": (),
     "pre-compact": ("block",), "pre-tool": ("context", "deny"), "post-tool": ("context",)},
    {"startup": "new", "resume": "resume", "compact": "compact"},
)

#: No harness identified (a plain shell, a setup): no event is reported as one it cannot fire.
ANYWHERE = Harness("", {}, {}, {}, anywhere=True)

HARNESSES = {CLAUDE.id: CLAUDE, CODEX.id: CODEX}


def harness_of(ident: "Optional[str]") -> Harness:
    """The harness named ``ident`` (an ``AGENTS_HARNESS`` value). One dotagents has no native
    hooks for fires only ``pre-env``."""
    if not ident:
        return ANYWHERE
    return HARNESSES.get(ident) or Harness(ident, {}, {}, {})


def partial_events(harness: Harness) -> "list[str]":
    """Events fired for only some occurrences. Claude Code's ``PostToolUse`` is for calls that
    ended well; Codex's tool hooks were measured on its shell tool only."""
    if harness.id == "claude-code":
        return ["post-tool"]
    if harness.id == "codex":
        return ["pre-tool", "post-tool"]
    return []


# --------------------------------------------------------------------------- #
# The user's allowances.
# --------------------------------------------------------------------------- #


def allowances_path(user_store: "str | os.PathLike[str]") -> Path:
    """Where the digest each overlay's hooks were allowed at is kept: the user's own store,
    never an overlay and never a project."""
    return Path(user_store) / "overlays" / ".dotagents-state" / "hooks-allowed.json"


def read_allowances(user_store: "str | os.PathLike[str]") -> "dict[str, str]":
    """``{overlay name: digest}``; nothing is allowed when the file is absent or unreadable."""
    try:
        data = json.loads(allowances_path(user_store).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    allowed = data.get("allowed") if isinstance(data, dict) else None
    if not isinstance(allowed, dict):
        return {}
    return {str(name): digest for name, digest in allowed.items()
            if isinstance(digest, str) and _aos.DIGEST_FORM.match(digest)}


def allowed_digest(user_store: "str | os.PathLike[str]", identity: str) -> "Optional[str]":
    return read_allowances(user_store).get(_aos.normalize_name(identity))


def _write_allowances(user_store: "str | os.PathLike[str]", allowed: "dict[str, str]") -> None:
    from dotagents._fs import write_text_lf

    path = allowances_path(user_store)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(path, json.dumps({"allowed": dict(sorted(allowed.items()))}, indent=2) + "\n", atomic=True)


def allow(user_store: "str | os.PathLike[str]", identity: str, digest: str) -> None:
    """Allow ``identity``'s hooks at ``digest``, replacing the digest allowed before: there is one."""
    allowed = read_allowances(user_store)
    allowed[_aos.normalize_name(identity)] = digest.lower()
    _write_allowances(user_store, allowed)


def revoke(user_store: "str | os.PathLike[str]", identity: str) -> bool:
    allowed = read_allowances(user_store)
    if allowed.pop(_aos.normalize_name(identity), None) is None:
        return False
    _write_allowances(user_store, allowed)
    return True


# --------------------------------------------------------------------------- #
# What an overlay's hooks are on this machine, for a harness.
# --------------------------------------------------------------------------- #


def fresh_digest(overlay: Any) -> "Optional[str]":
    """The overlay's tree digest, read from its files now: an allowance is checked each time, and
    a process that lives on (a server, a test) must not trust an earlier reading."""
    try:
        return overlay.tree_digest()
    except (_aos.DigestError, OSError):
        return None


def overlay_plan(overlay: Any, harness: Harness, user_store: "str | os.PathLike[str]") -> "dict[str, Any]":
    """The :func:`dotagents._aos.hook_plan` of ``overlay`` here, on ``harness``, plus its digest.

    Hooks are on only while the overlay's tree digest is the one the user allowed; an overlay
    whose tree has no digest has none, so none is allowed."""
    from dotagents import _activate

    digest = fresh_digest(overlay)
    plan = _aos.hook_plan(overlay.document(), {
        "os": _aos.current_os(), "interpreters": sorted(_activate.interpreters()),
        "files": _activate.files(overlay), "identity": overlay.name, "digest": digest,
        "allowed": allowed_digest(user_store, overlay.name), "fires": harness.fires,
        "partial": partial_events(harness), "limits": {"timeout": LIMITS["timeout"]},
    })
    plan["digest"] = digest
    plan["identity"] = overlay.name
    return plan


def declares_guard(overlay: Any) -> bool:
    """Whether the overlay has a hook with ``on_failure = "refuse"``."""
    from dotagents import _activate

    entries, _ = _aos.hooks_of(overlay.document(), _activate.files(overlay), _aos.current_os(),
                               sorted(_activate.interpreters()))
    return any(entry["on_failure"] == "refuse" for entry in entries)


def refusal(overlay: Any) -> "Optional[str]":
    """Why an overlay is not activated for its hooks, or None: a guard that is off would be
    missing without a word, so the overlay waits for the user's yes."""
    from dotagents._scope import resolve_user_store

    if not overlay.document().get("hooks") or not declares_guard(overlay):
        return None
    plan = overlay_plan(overlay, ANYWHERE, resolve_user_store())
    if plan["activates"]:
        return None
    return ("it declares a hook that refuses on failure and its hooks are not allowed at its digest "
            "(see them with `dotagents overlays hooks %s`, allow them with `dotagents overlays "
            "allow-hooks %s`)" % (overlay.name, overlay.name))


# --------------------------------------------------------------------------- #
# Wiring a harness's own hooks.
# --------------------------------------------------------------------------- #

#: The label that identifies the entries dotagents wrote for overlay hooks (the native event follows).
STATUS_PREFIX = "Overlay hooks: "
#: The harness's own timeout for the entry, in seconds: above the event's bound (120 s) and the
#: forwarder's (230 s), so the harness never stops the program between a hook and its answer.
ENTRY_TIMEOUT = 240
#: The script a harness's hook runs; it finds `dotagents` and runs `dotagents hook`.
FORWARDER = "overlay_hook.py"


def wanted_events(store: "str | os.PathLike[str]", harness: Harness) -> "dict[str, bool]":
    """The events of ``harness`` to wire for the overlays installed in ``store``, each with whether
    a hook of it refuses on failure (a guard). An event is wired when an installed overlay has a
    hook for it; ``session-start`` is wired for any overlay with hooks, since it is where an
    activation is reported. Nothing is wired for an overlay with none, so no process starts for them."""
    from dotagents import _activate
    from dotagents._overlays import Overlay

    wanted: "dict[str, bool]" = {}
    interpreters = sorted(_activate.interpreters())
    for overlay in Overlay.discover(Path(store) / "overlays", store):
        entries, _ = _aos.hooks_of(overlay.document(), _activate.files(overlay), _aos.current_os(), interpreters)
        for entry in entries:
            if entry["event"] in harness.native:
                wanted[entry["event"]] = wanted.get(entry["event"], False) or entry["on_failure"] == "refuse"
        if entries and "session-start" in harness.native:
            wanted.setdefault("session-start", False)
    return wanted


def forwarder_args(harness: Harness, event: str, level: str, guard: bool) -> "list[str]":
    """The arguments after the forwarder script's path."""
    return [harness.id, event, level] + (["--guard"] if guard else [])


# --------------------------------------------------------------------------- #
# Reports.
# --------------------------------------------------------------------------- #


def describe(report: "Iterable[str]") -> str:
    """One report as a line: the id and its subjects. It never quotes a hook's output."""
    return " ".join(str(part) for part in report)


# --------------------------------------------------------------------------- #
# Running one hook.
# --------------------------------------------------------------------------- #


class Subject(object):
    """The overlay and entry one hook run is for."""

    def __init__(self, overlay: Any, entry: "dict[str, Any]"):
        self.overlay = overlay
        self.entry = entry

    @property
    def name(self) -> str:
        return self.overlay.name


def execute(subject: Subject, event: str, payload: bytes, env: "dict[str, str]", *,
            limits: "Optional[dict[str, int]]" = None) -> "dict[str, Any]":
    """Start the hook and give its run in the model's terms (:func:`dotagents._aos.hook_result`).

    The program is an interpreter followed by the script's absolute path and the event, an argv
    list that no shell reads; its working directory is the overlay's root; ``env`` is exactly its
    environment, plus ``AOS_HOOK_EVENT``. The script must be a regular file inside the root."""
    from dotagents import _run
    from dotagents._env import _spawn_env

    limits = dict(LIMITS, **(limits or {}))
    entry = subject.entry
    run: "dict[str, Any]" = {"payload_size": len(payload), "sets": list(entry["sets"])}
    root = os.path.abspath(str(subject.overlay.path))
    windows = os.name == "nt"
    if entry["path"] is None or entry["interpreter"] is None or not entry["runs"]:
        run["not_run"] = True
        return run
    script = _aos.resolve_path(root, entry["path"], windows)
    real_root = os.path.realpath(root)
    real = os.path.realpath(script)
    if not (os.path.isfile(script) and os.path.normcase(real).startswith(
            os.path.normcase(real_root).rstrip(os.sep) + os.sep)):
        run["not_run"] = True
        return run
    try:
        argv = _run.interpreter_command(entry["interpreter"], python=env.get("AGENTS_PYTHON") or _python(),
                                        base_path=os.environ.get("PATH"))
    except _run.InterpreterError:
        run["not_run"] = True
        return run
    if len(payload) > limits["max_payload"]:
        return run
    child = _spawn_env(dict(env))
    child["AOS_HOOK_EVENT"] = event
    done = _run.run_hook(argv + [script, event], cwd=root, env=child, timeout=entry["timeout"], stdin=payload,
                         max_output=limits["max_output"], max_stderr=limits["max_stderr"])
    run.update(stdout_bytes=done["stdout"], timed_out=done["timed_out"], too_large=done["too_large"],
               seconds=done["seconds"], **{"exit": done["exit"]})
    if done["not_run"]:
        run["not_run"] = True
    run["stderr"] = done["stderr"]
    return run


def _python() -> str:
    import sys

    return sys.executable


# --------------------------------------------------------------------------- #
# Firing an event.
# --------------------------------------------------------------------------- #


class Fired(object):
    """The result of an event: the model's :func:`dotagents._aos.fire_event` result, plus the
    reports of the hooks that were not on or not fired."""

    def __init__(self, result: "dict[str, Any]", reports: "list[list[str]]"):
        self.result = result
        self.reports = reports + [list(r) for r in result["reports"]]

    @property
    def decision(self) -> "Optional[str]":
        return self.result["decision"]


def session_id(harness: str, native_id: str) -> str:
    """The consumer's own id for a session: the same for each of its events, and no secret
    (never the harness's id or the name of its transcript)."""
    digest = hashlib.sha256(("%s\0%s" % (harness, native_id)).encode("utf-8", "replace")).hexdigest()
    return "dotagents-" + digest[:16]


def value_for_match(event: str, facts: "dict[str, Any]") -> "Optional[str]":
    kind = _aos.HOOK_EVENTS[event][4]
    if kind == "reason":
        return facts.get("reason")
    if kind == "kind":
        tool = facts.get("tool")
        return tool.get("kind") if isinstance(tool, dict) else None
    return None


def fire(event: str, facts: "dict[str, Any]", plans: "list[tuple[Any, dict[str, Any]]]", harness: Harness, *,
         environment: "Callable[[Any], Optional[dict[str, str]]]", limits: "Optional[dict[str, int]]" = None,
         ) -> Fired:
    """Fire ``event`` for ``plans``, ``(overlay, plan)`` in composition order.

    Only an overlay whose hooks are on runs. Each hook is started when its turn comes, and not
    after an earlier one denied, blocked or used up the event's time. ``environment(overlay)`` is
    the environment of that overlay's own processes (None: it cannot be built, the hook fails
    as ``not-run``)."""
    reports: "list[list[str]]" = []
    limits = dict(LIMITS, **(limits or {}))
    runs: "list[dict[str, Any]]" = []
    matching = value_for_match(event, facts)
    payload = json.dumps(_aos.hook_payload(event, facts), ensure_ascii=False).encode("utf-8")
    for overlay, plan in plans:
        entries = [entry for entry in plan["hooks"] if entry["event"] == event]
        if not entries:
            continue
        if event not in harness.fires:
            continue
        if not plan["on"]:
            continue
        for entry in entries:
            if matching is not None and not _aos.hook_matches(event, entry["match"], matching):
                continue
            subject = Subject(overlay, entry)

            def start(subject=subject) -> "dict[str, Any]":
                env = environment(subject.overlay)
                if env is None:
                    return {"not_run": True}
                return execute(subject, event, payload, env, limits=limits)

            runs.append({"source": overlay.name, "on_failure": entry["on_failure"], "start": start,
                         "sets": entry["sets"]})
    applies = harness.applies.get(event, frozenset())
    result = _aos.fire_event(event, runs, applies, dict(LIMITS, **(limits or {})), os.name == "nt")
    return Fired(result, reports)


def unsupported_reports(plans: "list[tuple[Any, dict[str, Any]]]") -> "list[list[str]]":
    """Reports of an activation: the hooks that are off, and the events the harness cannot fire."""
    out: "list[list[str]]" = []
    for overlay, plan in plans:
        for report in plan["reports"]:
            line = [report[0], overlay.name] + ([report[1]] if report[0] != "hook-not-allowed" else [])
            out.append(line)
    return out


# --------------------------------------------------------------------------- #
# pre-env: fired by dotagents itself, as it builds the environment.
# --------------------------------------------------------------------------- #


def planned(overlays: "Iterable[Any]", harness: Harness, user_store: "str | os.PathLike[str]",
            ) -> "list[tuple[Any, dict[str, Any]]]":
    """``(overlay, plan)`` for each overlay that has a hook of any event, in the order given."""
    from dotagents import _activate

    out = []
    interpreters = sorted(_activate.interpreters())
    for overlay in overlays:
        entries, _ = _aos.hooks_of(overlay.document(), _activate.files(overlay), _aos.current_os(), interpreters)
        if entries:
            out.append((overlay, overlay_plan(overlay, harness, user_store)))
    return out


def pre_env(plans: "list[tuple[Any, dict[str, Any]]]", session: "_aos.Environment", base: "dict[str, str]",
            harness: Harness, *, cwd: "Optional[str]" = None) -> "tuple[list[list[str]], list[list[str]]]":
    """Fire ``pre-env`` for the overlays in ``plans`` (composition order) and apply their output to
    ``session``, after the root variables and before any overlay's declarations. Each hook runs in
    ``base`` (the base environment and the root variables) plus its own overlay's ``AOS_OVERLAY_*``.
    Returns the reports and the non-default settings; raises :class:`dotagents._aos.Refused`
    (``hook-refused``) when a guard fails."""
    from dotagents import _activate

    payload = json.dumps(_aos.hook_payload("pre-env", {"harness": harness.id or NO_HARNESS,
                                                       "cwd": os.path.abspath(cwd or os.getcwd())}),
                         ensure_ascii=False).encode("utf-8")
    runs: "dict[str, dict[str, Any]]" = {}
    identities: "list[str]" = []
    for overlay, plan in plans:
        entries = [entry for entry in plan["hooks"] if entry["event"] == "pre-env"]
        if not entries or not plan["on"]:
            continue
        entry = entries[0]
        env = dict(base)
        env["AOS_OVERLAY_ROOT"] = os.path.abspath(str(overlay.path))
        env["AOS_OVERLAY_NAME"] = overlay.normalized_name
        state = _activate.state_dir(overlay)
        if state is not None:
            state.mkdir(parents=True, exist_ok=True)
            env["AOS_OVERLAY_STATE"] = str(state)
        subject = Subject(overlay, entry)

        def start(subject=subject, env=env) -> "dict[str, Any]":
            return execute(subject, "pre-env", payload, env)

        identities.append(overlay.name)
        runs[overlay.name] = {"on_failure": entry["on_failure"], "start": start, "sets": []}
    reports: "list[list[str]]" = []
    non_defaults: "list[list[str]]" = []
    _aos.apply_pre_env(session, identities, runs, LIMITS, reports, non_defaults)
    return reports, non_defaults


def has_live_pre_env(scope: Any, harness_id: "Optional[str]") -> bool:
    """Whether an allowed overlay has a ``pre-env`` hook: it fires each time the environment is
    built, so a cached rendering of an earlier build cannot stand in for the build."""
    harness = harness_of(harness_id)
    return any(plan["on"] and any(entry["event"] == "pre-env" for entry in plan["hooks"])
               for _overlay, plan in planned(scope.overlays, harness, scope.user_root))
