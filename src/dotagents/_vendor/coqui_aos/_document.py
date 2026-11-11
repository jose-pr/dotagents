"""Reading: bytes to the data model, in TOML, JSON or restricted YAML."""

from __future__ import annotations

import enum
import json
import math
import re
from typing import Any, List, Optional, Tuple

from ._data import Document
from ._errors import Class, Error
from ._settings import Settings

try:
    import tomllib
except ImportError:  # Python before 3.11
    import tomli as tomllib  # type: ignore[no-redef]

__all__ = ["Format", "read_document", "read_text", "split_format_line"]

_INT_MIN, _INT_MAX = -(2**63), 2**63 - 1
_SAFE_FORMAT_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_TOML_POSITION = re.compile(r"at line (\d+), column (\d+)")
_BYTE_ORDER_MARK = b"\xef\xbb\xbf"


class Format(str, enum.Enum):
    """A document format an AOS reader reads."""

    TOML = "toml"
    JSON = "json"
    YAML = "yaml"

    @classmethod
    def named(cls, word: str) -> "Optional[Format]":
        """The format a format line's word names (``yml`` is YAML), or ``None``."""
        return {"toml": cls.TOML, "json": cls.JSON, "yaml": cls.YAML, "yml": cls.YAML}.get(word)

    def __str__(self) -> str:
        return self.value


def split_format_line(data: bytes, default: str) -> "Tuple[str, bytes]":
    """The word a first line ``#!`` names, else ``default``, and the bytes after that line.

    The word is the last of the line's space, tab, form feed and CR separated words, without
    its directory or leading dots, and with ASCII letters lowered. A byte-order mark is not
    dropped here: where the parser's text starts it is an error.
    """
    if not data.startswith(b"#!"):
        return default, data
    end = data.find(b"\n")
    line, rest = (data[2:], b"") if end < 0 else (data[2:end], data[end + 1 :])
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError:
        raise Error(Class.INVALID_UTF8, "the format line") from None
    words = [word for word in re.split(r"[ \t\x0c\r]+", text) if word]
    word = re.split(r"[/\\]", words[-1] if words else "")[-1].lstrip(".")
    word = "".join(c.lower() if "A" <= c <= "Z" else c for c in word)
    if not word:
        raise Error(Class.NO_FORMAT_NAMED)
    return word, rest


def _known(word: str) -> Format:
    found = Format.named(word)
    if found is None:
        raise Error(Class.UNSUPPORTED_FORMAT, word if _SAFE_FORMAT_NAME.match(word) else "(not shown)")
    return found


def read_document(data: bytes, settings: "Optional[Settings]" = None) -> Document:
    """``data`` as an AOS document: one optional byte-order mark, an optional format line
    (``#!json``, ``#!/usr/bin/env yaml``), else TOML; then the data model.

    Refused, each as its class: a duplicate key in one mapping, a null, a TOML date or time, a
    non-finite float, an integer outside the signed 64-bit range (refused before it is
    converted), a key that is not a string, a lone surrogate escape and a byte-order mark where
    the text starts. In YAML a plain scalar is typed by the 1.2 core schema, and a tag, anchor,
    alias, merge key, directive and second document are refused at the parser's events, before
    anything is expanded.
    """
    settings = settings or Settings()
    if settings.manifest_bytes is not None and len(data) > settings.manifest_bytes:
        raise Error(Class.TOO_LARGE)
    if data.startswith(_BYTE_ORDER_MARK):
        data = data[3:]
    word, payload = split_format_line(data, "toml")
    try:
        return read_text(_known(word), payload, settings)
    except Error as error:
        if error.line is not None and payload is not data:
            error.line += 1
            error.args = (error.class_, error.subject, error.line, error.column)
        raise


