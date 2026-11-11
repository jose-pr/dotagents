"""``dotenv`` env files and the output of an env script."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, NamedTuple, Optional, Set, Tuple

from ._errors import Class, Error
from ._names import ascii_upper, is_reserved_variable, is_variable_name
from ._settings import Settings

__all__ = ["DotenvLine", "ScriptOutput", "parse_dotenv", "read_env_script_output"]

_BYTE_ORDER_MARK = b"\xef\xbb\xbf"
_BLANK = " \t"
_ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)[ \t]*=(.*)", re.DOTALL)
_EXPORT = re.compile(r"export[ \t]+")


class DotenvLine(NamedTuple):
    """One line of a ``dotenv`` file: the variable, its text with the quotes removed, and whether it
    was in single quotes, so it is never substituted."""

    name: str
    text: str
    literal: bool


#: What an env script sets: each variable to a value, or to ``None`` to unset it. A later object
#: wins over an earlier one; the keys keep the order they were first set in.
ScriptOutput = Dict[str, Optional[str]]


def parse_dotenv(data: bytes) -> "List[DotenvLine]":
    """A ``dotenv`` file's lines, in order.

    Blank lines and ``#`` comments are skipped; ``export`` may precede a name; a value in single
    or double quotes loses them. Raises ``invalid-utf8``, ``dotenv-syntax`` for a line that is no
    assignment and ``duplicate-key`` for two names equal without regard to ASCII case; the error's
    line counts from the first line of the file.
    """
    if data.startswith(_BYTE_ORDER_MARK):
        data = data[3:]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Error(Class.INVALID_UTF8, "not UTF-8") from None
    lines: "List[DotenvLine]" = []
    seen = set()
    for number, line in enumerate(text.split("\n"), 1):
        if line.endswith("\r"):
            line = line[:-1]
        stripped = line.strip(_BLANK)
        if not stripped or stripped.startswith("#"):
            continue
        start = line.lstrip(_BLANK)
        found = None
        exported = _EXPORT.match(start)
        if exported:
            found = _ASSIGNMENT.fullmatch(start, exported.end())
        if found is None:
            found = _ASSIGNMENT.fullmatch(start)
        if found is None:
            raise Error(Class.DOTENV_SYNTAX, "a line that is no NAME=VALUE", number)
        name, value = found.group(1), found.group(2).strip(_BLANK)
        literal = False
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            literal = value[0] == "'"
            value = value[1:-1]
        key = ascii_upper(name)
        if key in seen:
            raise Error(Class.DUPLICATE_KEY, "%s is set twice" % name, number)
        seen.add(key)
        lines.append(DotenvLine(name, value, literal))
    return lines


def _depth_within(text: str, limit: "Optional[int]") -> bool:
    """Whether the brackets of JSON ``text``, outside strings, nest no deeper than ``limit``."""
    if limit is None:
        return True
    depth = 0
    in_string = escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > limit:
                return False
        elif char in "]}":
            depth -= 1
    return True


def _pairs(items: "List[Tuple[str, Any]]") -> "Dict[str, Any]":
    out: "Dict[str, Any]" = {}
    for key, value in items:
        if key in out:
            raise Error(Class.DUPLICATE_KEY, "a key set twice")
        out[key] = value
    return out


def _constant(name: str) -> Any:
    raise Error(Class.PARSE_ERROR, "%s is not JSON" % name)


def _json(text: str, settings: Settings) -> Any:
    if not _depth_within(text, settings.manifest_depth):
        raise Error(Class.TOO_DEEP)
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except json.JSONDecodeError as error:
        raise Error(Class.PARSE_ERROR, "JSON that does not parse", error.lineno, error.colno) from None
    except RecursionError:
        raise Error(Class.TOO_DEEP) from None
    except ValueError:
        # An integer too long to convert, which no variable holds.
        raise Error(Class.PARSE_ERROR, "a number that does not read") from None


def read_env_script_output(data: bytes, settings: "Optional[Settings]" = None) -> ScriptOutput:
    """An env script's standard output: the variables it sets (a string) or unsets (``None``).

    The whole output is one JSON object, possibly over several lines; or else every line that is not
    blank is one. The objects apply in order. Raises ``too-large`` past ``manifest_bytes``,
    ``invalid-utf8``, the JSON reading classes, and ``env-script-output`` for anything but objects of
    variable names that are not reserved, mapped to a string without a zero byte or to null.
    """
    settings = settings or Settings()
    if not settings.allows("manifest_bytes", len(data)):
        raise Error(Class.TOO_LARGE, "an env script's output")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Error(Class.INVALID_UTF8, "not UTF-8") from None
    whole = text.strip(" \t\r\n")
    if not whole:
        return {}
    try:
        objects = [_json(whole, settings)]
    except Error as error:
        if error.class_ is not Class.PARSE_ERROR:
            raise
        objects = []
        for line in text.split("\n"):
            line = line[:-1] if line.endswith("\r") else line
            if line.strip(_BLANK):
                objects.append(_json(line, settings))
    out: ScriptOutput = {}
    for value in objects:
        if not isinstance(value, dict):
            raise Error(Class.ENV_SCRIPT_OUTPUT, "not an object")
        folded: "Set[str]" = set()
        for name, item in value.items():
            if not is_variable_name(name) or is_reserved_variable(name):
                raise Error(Class.ENV_SCRIPT_OUTPUT, "a name that may not be set")
            if ascii_upper(name) in folded:
                raise Error(Class.ENV_SCRIPT_OUTPUT, "two names equal under case")
            folded.add(ascii_upper(name))
            if item is not None and not (isinstance(item, str) and "\0" not in item):
                raise Error(Class.ENV_SCRIPT_OUTPUT, "a value that is not text")
            try:
                if item is not None:
                    item.encode("utf-8")
            except UnicodeEncodeError:
                raise Error(Class.INVALID_UNICODE, "a value") from None
            out[name] = item
    return out
