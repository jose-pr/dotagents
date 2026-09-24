"""Merge dotagents' hooks into an agent's ``settings.json`` without disturbing it.

An agent's settings file belongs to the **user**: it may carry unrelated keys and
hooks we did not write, in shapes we did not choose. So every function here is
additive and defensive -- foreign entries survive verbatim, malformed ones are
coerced or dropped rather than raising, and re-running is a no-op.

Idempotence is the property that matters: ``dotagents init`` is re-run often, and a
merge that appended a duplicate hook each time would quietly grow the file until
the agent ran our command N times per session.

The schema (Claude Code's; Codex's is the same shape) is::

    hooks: { "<Event>": [ { "matcher"?: str,
                            "hooks": [ {"type": "command", "command": str,
                                        "statusMessage"?: str} ] } ] }

``hooks.<Event>`` is a **list of matcher-objects**, each holding its own ``hooks``
list -- not a flat list of commands. ``statusMessage`` is a supported key.

Pure stdlib (``json``/``pathlib``) so it works in a plain ``pip install`` and inside
the ``.pyz``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


#: Our hooks' statusMessage is `STATUS_PREFIX + label`, so a foreign hook that
#: happens to use the same generic label ("Loading agent context") is never
#: taken for ours. The bare label an earlier release wrote is still ours when
#: the hook's command runs dotagents, so those hooks converge to the new one.
STATUS_PREFIX = "dotagents: "

#: The hook keys dotagents manages; any other key a user added (a `timeout`,
#: say) survives a refresh.
_MANAGED_KEYS = frozenset({"type", "command", "shell", "commandWindows", "statusMessage"})


def _labels(status_message: "Optional[str]") -> "tuple[str, ...]":
    if not status_message:
        return ()
    bare = status_message[len(STATUS_PREFIX):] if status_message.startswith(STATUS_PREFIX) else status_message
    return (STATUS_PREFIX + bare, bare)


def _labelled_ours(hook: Any, status_message: "Optional[str]") -> bool:
    """``hook`` carries our label: the namespaced one, or the bare one on a
    command that runs dotagents (what an earlier release wrote)."""
    if not isinstance(hook, dict) or not status_message:
        return False
    labels = _labels(status_message)
    status = hook.get("statusMessage")
    if status == labels[0]:
        return True
    return status == labels[1] and "dotagents" in str(hook.get("command", ""))


def build_hook_entry(
    command: str,
    *,
    matcher: "Optional[str]" = None,
    status_message: "Optional[str]" = None,
    shell: "Optional[str]" = None,
    command_windows: "Optional[str]" = None,
) -> "dict[str, Any]":
    """One matcher-object wrapping a single ``type: command`` hook.

    ``matcher``/``statusMessage``/``shell``/``command_windows`` are omitted
    entirely when None rather than written as null -- an absent key is the
    documented "no matcher" / "default shell" form. ``shell`` (Claude) selects
    the interpreter for THIS hook's own ``command`` (``"bash"`` or
    ``"powershell"``); ``command_windows`` (Codex, emitted as ``commandWindows``)
    is a separate Windows-only command that Codex substitutes for ``command``
    on Windows.
    """
    hook: "dict[str, Any]" = {"type": "command", "command": command}
    if shell:
        hook["shell"] = shell
    if command_windows:
        hook["commandWindows"] = command_windows
    if status_message:
        hook["statusMessage"] = _labels(status_message)[0]
    entry: "dict[str, Any]" = {}
    if matcher is not None:
        entry["matcher"] = matcher
    entry["hooks"] = [hook]
    return entry


def _has_status(entry: Any, status_message: str) -> bool:
    """True if `entry` carries a hook stamped with our `statusMessage`.

    The status message is a stable label we choose, so it identifies our hook
    across revisions of the command text.
    """
    if not isinstance(entry, dict):
        return False
    nested = entry.get("hooks")
    if not isinstance(nested, list):
        return False
    return any(_labelled_ours(h, status_message) for h in nested)


def _is_ours(entry: Any, command: str) -> bool:
    """True if `entry` is a well-formed matcher-object containing our command."""
    if not isinstance(entry, dict):
        return False
    nested = entry.get("hooks")
    if not isinstance(nested, list):
        return False
    return any(
        isinstance(h, dict) and h.get("command") == command for h in nested
    )


def merge_hook(
    existing: Any,
    command: str,
    *,
    matcher: "Optional[str]" = None,
    status_message: "Optional[str]" = None,
    shell: "Optional[str]" = None,
    command_windows: "Optional[str]" = None,
) -> "tuple[list, bool]":
    """Fold our hook into `existing`, returning ``(normalized_list, changed)``.

    Rules, in order:

    * absent / not-a-list ``existing`` -> a fresh single-entry list (changed).
    * an entry that is exactly what we would write -> kept as-is (unchanged);
      one carrying our hook in a different shape (``shell`` / ``matcher`` /
      ``commandWindows`` / status, or a different command text under the same
      status message) -> replaced in place; any later duplicate is dropped
      (changed), so repeated ``init`` runs converge instead of accumulating.
    * a matcher-object holding a foreign hook AND ours -> the foreign hook stays
      verbatim in that entry, ours moves to its own entry.
    * foreign entries -> preserved verbatim, untouched, in their original order.
    * malformed entries (bare strings, dicts without a ``hooks`` list, non-dicts)
      -> kept verbatim: they are the user's, and deleting them silently lost
      whatever they were meant to be. Never raises: a user's hand-edited
      settings file must not make ``init`` explode.

    ``status_message`` doubles as our hook's IDENTITY, written namespaced
    (:data:`STATUS_PREFIX`); a hook carrying that status message, or the bare
    label an earlier release wrote on a command that runs dotagents, is ours
    and gets refreshed, so revising the
    command text never leaves the previous version running beside the new one.
    A refresh rewrites only the keys dotagents manages: a key the user added to
    our hook or its entry stays.
    """
    ours = build_hook_entry(
        command, matcher=matcher, status_message=status_message, shell=shell,
        command_windows=command_windows,
    )
    if not isinstance(existing, list):
        # None/absent is the common case; a non-list is a malformed file we replace.
        return [ours], True

    def _mine(hook: Any) -> bool:
        return isinstance(hook, dict) and (
            hook.get("command") == command or _labelled_ours(hook, status_message)
        )

    def _refreshed(entry: dict) -> dict:
        old = next(h for h in entry["hooks"] if _mine(h))
        hook = {k: v for k, v in old.items() if k not in _MANAGED_KEYS}
        hook.update(ours["hooks"][0])
        new = {k: v for k, v in entry.items() if k not in ("matcher", "hooks")}
        if "matcher" in ours:
            new["matcher"] = ours["matcher"]
        new["hooks"] = [hook]
        return new

    normalized: list = []
    changed = False
    seen_ours = False

    for entry in existing:
        if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
            normalized.append(entry)  # malformed, but the user's: keep it
            continue
        inner = entry["hooks"]
        if not any(_mine(h) for h in inner):
            normalized.append(entry)  # foreign but well-formed: leave alone
            continue
        foreign = [h for h in inner if not _mine(h)]
        if foreign:
            # A matcher-object holding BOTH a foreign hook and ours: the foreign
            # sibling stays, verbatim, in its own entry; ours moves to (or is
            # refreshed in) our own entry below.
            kept = dict(entry)
            kept["hooks"] = foreign
            normalized.append(kept)
            changed = True
            continue
        if seen_ours:
            changed = True  # a duplicate of our own hook -- collapse it
            continue
        seen_ours = True
        # Ours, and only ours: keep it exactly when it already equals what we
        # would write, otherwise REPLACE it -- a changed `shell` / `matcher` /
        # `commandWindows` / status must land even when the command text is
        # unchanged.
        refreshed = _refreshed(entry)
        normalized.append(refreshed)
        if refreshed != entry:
            changed = True

    if not seen_ours:
        normalized.append(ours)
        changed = True

    return normalized, changed


def remove_hook(existing: Any, status_message: str) -> "tuple[list, bool]":
    """Retract our hook identified by ``status_message`` from ``existing`` --
    the inverse of :func:`merge_hook`, for a hook this platform does not
    register (a `shell: powershell` variant on a POSIX host). Returns
    ``(list, changed)``. A foreign hook sharing an entry with ours stays; an
    entry left empty is dropped; a non-list ``existing`` is ``([], False)``."""
    if not isinstance(existing, list):
        return [], False
    kept_entries, changed = [], False
    for entry in existing:
        if not _has_status(entry, status_message):
            kept_entries.append(entry)
            continue
        changed = True
        remaining = [h for h in entry["hooks"] if not _labelled_ours(h, status_message)]
        if remaining:
            kept_entries.append(dict(entry, hooks=remaining))
    return kept_entries, changed


def load_settings(path: Path) -> "dict[str, Any]":
    """Read a settings.json, tolerating absence but never corruption.

    A missing or empty file yields ``{}``. Invalid JSON raises ``SystemExit`` with
    the path and parse error: silently starting from ``{}`` there would overwrite a
    user's whole settings file on the next write. A UTF-8 byte-order mark (what
    Windows PowerShell 5's ``-Encoding UTF8`` writes) is accepted; the file is
    written back without one.
    """
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise SystemExit(
            "error: %s is not UTF-8 text (%s). Fix or move it, then re-run." % (path, exc)
        ) from exc
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            "error: %s is not valid JSON (%s). Fix or move it, then re-run." % (path, exc)
        ) from exc
    if not isinstance(data, dict):
        raise SystemExit("error: %s must contain a JSON object, got %s" % (path, type(data).__name__))
    return data


def write_settings(path: Path, data: "dict[str, Any]", *, dry_run: bool = False) -> None:
    """Write settings.json with a stable 2-space indent and trailing newline.

    LF-only and ATOMIC (temp file + ``os.replace``): the agent may read its own
    settings at any moment, and a half-written file is invalid JSON. Non-ASCII
    is kept as-is (``ensure_ascii=False``) rather than rewriting a user's own
    values as ``\\uXXXX`` escapes."""
    if dry_run:
        return
    from dotagents._fs import write_text_lf

    write_text_lf(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n", atomic=True)
