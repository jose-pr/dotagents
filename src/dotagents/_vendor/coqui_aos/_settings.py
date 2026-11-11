"""Every limit a reader enforces, as one settings object."""

from __future__ import annotations

import dataclasses
from typing import List, NamedTuple, Optional

__all__ = ["NonDefault", "Settings", "UNBOUNDED"]

#: A limit that is lifted. A limit is a whole number, or ``None`` for no bound.
UNBOUNDED = None

_KIB, _MIB = 1024, 1024 * 1024


class NonDefault(NamedTuple):
    """A setting that differs from its default; ``value`` and ``default`` are text, ``"none"`` for no bound."""

    setting: str
    value: str
    default: str


def _show(limit: "Optional[int]") -> str:
    return "none" if limit is None else str(limit)


@dataclasses.dataclass(frozen=True)
class Settings:
    """How much a reader reads and builds.

    Every limit has a default and no floor; ``None`` lifts one, and ``non_defaults`` reports it.
    ``manifest_*`` bound a manifest, an env file and an env script's output; ``tree_files``
    counts files and directories together, excluded names not counted; ``tree_depth`` counts
    directory levels, the root's own being the first. ``hook_timeout`` and
    ``hook_event_timeout`` are seconds. Use ``replace`` for a copy with some limits changed.
    """

    manifest_bytes: Optional[int] = _MIB
    manifest_depth: Optional[int] = 64
    tree_files: Optional[int] = 4096
    tree_depth: Optional[int] = 64
    tree_bytes: Optional[int] = 64 * _MIB
    file_bytes: Optional[int] = 16 * _MIB
    context_bytes: Optional[int] = _MIB
    catalog_bytes: Optional[int] = 4 * _MIB
    icon_bytes: Optional[int] = 512 * _KIB
    icon_width: Optional[int] = 1024
    icon_height: Optional[int] = 1024
    hook_timeout: Optional[int] = 60
    hook_event_timeout: Optional[int] = 120
    hook_output_bytes: Optional[int] = 64 * _KIB
    hook_payload_bytes: Optional[int] = _MIB
    hook_reason_chars: Optional[int] = 1024
    substitution_bytes: Optional[int] = _MIB
    substitution_depth: Optional[int] = 32
    substitution_steps: Optional[int] = 10_000_000

    def replace(self, **limits: "Optional[int]") -> "Settings":
        """A copy with the named limits changed; an unknown name is a ``TypeError``."""
        return dataclasses.replace(self, **limits)

    def non_defaults(self) -> "List[NonDefault]":
        """Every setting that differs from its default, in field order."""
        default = Settings()
        return [
            NonDefault(field.name, _show(getattr(self, field.name)), _show(getattr(default, field.name)))
            for field in dataclasses.fields(self)
            if getattr(self, field.name) != getattr(default, field.name)
        ]

    def allows(self, name: str, n: int) -> bool:
        """Whether ``n`` is within the limit called ``name``."""
        limit = getattr(self, name)
        return limit is None or n <= limit