def read_text(kind: Format, payload: bytes, settings: "Optional[Settings]" = None) -> Document:
    """The text after any format line, parsed as ``kind`` and checked against the data model."""
    settings = settings or Settings()
    max_depth = settings.manifest_depth
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise Error(Class.INVALID_UTF8) from None
    if text.startswith("﻿"):
        raise Error(Class.PARSE_ERROR, "a byte-order mark where the document starts", 1, 1)
    if not text.strip(" \t\r\n"):
        return {}
    try:
        if kind is Format.TOML:
            value = _read_toml(text)
        elif kind is Format.JSON:
            value = _read_json(text)
        else:
            value = _read_yaml(text, max_depth)
    except RecursionError:
        raise Error(Class.TOO_DEEP) from None
    except (ValueError, OverflowError) as error:
        # What a parser cannot convert: an integer too long to read, an escape past Unicode.
        raise Error(
            Class.INTEGER_RANGE if "digits" in str(error) else Class.PARSE_ERROR,
            "a value the parser cannot read",
        ) from None
    if not isinstance(value, dict):
        raise Error(Class.NOT_A_MAPPING)
    _check_model(value, max_depth)
    return value


def _read_toml(text: str) -> Any:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        at = _TOML_POSITION.search(str(error))
        raise Error(
            Class.PARSE_ERROR,
            "TOML that does not parse",
            int(at.group(1)) if at else None,
            int(at.group(2)) if at else None,
        ) from None


def _read_json(text: str) -> Any:
    def pairs(items: "List[Tuple[str, Any]]") -> "dict[str, Any]":
        out: "dict[str, Any]" = {}
        for key, value in items:
            if key in out:
                raise Error(Class.DUPLICATE_KEY, "a key set twice")
            out[key] = value
        return out

    def constant(name: str) -> Any:
        raise Error(Class.PARSE_ERROR, "%s is not JSON" % name)

    def integer(digits: str) -> int:
        return _int(digits.lstrip("-").lstrip("0") or "0", 10, digits.startswith("-"))

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_int=integer)
    except json.JSONDecodeError as error:
        raise Error(Class.PARSE_ERROR, "JSON that does not parse", error.lineno, error.colno) from None


_YAML_INT = re.compile(r"^[-+]?[0-9]+$")
_YAML_OCT = re.compile(r"^0o[0-7]+$")
_YAML_HEX = re.compile(r"^0x[0-9a-fA-F]+$")
_YAML_FLOAT = re.compile(r"^[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?$")
_YAML_NON_FINITE = re.compile(r"^(?:[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$")


def _int(digits: str, base: int, negative: bool = False) -> int:
    # Past 64 bits a number is refused unread: some runtimes refuse to convert one this long.
    if len(digits) > {10: 19, 8: 22, 16: 16}[base]:
        raise Error(Class.INTEGER_RANGE)
    return -int(digits, base) if negative else int(digits, base)


def _yaml_scalar(value: str, style: Any) -> Any:
    """A scalar under the YAML 1.2 core schema; a quoted or block scalar is a string."""
    if style is not None:
        return value
    if value in ("", "~", "null", "Null", "NULL"):
        raise Error(Class.NULL_VALUE)
    if value in ("true", "True", "TRUE"):
        return True
    if value in ("false", "False", "FALSE"):
        return False
    if _YAML_INT.match(value):
        return _int(value.lstrip("+-0") or "0", 10, value.startswith("-"))
    if _YAML_OCT.match(value):
        return _int(value[2:].lstrip("0") or "0", 8)
    if _YAML_HEX.match(value):
        return _int(value[2:].lstrip("0") or "0", 16)
    if _YAML_FLOAT.match(value):
        return float(value)
    if _YAML_NON_FINITE.match(value):
        raise Error(Class.NON_FINITE_FLOAT)
    return value


