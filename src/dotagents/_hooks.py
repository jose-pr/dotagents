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
) -> "dict[str, Any]":
    """One matcher-object wrapping a single ``type: command`` hook.

    ``matcher``/``statusMessage``/``shell`` are omitted entirely when None rather
    than written as null -- an absent key is the documented "no matcher" /
    "default shell" form. ``shell`` selects the interpreter for THIS hook's own
    ``command`` (``"bash"`` or ``"powershell"``) -- unrelated to which tool the
    hook is matched against.
    """
    hook: "dict[str, Any]" = {"type": "command", "command": command}
    if shell:
        hook["shell"] = shell
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
) -> "tuple[list, bool]":
    """Fold our hook into `existing`, returning ``(normalized_list, changed)``.

    Rules, in order:

    * absent / not-a-list ``existing`` -> a fresh single-entry list (changed).
    * an entry already carrying our exact ``command`` -> kept as-is; the first
      such entry wins and any later duplicate is dropped (changed), so repeated
      ``init`` runs converge instead of accumulating.
    * foreign entries -> preserved verbatim, untouched, in their original order.
    * malformed entries (bare strings, dicts without a ``hooks`` list, non-dicts)
      -> dropped, flagged changed. Never raises: a user's hand-edited settings
      file must not make ``init`` explode.

    ``status_message`` doubles as our hook's IDENTITY. An entry carrying the same
    status message is treated as an older shape of our own hook and replaced, not
    left beside the new one. Without that, dedup is by exact command string, so
    revising the command we write silently orphans the previous version and the
    old (often broken) hook keeps running next to the new one. The status message
    is a stable label we choose, which makes it a better key than the command text
    it describes.
    """
    if not isinstance(existing, list):
        # None/absent is the common case; a non-list is a malformed file we replace.
        return [
            build_hook_entry(command, matcher=matcher, status_message=status_message, shell=shell)
        ], True

    normalized: list = []
    changed = False
    seen_ours = False

    for entry in existing:
        if _is_ours(entry, command):
            if seen_ours:
                changed = True  # collapse duplicates of our own command
                continue
            seen_ours = True
            normalized.append(entry)
            continue
        if status_message and _has_status(entry, status_message):
            changed = True  # an older shape of OUR hook -- replace, don't duplicate
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
            changed = True  # malformed -- drop it
            continue
        normalized.append(entry)  # foreign but well-formed: leave alone

    if not seen_ours:
        normalized.append(
            build_hook_entry(command, matcher=matcher, status_message=status_message, shell=shell)
        )
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
    """Write settings.json with a stable 2-space indent and trailing newline."""
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
