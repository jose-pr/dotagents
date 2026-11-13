"""Templating (``spec/v0/harness.md#templating``): ``${NAME}`` in the context a consumer assembles
and in the body of a bundled skill it serves.

Only the names listed, the overlay's non-sensitive options among them, are replaced; every other
text is itself. Nothing runs, no file is read, and an installed file is never changed.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, Optional

from ._data import Document
from ._errors import Class, Error
from ._front_matter import SplitCase, context_file_text, split_front_matter
from ._names import is_variable_name
from ._settings import Settings

__all__ = ["TemplateFacts", "context_text", "skill_text", "template", "template_values"]


@dataclasses.dataclass(frozen=True)
class TemplateFacts:
    """What a consumer knows about the overlay a text is read for. A name with no value is left
    as written in the text.

    ``root`` is ``AOS_OVERLAY_ROOT`` as the consumer holds it, ``state`` ``AOS_OVERLAY_STATE``,
    ``name`` ``AOS_OVERLAY_NAME`` (the normalized identity), ``scope`` ``AOS_OVERLAY_SCOPE`` and
    ``project`` ``AOS_PROJECT_ROOT``. ``options`` holds the values the user configured, by name; a
    name the overlay does not declare is ignored.
    """

    root: Optional[str] = None
    state: Optional[str] = None
    name: Optional[str] = None
    scope: Optional[str] = None
    project: Optional[str] = None
    options: Dict[str, Any] = dataclasses.field(default_factory=dict)


_NAMES = (
    ("AOS_OVERLAY_ROOT", "root"),
    ("AOS_OVERLAY_STATE", "state"),
    ("AOS_OVERLAY_NAME", "name"),
    ("AOS_OVERLAY_SCOPE", "scope"),
    ("AOS_PROJECT_ROOT", "project"),
)


def _option_text(kind: Any, name: str, value: Any) -> str:
    if kind == "string" and isinstance(value, str):
        return value
    if kind == "integer" and isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if kind == "boolean" and isinstance(value, bool):
        return "true" if value else "false"
    raise Error(Class.INVALID_OPTION, name)


def template_values(manifest: Document, facts: TemplateFacts) -> "Dict[str, str]":
    """The names that are replaced and their text, to give to ``template``, ``context_text`` and
    ``skill_text``. A name with no value is not in it, and a sensitive option never is. An
    option's value is the configured one, else its default, else none.

    Raises ``Error`` of class ``invalid-option``, the option's name its subject, when a configured
    value or a default is not of the declared type: it is never converted.
    """
    values = {}
    for name, key in _NAMES:
        value = getattr(facts, key)
        if value is not None:
            values[name] = value
    options = manifest.get("options")
    for name, declared in (options if isinstance(options, dict) else {}).items():
        if not isinstance(declared, dict) or declared.get("sensitive") is True:
            continue
        if not is_variable_name(name):
            continue
        if name in facts.options:
            values[name] = _option_text(declared.get("type"), name, facts.options[name])
        elif "default" in declared:
            values[name] = _option_text(declared.get("type"), name, declared["default"])
    return values


def template(text: str, values: "Dict[str, str]") -> str:
    """``text`` with each ``${NAME}`` whose name is in ``values`` replaced, read from left to
    right: ``$${`` is ``${``, and every other text is itself (an unknown name, an operator,
    ``$NAME``, ``$(...)``, a lone ``$$``). A value is inserted as it is and never read again."""
    out = []
    at = 0
    size = len(text)
    while at < size:
        if text.startswith("$${", at):
            out.append("${")
            at += 3
            continue
        if text.startswith("${", at):
            end = text.find("}", at + 2)
            if end != -1 and text[at + 2 : end] in values:
                out.append(values[text[at + 2 : end]])
                at = end + 1
                continue
        dollar = text.find("$", at + 1)
        stop = size if dollar == -1 else dollar
        out.append(text[at:stop])
        at = stop
    return "".join(out)


def _utf8(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise Error(Class.INVALID_UTF8) from None


def context_text(data: bytes, values: "Dict[str, str]", settings: "Optional[Settings]" = None) -> str:
    """A context file as the agent receives it: a closed front matter block dropped, the rest
    templated. Raises ``too-large`` for a head past ``manifest_bytes`` and ``invalid-utf8``."""
    return template(_utf8(context_file_text(data, settings)), values)


def skill_text(data: bytes, values: "Dict[str, str]", settings: "Optional[Settings]" = None) -> str:
    """A bundled skill's ``SKILL.md`` as served: the front matter exactly as written, the body
    templated. Raises as ``context_text`` does."""
    found = split_front_matter(data, settings)
    if found.case is SplitCase.TOO_LARGE:
        raise Error(Class.TOO_LARGE, "front matter", 1).over("manifest_bytes")
    offset = found.body_offset or 0
    return _utf8(data[:offset]) + template(_utf8(data[offset:]), values)
