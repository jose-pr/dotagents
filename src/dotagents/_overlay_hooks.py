"""Firing overlay hooks (AOS "Hooks"): which overlay hooks a session has, whether the user
allowed them at the overlay's digest, running them, and what of their output applies on a harness.

:mod:`dotagents._aos` is the model (events, payloads, outputs, the combination); this module
gives it a filesystem, the user's allowances, a process and a harness. A hook is the overlay's
code: it runs outside the harness's sandbox with the user's access, so each step that starts one
is explicit and bounded -- allowed at one digest, argv as a list, the limits of :data:`LIMITS`,
nothing of the output trusted before :func:`coqui_aos.read_hook_output` has read it, and no
output able to grant anything.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import coqui_aos

from dotagents import _aos
from dotagents._overlays import Overlay

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


#: The settings a user may change, with their bounds (low, high): read from the user's own
#: configuration only, never from an overlay or a project.
SETTING_BOUNDS = {"timeout": (1, 580), "event_timeout": (1, 580), "deadline": (10, 580),
                  "max_output": (1024, 16 * 1024 * 1024), "max_payload": (1024, 8 * 1024 * 1024)}
#: One deadline for everything a harness's hook entry does, from the process's start: scope
#: building, hashing, env scripts and hooks.
DEFAULT_DEADLINE = 150
#: Never above what a harness's own hook limit allows; no setting of a run or an event is above it.
ENTRY_TIMEOUT_CAP = 600
#: Names a store in a hook entry: the user's, or a project's by hash. Checked before it is a path.
TOKEN_FORM = r"^(?:user|project-[0-9a-f]{10})$"


def valid_token(token: str) -> bool:
    import re

    return isinstance(token, str) and re.match(TOKEN_FORM, token) is not None


def settings_path(user_store: "str | os.PathLike[str]") -> Path:
    return Path(user_store) / "overlays" / ".dotagents-state" / "hooks-settings.json"


def read_settings(user_store: "str | os.PathLike[str]") -> "dict[str, int]":
    """The user's hook settings (:data:`SETTING_BOUNDS`) that are valid integers in bounds; the rest
    is ignored. Absent file: no settings."""
    try:
        data = json.loads(settings_path(user_store).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if key in SETTING_BOUNDS
            and isinstance(value, int) and not isinstance(value, bool)
            and SETTING_BOUNDS[key][0] <= value <= SETTING_BOUNDS[key][1]}


def limits_for(user_store: "str | os.PathLike[str]") -> "dict[str, int]":
    """:data:`LIMITS` with the user's settings over them; a hook is stopped by these, and the
    report of the stop names the setting."""
    return dict(LIMITS, **{k: v for k, v in read_settings(user_store).items() if k != "deadline"})


def deadline_for(user_store: "str | os.PathLike[str]") -> int:
    return read_settings(user_store).get("deadline", DEFAULT_DEADLINE)


def entry_timeout(user_store: "str | os.PathLike[str]") -> int:
    """The harness's own timeout for a hook entry: the deadline and a margin, below the cap."""
    return min(deadline_for(user_store) + 20, ENTRY_TIMEOUT_CAP)


def allowances_path(user_store: "str | os.PathLike[str]") -> Path:
    """Where the digest each overlay's hooks were allowed at is kept: the user's own store,
    never an overlay and never a project."""
    return Path(user_store) / "overlays" / ".dotagents-state" / "hooks-allowed.json"


