"""Merge dotagents' hooks into an agent's ``settings.json`` without disturbing it.

An agent's settings file belongs to the **user**: it may carry unrelated keys and
hooks we did not write, in shapes we did not choose. So every function here is
additive and defensive -- foreign entries survive verbatim, malformed ones are
coerced or dropped rather than raising, and re-running is a no-op.

Idempotence is the property that matters: ``dotagents init`` is re-run often, and a
merge that appended a duplicate hook each time would quietly grow the file until
the agent ran our command N times per session.

The schema (verified against current Claude Code docs, 2026-07-24) is::

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
    ``"powershell"``); ``command_windows`` (Codex, emitted as the camelCase
    ``commandWindows`` key its docs specify) is a separate Windows-only command
    OVERRIDE -- Codex runs ``command`` normally and substitutes
    ``commandWindows`` for it on Windows, rather than picking an interpreter for
    one shared string. Both are agent-specific vocabulary living in this one
    shared merge function rather than duplicated per adapter.
    """
    hook: "dict[str, Any]" = {"type": "command", "command": command}
    if shell:
        hook["shell"] = shell
    if command_windows:
        hook["commandWindows"] = command_windows
    if status_message:
        hook["statusMessage"] = status_message
    entry: "dict[str, Any]" = {}
    if matcher is not None:
        entry["matcher"] = matcher
    entry["hooks"] = [hook]
    return entry


def _has_status(entry: Any, status_message: str) -> bool:
    """True if `entry` carries a hook stamped with our `statusMessage`.

    The status message is a stable label we choose, so it identifies our hook
    across revisions of the command text -- unlike the command itself, which
    changes and would leave the old version orphaned beside the new one.
    """
    if not isinstance(entry, dict):
        return False
    nested = entry.get("hooks")
    if not isinstance(nested, list):
        return False
    return any(
        isinstance(h, dict) and h.get("statusMessage") == status_message
        for h in nested
    )


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
      one carrying our hook in an older shape (a revised ``shell`` / ``matcher``
      / ``commandWindows`` / status, or an older command text under the same
      status message) -> replaced in place; any later duplicate is dropped
      (changed), so repeated ``init`` runs converge instead of accumulating.
    * a matcher-object holding a foreign hook AND ours -> the foreign hook stays
      verbatim in that entry, ours moves to its own entry (never drop a user's
      hook because it happened to share an object with ours).
    * foreign entries -> preserved verbatim, untouched, in their original order.
    * malformed entries (bare strings, dicts without a ``hooks`` list, non-dicts)
      -> dropped, flagged changed. Never raises: a user's hand-edited settings
      file must not make ``init`` explode.

    ``status_message`` doubles as our hook's IDENTITY. A hook carrying the same
    status message is treated as an older shape of our own hook and replaced, not
    left beside the new one. Without that, dedup is by exact command string, so
    revising the command we write silently orphans the previous version and the
    old (often broken) hook keeps running next to the new one. The status message
    is a stable label we choose, which makes it a better key than the command text
    it describes.
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
            hook.get("command") == command
            or bool(status_message and hook.get("statusMessage") == status_message)
        )

    normalized: list = []
    changed = False
    seen_ours = False

    for entry in existing:
        if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
            changed = True  # malformed -- drop it
            continue
        inner = entry["hooks"]
        if not any(_mine(h) for h in inner):
            normalized.append(entry)  # foreign but well-formed: leave alone
            continue
        foreign = [h for h in inner if not _mine(h)]
        if foreign:
            # A matcher-object holding BOTH a foreign hook and ours: the foreign
            # sibling stays, verbatim, in its own entry; ours moves to (or is
            # refreshed in) our own entry below. Dropping the whole object took
            # the user's hook with it (review 2026-09-09).
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
        # would write, otherwise REPLACE it -- a revised `shell` / `matcher` /
        # `commandWindows` / status must reach existing users even when the
        # command text itself is unchanged.
        if entry == ours:
            normalized.append(entry)
        else:
            normalized.append(ours)
            changed = True

    if not seen_ours:
        normalized.append(ours)
        changed = True

    return normalized, changed


def load_settings(path: Path) -> "dict[str, Any]":
    """Read a settings.json, tolerating absence but never corruption.

    A missing or empty file yields ``{}``. Invalid JSON raises ``SystemExit`` with
    the path and parse error: silently starting from ``{}`` there would overwrite a
    user's whole settings file on the next write.
    """
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
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
