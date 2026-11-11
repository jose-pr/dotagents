"""The shared data model and one conversion out of the library's result types."""

from __future__ import annotations

import dataclasses
import enum
from typing import Any, Dict

__all__ = ["Document", "to_data"]

#: What a document reads as: a mapping of strings to strings, integers, floats, booleans,
#: lists and mappings. Insertion order is the document's.
Document = Dict[str, Any]


def to_data(value: Any) -> Any:
    """``value`` as plain JSON data: dataclasses and named tuples become mappings, enums their
    words, tuples lists. The vectors write results in this shape."""
    if isinstance(value, enum.Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_data(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, tuple) and hasattr(value, "_fields"):
        return {name: to_data(item) for name, item in zip(value._fields, value)}
    if isinstance(value, dict):
        return {to_data(key): to_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_data(item) for item in value]
    return value