def read_allowances(user_store: "str | os.PathLike[str]") -> "dict[str, str]":
    """``{store|name: digest}``; nothing is allowed when the file is absent or unreadable."""
    try:
        data = json.loads(allowances_path(user_store).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    allowed = data.get("allowed") if isinstance(data, dict) else None
    if not isinstance(allowed, dict):
        return {}
    return {str(name): digest for name, digest in allowed.items()
            if isinstance(digest, str) and _aos.DIGEST_FORM.match(digest)}


def _key(user_store: "str | os.PathLike[str]", identity: str, store: "Optional[str | os.PathLike[str]]") -> str:
    """An allowance belongs to an overlay of one store: a project's overlay of the same name is
    another overlay."""
    where = os.path.normcase(os.path.abspath(str(store if store is not None else user_store)))
    return "%s|%s" % (where, _aos.normalize_name(identity))


def allowed_digest(user_store: "str | os.PathLike[str]", identity: str,
                   store: "Optional[str | os.PathLike[str]]" = None) -> "Optional[str]":
    return read_allowances(user_store).get(_key(user_store, identity, store))


def read_revoked(user_store: "str | os.PathLike[str]") -> "set[str]":
    """The overlays (by store and name) whose hooks the user turned off or declined at install: a
    later sync does not allow them again; `allow-hooks` or a fresh `overlays add` does."""
    try:
        data = json.loads(allowances_path(user_store).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    revoked = data.get("revoked") if isinstance(data, dict) else None
    return {str(item) for item in revoked} if isinstance(revoked, list) else set()


def _write_allowances(user_store: "str | os.PathLike[str]", allowed: "dict[str, str]",
                      revoked: "Optional[set[str]]" = None) -> None:
    from dotagents._fs import write_text_lf

    path = allowances_path(user_store)
    path.parent.mkdir(parents=True, exist_ok=True)
    if revoked is None:
        revoked = read_revoked(user_store)
    write_text_lf(path, json.dumps({"allowed": dict(sorted(allowed.items())), "revoked": sorted(revoked)},
                                   indent=2) + "\n", atomic=True)


def is_revoked(user_store: "str | os.PathLike[str]", identity: str,
               store: "Optional[str | os.PathLike[str]]" = None) -> bool:
    return _key(user_store, identity, store) in read_revoked(user_store)


def allow(user_store: "str | os.PathLike[str]", identity: str, digest: str,
          store: "Optional[str | os.PathLike[str]]" = None) -> None:
    """Allow ``identity``'s hooks (of the overlay installed in ``store``, default the user store) at
    ``digest``, replacing the digest allowed before: there is one."""
    allowed = read_allowances(user_store)
    key = _key(user_store, identity, store)
    allowed[key] = digest.lower()
    _write_allowances(user_store, allowed, read_revoked(user_store) - {key})


def revoke(user_store: "str | os.PathLike[str]", identity: str,
           store: "Optional[str | os.PathLike[str]]" = None) -> bool:
    """Turn the overlay's hooks off and keep them off across syncs. True when that changed anything."""
    allowed = read_allowances(user_store)
    key = _key(user_store, identity, store)
    revoked = read_revoked(user_store)
    if allowed.pop(key, None) is None and key in revoked:
        return False
    _write_allowances(user_store, allowed, revoked | {key})
    return True


def mcp_settings_path(user_store: "str | os.PathLike[str]") -> Path:
    return Path(user_store) / "overlays" / ".dotagents-state" / "mcp.json"


def mcp_strict_setting(user_store: "str | os.PathLike[str]") -> bool:
    """The user's own setting ``{"strict": true}`` for the MCP server, read from the user store only
    (never from a project, whose MCP configuration an agent can write)."""
    try:
        data = json.loads(mcp_settings_path(user_store).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("strict") is True


#: Set by `dotagents mcp --strict` (or the user's setting): installs through this server allow no hooks.
_MCP_STRICT: "list[bool]" = []


def strict_mcp(user_store: "str | os.PathLike[str]") -> bool:
    """Whether this process serves MCP tools in strict mode, chosen when the server was started: an
    add or sync through it installs without allowing hooks, as ``--no-hooks`` does. The default server
    behaves as the command line does."""
    return bool(_MCP_STRICT) or (bool(os.environ.get("DOTAGENTS_MCP")) and mcp_strict_setting(user_store))


def record_install(user_store: "str | os.PathLike[str]", overlay: Any, *, fresh: bool, no_hooks: bool,
                   dry_run: bool = False) -> "tuple[str, list[dict[str, Any]]]":
    """What installing or syncing ``overlay`` (the installed copy) does to its hooks' allowance.
    Returns ``(outcome, entries)``: ``none`` (it has no hooks), or ``on`` (allowed at the digest it
    holds, which is the digest installed), ``declined`` (``--no-hooks``: recorded so a later sync
    does not allow them), ``strict`` (installed through a strict MCP server: nothing is allowed or recorded),
    ``revoked`` (turned off earlier and kept off by a sync). A fresh add allows, and forgets an
    earlier revoke. With ``dry_run`` nothing is written."""
    plan = overlay_plan(overlay, ANYWHERE, user_store)
    entries = plan["hooks"]
    if not entries:
        return "none", []
    key = _key(user_store, overlay.name, overlay.store)
    if strict_mcp(user_store) and not no_hooks:
        # Installs without allowing and writes nothing: what was allowed stays allowed at its own digest.
        return "strict", entries
    if no_hooks:
        if not dry_run and plan["digest"] and key not in read_revoked(user_store):
            allowed = read_allowances(user_store)
            allowed.pop(key, None)
            _write_allowances(user_store, allowed, read_revoked(user_store) | {key})
        return "declined", entries
    if not fresh and key in read_revoked(user_store):
        return "revoked", entries
    if plan["digest"] is None:
        return "declined", entries
    if not dry_run:
        allow(user_store, overlay.name, plan["digest"], overlay.store)
    return "on", entries



# --------------------------------------------------------------------------- #
# What an overlay's hooks are on this machine, for a harness.
# --------------------------------------------------------------------------- #


def read_tree(overlay: Any, keep: bool = False) -> "tuple[Optional[str], dict[str, bytes]]":
    """``(digest, files)`` of the overlay, read from its files now: an allowance is checked each
    time, and a process that lives on must not trust an earlier reading. With ``keep``, ``files``
    holds the bytes the digest was computed over, each file read once; otherwise it is empty. A
    tree with no digest (a link, a special file, a refused name) is ``(None, {})``."""
    try:
        tree, digest = _aos.read_tree(overlay.path)
    except (coqui_aos.Error, OSError):
        return None, {}
    return digest, ({path: tree.bytes(path) for path in tree.paths} if keep else {})


def fresh_digest(overlay: Any) -> "Optional[str]":
    return read_tree(overlay)[0]


def overlay_plan(overlay: Any, harness: Harness, user_store: "str | os.PathLike[str]",
                 keep_tree: bool = False) -> "dict[str, Any]":
    """The :func:`dotagents._aos.hook_plan` of ``overlay`` here, on ``harness``, plus its digest.

    Hooks are on only while the overlay's tree digest is the one the user allowed; an overlay
    whose tree has no digest has none, so none is allowed. With ``keep_tree`` the bytes the digest
    covers are written to a snapshot first (:func:`snapshot`) and the plan is made from the
    snapshot's manifest, not from a second reading of the live one: ``plan["snap"]`` is the
    :class:`SnapshotOverlay` everything that runs (env scripts, paths, hooks) is resolved against."""
    from dotagents import _activate

    digest, files = read_tree(overlay, keep_tree)
    doc, members, snap = None, None, None
    if keep_tree and digest is not None:
        snap = SnapshotOverlay(snapshot(user_store, overlay, digest, files), overlay)
        doc, members = snap.document(), _activate.files(snap)
    plan = _aos.hook_plan(overlay.document() if doc is None else doc, {
        "os": _aos.current_os(), "interpreters": sorted(_activate.interpreters()),
        "files": _activate.files(overlay) if members is None else members, "identity": overlay.name,
        "digest": digest, "allowed": allowed_digest(user_store, overlay.name, overlay.store),
        "fires": harness.fires, "partial": partial_events(harness),
        "limits": {"timeout": limits_for(user_store)["timeout"]},
    })
    plan["digest"] = digest
    plan["identity"] = overlay.name
    if snap is not None:
        plan["snap"] = snap
    return plan


class SnapshotOverlay(Overlay):
    """An overlay read from its snapshot: its manifest, files, paths and scripts are the hashed
    bytes. Only what must outlive a snapshot -- its state directory, its install and preparation
    records -- is the live overlay's."""

    __slots__ = ("live",)

    def __init__(self, path: "str | os.PathLike[str]", live: Any):
        super().__init__(path, live.store)
        self.live = live

    def state_dir(self, os_name: "Optional[str]" = None) -> Path:
        return self.live.state_dir(os_name)

    def preparation_path(self, os_name: "Optional[str]" = None) -> Path:
        return self.live.preparation_path(os_name)

    @property
    def install_record_path(self) -> Path:
        return self.live.install_record_path


#: A snapshot nobody has used for this long is removed; one of another digest may still be in use
#: by a session that started before an update, so nothing is removed because it is not the latest.
SNAPSHOT_MAX_AGE = 7 * 24 * 3600


def snapshot(user_store: "str | os.PathLike[str]", overlay: Any, digest: str, files: "dict[str, bytes]") -> Path:
    """The copy of an overlay a hook runs from: exactly the bytes whose digest was computed, in the
    user store (outside every project), one directory per digest. An existing copy is used only
    after its own contents are hashed again and found to be that digest; otherwise it is made
    again. What runs is what was hashed, whatever happens to the overlay's own files afterwards.
    Copies of other digests are left for the age-based sweep: a session started before an update
    still runs from its own."""
    where = hashlib.sha256(os.path.normcase(os.path.abspath(str(overlay.store or user_store))).encode()).hexdigest()[:8]
    base = Path(user_store) / ".cache" / "hook-snapshots" / ("%s-%s" % (where, overlay.normalized_name))
    holder = base / digest.split(":", 1)[-1][:32]
    target = holder / overlay.path.name
    if target.is_dir():
        try:
            if _aos.same_digest(_aos.tree_digest(target), digest):
                os.utime(str(holder), None)
                return target
        except (coqui_aos.Error, OSError, ValueError):
            pass
        shutil.rmtree(str(holder), ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)
    staging = base / (".new-%d-%s" % (os.getpid(), _token()))
    try:
        root = staging / overlay.path.name
        root.mkdir(parents=True)
        for rel, data in files.items():
            path = root.joinpath(*rel.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(str(path), "wb") as fh:
                fh.write(data)
        try:
            os.replace(str(staging), str(holder))
        except OSError:
            if not target.is_dir():
                raise
    finally:
        shutil.rmtree(str(staging), ignore_errors=True)
    now = time.time()
    for other in base.iterdir():
        try:
            if other != holder and now - other.stat().st_mtime > SNAPSHOT_MAX_AGE:
                shutil.rmtree(str(other), ignore_errors=True)
        except OSError:
            pass
    return target


def _token() -> str:
    import secrets

    return secrets.token_hex(4)


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
#: The script a harness's hook runs; it finds `dotagents` and runs `dotagents hook`.
FORWARDER = "overlay_hook.py"


def wanted_events(store: "str | os.PathLike[str]", harness: Harness) -> "dict[str, bool]":  # noqa: D401
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


def sh_quote(text: str) -> str:
    """``text`` as one word for a POSIX shell: single quotes, so nothing in it is expanded."""
    return "'" + text.replace("'", "'\\''") + "'"


def ps_quote(text: str) -> str:
    """``text`` as one string for PowerShell: single quotes, so nothing in it is expanded."""
    return "'" + text.replace("'", "''") + "'"


def level_token(dest: "str | os.PathLike[str]", user_store: "str | os.PathLike[str]") -> str:
    """Names the store a hook entry is for: ``user``, or ``project-`` and a hash of the project's
    store. The entry text holds it and nothing else that changes (a guard is read from the
    forwarder's record, so the text stays the same when one comes or goes)."""
    def norm(path: "str | os.PathLike[str]") -> str:
        return os.path.normcase(os.path.abspath(str(path)))

    if norm(dest) == norm(user_store):
        return "user"
    return "project-" + hashlib.sha1(norm(dest).encode("utf-8", "replace")).hexdigest()[:10]


def code_root() -> str:
    """Where `dotagents` is imported from, recorded for the forwarder: the `.pyz`, or the directory
    holding the package."""
    from dotagents._resources import pyz_archive

    return pyz_archive() or str(Path(__file__).resolve().parent.parent)


def record_path(config_dir: "str | os.PathLike[str]", token: str) -> Path:
    if not valid_token(token):
        raise ValueError("not a store token: %r" % (token,))
    return Path(config_dir) / "hooks" / ("overlay_hook.%s.json" % token)


def forwarder_record(user_store: "str | os.PathLike[str]", guards: "Iterable[str]") -> "dict[str, Any]":
    """What the forwarder needs, recorded when the entry is wired: the interpreter that runs it,
    the place `dotagents` is imported from, the user store, the deadline, and the events that have a
    guard (it fails closed for those)."""
    import sys

    return {"python": os.path.abspath(sys.executable), "code_root": code_root(), "store": str(user_store),
            "deadline": deadline_for(user_store), "guards": sorted(guards)}


def write_record(config_dir: "str | os.PathLike[str]", token: str, record: "dict[str, Any]") -> bool:
    """Write the forwarder's record; True when it changed."""
    from dotagents._fs import write_text_lf

    path = record_path(config_dir, token)
    text = json.dumps(record, indent=2) + "\n"
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(path, text, atomic=True)
    return True


def remove_record(config_dir: "str | os.PathLike[str]", token: str) -> bool:
    try:
        record_path(config_dir, token).unlink()
    except OSError:
        return False
    return True


def interpreter_problem(config_dir: "str | os.PathLike[str]", token: str) -> "Optional[str]":
    """Why the entry for ``token`` would not run: its record is unreadable, or the interpreter or the
    place `dotagents` is imported from is gone (None when nothing was wired). The entry cannot say
    so itself when the interpreter is gone, so `overlays hooks` looks for it, and every wiring
    writes the record again."""
    if not record_path(config_dir, token).is_file():
        return None
    try:
        record = json.loads(record_path(config_dir, token).read_text(encoding="utf-8"))
        python, root = record["python"], record["code_root"]
    except (OSError, ValueError, KeyError, TypeError):
        return "its record is unreadable"
    if not os.path.isfile(python):
        return "its interpreter %s is gone" % python
    if not os.path.exists(root):
        return "dotagents is no longer at %s" % root
    return None


# --------------------------------------------------------------------------- #
# Reports.
# --------------------------------------------------------------------------- #


def describe(report: "Iterable[str]") -> str:
    """One report as a line: the id and its subjects. It never quotes a hook's output."""
    return " ".join(str(part) for part in report)


# --------------------------------------------------------------------------- #
# Running one hook.
# --------------------------------------------------------------------------- #


#: Runs a Python hook as a script would run, under ``-I``: argv is ``[libs, script, event]``.
PYTHON_BOOT = ("import os, runpy, sys; libs, script = sys.argv[1], sys.argv[2]; sys.argv = sys.argv[2:]; "
               "sys.path[:0] = [os.path.dirname(script)] + [p for p in libs.split(os.pathsep) if p]; "
               "runpy.run_path(script, run_name='__main__')")


class Subject(object):
    """The overlay and entry one hook run is for."""

    def __init__(self, overlay: Any, entry: "dict[str, Any]"):
        self.overlay = overlay
        self.entry = entry


def execute(subject: Subject, event: str, payload: bytes, env: "dict[str, str]", *,
            limits: "Optional[dict[str, int]]" = None, root: "Optional[str | os.PathLike[str]]" = None,
            deadline: "Optional[float]" = None, base_path: "Optional[str]" = None) -> "dict[str, Any]":
    """Start the hook and give its run in the model's terms (:func:`coqui_aos.hook_result`).

    The program is an interpreter followed by the script's absolute path and the event, an argv
    list that no shell reads; its working directory is ``root`` (the overlay's snapshot, else the
    overlay's own directory); ``env`` is exactly its environment, plus ``AOS_HOOK_EVENT``. The
    script must be a regular file inside the root. ``deadline`` is a :func:`time.monotonic` time
    the run may not pass: a hook that would start past it, or run past it, is stopped."""
    from dotagents import _run
    from dotagents._env import _spawn_env

    limits = dict(LIMITS, **(limits or {}))
    entry = subject.entry
    run: "dict[str, Any]" = {"payload_size": len(payload), "sets": list(entry["sets"])}
    root = os.path.abspath(str(root if root is not None else subject.overlay.path))
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
                                        base_path=os.environ.get("PATH") if base_path is None else base_path,
                                        trusted=base_path is not None)
    except _run.InterpreterError:
        run["not_run"] = True
        return run
    if len(payload) > limits["max_payload"]:
        return run
    child = _spawn_env(dict(env))
    child["AOS_HOOK_EVENT"] = event
    timeout = entry["timeout"]
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            run["timed_out"] = True
            return run
        timeout = min(timeout, remaining)
    if entry["interpreter"] == "python":
        # Isolated (no user site, no PYTHON* variables, no working directory on the path), with the
        # script's directory and the libraries the environment names put on the path explicitly.
        command = argv + ["-I", "-c", PYTHON_BOOT, child.get("PYTHONPATH", ""), script, event]
    else:
        command = argv + [script, event]
    done = _run.run_hook(command, cwd=root, env=child, timeout=timeout, stdin=payload,
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

    def __init__(self, result: "dict[str, Any]", reports: "list[list[str]]",
                 errors: "Optional[list[tuple[str, str, str]]]" = None):
        self.result = result
        self.reports = reports + [list(r) for r in result["reports"]]
        #: ``(overlay, event, text)``: what each hook wrote to standard error, for the user's log.
        self.errors = errors or []

    @property
    def decision(self) -> "Optional[str]":
        return self.result["decision"]


def session_id(harness: str, native_id: str) -> str:
    """The consumer's own id for a session: the same for each of its events, and no secret
    (never the harness's id or the name of its transcript)."""
    digest = hashlib.sha256(("%s\0%s" % (harness, native_id)).encode("utf-8", "replace")).hexdigest()
    return "dotagents-" + digest[:16]


def value_for_match(event: str, facts: "dict[str, Any]") -> "Optional[str]":
    kind = coqui_aos.HookEvent(event).selects
    if kind == "reason":
        return facts.get("reason")
    if kind == "kind":
        tool = facts.get("tool")
        return tool.get("kind") if isinstance(tool, dict) else None
    return None


def fire(event: str, facts: "dict[str, Any]", plans: "list[tuple[Any, dict[str, Any]]]", harness: Harness, *,
         environment: "Callable[[Any], Optional[dict[str, str]]]", limits: "Optional[dict[str, int]]" = None,
         user_store: "Optional[str | os.PathLike[str]]" = None, deadline: "Optional[float]" = None,
         base_path: "Optional[str]" = None) -> Fired:
    """Fire ``event`` for ``plans``, ``(overlay, plan)`` in composition order.

    Only an overlay whose hooks are on runs, and it runs from the snapshot of the bytes whose digest
    was allowed (the plan must have been made with ``keep_tree``; ``user_store`` says where the
    snapshots are kept). A guard (``on_failure = "refuse"``) whose hooks are off is a failed guard:
    the event is refused as a failure of it would refuse it, and ``hook-not-allowed`` is reported.
    Each hook is started when its turn comes, and not after an earlier one denied, blocked or used up
    the event's time. ``environment(overlay)`` is the environment of that overlay's own processes
    (None: it cannot be built, the hook fails as ``not-run``)."""
    reports: "list[list[str]]" = []
    errors: "list[tuple[str, str, str]]" = []
    limits = dict(LIMITS, **(limits or {}))
    runs: "list[dict[str, Any]]" = []
    matching = value_for_match(event, facts)
    model = coqui_aos.HookEvent(event)
    payload = json.dumps(coqui_aos.hook_payload(model, facts), ensure_ascii=False).encode("utf-8")
    for overlay, plan in plans:
        entries = [entry for entry in plan["hooks"] if entry["event"] == event]
        if not entries:
            continue
        if event not in harness.fires:
            continue
        if not plan["on"]:
            for entry in entries:
                if entry["on_failure"] == "refuse" and (
                        matching is None or coqui_aos.hook_matches(model, entry["match"], matching)):
                    # A guard that is off is a failed guard, not a missing one.
                    runs.append({"source": overlay.name, "on_failure": "refuse", "sets": [],
                                 "start": lambda: {"not_run": True}})
                    reports.append(["hook-not-allowed", overlay.name])
            continue
        snap_overlay = plan.get("snap")
        snap = snap_overlay.path if snap_overlay is not None else None
        for entry in entries:
            if matching is not None and not coqui_aos.hook_matches(model, entry["match"], matching):
                continue
            subject = Subject(overlay, entry)

            def start(subject=subject, snap=snap, snap_overlay=snap_overlay) -> "dict[str, Any]":
                env = environment(snap_overlay if snap_overlay is not None else subject.overlay)
                if env is None:
                    return {"not_run": True}
                done = execute(subject, event, payload, env, limits=limits, root=snap, deadline=deadline,
                               base_path=base_path)
                text = done.pop("stderr", b"")
                if text:
                    errors.append((subject.overlay.name, event, printable(text)))
                return done

            runs.append({"source": overlay.name, "on_failure": entry["on_failure"], "start": start,
                         "sets": entry["sets"]})
    applies = harness.applies.get(event, frozenset())
    result = _aos.fire_event(event, runs, applies, dict(LIMITS, **(limits or {})), os.name == "nt")
    return Fired(result, reports, errors)


#: Names the harness sets for itself (its env file, its credentials): not for an overlay's code.
_HARNESS_INTERNAL = ("CLAUDE_ENV_FILE",)
_HARNESS_INTERNAL_PREFIXES = ("CLAUDE_CODE_", "CODEX_")


#: The project's name for itself stays in a hook's environment: a hook that acts on the project
#: (private-sync) takes it from there and not from a working directory the agent chose.
KEEP_PROJECT = ("AGENTS_PROJECT_ROOT", "CLAUDE_PROJECT_DIR")


def _shown(path: "str | os.PathLike[str]") -> str:
    return os.path.normcase(os.path.abspath(str(path))).replace("\\", "/").rstrip("/")


def project_roots(environ: "dict[str, str]", cwd: "Optional[str]" = None) -> "list[str]":
    """Where the agent works: what the harness calls the project, the directory holding the
    `.agents` or `.git` found walking up from ``cwd`` (default: the working directory), and ``cwd``
    itself. Anything below these is the agent's to write."""
    roots = [environ[name] for name in KEEP_PROJECT if environ.get(name)]
    here = os.path.abspath(cwd or os.getcwd())
    roots.append(here)
    walk = here
    while True:
        if os.path.exists(os.path.join(walk, ".agents")) or os.path.exists(os.path.join(walk, ".git")):
            roots.append(walk)
            break
        parent = os.path.dirname(walk)
        if parent == walk:
            break
        walk = parent
    return roots


def clean_path(path: "Optional[str]", user_store: "str | os.PathLike[str]",
               roots: "Iterable[str]" = ()) -> str:
    """``path`` with the entries removed that the agent can put an interpreter in: relative ones
    (the working directory), anything below the user store or a project root (``roots``: its
    ``.venv/Scripts``, ``node_modules/.bin``, ``.agents/bin``), any store's or overlay's ``bin``
    and a venv's. A root that is the home directory, a drive's root or holds the user store is not
    used (it holds the user's own tools). Interpreters of hooks and env scripts (``pwsh``, ``node``) are found on
    this, never on the PATH a project's ``.agents/bin`` leads."""
    home = _shown(Path.home())
    store = _shown(user_store)
    barred = [store]
    for root in roots:
        shown = _shown(root)
        # Not the home directory, a drive's root, or anything holding the user store: those hold the
        # user's own tools, and the store is barred by itself.
        if (shown and shown != home and shown.count("/") >= 2 and not shown.endswith(":")
                and not (store + "/").startswith(shown + "/")):
            barred.append(shown)
    keep = []
    for entry in (path or "").split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            continue
        shown = _shown(entry)
        if any(shown == bar or shown.startswith(bar + "/") for bar in barred):
            continue
        if shown.endswith("/.agents/bin") or "/.pyvenv/" in shown + "/" or (
                "/overlays/" in shown and shown.endswith("/bin")):
            continue
        keep.append(entry)
    return os.pathsep.join(keep)


def shadowed_with_hooks(scope: Any) -> "list[Any]":
    """Overlays of the user and system stores that have a hook and are hidden by a same-named
    overlay of a nearer store (a project's): the hook is the user's, and a project folder of the
    same name must not take it away."""
    from dotagents import _activate
    from dotagents._overlays import Overlay

    shown = {overlay.path for overlay in scope.installed_overlays}
    interpreters = sorted(_activate.interpreters())
    hidden = []
    for store in scope.stores:
        if scope.project_store is not None and os.path.normcase(str(store)) == os.path.normcase(str(scope.project_store)):
            continue
        for overlay in Overlay.discover(Path(store) / "overlays", store):
            if overlay.path in shown:
                continue
            entries, _ = _aos.hooks_of(overlay.document(), _activate.files(overlay), _aos.current_os(), interpreters)
            if entries:
                hidden.append(overlay)
    return hidden


def session_base(environ: "dict[str, str]") -> "dict[str, str]":
    """The environment a hook starts from: the one the session was started from, not the session's.

    ``dotagents launch`` records, in ``AGENTS_SESSION_BASE``, what each variable it changed held
    before (null: it was not set), so another overlay's secret, which that session carries to every
    process in it, is not handed to this overlay's hook. The names a harness keeps for itself
    (``CLAUDE_ENV_FILE``, ``CLAUDE_CODE_*``, ``CODEX_*``) are dropped as well."""
    env = dict(environ)
    windows = os.name == "nt"
    raw = env.pop("AGENTS_SESSION_BASE", None)
    changed = {}
    if raw:
        try:
            data = json.loads(raw)
        except ValueError:
            data = {}
        if isinstance(data, dict):
            changed = {str(k): v for k, v in data.items() if v is None or isinstance(v, str)}
    for name, before in changed.items():
        if name.upper() in KEEP_PROJECT:
            continue
        for key in [k for k in env if (k.upper() == name.upper() if windows else k == name)]:
            del env[key]
        if before is not None:
            env[name] = before
    for key in list(env):
        upper = key.upper()
        if upper in _HARNESS_INTERNAL or upper.startswith(_HARNESS_INTERNAL_PREFIXES):
            del env[key]
    return env


def log_path(user_store: "str | os.PathLike[str]") -> Path:
    return Path(user_store) / ".cache" / "hooks.log"


#: The log is cut to its last half when it passes this.
LOG_LIMIT = 256 * 1024


def log_errors(user_store: "str | os.PathLike[str]", errors: "Iterable[tuple[str, str, str]]") -> None:
    """Append what hooks wrote to standard error to the user's log (never to the harness, where it
    could reach the agent), bounded. Best effort: a log that cannot be written is not a failure."""
    lines = ["%s %s %s: %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), overlay, event, text.strip().replace("\n", " | "))
             for overlay, event, text in errors if text.strip()]
    if not lines:
        return
    path = log_path(user_store)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > LOG_LIMIT:
            keep = path.read_bytes()[-LOG_LIMIT // 2:]
            path.write_bytes(keep[keep.find(b"\n") + 1:])
        with open(str(path), "a", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass


def printable(data: bytes, bound: int = 4096) -> str:
    """A hook's standard error as text fit for a person's log: no control character but the line end
    and the tab (a terminal escape would be run by the terminal), and no more than ``bound``."""
    text = data[:bound].decode("utf-8", "replace")
    return "".join(c if c in "\n\t" or (c.isprintable() and c not in "\u202a\u202b\u202c\u202d\u202e"
                                         "\u2066\u2067\u2068\u2069") else "?" for c in text)


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
            keep_tree: bool = False) -> "list[tuple[Any, dict[str, Any]]]":
    """``(overlay, plan)`` for each overlay that has a hook of any event, in the order given."""
    from dotagents import _activate

    out = []
    interpreters = sorted(_activate.interpreters())
    for overlay in overlays:
        entries, _ = _aos.hooks_of(overlay.document(), _activate.files(overlay), _aos.current_os(), interpreters)
        if entries:
            out.append((overlay, overlay_plan(overlay, harness, user_store, keep_tree)))
    return out


def pre_env(plans: "list[tuple[Any, dict[str, Any]]]", session: "_aos.Environment", base: "dict[str, str]",
            harness: Harness, *, cwd: "Optional[str]" = None, user_store: "Optional[str | os.PathLike[str]]" = None,
            ) -> "tuple[list[list[str]], list[list[str]]]":
    """Fire ``pre-env`` for the overlays in ``plans`` (composition order) and apply their output to
    ``session``, after the root variables and before any overlay's declarations. Each hook runs in
    ``base`` (the base environment and the root variables) plus its own overlay's ``AOS_OVERLAY_*``.
    Returns the reports and the non-default settings; raises :class:`dotagents._aos.Refused`
    (``hook-refused``) when a guard fails."""
    from dotagents import _activate

    deadline = time.monotonic() + (deadline_for(user_store) if user_store is not None else DEFAULT_DEADLINE) - 5
    base_path = (clean_path(session_base(base).get("PATH"), user_store, project_roots(session_base(base), cwd))
                 if user_store is not None else None)
    payload = json.dumps(
        coqui_aos.hook_payload(coqui_aos.HookEvent.PRE_ENV, {"cwd": os.path.abspath(cwd or os.getcwd()),
                                                             "harness": harness.id or NO_HARNESS}),
        ensure_ascii=False).encode("utf-8")
    runs: "dict[str, dict[str, Any]]" = {}
    identities: "list[str]" = []
    for overlay, plan in plans:
        entries = [entry for entry in plan["hooks"] if entry["event"] == "pre-env"]
        if not entries or not plan["on"]:
            continue
        entry = entries[0]
        env = session_base(base)
        snap = plan["snap"].path if plan.get("snap") is not None else None
        if snap is not None:
            env[overlay.root_var] = str(snap)
        env["AOS_OVERLAY_ROOT"] = str(snap) if snap is not None else os.path.abspath(str(overlay.path))
        env["AOS_OVERLAY_NAME"] = overlay.normalized_name
        state = _activate.state_dir(overlay)
        if state is not None:
            state.mkdir(parents=True, exist_ok=True)
            env["AOS_OVERLAY_STATE"] = str(state)
        subject = Subject(overlay, entry)

        def start(subject=subject, env=env, snap=snap) -> "dict[str, Any]":
            return execute(subject, "pre-env", payload, env, root=snap, deadline=deadline, base_path=base_path,
                           limits=limits_for(user_store) if user_store is not None else None)

        identities.append(overlay.name)
        runs[overlay.name] = {"on_failure": entry["on_failure"], "start": start, "sets": []}
    reports: "list[list[str]]" = []
    non_defaults: "list[list[str]]" = []
    _aos.apply_pre_env(session, identities, runs, limits_for(user_store) if user_store is not None else LIMITS,
                       reports, non_defaults)
    return reports, non_defaults


def has_live_pre_env(scope: Any, harness_id: "Optional[str]") -> bool:
    """Whether an allowed overlay has a ``pre-env`` hook: it fires each time the environment is
    built, so a cached rendering of an earlier build cannot stand in for the build."""
    harness = harness_of(harness_id)
    return any(plan["on"] and any(entry["event"] == "pre-env" for entry in plan["hooks"])
               for _overlay, plan in planned(scope.overlays, harness, scope.user_root))