def _read_yaml(text: str, max_depth: "Optional[int]") -> Any:
    """One YAML document built from parser events one at a time, so a refusal comes at the first
    node that earns it, with the rest unparsed and nothing expanded."""
    from ruamel.yaml import events as ev
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    if any(c in text for c in "\x85  ﻿"):
        raise Error(Class.PARSE_ERROR, "a Unicode line break YAML 1.1 and 1.2 read differently")
    stack: "List[Any]" = []
    keys: "List[Optional[str]]" = []
    root: Any = None
    documents = 0

    def refuse(class_: Class, event: Any, detail: str = "") -> Error:
        mark = event.start_mark
        return Error(class_, detail, mark.line + 1, mark.column + 1)

    def add(value: Any, event: Any) -> None:
        nonlocal root
        if not stack:
            root = value
            return
        top = stack[-1]
        if isinstance(top, list):
            top.append(value)
        elif keys[-1] is None:
            if not isinstance(value, str):
                raise refuse(Class.NON_STRING_KEY, event)
            if value == "<<":
                raise refuse(Class.YAML_MERGE_KEY, event)
            if value in top:
                raise refuse(Class.DUPLICATE_KEY, event, "a key set twice")
            keys[-1] = value
        else:
            top[keys[-1]] = value
            keys[-1] = None

    try:
        for event in YAML(typ="safe", pure=True).parse(text):
            if isinstance(event, ev.AliasEvent):
                raise refuse(Class.YAML_ANCHOR, event, "alias")
            if getattr(event, "anchor", None) is not None:
                raise refuse(Class.YAML_ANCHOR, event, "anchor")
            if getattr(event, "tag", None) is not None:
                raise refuse(Class.YAML_TAG, event)
            if isinstance(event, ev.DocumentStartEvent):
                if event.version is not None or event.tags:
                    raise refuse(Class.YAML_DIRECTIVE, event)
                documents += 1
                if documents > 1:
                    raise refuse(Class.PARSE_ERROR, event, "more than one document")
            elif isinstance(event, ev.ScalarEvent):
                try:
                    scalar = _yaml_scalar(event.value, event.style)
                except Error as error:
                    raise refuse(error.class_, event) from None
                add(scalar, event)
            elif isinstance(event, (ev.SequenceStartEvent, ev.MappingStartEvent)):
                if max_depth is not None and len(stack) + 1 > max_depth:
                    raise refuse(Class.TOO_DEEP, event)
                value: Any = [] if isinstance(event, ev.SequenceStartEvent) else {}
                add(value, event)
                stack.append(value)
                keys.append(None)
            elif isinstance(event, (ev.SequenceEndEvent, ev.MappingEndEvent)):
                stack.pop()
                keys.pop()
    except YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        raise Error(
            Class.PARSE_ERROR,
            "YAML that does not parse",
            mark.line + 1 if mark else None,
            mark.column + 1 if mark else None,
        ) from None
    return {} if documents == 0 or root is None else root


def _check_model(root: Any, max_depth: "Optional[int]") -> None:
    """Refuse what the data model has no type for. Iterative, so a lifted depth limit leaves the
    stack alone; the root container is depth 1."""
    pending: "List[Tuple[Any, int]]" = [(root, 1)]
    while pending:
        value, depth = pending.pop()
        if isinstance(value, (list, dict)) and max_depth is not None and depth > max_depth:
            raise Error(Class.TOO_DEEP)
        if value is None:
            raise Error(Class.NULL_VALUE)
        if isinstance(value, bool):
            continue
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                raise Error(Class.INVALID_UNICODE) from None
        elif isinstance(value, int):
            if not _INT_MIN <= value <= _INT_MAX:
                raise Error(Class.INTEGER_RANGE)
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise Error(Class.NON_FINITE_FLOAT)
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in reversed(value))
        elif isinstance(value, dict):
            for key in value:
                if not isinstance(key, str):
                    raise Error(Class.NON_STRING_KEY)
                pending.append((key, depth))
            pending.extend((item, depth + 1) for item in reversed(list(value.values())))
        else:
            # TOML dates and times, and anything else a parser may produce.
            raise Error(Class.DATETIME_VALUE if hasattr(value, "isoformat") else Class.PARSE_ERROR)
