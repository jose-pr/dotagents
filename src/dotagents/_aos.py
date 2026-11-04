"""The Agent Overlay Specification (AOS) format: reading, validation, digests
and applying declarations.

This module is the format and nothing else: no store, no scope, no command.
It reads ``overlay.cfg`` and env files into the AOS data model, validates a
manifest against the schema's rules and the semantic ones, computes the tree
digest, and applies one overlay's ``env``, ``env_files`` and ``paths`` to an
environment. :mod:`dotagents._overlays`, :mod:`dotagents._env` and the
``overlays`` commands give it a filesystem, secrets and a policy.

The rules are those of AOS ``0.1.0-draft``
(https://boriken-dev.github.io/agent-overlay-spec/); the error codes are the
classes its conformance vectors name.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

#: The AOS edition this module implements.
EDITION = "0.1.0-draft"

# --------------------------------------------------------------------------- #
# Reading: bytes to the data model.
# --------------------------------------------------------------------------- #

#: Format names a format line may use, to the format they select. YAML is read
#: only when PyYAML imports (the ``yaml`` extra).
FORMATS = {"toml": "toml", "json": "json", "yaml": "yaml", "yml": "yaml"}
DEFAULT_MAX_BYTES = 1024 * 1024
DEFAULT_MAX_DEPTH = 64
_INT_MIN, _INT_MAX = -(2 ** 63), 2 ** 63 - 1
_SAFE_FORMAT_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


class ReadError(ValueError):
    """A document AOS refuses. ``code`` is the vectors' error class."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__("%s: %s" % (code, detail) if detail else code)
        self.code = code
        self.detail = detail


def _toml() -> Any:
    try:
        import tomllib  # type: ignore[import-not-found]

        return tomllib
    except ImportError:
        pass
    try:
        import tomli  # type: ignore[import-not-found]

        return tomli
    except ImportError:
        return None


def _yaml() -> Any:
    try:
        import yaml  # type: ignore[import-untyped]

        return yaml
    except ImportError:
        return None


def supported_formats() -> "list[str]":
    """The format names this reader reads here (``yaml`` needs PyYAML)."""
    out = ["toml", "json"]
    if _yaml() is not None:
        out.append("yaml")
    return out


def select_format(data: bytes) -> "tuple[str, bytes]":
    """The format a document's format line names (``toml`` without one) and
    the bytes its parser is given."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    if not data.startswith(b"#!"):
        return "toml", data
    end = data.find(b"\n")
    line, rest = (data[2:], b"") if end < 0 else (data[2:end], data[end + 1:])
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError:
        raise ReadError("invalid-utf8", "the format line")
    words = [word for word in re.split(r"[ \t\x0c\r]+", text) if word]
    word = re.split(r"[/\\]", words[-1] if words else "")[-1].lstrip(".")
    word = "".join(c.lower() if "A" <= c <= "Z" else c for c in word)
    if not word:
        raise ReadError("no-format-named")
    return word, rest


def read_document(
    data: bytes, *, max_bytes: int = DEFAULT_MAX_BYTES, max_depth: int = DEFAULT_MAX_DEPTH
) -> "dict[str, Any]":
    """``data`` as an AOS document (``overlay.cfg``, an ``aos`` env file):
    the format line, TOML by default, static reading, the data model.
    Raises :class:`ReadError`."""
    if len(data) > max_bytes:
        raise ReadError("too-large")
    name, payload = select_format(data)
    kind = FORMATS.get(name)
    if kind is None or (kind == "yaml" and _yaml() is None):
        raise ReadError("unsupported-format", name if _SAFE_FORMAT_NAME.match(name) else "(not shown)")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise ReadError("invalid-utf8")
    if text.startswith("﻿"):
        raise ReadError("parse-error", "a byte-order mark where the document starts")
    if not text.strip(" \t\r\n"):
        return {}
    if kind == "toml":
        toml = _toml()
        if toml is None:
            raise ReadError("unsupported-format", "toml (needs Python 3.11+ or tomli)")
        try:
            value = toml.loads(text)
        except ValueError as exc:  # TOMLDecodeError
            raise ReadError("parse-error", str(exc))
    elif kind == "json":
        value = _read_json(text)
    else:
        value = _read_yaml(text)
    if not isinstance(value, dict):
        raise ReadError("not-a-mapping")
    _check_model(value, 1, max_depth)
    return value


def _read_json(text: str) -> Any:
    def pairs(items: "list[tuple[str, Any]]") -> "dict[str, Any]":
        out: "dict[str, Any]" = {}
        for key, value in items:
            if key in out:
                raise ReadError("duplicate-key", key)
            out[key] = value
        return out

    def constant(name: str) -> Any:
        raise ReadError("parse-error", "%s is not JSON" % name)

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except json.JSONDecodeError as exc:
        raise ReadError("parse-error", str(exc))


_YAML_INT = re.compile(r"^[-+]?[0-9]+$")
_YAML_OCT = re.compile(r"^0o[0-7]+$")
_YAML_HEX = re.compile(r"^0x[0-9a-fA-F]+$")
_YAML_FLOAT = re.compile(r"^[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?$")
_YAML_INF = re.compile(r"^[-+]?\.(?:inf|Inf|INF)$")
_YAML_NAN = re.compile(r"^\.(?:nan|NaN|NAN)$")


def _yaml_scalar(event: Any) -> Any:
    """A plain scalar under the YAML 1.2 core schema; a quoted one is a string."""
    if event.style is not None:
        return event.value
    value = event.value
    if value in ("", "~", "null", "Null", "NULL"):
        raise ReadError("null-value")
    if value in ("true", "True", "TRUE"):
        return True
    if value in ("false", "False", "FALSE"):
        return False
    if _YAML_INT.match(value):
        return int(value)
    if _YAML_OCT.match(value):
        return int(value[2:], 8)
    if _YAML_HEX.match(value):
        return int(value[2:], 16)
    if _YAML_FLOAT.match(value):
        return float(value)
    if _YAML_INF.match(value) or _YAML_NAN.match(value):
        raise ReadError("non-finite-float")
    return value


def _read_yaml(text: str) -> Any:
    """One YAML document built from parser events, so no tag, anchor, alias
    or merge key is ever applied."""
    yaml = _yaml()
    if any(c in text for c in "\x85  "):
        raise ReadError("parse-error", "a Unicode line break YAML 1.1 and 1.2 read differently")
    try:
        events = list(yaml.parse(text, Loader=yaml.SafeLoader))
    except yaml.YAMLError as exc:
        raise ReadError("parse-error", str(exc).splitlines()[0])
    stack: "list[Any]" = []
    keys: "list[Optional[str]]" = []
    root: Any = None
    documents = 0

    def add(value: Any) -> None:
        nonlocal root
        if not stack:
            root = value
            return
        top = stack[-1]
        if isinstance(top, list):
            top.append(value)
        elif keys[-1] is None:
            if not isinstance(value, str):
                raise ReadError("non-string-key")
            if value == "<<":
                raise ReadError("yaml-merge-key")
            if value in top:
                raise ReadError("duplicate-key", value)
            keys[-1] = value
        else:
            top[keys[-1]] = value
            keys[-1] = None

    for event in events:
        if isinstance(event, yaml.AliasEvent):
            raise ReadError("yaml-anchor", "alias")
        if getattr(event, "anchor", None) is not None:
            raise ReadError("yaml-anchor", "anchor")
        if getattr(event, "tag", None) is not None:
            raise ReadError("yaml-tag")
        if isinstance(event, yaml.DocumentStartEvent):
            if event.version is not None or event.tags:
                raise ReadError("yaml-directive")
            documents += 1
            if documents > 1:
                raise ReadError("parse-error", "more than one document")
        elif isinstance(event, yaml.ScalarEvent):
            add(_yaml_scalar(event))
        elif isinstance(event, (yaml.SequenceStartEvent, yaml.MappingStartEvent)):
            value: Any = [] if isinstance(event, yaml.SequenceStartEvent) else {}
            add(value)
            stack.append(value)
            keys.append(None)
        elif isinstance(event, (yaml.SequenceEndEvent, yaml.MappingEndEvent)):
            stack.pop()
            keys.pop()
    return {} if documents == 0 or root is None else root


def _check_model(value: Any, depth: int, max_depth: int) -> None:
    """Refuse what the data model has no type for. ``depth`` counts
    containers, the top-level mapping being 1."""
    if isinstance(value, (list, dict)) and depth > max_depth:
        raise ReadError("too-deep")
    if value is None:
        raise ReadError("null-value")
    if isinstance(value, (bool, str)):
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                raise ReadError("invalid-unicode")
        return
    if isinstance(value, int):
        if not _INT_MIN <= value <= _INT_MAX:
            raise ReadError("integer-range")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ReadError("non-finite-float")
        return
    if isinstance(value, list):
        for item in value:
            _check_model(item, depth + 1, max_depth)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ReadError("non-string-key")
            _check_model(key, depth, max_depth)
            _check_model(item, depth + 1, max_depth)
        return
    raise ReadError("datetime-value" if hasattr(value, "isoformat") else "unsupported-value")


_DOTENV_LINE = re.compile(r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=(.*)$")


def parse_dotenv(data: bytes) -> "list[tuple[str, str, bool]]":
    """A ``dotenv`` env file's lines as ``(name, text, literal)``, in order;
    ``literal`` is true for a value in single quotes, which is never
    substituted. No references, escapes or command substitution. Raises
    :class:`ReadError`."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ReadError("invalid-utf8")
    out: "list[tuple[str, str, bool]]" = []
    folded: "set[str]" = set()
    for line in text.split("\n"):
        if line.endswith("\r"):
            line = line[:-1]
        stripped = line.strip(" \t")
        if not stripped or stripped.startswith("#"):
            continue
        match = _DOTENV_LINE.match(line)
        if not match:
            raise ReadError("dotenv-syntax")
        name, value = match.group(1), match.group(2).strip(" \t")
        literal = False
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            literal = value[0] == "'"
            value = value[1:-1]
        if name.upper() in folded:
            raise ReadError("duplicate-key", name)
        folded.add(name.upper())
        out.append((name, value, literal))
    return out


def read_dotenv(data: bytes, env: "Optional[dict[str, str]]" = None, *, windows: bool = False,
                secret_variables: "Iterable[str]" = ()) -> "dict[str, str]":
    """A ``dotenv`` env file applied over ``env``: each line substituted
    against the environment the earlier lines left. Returns the values the
    file sets. ``secret_variables`` are the variables of ``env`` whose value
    came from a secret reference. Raises :class:`ReadError` (a
    :class:`SubstitutionError` for a placeholder)."""
    built = Environment(env or {}, windows, secret=secret_variables)
    out: "dict[str, str]" = {}
    for name, text, literal in parse_dotenv(data):
        value = text if literal else substitute(text, built.lookup)
        built.set(name, value)
        out[name] = value
    return out


# --------------------------------------------------------------------------- #
# Substitution: ``${NAME}`` and its operators in env values, applied when a
# value is set.
# --------------------------------------------------------------------------- #


class SubstitutionError(ReadError):
    """``undefined-variable``, ``secret-placeholder`` or ``invalid-pattern``;
    ``name`` is the placeholder's variable, or the pattern."""

    def __init__(self, code: str, name: str):
        super().__init__(code, name)
        self.name = name


_NAME_AT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SPACE = "[ \t\n\r\f\v]"
# Digits and white space are ASCII only; Python's \d and \s would take others.
_SLICE = re.compile(
    r":{s}*(?:\({s}*(-?[0-9]+){s}*\)|([0-9]+|{s}-[0-9]+))(?::{s}*(-?[0-9]+))?{s}*".format(s=_SPACE))
_WORD_OPERATORS = (":-", ":=", ":+", "-", "=", "+")


def substitute(text: str, lookup: "Callable[[str], Optional[str]]") -> str:
    """``text`` with every placeholder replaced. ``lookup(name)`` gives a
    variable's value, None when it is unset, and raises
    :class:`SubstitutionError` for one that holds a secret. Nothing runs and
    nothing is read but through ``lookup``; an inserted value is never read
    again."""
    out: "list[str]" = []
    index = 0
    while index < len(text):
        char = text[index]
        if char != "$":
            out.append(char)
            index += 1
            continue
        after = text[index + 1:index + 2]
        if after == "$":
            out.append("$")
            index += 2
            continue
        if after == "{":
            close = _closing_brace(text, index + 2)
            if close is not None:
                found = _expression(text[index + 2:close], lookup)
                if found is not None:
                    out.append(found)
                    index = close + 1
                    continue
        # No expression: the '$' is itself, and reading goes on after it.
        out.append("$")
        index += 1
    return "".join(out)


def _closing_brace(text: str, index: int) -> "Optional[int]":
    """The '}' ending an expression that starts at ``index``: the first one
    that does not close a '${' opened inside it."""
    depth = 0
    while index < len(text):
        if text.startswith("${", index):
            depth += 1
            index += 2
            continue
        if text[index] == "}":
            if depth == 0:
                return index
            depth -= 1
        index += 1
    return None


def _required(name: str, lookup: "Callable[[str], Optional[str]]") -> str:
    value = lookup(name)
    if value is None:
        raise SubstitutionError("undefined-variable", name)
    return value


def _expression(body: str, lookup: "Callable[[str], Optional[str]]") -> "Optional[str]":
    """The text for ``${body}``, or None when it is no expression AOS defines."""
    if body.startswith("#"):
        name = _NAME_AT.fullmatch(body, 1)
        if name is not None:
            return str(len(_required(name.group(), lookup)))
    name = _NAME_AT.match(body)
    if name is None:
        return None
    key, rest = name.group(), body[name.end():]
    if not rest:
        return _required(key, lookup)
    for operator in _WORD_OPERATORS:
        if rest.startswith(operator):
            word = rest[len(operator):]
            value = lookup(key)
            present = value is not None and not (operator[0] == ":" and value == "")
            if operator.endswith("+"):
                return substitute(word, lookup) if present else ""
            return value if present else substitute(word, lookup)
    if rest.startswith(":"):
        found = _SLICE.fullmatch(rest)
        if found is None:
            return None
        offset = int(found.group(1) or found.group(2))
        length = None if found.group(3) is None else int(found.group(3))
        return _slice(_required(key, lookup), offset, length)
    if rest.startswith("/"):
        spec = rest[1:]
        kind = spec[:1] if spec[:1] in ("/", "#", "%") else ""
        pattern, replacement = _split_pattern(spec[len(kind):])
        return _replace(_required(key, lookup), kind, pattern, replacement)
    if rest[:1] in ("#", "%"):
        operator = rest[:2] if rest[:2] in ("##", "%%") else rest[:1]
        return _trim(_required(key, lookup), operator, rest[len(operator):])
    if rest in ("^", "^^", ",", ",,"):
        value = _required(key, lookup)
        if rest == "^^":
            return value.upper()
        if rest == ",,":
            return value.lower()
        return (value[:1].upper() if rest == "^" else value[:1].lower()) + value[1:]
    return None


def _slice(value: str, offset: int, length: "Optional[int]") -> str:
    """Code points ``offset`` to the end, or ``length`` of them; negative
    numbers count from the end."""
    size = len(value)
    if offset < 0:
        offset += size
        if offset < 0:
            return ""
    end = size if length is None else (offset + length if length >= 0 else size + length)
    return value[offset:end] if end > offset else ""


def _split_pattern(spec: str) -> "tuple[str, str]":
    """A replacement's pattern and text, split at the first '/' not after a backslash."""
    index = 0
    while index < len(spec):
        if spec[index] == "\\":
            index += 2
            continue
        if spec[index] == "/":
            return spec[:index], spec[index + 1:]
        index += 1
    return spec, ""


def _glob(pattern: str) -> "re.Pattern[str]":
    """A pattern as a regular expression matched whole: '*', '?', bracket
    expressions, '\\x' for x. Raises :class:`SubstitutionError` for empty
    brackets or a reversed range."""
    parts: "list[str]" = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\" and index + 1 < len(pattern):
            parts.append(re.escape(pattern[index + 1]))
            index += 2
            continue
        close = pattern.find("]", index + 2) if char == "[" else -1
        if char == "*":
            parts.append(".*")
        elif char == "?":
            parts.append(".")
        elif close >= 0:
            body = pattern[index + 1:close]
            negate = body[:1] in ("!", "^")
            items = body[1:] if negate else body
            if not items:
                raise SubstitutionError("invalid-pattern", pattern)
            members: "list[str]" = []
            at = 0
            while at < len(items):
                if at + 2 < len(items) and items[at + 1] == "-":
                    low, high = items[at], items[at + 2]
                    if ord(high) < ord(low):
                        raise SubstitutionError("invalid-pattern", pattern)
                    members.append(re.escape(low) + "-" + re.escape(high))
                    at += 3
                else:
                    members.append(re.escape(items[at]))
                    at += 1
            parts.append("[" + ("^" if negate else "") + "".join(members) + "]")
            index = close + 1
            continue
        else:
            parts.append(re.escape(char))
        index += 1
    return re.compile("".join(parts), re.DOTALL)


def _ends(regex: "re.Pattern[str]", value: str, start: int) -> "list[int]":
    """Every end at which the pattern matches the value from ``start``, shortest first."""
    return [end for end in range(start, len(value) + 1) if regex.fullmatch(value, start, end)]


def _replace(value: str, kind: str, pattern: str, replacement: str) -> str:
    if not pattern:
        return value
    regex = _glob(pattern)
    if kind == "#":
        ends = _ends(regex, value, 0)
        return replacement + value[ends[-1]:] if ends else value
    if kind == "%":
        for start in range(len(value) + 1):
            if regex.fullmatch(value, start):
                return value[:start] + replacement
        return value
    out: "list[str]" = []
    position = 0
    while True:
        match = None
        for start in range(position, len(value)):
            ends = _ends(regex, value, start)
            if ends and ends[-1] > start:
                match = (start, ends[-1])
                break
        if match is None:
            break
        out.append(value[position:match[0]] + replacement)
        position = match[1]
        if kind != "/":
            break
    return "".join(out) + value[position:]


def _trim(value: str, operator: str, pattern: str) -> str:
    regex = _glob(pattern)
    if operator in ("#", "##"):
        ends = _ends(regex, value, 0)
        if not ends:
            return value
        return value[ends[0] if operator == "#" else ends[-1]:]
    starts = [start for start in range(len(value) + 1) if regex.fullmatch(value, start)]
    if not starts:
        return value
    return value[:starts[-1] if operator == "%" else starts[0]]


# --------------------------------------------------------------------------- #
# Names and paths.
# --------------------------------------------------------------------------- #

#: Names an overlay may not have: dotagents' scope labels.
RESERVED_OVERLAY_NAMES = frozenset({"default", "overlay", "system", "user", "project", "project-root"})
#: Windows device names, refused as a whole name or before its first ``.``.
DEVICE_NAMES = frozenset(
    ["con", "prn", "aux", "nul"] + ["com%d" % i for i in range(1, 10)] + ["lpt%d" % i for i in range(1, 10)]
)
OVERLAY_NAME = re.compile(r"^[A-Za-z](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?$")


def valid_overlay_name(name: str) -> bool:
    return (
        bool(OVERLAY_NAME.match(name))
        and name.lower() not in RESERVED_OVERLAY_NAMES
        and name.split(".", 1)[0].lower() not in DEVICE_NAMES
    )


def normalize_name(name: str) -> str:
    """An identity compared normalized: ASCII lowercase, ``_`` and ``.`` as ``-``."""
    return name.lower().replace("_", "-").replace(".", "-")


def _variable_stem(identity: str) -> str:
    return re.sub(r"[^A-Z0-9]", "_", normalize_name(identity).upper())


def root_variable(identity: str) -> str:
    """``<NAME>_OVERLAY_ROOT``."""
    return _variable_stem(identity) + "_OVERLAY_ROOT"


def state_variable(identity: str) -> str:
    """``<NAME>_OVERLAY_STATE``."""
    return _variable_stem(identity) + "_OVERLAY_STATE"


_FORBIDDEN = "/\\\\:*?\"<>|\\x00-\\x1f\\x7f-\\x9f\\u200c-\\u200f\\u202a-\\u202e\\u2066-\\u206f\\ufeff"
_COMPONENT = "[^%s]*[^%s. ]" % (_FORBIDDEN, _FORBIDDEN)
_RELPATH = re.compile("^%s(?:/%s)*$" % (_COMPONENT, _COMPONENT))


def _ascii_fold(word: str) -> str:
    """``word`` as a regex matching it in any ASCII case (``.git`` ->
    ``\\.[Gg][Ii][Tt]``): re.IGNORECASE would also fold non-ASCII letters."""
    return "".join("[%s%s]" % (c.upper(), c) if "a" <= c <= "z" else re.escape(c) for c in word)


_EXCLUDED_WORDS = (".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
                   ".tox", ".venv", "node_modules")
_DEVICE_WORDS = ("con", "prn", "aux", "nul", "conin$", "conout$")
_RELPATH_EXCLUDED = re.compile(
    "(?:^|/)(?:(?:%s)(?:/|$)|(?:%s|(?:%s|%s)[0-9¹²³])(?:\\.[^/]*)?(?:/|$)|[^/]*\\.%s(?:/|$))|~[0-9]"
    % ("|".join(_ascii_fold(w) for w in _EXCLUDED_WORDS), "|".join(_ascii_fold(w) for w in _DEVICE_WORDS),
       _ascii_fold("com"), _ascii_fold("lpt"), _ascii_fold("pyc"))
)


def valid_relpath(path: str) -> bool:
    """A relative path as the manifest defines it."""
    return isinstance(path, str) and bool(_RELPATH.match(path)) and not _RELPATH_EXCLUDED.search(path)


#: Path components that are never part of an overlay.
EXCLUDED_COMPONENTS = frozenset(
    (".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
     ".venv", "node_modules")
)

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_VARIABLE = re.compile(r"^(?:AOS_.*|.*_OVERLAY_(?:ROOT|STATE))$", re.IGNORECASE)


def valid_variable_name(name: str) -> bool:
    return isinstance(name, str) and bool(_ENV_NAME.match(name))


def is_reserved_variable(name: str) -> bool:
    """``AOS_*``, ``*_OVERLAY_ROOT`` and ``*_OVERLAY_STATE``: the consumer's."""
    return bool(_RESERVED_VARIABLE.match(name))


SENSITIVE_NAMES = frozenset(
    """PATH PATHEXT HOME USERPROFILE SHELL COMSPEC SYSTEMROOT BASH_ENV ENV ZDOTDIR PROMPT_COMMAND
    IFS EDITOR VISUAL PAGER LESSOPEN LESSCLOSE XDG_CONFIG_HOME XDG_DATA_HOME APPDATA LOCALAPPDATA
    TMPDIR TEMP TMP PSMODULEPATH CLASSPATH JAVA_TOOL_OPTIONS _JAVA_OPTIONS JDK_JAVA_OPTIONS
    PERL5OPT PERL5LIB RUBYOPT RUBYLIB RUSTFLAGS GOPROXY GONOSUMDB GONOSUMCHECK GOPRIVATE GOFLAGS
    SSH_ASKPASS SSLKEYLOGFILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE""".split()
)
SENSITIVE_PREFIXES = ("LD_", "DYLD_", "GIT_", "PYTHON", "NODE_", "NPM_CONFIG_", "PIP_", "UV_", "CARGO_",
                      "RUSTC_", "DOTNET_", "COR_", "CORECLR_", "SSL_")
SENSITIVE_SUFFIXES = ("_PROXY", "_BASE_URL")
#: Path additions exist for these: adding to them is no weakening.
PATH_VARIABLES = frozenset(("PATH", "PYTHONPATH", "NODE_PATH"))


def is_sensitive(name: str) -> bool:
    upper = name.upper()
    return upper in SENSITIVE_NAMES or upper.startswith(SENSITIVE_PREFIXES) or upper.endswith(SENSITIVE_SUFFIXES)


# --------------------------------------------------------------------------- #
# Validation: the schema's rules, then those JSON Schema cannot express.
# --------------------------------------------------------------------------- #

_TOOL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_OS_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
_INTERPRETER = _OS_NAME
_HARNESS_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_PROVIDER = re.compile(r"^[a-z][a-z0-9_-]*$")
_DIGEST = re.compile(r"^sha256:[0-9a-fA-F]{64}$")
_VERSION = re.compile(r"^[0-9]{1,9}(?:\.[0-9]{1,9}){0,2}$")
_COMMAND_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_SCRIPT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SCRIPT_PATH = re.compile(r"^[A-Za-z0-9_+-][A-Za-z0-9._+-]*(?:/[A-Za-z0-9_+-][A-Za-z0-9._+-]*)*$")
_PACKAGE_JSON = re.compile(r"(?:^|/)package\.json$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?$")
_SCRIPT_EXTENSION = re.compile(r"\.(?:py|sh|bash|ps1|js|mjs|cjs)$")
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_URL = re.compile(r"^[Hh][Tt][Tt][Pp][Ss]?://[^/?#@\s]+(?:[/?][^#\s]*)?$")
TIMEOUT_MAX = 2147483647

#: A ``scripts`` path's interpreter, by extension.
SCRIPT_EXTENSIONS = {".py": "python", ".sh": "sh", ".bash": "bash", ".ps1": "powershell",
                     ".js": "node", ".mjs": "node", ".cjs": "node"}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class _Checker(object):
    """Collects ``(code, pointer)`` problems while walking a document."""

    def __init__(self) -> None:
        self.issues: "list[tuple[str, str]]" = []

    def add(self, code: str, at: str) -> None:
        self.issues.append((code, at))

    def type(self, value: Any, kind: str, at: str) -> bool:
        ok = {
            "string": isinstance(value, str),
            "boolean": isinstance(value, bool),
            "integer": _is_int(value),
            "number": _is_number(value),
            "array": isinstance(value, list),
            "object": isinstance(value, dict),
        }[kind]
        if not ok:
            self.add("type", at)
        return ok

    def pattern(self, value: Any, regex: "re.Pattern[str]", at: str) -> bool:
        if not isinstance(value, str) or not regex.match(value):
            self.add("pattern", at)
            return False
        return True

    def relpath(self, value: Any, at: str) -> bool:
        if not valid_relpath(value):
            self.add("relative-path", at)
            return False
        return True

    def text(self, value: Any, at: str) -> bool:
        if not isinstance(value, str) or "\0" in value:
            self.add("text", at)
            return False
        return True

    def keys(self, value: "dict[str, Any]", allowed: Iterable[str], at: str) -> None:
        allowed = set(allowed)
        for key in value:
            if key not in allowed:
                self.add("unknown-key", "%s/%s" % (at, key))

    def required(self, value: "dict[str, Any]", names: Iterable[str], at: str) -> bool:
        missing = [n for n in names if n not in value]
        if missing:
            self.add("required", at)
        return not missing

    def array(self, value: Any, at: str, item: "Callable[[Any, str], Any]") -> None:
        if self.type(value, "array", at):
            for index, entry in enumerate(value):
                item(entry, "%s/%d" % (at, index))

    def oslist(self, value: Any, at: str) -> None:
        self.array(value, at, lambda v, p: self.pattern(v, _OS_NAME, p))

    def timeout(self, value: Any, at: str) -> None:
        if not _is_int(value) or not 0 <= value <= TIMEOUT_MAX:
            self.add("timeout", at)

    def any_value(self, value: Any, at: str) -> None:
        if value is None or not isinstance(value, (str, int, float, bool, list, dict)):
            self.add("null-value", at)
        elif isinstance(value, list):
            for index, entry in enumerate(value):
                self.any_value(entry, "%s/%d" % (at, index))
        elif isinstance(value, dict):
            for key, entry in value.items():
                self.any_value(entry, "%s/%s" % (at, key))


def _env_value_ok(value: Any) -> bool:
    """Whether ``value`` is exactly one of the env value forms."""
    if isinstance(value, str):
        return "\0" not in value
    if not isinstance(value, dict):
        return False
    optional_ok = lambda v: isinstance(v, bool)  # noqa: E731
    flags = {"if_unset"}
    sources = [k for k in ("value", "path", "secret", "provider") if k in value]
    if len(sources) != 1:
        return False
    source = sources[0]
    if source == "value":
        return set(value) <= {"value"} | flags and isinstance(value["value"], str) and "\0" not in value["value"] \
            and all(optional_ok(value[k]) for k in flags & set(value))
    if source == "path":
        return set(value) <= {"path"} | flags and valid_relpath(value["path"]) \
            and all(optional_ok(value[k]) for k in flags & set(value))
    flags = {"if_unset", "optional"}
    if source == "secret":
        return set(value) <= {"secret"} | flags and isinstance(value["secret"], str) and bool(value["secret"]) \
            and all(optional_ok(value[k]) for k in flags & set(value))
    provider, key = value.get("provider"), value.get("key")
    if not (isinstance(provider, str) and _PROVIDER.match(provider)):
        return False
    if not (isinstance(key, str) and key and "\0" not in key):
        return False
    if provider == "file" and not valid_relpath(key):
        return False
    if provider == "env" and not _ENV_NAME.match(key):
        return False
    for name, item in value.items():
        if name in ("provider", "key"):
            continue
        if name in flags:
            if not optional_ok(item):
                return False
        elif not (isinstance(item, (str, bool)) or _is_int(item)):
            return False
    return True


def _check_env_map(c: _Checker, value: Any, at: str) -> None:
    if not c.type(value, "object", at):
        return
    for name, entry in value.items():
        here = "%s/%s" % (at, name)
        if not _ENV_NAME.match(name):
            c.add("variable-name", here)
        elif is_reserved_variable(name):
            c.add("reserved-variable", here)
        if not _env_value_ok(entry):
            c.add("env-value", here)


def _check_env_file(c: _Checker, value: Any, at: str) -> None:
    if isinstance(value, str):
        c.relpath(value, at)
        return
    if not c.type(value, "object", at):
        return
    c.keys(value, ("path", "format", "os"), at)
    if c.required(value, ("path",), at):
        c.relpath(value["path"], at + "/path")
    if "format" in value and value["format"] not in ("aos", "dotenv"):
        c.add("enum", at + "/format")
    if "os" in value:
        c.oslist(value["os"], at + "/os")


def _check_path_addition(c: _Checker, value: Any, at: str) -> None:
    if not c.type(value, "object", at):
        return
    c.keys(value, ("variable", "path", "position", "os"), at)
    if c.required(value, ("variable", "path"), at):
        variable = value["variable"]
        if c.pattern(variable, _ENV_NAME, at + "/variable") and is_reserved_variable(variable):
            c.add("reserved-variable", at + "/variable")
        c.relpath(value["path"], at + "/path")
    if "position" in value and value["position"] not in ("prepend", "append"):
        c.add("enum", at + "/position")
    if "os" in value:
        c.oslist(value["os"], at + "/os")


def _check_step(c: _Checker, value: Any, at: str, setup: bool) -> None:
    if not c.type(value, "object", at):
        return
    c.keys(value, ("interpreter", "path", "args", "timeout", "os") + (("network",) if setup else ()), at)
    if c.required(value, ("interpreter", "path"), at):
        c.pattern(value["interpreter"], _INTERPRETER, at + "/interpreter")
        c.relpath(value["path"], at + "/path")
    if "args" in value:
        c.array(value["args"], at + "/args", c.text)
    if "timeout" in value:
        c.timeout(value["timeout"], at + "/timeout")
    if "network" in value and setup:
        c.type(value["network"], "boolean", at + "/network")
    if "os" in value:
        c.oslist(value["os"], at + "/os")


def _check_runtime(c: _Checker, value: Any, at: str) -> None:
    if not c.type(value, "object", at):
        return
    c.keys(value, ("os", "commands", "python", "node"), at)
    if "os" in value:
        c.oslist(value["os"], at + "/os")

    def command(entry: Any, here: str) -> None:
        if isinstance(entry, str):
            c.pattern(entry, _COMMAND_NAME, here)
        elif c.type(entry, "object", here):
            c.keys(entry, ("name", "os"), here)
            if c.required(entry, ("name",), here):
                c.pattern(entry["name"], _COMMAND_NAME, here + "/name")
            if "os" in entry:
                c.oslist(entry["os"], here + "/os")

    if "commands" in value:
        c.array(value["commands"], at + "/commands", command)

    def python(entry: Any, here: str) -> None:
        if not c.type(entry, "object", here):
            return
        c.keys(entry, ("version", "requirements", "lock", "lock_digest", "environment", "os"), here)
        if "version" in entry:
            c.pattern(entry["version"], _VERSION, here + "/version")
        if "environment" in entry:
            c.pattern(entry["environment"], _ENVIRONMENT_NAME, here + "/environment")
        for field in ("requirements", "lock"):
            if field in entry:
                c.relpath(entry[field], "%s/%s" % (here, field))
        if "requirements" in entry and "lock" in entry:
            c.add("exclusive", here)
        if "lock_digest" in entry:
            c.pattern(entry["lock_digest"], _DIGEST, here + "/lock_digest")
            if "lock" not in entry:
                c.add("dependent-required", here)
        if "os" in entry:
            c.oslist(entry["os"], here + "/os")

    def node(entry: Any, here: str) -> None:
        if c.type(entry, "object", here):
            c.keys(entry, ("version", "package", "environment", "os"), here)
            if "version" in entry:
                c.pattern(entry["version"], _VERSION, here + "/version")
            if "package" in entry:
                c.relpath(entry["package"], here + "/package")
                if isinstance(entry["package"], str) and not _PACKAGE_JSON.search(entry["package"]):
                    c.add("pattern", here + "/package")
            if "environment" in entry:
                c.pattern(entry["environment"], _ENVIRONMENT_NAME, here + "/environment")
            if "os" in entry:
                c.oslist(entry["os"], here + "/os")

    if "python" in value:
        c.array(value["python"], at + "/python", python)
    if "node" in value:
        c.array(value["node"], at + "/node", node)


def _script_path_ok(value: Any) -> bool:
    return isinstance(value, str) and bool(_SCRIPT_PATH.match(value)) and valid_relpath(value)


def _check_scripts(c: _Checker, value: Any, at: str) -> None:
    if not c.type(value, "object", at):
        return
    for name, target in value.items():
        here = "%s/%s" % (at, name)
        if not _SCRIPT_NAME.match(name) or name.lower().endswith(".exe"):
            c.add("script-name", here)
        if target is False:
            continue
        if isinstance(target, str):
            if not (_script_path_ok(target) and _SCRIPT_EXTENSION.search(target)):
                c.add("script-path", here)
            continue
        if not isinstance(target, dict):
            c.add("type", here)
            continue
        c.keys(target, ("path", "interpreter", "os"), here)
        if c.required(target, ("path", "interpreter"), here):
            if not _script_path_ok(target["path"]):
                c.add("script-path", here + "/path")
            c.pattern(target["interpreter"], _INTERPRETER, here + "/interpreter")
        if "os" in target:
            c.oslist(target["os"], here + "/os")


def _check_harness(c: _Checker, value: Any, at: str) -> None:
    if not c.type(value, "object", at):
        return
    for harness_id, hints in value.items():
        here = "%s/%s" % (at, harness_id)
        if not _HARNESS_ID.match(harness_id):
            c.add("harness-id", here)
        if not c.type(hints, "object", here):
            continue
        for key, item in hints.items():
            spot = "%s/%s" % (here, key)
            if key == "context":
                c.array(item, spot, c.relpath)
            elif key == "skills":
                c.type(item, "boolean", spot)
            elif key in ("model", "effort"):
                if not (isinstance(item, str) and item):
                    c.add("type", spot)
            else:
                c.any_value(item, spot)


def _check_mcp(c: _Checker, value: Any, at: str) -> None:
    if not c.type(value, "object", at):
        return
    c.keys(value, ("servers", "skills"), at)
    names: "set[str]" = set()

    def server(entry: Any, here: str) -> None:
        if not c.type(entry, "object", here):
            return
        c.keys(entry, ("name", "transport", "timeout", "command", "args", "env", "env_passthrough", "url",
                       "headers_env"), here)
        if c.required(entry, ("name", "transport"), here):
            name = entry["name"]
            if c.pattern(name, _TOOL_NAME, here + "/name"):
                if name in names:
                    c.add("mcp-duplicate-server", here + "/name")
                names.add(name)
            if entry["transport"] not in ("stdio", "streamable-http"):
                c.add("enum", here + "/transport")
        if "timeout" in entry and not (_is_number(entry["timeout"]) and 0.001 <= entry["timeout"] <= TIMEOUT_MAX):
            c.add("timeout", here + "/timeout")
        if "command" in entry:
            command = entry["command"]
            if not (isinstance(command, str) and command and "\0" not in command):
                c.add("type", here + "/command")
            elif ("/" in command or "\\" in command) and not valid_relpath(command):
                c.add("mcp-command", here + "/command")
        if "args" in entry:
            c.array(entry["args"], here + "/args", c.text)
        if "env" in entry:
            _check_env_map(c, entry["env"], here + "/env")
            if isinstance(entry["env"], dict):
                seen: "set[str]" = set()
                for var in entry["env"]:
                    if var.upper() in seen:
                        c.add("env-name-case-duplicate", "%s/env/%s" % (here, var))
                    seen.add(var.upper())
        if "env_passthrough" in entry:
            passthrough = entry["env_passthrough"]
            c.array(passthrough, here + "/env_passthrough", lambda v, p: c.pattern(v, _ENV_NAME, p))
            if isinstance(passthrough, list) and len(passthrough) != len(
                    {json.dumps(v, sort_keys=True) for v in passthrough}):
                c.add("unique", here + "/env_passthrough")
        if "url" in entry:
            url = entry["url"]
            if not (isinstance(url, str) and _URL.match(url)) or (isinstance(url, str) and "#" in url):
                c.add("mcp-url", here + "/url")
        if "headers_env" in entry:
            headers = entry["headers_env"]
            if c.type(headers, "object", here + "/headers_env"):
                for header, var in headers.items():
                    spot = "%s/headers_env/%s" % (here, header)
                    if not _HEADER_NAME.match(header):
                        c.add("header-name", spot)
                    c.pattern(var, _ENV_NAME, spot)
        transport = entry.get("transport")
        if transport == "stdio":
            if "command" not in entry or "url" in entry or "headers_env" in entry:
                c.add("mcp-transport", here)
        elif transport == "streamable-http":
            if "url" not in entry or any(k in entry for k in ("command", "args", "env", "env_passthrough")):
                c.add("mcp-transport", here)

    if "servers" in value:
        c.array(value["servers"], at + "/servers", server)
    linked: "set[str]" = set()

    def skill(entry: Any, here: str) -> None:
        if not c.type(entry, "object", here):
            return
        c.keys(entry, ("name", "server"), here)
        if c.required(entry, ("name", "server"), here):
            if c.pattern(entry["name"], _TOOL_NAME, here + "/name"):
                if entry["name"] in linked:
                    c.add("mcp-duplicate-skill", here + "/name")
                linked.add(entry["name"])
            if c.pattern(entry["server"], _TOOL_NAME, here + "/server") and entry["server"] not in names:
                c.add("mcp-unknown-server", here + "/server")

    if "skills" in value:
        c.array(value["skills"], at + "/skills", skill)


def named_files(doc: "dict[str, Any]") -> "list[tuple[str, str]]":
    """Every ``(pointer, relative path)`` of a file a field requires to exist."""
    out: "list[tuple[str, str]]" = []

    def each(key: str, items: Any, field: "Optional[str]" = None) -> None:
        if not isinstance(items, list):
            return
        for index, item in enumerate(items):
            if field is None and isinstance(item, str):
                out.append(("/%s/%d" % (key, index), item))
            elif field is not None and isinstance(item, dict) and isinstance(item.get(field), str):
                out.append(("/%s/%d/%s" % (key, index, field), item[field]))

    each("rules", doc.get("rules"))
    each("context", doc.get("context"))
    files = doc.get("env_files")
    if isinstance(files, list):
        for index, item in enumerate(files):
            if isinstance(item, str):
                out.append(("/env_files/%d" % index, item))
            elif isinstance(item, dict) and isinstance(item.get("path"), str):
                out.append(("/env_files/%d/path" % index, item["path"]))
    each("env_scripts", doc.get("env_scripts"), "path")
    each("setup", doc.get("setup"), "path")
    runtime = doc.get("runtime")
    if isinstance(runtime, dict) and isinstance(runtime.get("python"), list):
        for index, variant in enumerate(runtime["python"]):
            if isinstance(variant, dict):
                for field in ("requirements", "lock"):
                    if isinstance(variant.get(field), str):
                        out.append(("/runtime/python/%d/%s" % (index, field), variant[field]))
    if isinstance(runtime, dict) and isinstance(runtime.get("node"), list):
        for index, variant in enumerate(runtime["node"]):
            if isinstance(variant, dict) and isinstance(variant.get("package"), str):
                out.append(("/runtime/node/%d/package" % index, variant["package"]))
    scripts = doc.get("scripts")
    if isinstance(scripts, dict):
        for name, target in scripts.items():
            if isinstance(target, str):
                out.append(("/scripts/%s" % name, target))
            elif isinstance(target, dict) and isinstance(target.get("path"), str):
                out.append(("/scripts/%s/path" % name, target["path"]))
    harness = doc.get("harness")
    if isinstance(harness, dict):
        for harness_id, hints in harness.items():
            if isinstance(hints, dict) and isinstance(hints.get("context"), list):
                for index, path in enumerate(hints["context"]):
                    if isinstance(path, str):
                        out.append(("/harness/%s/context/%d" % (harness_id, index), path))
    return out


def validate(doc: Any, files: "Optional[Iterable[str]]" = None) -> "list[tuple[str, str]]":
    """Problems with a data model value as ``(code, pointer)``; empty when
    it is valid. ``files``, the overlay's relative file paths, makes every
    file a field names a requirement too."""
    c = _Checker()
    if not isinstance(doc, dict):
        c.add("type", "")
        return c.issues
    for key in ("name", "description"):
        if key in doc:
            c.type(doc[key], "string", "/" + key)
    if "requires" in doc:
        def require(value: Any, at: str) -> None:
            if c.pattern(value, OVERLAY_NAME, at) and not valid_overlay_name(value):
                c.add("reserved-name", at)
        c.array(doc["requires"], "/requires", require)
    if "routing" in doc:
        c.array(doc["routing"], "/routing", lambda v, p: c.type(v, "string", p))
    for key in ("rules", "context"):
        if key in doc:
            c.array(doc[key], "/" + key, c.relpath)
    if "priority" in doc:
        c.type(doc["priority"], "integer", "/priority")
    if "skills" in doc:
        c.array(doc["skills"], "/skills", lambda v, p: c.pattern(v, _TOOL_NAME, p))
        if isinstance(doc["skills"], list) and len(doc["skills"]) != len(
                {json.dumps(v, sort_keys=True) for v in doc["skills"]}):
            c.add("unique", "/skills")
    env_names: "dict[str, str]" = {}
    if "env" in doc:
        _check_env_map(c, doc["env"], "/env")
        if isinstance(doc["env"], dict):
            for name in doc["env"]:
                if name.upper() in env_names:
                    c.add("env-name-case-duplicate", "/env/%s" % name)
                env_names.setdefault(name.upper(), name)
    if "env_files" in doc:
        c.array(doc["env_files"], "/env_files", lambda v, p: _check_env_file(c, v, p))
    if "paths" in doc:
        c.array(doc["paths"], "/paths", lambda v, p: _check_path_addition(c, v, p))
        if isinstance(doc["paths"], list):
            for index, entry in enumerate(doc["paths"]):
                if isinstance(entry, dict) and isinstance(entry.get("variable"), str) \
                        and entry["variable"].upper() in env_names:
                    c.add("env-path-conflict", "/paths/%d/variable" % index)
    if "env_scripts" in doc:
        c.array(doc["env_scripts"], "/env_scripts", lambda v, p: _check_step(c, v, p, False))
    if "runtime" in doc:
        _check_runtime(c, doc["runtime"], "/runtime")
    if "setup" in doc:
        c.array(doc["setup"], "/setup", lambda v, p: _check_step(c, v, p, True))
    if "scripts" in doc:
        _check_scripts(c, doc["scripts"], "/scripts")
    if "mcp" in doc:
        _check_mcp(c, doc["mcp"], "/mcp")
    if "harness" in doc:
        _check_harness(c, doc["harness"], "/harness")
    if "defaults" in doc and c.type(doc["defaults"], "object", "/defaults"):
        for key, value in doc["defaults"].items():
            if key in DEFAULT_KEYS:
                c.type(value, "boolean", "/defaults/" + key)
            else:
                c.add("unknown-key", "/defaults/" + key)
    known = {"name", "description", "requires", "routing", "rules", "priority", "context", "skills", "env",
             "env_files", "paths", "env_scripts", "runtime", "setup", "scripts", "mcp", "harness", "defaults"}
    for key, value in doc.items():
        if key not in known:
            c.any_value(value, "/" + key)
    if files is not None:
        have = set(files)
        env = doc.get("env")
        if isinstance(env, dict) and isinstance(doc.get("paths", []), list):
            added = {entry["variable"].upper() for entry in default_paths(doc, have)}
            for name in env:
                if name.upper() in added:
                    c.add("env-path-conflict", "/env/%s" % name)
        for at, path in named_files(doc):
            if path not in have:
                c.add("missing-file", at)
        skills = doc.get("skills")
        if isinstance(skills, list):
            for index, name in enumerate(skills):
                if isinstance(name, str) and "skills/%s/SKILL.md" % name not in have:
                    c.add("missing-skill", "/skills/%d" % index)
        mcp = doc.get("mcp")
        if isinstance(mcp, dict) and isinstance(mcp.get("skills"), list):
            for index, link in enumerate(mcp["skills"]):
                name = link.get("name") if isinstance(link, dict) else None
                if isinstance(name, str) and "skills/%s/SKILL.md" % name not in have:
                    c.add("missing-skill", "/mcp/skills/%d/name" % index)
    return sorted(set(c.issues), key=lambda issue: (issue[1], issue[0]))


# --------------------------------------------------------------------------- #
# Integrity: the tree digest.
# --------------------------------------------------------------------------- #

_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: symlinks and junctions
DIGEST_FORM = _DIGEST
EMPTY_DIGEST = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class DigestError(ValueError):
    """A tree whose digest cannot be computed. ``code`` is the vectors' class."""

    def __init__(self, code: str, path: str = ""):
        super().__init__("%s: %s" % (code, path) if path else code)
        self.code = code
        self.path = path


def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def file_digest(path: "str | os.PathLike[str]") -> str:
    h = hashlib.sha256()
    with open(str(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def same_digest(a: str, b: str) -> bool:
    """Digests compare by their hexadecimal digits, without regard to case."""
    if not (DIGEST_FORM.match(a) and DIGEST_FORM.match(b)):
        raise ValueError("not a sha256 digest")
    return a.lower() == b.lower()


def _excluded(components: "Iterable[bytes]") -> bool:
    names = [c.decode("utf-8", "replace") for c in components]
    return any(name in EXCLUDED_COMPONENTS for name in names) or names[-1].endswith(".pyc")


def digest_entries(entries: "Iterable[tuple[tuple[bytes, ...], str, Any]]") -> "tuple[str, list[bytes]]":
    """The tree digest of ``(components, kind, data)`` entries, and its lines.
    ``kind`` is ``file``, ``dir``, ``symlink`` or ``special``; ``data`` is a
    file's bytes, or its digest when it is a ``str``."""
    lines = []
    for components, kind, data in entries:
        if kind == "dir" or _excluded(components):
            continue
        shown = b"/".join(components).decode("utf-8", "replace")
        if kind == "symlink":
            raise DigestError("symlink", shown)
        if kind != "file":
            raise DigestError("special-file", shown)
        try:
            path = b"/".join(components).decode("utf-8")
        except UnicodeDecodeError:
            raise DigestError("non-utf8-name", shown)
        if not valid_relpath(path):
            raise DigestError("invalid-name", shown)
        digest = data if isinstance(data, str) else sha256_digest(data)
        lines.append(path.encode("utf-8") + b"\0" + digest.encode("ascii") + b"\n")
    folded: "dict[bytes, bytes]" = {}
    for line in lines:
        path = line.split(b"\0", 1)[0]
        if folded.setdefault(path.lower(), path) != path:
            raise DigestError("case-collision", path.decode("utf-8"))
    lines.sort(key=lambda line: line.split(b"\0", 1)[0])
    return sha256_digest(b"".join(lines)), lines


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def tree_entries(root: "str | os.PathLike[str]") -> "list[tuple[tuple[bytes, ...], str, Any]]":
    """A directory's entries for :func:`digest_entries`, read without
    following any link inside it; a file's data is its digest."""
    out: "list[tuple[tuple[bytes, ...], str, Any]]" = []

    def walk(directory: str, prefix: "tuple[bytes, ...]") -> None:
        with os.scandir(directory) as it:
            found = sorted(it, key=lambda e: e.name)
        for entry in found:
            components = prefix + (os.fsencode(entry.name),)
            info = entry.stat(follow_symlinks=False)
            if entry.name in EXCLUDED_COMPONENTS:
                out.append((components, "dir" if stat.S_ISDIR(info.st_mode) else "file", ""))
                continue
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
                out.append((components, "symlink", ""))
            elif stat.S_ISDIR(info.st_mode):
                out.append((components, "dir", ""))
                walk(entry.path, components)
            elif stat.S_ISREG(info.st_mode):
                out.append((components, "file", file_digest(entry.path)))
            else:
                out.append((components, "special", ""))

    walk(str(root), ())
    return out


def tree_digest(root: "str | os.PathLike[str]") -> str:
    """The tree digest of the overlay at ``root``. Raises :class:`DigestError`."""
    return digest_entries(tree_entries(root))[0]


def members(root: "str | os.PathLike[str]") -> "list[str]":
    """The relative paths of an overlay's members (the files the digest
    counts), sorted. Raises :class:`DigestError` as the digest would."""
    _digest, lines = digest_entries(tree_entries(root))
    return [line.split(b"\0", 1)[0].decode("utf-8") for line in lines]


def member_paths(root: "str | os.PathLike[str]") -> "list[str]":
    """The overlay's member files as relative paths, read without hashing
    and without following a link: what decides which defaults are present.
    Unlike :func:`members` it raises nothing; a link, a special file or a
    name AOS refuses is simply not a member."""
    out: "list[str]" = []

    def walk(directory: str, prefix: str) -> None:
        try:
            with os.scandir(directory) as it:
                found = sorted(it, key=lambda e: e.name)
        except OSError:
            return
        for entry in found:
            if entry.name in EXCLUDED_COMPONENTS:
                continue
            rel = prefix + entry.name
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
                continue
            if stat.S_ISDIR(info.st_mode):
                walk(entry.path, rel + "/")
            elif stat.S_ISREG(info.st_mode) and valid_relpath(rel):
                out.append(rel)

    walk(str(root), "")
    return out


def foreign_entries(root: "str | os.PathLike[str]") -> "list[str]":
    """Entries under ``root`` that are not part of the overlay (an excluded
    name, a ``.pyc``): in a verified copy each one is a mismatch."""
    out = []
    for components, kind, _data in tree_entries(root):
        if _excluded(components) and not any(
                c.decode("utf-8", "replace") in EXCLUDED_COMPONENTS for c in components[:-1]):
            out.append(b"/".join(components).decode("utf-8", "replace"))
    return out


# --------------------------------------------------------------------------- #
# Operating systems, variants, interpreters.
# --------------------------------------------------------------------------- #

UNIX_NAMES = frozenset(("macos", "linux", "freebsd", "openbsd", "netbsd"))
#: The interpreters AOS defines.
INTERPRETERS = ("python", "bash", "sh", "powershell", "node")


def current_os() -> str:
    """This system's AOS OS name."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    for name in ("freebsd", "openbsd", "netbsd"):
        if sys.platform.startswith(name):
            return name
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def os_matches(entry: Any, current: str) -> bool:
    """Whether an entry's ``os`` (absent: every OS) includes ``current``."""
    names = entry.get("os") if isinstance(entry, dict) else None
    if names is None:
        return True
    return current in names or ("unix" in names and current in UNIX_NAMES)


def first_variant(variants: Any, current: str) -> "Optional[dict[str, Any]]":
    """The first variant whose ``os`` matches, else None."""
    for variant in variants or ():
        if os_matches(variant, current):
            return variant
    return None


def script_interpreter(target: Any) -> "Optional[str]":
    """The interpreter a ``scripts`` value runs under."""
    if isinstance(target, str):
        return SCRIPT_EXTENSIONS.get(os.path.splitext(target)[1])
    if isinstance(target, dict):
        return target.get("interpreter")
    return None


def version_tuple(text: str) -> "tuple[int, int, int]":
    parts = [int(p) for p in text.split(".")] + [0, 0, 0]
    return (parts[0], parts[1], parts[2])


# --------------------------------------------------------------------------- #
# Requirement and lock files.
# --------------------------------------------------------------------------- #

_REQUIREMENT = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"  # name
    r"(?:\[[A-Za-z0-9._,\s-]*\])?"  # extras
    r"\s*(?:(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+"
    r"(?:\s*,\s*(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+)*)?"  # specifiers
    r"\s*(?:;[^#]*)?$"  # marker
)
_PINNED = re.compile(r"^[^=<>!~;\[\s]+(?:\[[^\]]*\])?\s*==\s*[A-Za-z0-9.+!_-]+\s*(?:;.*)?$")
_HASH = re.compile(r"^--hash=sha256:[0-9a-fA-F]{64}$")


def check_requirements(text: str, *, locked: bool) -> "list[str]":
    """Problems with a requirements (``locked=False``) or lock file: each
    logical line must be a requirement and, in a lock, pinned with ``==`` and
    carry ``--hash=sha256:`` options; any other line is refused."""
    problems: "list[str]" = []
    logical: "list[tuple[int, str]]" = []
    pending, start = "", 0
    for number, line in enumerate(text.splitlines(), 1):
        if not pending:
            start = number
        if line.rstrip().endswith("\\"):
            pending += line.rstrip()[:-1] + " "
            continue
        logical.append((start, pending + line))
        pending = ""
    if pending:
        logical.append((start, pending))
    for number, line in logical:
        content = re.sub(r"(?:^|\s)#.*$", "", line).strip()
        if not content:
            continue
        words = content.split()
        hashes = [w for w in words if w.startswith("--hash")]
        requirement = " ".join(w for w in words if not w.startswith("--hash"))
        if requirement.startswith("-") or "://" in requirement or " @ " in " %s " % requirement:
            problems.append("line %d: options, URLs and paths are not allowed" % number)
            continue
        if not _REQUIREMENT.match(requirement):
            problems.append("line %d: not a requirement" % number)
            continue
        if any(not _HASH.match(h) for h in hashes):
            problems.append("line %d: a hash that is not --hash=sha256:<hex>" % number)
        if locked:
            if not _PINNED.match(requirement):
                problems.append("line %d: not pinned with ==" % number)
            if not hashes:
                problems.append("line %d: no --hash" % number)
        elif hashes:
            problems.append("line %d: hashes belong in a lock file" % number)
    return problems


# --------------------------------------------------------------------------- #
# Applying declarations.
# --------------------------------------------------------------------------- #


class Refused(Exception):
    """A declaration a consumer refuses to apply. ``code`` is the vectors'
    class, ``subject`` a variable name or a path, never a value."""

    def __init__(self, code: str, subject: str, detail: str = ""):
        super().__init__("%s: %s%s" % (code, subject, " (%s)" % detail if detail else ""))
        self.code = code
        self.subject = subject
        self.detail = detail


class Environment(object):
    """A variable mapping; on Windows names compare without regard to ASCII
    case, and setting one that exists under another case keeps that name.

    ``secret`` holds the variables whose value came from a secret reference,
    which no placeholder may read. ``extra`` holds what a placeholder sees
    that is not set (an overlay's own ``AOS_OVERLAY_*``). ``assigned`` lists
    the variables :meth:`set` was called for, in order."""

    def __init__(self, values: "dict[str, str]", windows: bool, *, secret: "Iterable[str]" = (),
                 extra: "Optional[dict[str, str]]" = None):
        self.windows = windows
        self.values = dict(values)
        self.secret = {key for key in (self.key(name) for name in secret) if key is not None}
        self.extra = dict(extra or {})
        self.assigned: "list[str]" = []

    def key(self, name: str) -> "Optional[str]":
        if name in self.values:
            return name
        if self.windows:
            upper = name.upper()
            for key in self.values:
                if key.upper() == upper:
                    return key
        return None

    def get(self, name: str) -> "Optional[str]":
        key = self.key(name)
        return None if key is None else self.values[key]

    def lookup(self, name: str) -> "Optional[str]":
        """What a placeholder reads: None when unset; raises
        :class:`SubstitutionError` for a variable that holds a secret."""
        for extra, value in self.extra.items():
            if extra == name or (self.windows and extra.upper() == name.upper()):
                return value
        key = self.key(name)
        if key is None:
            return None
        if key in self.secret:
            raise SubstitutionError("secret-placeholder", name)
        return self.values[key]

    def set(self, name: str, value: str, *, secret: bool = False) -> None:
        key = self.key(name) or name
        self.values[key] = value
        if secret:
            self.secret.add(key)
        else:
            self.secret.discard(key)
        if key not in self.assigned:
            self.assigned.append(key)

    def unset(self, name: str) -> None:
        key = self.key(name)
        if key is not None:
            del self.values[key]
            self.secret.discard(key)

    def copy(self) -> "Environment":
        copied = Environment(self.values, self.windows, extra=self.extra)
        copied.secret = set(self.secret)
        return copied


def resolve_path(root: str, relative: str, windows: bool) -> str:
    """The absolute path of ``relative`` under ``root``, with the platform's separator."""
    separator = "\\" if windows else "/"
    return root.rstrip("/\\") + separator + relative.replace("/", separator)


def reference_name(entry: "dict[str, Any]") -> str:
    """How a report names a secret reference: never a value, never a
    provider's other fields."""
    if "secret" in entry:
        return "secret:" + entry["secret"]
    return "%s:%s" % (entry["provider"], entry["key"])


def is_reference(entry: Any) -> bool:
    return isinstance(entry, dict) and ("provider" in entry or "secret" in entry)


class Secrets(object):
    """Where secret references resolve, and what each overlay is granted.

    ``grants`` maps a provider (``env``, ``file``, any other) or ``secret`` to
    the keys or names granted. ``host_env`` is the consumer's own environment
    (the ``env`` provider). ``named(name)`` gives a named secret's value and
    ``read_file(key)`` a ``file`` secret's content (``None``: absent, or no
    secrets directory). ``providers`` lists the providers there are;
    ``provider_values`` holds values of other providers by key."""

    def __init__(
        self,
        *,
        grants: "Optional[dict[str, Iterable[str]]]" = None,
        host_env: "Optional[dict[str, str]]" = None,
        named: "Optional[Callable[[str], Optional[str]]]" = None,
        read_file: "Optional[Callable[[str], Optional[str]]]" = None,
        providers: "Iterable[str]" = ("env", "file"),
        provider_values: "Optional[dict[str, dict[str, str]]]" = None,
        windows: bool = False,
    ):
        self.grants = {k: set(v) for k, v in (grants or {}).items()}
        self.host_env = Environment(host_env or {}, windows)
        self.named = named or (lambda name: None)
        self.read_file = read_file or (lambda key: None)
        self.providers = set(providers)
        self.provider_values = provider_values or {}

    def granted(self, entry: "dict[str, Any]") -> bool:
        if "secret" in entry:
            return entry["secret"] in self.grants.get("secret", ())
        return entry["key"] in self.grants.get(entry["provider"], ())

    def resolve(self, entry: "dict[str, Any]") -> "Optional[str]":
        """A granted reference's value, or None when it cannot be resolved.
        Raises :class:`Refused` (``invalid-reference``) for a ``file`` key
        that is not a relative path."""
        if entry.get("provider") == "file" and not valid_relpath(entry.get("key")):
            raise Refused("invalid-reference", str(entry.get("key")))
        if not self.granted(entry):
            return None
        if "secret" in entry:
            return self.named(entry["secret"])
        provider = entry["provider"]
        if provider not in self.providers:
            return None
        if provider == "env":
            return self.host_env.get(entry["key"])
        if provider == "file":
            value = self.read_file(entry["key"])
            if value is None:
                return None
            for ending in ("\r\n", "\n"):
                if value.endswith(ending):
                    return value[: -len(ending)]
            return value
        return self.provider_values.get(provider, {}).get(entry["key"])


class Context(object):
    """What applying one overlay needs from its consumer: the OS, the
    overlay's root and identity, its env files' bytes (``read(relpath)``,
    ``None`` when absent), the :class:`Secrets`, whether its env scripts may
    run (the consumer's policy), the overlay's member files (``files``, from
    which the defaults follow), the interpreters the consumer can run, and
    its state directory (``state``, None when it has none), which its
    placeholders see as ``AOS_OVERLAY_STATE``, and the packages directory
    of each runtime's environment it installs into (``packages``, runtime to
    path), added after the runtime's ``lib/<runtime>/``."""

    def __init__(
        self,
        *,
        os_name: str,
        root: str,
        identity: str,
        read: "Callable[[str], Optional[bytes]]",
        secrets: "Optional[Secrets]" = None,
        env_scripts_enabled: bool = True,
        files: "Iterable[str]" = (),
        interpreters: "Iterable[str]" = (),
        state: "Optional[str]" = None,
        packages: "Optional[dict[str, str]]" = None,
    ):
        self.os = os_name
        self.windows = os_name == "windows"
        self.root = root
        self.identity = identity
        self.state = state
        self.packages = dict(packages or {})
        self.read = read
        self.secrets = secrets or Secrets(windows=self.windows)
        self.env_scripts_enabled = env_scripts_enabled
        self.files = list(files)
        self.interpreters = set(interpreters)

    def placeholders(self) -> "dict[str, str]":
        """What this overlay's placeholders see beyond the environment: its
        ``AOS_OVERLAY_ROOT``, ``AOS_OVERLAY_NAME`` and ``AOS_OVERLAY_STATE``."""
        seen = {"AOS_OVERLAY_ROOT": self.root, "AOS_OVERLAY_NAME": normalize_name(self.identity)}
        if self.state is not None:
            seen["AOS_OVERLAY_STATE"] = self.state
        return seen


def _apply_mapping(env: Environment, mapping: "dict[str, Any]", ctx: Context,
                   weakenings: "list[tuple[str, str]]") -> None:
    # Mapping order carries no meaning: every entry, its if_unset and its
    # placeholders see the environment as it was before the mapping.
    before = env.copy()
    for name, entry in mapping.items():
        if is_reserved_variable(name):
            raise Refused("reserved-variable", name)
        if isinstance(entry, str):
            entry = {"value": entry}
        if entry.get("if_unset") and before.get(name) is not None:
            continue
        secret = False
        if "value" in entry:
            try:
                value: "Optional[str]" = substitute(entry["value"], before.lookup)
            except SubstitutionError as error:
                raise Refused(error.code, name, error.name)
        elif "path" in entry:
            value = resolve_path(ctx.root, entry["path"], ctx.windows)
        else:
            secret = True
            value = ctx.secrets.resolve(entry)
            if value is None:
                if entry.get("optional"):
                    continue
                provider = "secret" if "secret" in entry else entry["provider"]
                raise Refused("unresolved-reference", name,
                              "%s %s" % (provider, entry.get("secret", entry.get("key"))))
            if entry.get("provider") == "env":
                weakenings.append(("env-passthrough", entry["key"]))
            else:
                weakenings.append(("secret-read", reference_name(entry)))
        if is_sensitive(name):
            weakenings.append(("env-sensitive", name))
        env.set(name, value, secret=secret)  # type: ignore[arg-type]


def _apply_dotenv(env: Environment, lines: "list[tuple[str, str, bool]]",
                  weakenings: "list[tuple[str, str]]") -> None:
    """A ``dotenv`` file's lines in order, each substituted against the
    environment the earlier lines left."""
    for name, text, literal in lines:
        if is_reserved_variable(name):
            raise Refused("reserved-variable", name)
        try:
            value = text if literal else substitute(text, env.lookup)
        except SubstitutionError as error:
            raise Refused(error.code, name, error.name)
        if is_sensitive(name):
            weakenings.append(("env-sensitive", name))
        env.set(name, value)


def apply_paths(env: Environment, paths: "list[dict[str, Any]]", ctx: Context,
                weakenings: "list[tuple[str, str]]") -> None:
    """``paths``: per variable, this overlay's ``prepend`` entries, the
    other components (empty ones and these entries dropped), its ``append``
    entries."""
    separator = ";" if ctx.windows else ":"
    grouped: "dict[str, list[dict[str, Any]]]" = {}
    names: "dict[str, str]" = {}
    for entry in paths:
        if not os_matches(entry, ctx.os):
            continue
        key = entry["variable"].upper() if ctx.windows else entry["variable"]
        names.setdefault(key, entry["variable"])
        grouped.setdefault(key, []).append(entry)
    for key, entries in grouped.items():
        variable = names[key]
        if is_sensitive(variable) and variable.upper() not in PATH_VARIABLES:
            weakenings.append(("env-sensitive", variable))
        prepend: "list[str]" = []
        append: "list[str]" = []
        seen: "set[str]" = set()
        for entry in entries:
            resolved = entry["packages"] if "packages" in entry else resolve_path(ctx.root, entry["path"], ctx.windows)
            if resolved in seen:
                continue
            seen.add(resolved)
            (append if entry.get("position") == "append" else prepend).append(resolved)
        kept = [c for c in (env.get(variable) or "").split(separator) if c and c not in seen]
        env.set(variable, separator.join(prepend + kept + append), secret=env.key(variable) in env.secret)


def read_env_file(item: "dict[str, Any]", ctx: Context,
                  listed: "set[str]") -> "dict[str, Any] | list[tuple[str, str, bool]]":
    """One env file, checked: an ``aos`` file's mapping, or a ``dotenv``
    file's lines (:func:`parse_dotenv`). Raises :class:`Refused`."""
    data = ctx.read(item["path"])
    if data is None:
        raise Refused("missing-file", item["path"])
    try:
        if item.get("format", "aos") == "dotenv":
            found: "dict[str, Any] | list[tuple[str, str, bool]]" = parse_dotenv(data)
            names = [name for name, _text, _literal in found]
        else:
            found = read_document(data)
            if validate({"env": found}):
                raise Refused("invalid-env-file", item["path"])
            names = list(found)
    except ReadError as exc:
        raise Refused("invalid-env-file", item["path"], exc.code)
    for name in names:
        if name.upper() in listed:
            raise Refused("env-path-conflict", name)
    return found


# --------------------------------------------------------------------------- #
# Defaults: what conventional files contribute unless ``defaults`` turns them off.
# --------------------------------------------------------------------------- #

#: The keys of ``defaults``; each is true unless the manifest writes it false.
DEFAULT_KEYS = ("bin", "lib", "env", "env_scripts", "setup", "deps", "context", "skills", "rules")
#: Default path additions as ``(key, variable, directory)``, in the order they apply.
DEFAULT_PATHS = (("bin", "PATH", "bin"), ("lib", "PYTHONPATH", "lib/python"), ("lib", "NODE_PATH", "lib/node"))
#: The default dependency file of each runtime, and the lock looked for beside
#: it (None: the file is a lock when every requirement in it has a hash).
DEFAULT_DEPENDENCIES = (
    ("python", "lib/python/requirements.txt", None),
    ("node", "lib/node/package.json", "lib/node/package-lock.json"),
)
#: The list variable each runtime's installed packages are added to.
PACKAGE_VARIABLES = {"python": "PYTHONPATH", "node": "NODE_PATH"}
#: Default script extensions in the order a consumer tries them, with the OSes
#: each applies on (``None``: every OS).
DEFAULT_SCRIPTS = (
    (".py", "python", None),
    (".js", "node", None),
    (".mjs", "node", None),
    (".cjs", "node", None),
    (".ps1", "powershell", ["windows"]),
    (".bash", "bash", ["unix"]),
    (".sh", "sh", ["unix"]),
)
_SKILL_DIR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_RULES_FILE = re.compile(r"^rules/[^/]+\.md$")


def default_on(manifest: "dict[str, Any]", key: str) -> bool:
    """Whether the default ``key`` is on: true unless ``[defaults]`` says false."""
    defaults = manifest.get("defaults")
    return not (isinstance(defaults, dict) and defaults.get(key) is False)


def _members(files: "Iterable[str]") -> "list[str]":
    """The files that make a path present: digest members only."""
    return [path for path in files if valid_relpath(path)]


def _by_bytes(paths: "Iterable[str]") -> "list[str]":
    return sorted(paths, key=lambda path: path.encode("utf-8"))


def _named(entry: Any) -> Any:
    """The file or directory an entry names: a string, or a mapping's ``path``."""
    return entry.get("path") if isinstance(entry, dict) else entry


def _declared(manifest: "dict[str, Any]", field: str) -> "list[Any]":
    value = manifest.get(field)
    return list(value) if isinstance(value, list) else []


def _merge(defaults: "list[Any]", declared: "list[Any]") -> "list[Any]":
    """Default entries first, then declared ones; a declared entry naming the
    same file or directory as a default entry replaces it."""
    named = {_named(entry) for entry in declared}
    return [entry for entry in defaults if _named(entry) not in named] + declared


def default_paths(manifest: "dict[str, Any]", files: "Iterable[str]") -> "list[dict[str, Any]]":
    """The default path additions that are on, present (a member file under
    the directory) and not replaced by a declared entry with that ``path``."""
    members = _members(files)
    found = [
        {"variable": variable, "path": directory}
        for key, variable, directory in DEFAULT_PATHS
        if default_on(manifest, key) and any(p.startswith(directory + "/") for p in members)
    ]
    named = {_named(entry) for entry in _declared(manifest, "paths")}
    return [entry for entry in found if entry["path"] not in named]


def paths_of(manifest: "dict[str, Any]", files: "Iterable[str]") -> "list[dict[str, Any]]":
    """The ``bin`` and ``lib`` defaults, then ``paths``."""
    return default_paths(manifest, files) + _declared(manifest, "paths")


def overlay_paths(manifest: "dict[str, Any]", ctx: "Context") -> "list[dict[str, Any]]":
    """What :func:`apply_paths` applies for one overlay: the defaults, then
    each runtime's installed packages directory (``ctx.packages``, an entry
    with ``packages`` instead of ``path``), then ``paths``."""
    packages = [
        {"variable": PACKAGE_VARIABLES[dep["runtime"]], "packages": ctx.packages[dep["runtime"]]}
        for dep in dependencies_of(manifest, ctx.files, ctx.os)
        if dep["runtime"] in ctx.packages
    ]
    return default_paths(manifest, ctx.files) + packages + _declared(manifest, "paths")


_HASH_OPTION = re.compile(r"(?:^|\s)--hash[=\s]")


def requirements_locked(text: str, path: str) -> bool:
    """Whether a requirements file is a lock: every requirement in it has a
    ``--hash``. Raises :class:`Refused` (``partial-lock``) when only some do:
    pip checks hashes for all or none."""
    hashed = []
    for line in re.sub(r"\\\r?\n", " ", text).splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            hashed.append(_HASH_OPTION.search(line) is not None)
    if hashed and all(hashed):
        return True
    if any(hashed):
        raise Refused("partial-lock", path)
    return False


def environment_name(variant: "dict[str, Any]") -> str:
    """The environment a runtime variant installs into: ``shared``,
    ``isolated`` or a name, normalized as an identity is."""
    return normalize_name(variant.get("environment", "shared"))


def dependencies_of(manifest: "dict[str, Any]", files: "Iterable[str]", os_name: str,
                    read: "Optional[Callable[[str], Optional[bytes]]]" = None) -> "list[dict[str, Any]]":
    """The dependency files installed for this OS, one per runtime, as
    ``{"runtime", "file", "lock", "environment"}``: a selected variant naming
    a file replaces the default one, and one naming none still chooses the
    default file's environment. ``read`` gives a file's bytes, so a default
    requirements file is a lock when every requirement has a hash; without
    it, the default file counts as unlocked. Raises :class:`Refused`
    (``partial-lock``)."""
    runtime = manifest.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    members = set(_members(files))
    found = []
    for name, default, beside in DEFAULT_DEPENDENCIES:
        variant = first_variant(runtime.get(name, []), os_name) or {}
        lock: "Optional[str]"
        if name == "python" and ("lock" in variant or "requirements" in variant):
            path = variant.get("lock", variant.get("requirements"))
            lock = variant.get("lock")
        elif name == "node" and "package" in variant:
            path = variant["package"]
            candidate = path[:-len("package.json")] + "package-lock.json"
            lock = candidate if candidate in members else None
        elif default_on(manifest, "deps") and default in members:
            path = default
            if beside is None:
                data = read(path) if read is not None else None
                text = data.decode("utf-8", "replace") if data is not None else None
                lock = path if text is not None and requirements_locked(text, path) else None
            else:
                lock = beside if beside in members else None
        else:
            continue
        found.append({"runtime": name, "file": path, "lock": lock, "environment": environment_name(variant)})
    return found


def rules_of(manifest: "dict[str, Any]", files: "Iterable[str]") -> "list[str]":
    """Every ``rules/<name>.md`` by path bytes, then ``rules``."""
    found: "list[str]" = []
    if default_on(manifest, "rules"):
        found = [path for path in _by_bytes(_members(files)) if _RULES_FILE.match(path)]
    return _merge(found, _declared(manifest, "rules"))


def context_of(manifest: "dict[str, Any]", files: "Iterable[str]") -> "list[str]":
    """The root ``CONTEXT.md``, then ``context``."""
    found: "list[str]" = []
    if default_on(manifest, "context") and "CONTEXT.md" in _members(files):
        found = ["CONTEXT.md"]
    return _merge(found, _declared(manifest, "context"))


def skills_of(manifest: "dict[str, Any]", files: "Iterable[str]") -> "list[str]":
    """Every ``skills/<name>/SKILL.md`` with a skill name, and the skills
    ``skills`` lists, by name bytes."""
    found = set(_declared(manifest, "skills"))
    if default_on(manifest, "skills"):
        for path in _members(files):
            parts = path.split("/")
            if len(parts) == 3 and parts[0] == "skills" and parts[2] == "SKILL.md" and _SKILL_DIR.match(parts[1]):
                found.add(parts[1])
    return _by_bytes(found)


def env_files_of(manifest: "dict[str, Any]", files: "Iterable[str]") -> "list[Any]":
    """The root file ``env`` read as dotenv, then ``env_files``."""
    found: "list[Any]" = []
    if default_on(manifest, "env") and "env" in _members(files):
        found = [{"path": "env", "format": "dotenv"}]
    return _merge(found, _declared(manifest, "env_files"))


def default_script(
    manifest: "dict[str, Any]", field: str, stem: str, files: "Iterable[str]", os_name: str,
    interpreters: "Iterable[str]",
) -> "tuple[Optional[dict[str, Any]], bool, list[str], list[Any]]":
    """The default ``<stem>.<ext>`` a consumer runs for ``field``
    (``env_scripts`` / ``env``, ``setup`` / ``setup``): ``(entry, configured,
    present, others)`` -- the chosen entry, whether a declared entry
    configured it, the candidate files present (reported when none is
    chosen), and the declared entries that name no candidate. A declared
    entry naming a candidate keeps its place in :data:`DEFAULT_SCRIPTS`, with
    its own settings."""
    declared = _declared(manifest, field)
    if not default_on(manifest, field):
        return None, False, [], declared
    members = set(_members(files))
    present = [stem + ext for ext, _interpreter, _os in DEFAULT_SCRIPTS if stem + ext in members]
    configured = {entry["path"]: entry for entry in declared
                  if isinstance(entry, dict) and entry.get("path") in present}
    others = [entry for entry in declared if _named(entry) not in configured]
    can = set(interpreters)
    for ext, interpreter, names in DEFAULT_SCRIPTS:
        path = stem + ext
        if path not in present:
            continue
        entry = configured.get(path, {"interpreter": interpreter, "path": path, "os": names})
        if os_matches(entry, os_name) and entry["interpreter"] in can:
            return entry, path in configured, present, others
    return None, False, present, others


# --------------------------------------------------------------------------- #
# Applying one overlay.
# --------------------------------------------------------------------------- #


def apply_overlay(env: Environment, manifest: "dict[str, Any]", ctx: Context,
                  weakenings: "list[tuple[str, str]]") -> "list[dict[str, Any]]":
    """Steps 2.1 to 2.3 of the order of application -- the overlay's env
    files for this OS, its ``env``, its ``paths``, each after its defaults --
    applied to ``env``. Returns the env scripts that run next (the default
    one, then the declared ones; none when the consumer turned them off),
    each reported, and reports ``script-not-run`` for a default script
    present that cannot run. Raises :class:`Refused`; ``env`` may then hold
    part of the overlay's changes, so a caller applies to a copy.

    Text values are substituted as they are set, against ``env`` as it
    stands, with ``ctx``'s :meth:`Context.placeholders` seen too."""
    paths = overlay_paths(manifest, ctx)
    listed = {entry["variable"].upper() for entry in paths}
    for name in manifest.get("env", {}):
        if name.upper() in listed:
            raise Refused("env-path-conflict", name)
    outer, env.extra = env.extra, ctx.placeholders()
    try:
        for item in env_files_of(manifest, ctx.files):
            item = {"path": item} if isinstance(item, str) else item
            if os_matches(item, ctx.os):
                found = read_env_file(item, ctx, listed)
                if isinstance(found, list):
                    _apply_dotenv(env, found, weakenings)
                else:
                    _apply_mapping(env, found, ctx, weakenings)
        _apply_mapping(env, manifest.get("env", {}), ctx, weakenings)
    finally:
        env.extra = outer
    apply_paths(env, paths, ctx, weakenings)
    scripts: "list[dict[str, Any]]" = []
    if ctx.env_scripts_enabled:
        chosen, _configured, present, others = default_script(
            manifest, "env_scripts", "env", ctx.files, ctx.os, ctx.interpreters)
        if chosen is None:
            weakenings.extend(("script-not-run", path) for path in present)
        for script in ([chosen] if chosen else []) + others:
            if os_matches(script, ctx.os):
                selected = {"interpreter": script["interpreter"], "path": script["path"],
                            "args": list(script.get("args", [])), "timeout": script.get("timeout", 60)}
                scripts.append(selected)
                weakenings.append(("env-script", script["path"]))
                if selected["timeout"] == 0:
                    weakenings.append(("no-timeout", script["path"]))
    return scripts


def read_env_script_output(data: bytes, *, max_bytes: int = DEFAULT_MAX_BYTES) -> "dict[str, Optional[str]]":
    """An env script's standard output: the variables it sets (a string) or
    unsets (None), in order. The whole output is one JSON object, possibly
    over several lines; or else every line that is not blank is one. Raises
    :class:`ReadError` (``env-script-output`` for a name or value it may not
    set)."""
    if len(data) > max_bytes:
        raise ReadError("too-large")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ReadError("invalid-utf8")
    whole = text.strip(" \t\r\n")
    if not whole:
        return {}
    try:
        objects = [_read_json(whole)]
    except ReadError as error:
        if error.code != "parse-error":
            raise
        objects = []
        for line in text.split("\n"):
            line = line[:-1] if line.endswith("\r") else line
            if line.strip(" \t"):
                objects.append(_read_json(line))
    out: "dict[str, Optional[str]]" = {}
    for value in objects:
        if not isinstance(value, dict):
            raise ReadError("env-script-output", "not an object")
        for name, item in value.items():
            if not valid_variable_name(name) or is_reserved_variable(name):
                raise ReadError("env-script-output", "variable name")
            if item is not None and not (isinstance(item, str) and "\0" not in item):
                raise ReadError("env-script-output", "value")
            out[name] = item
    return out


def setup_of(manifest: "dict[str, Any]", files: "Iterable[str]", os_name: str,
             interpreters: "Iterable[str]") -> "tuple[Optional[dict[str, Any]], list[str]]":
    """``(variant, not_run)``: the setup that runs on ``os_name`` -- the first
    declared variant that applies, else the default ``setup.<ext>`` (taken to
    need the network unless a declared entry configured it) -- normalized with
    its ``args``, ``timeout`` and ``network``; and the default scripts present
    that cannot run, when none was chosen."""
    chosen, configured, present, others = default_script(manifest, "setup", "setup", files, os_name, interpreters)
    setup = first_variant(others, os_name)
    network = False
    not_run: "list[str]" = []
    if setup is None:
        setup = chosen
        network = not configured
        if setup is None:
            not_run = present
    if setup is None:
        return None, not_run
    return {"interpreter": setup["interpreter"], "path": setup["path"], "args": list(setup.get("args", [])),
            "timeout": setup.get("timeout", 300), "network": setup.get("network", network)}, not_run


def plan(manifest: "dict[str, Any]", ctx: Context, *, pinned: bool, enforces_network: bool = False,
         scripts: "Optional[list[dict[str, Any]]]" = None) -> "dict[str, Any]":
    """What one overlay needs and declares on ``ctx.os``, beyond its
    environment: whether the OS is supported, the selected setup (with its
    defaults), Python and Node variants, the dependency files
    (:func:`dependencies_of`), the interpreters and commands a probe looks
    for, the references not granted, and the weakenings (not counting the
    environment's own). ``scripts`` are the env scripts that will run.
    Raises :class:`Refused` for a requirements file only partly hashed."""
    os_name = ctx.os
    weakenings: "list[tuple[str, str]]" = []
    if not pinned:
        weakenings.append(("unpinned", ""))
    runtime = manifest.get("runtime", {})
    setup, not_run = setup_of(manifest, ctx.files, os_name, ctx.interpreters)
    weakenings.extend(("script-not-run", path) for path in not_run)
    if setup is not None:
        if setup["network"]:
            weakenings.append(("setup-network", setup["path"]))
        elif not enforces_network:
            weakenings.append(("network-not-enforced", setup["path"]))
        if setup["timeout"] == 0:
            weakenings.append(("no-timeout", setup["path"]))
    python = first_variant(runtime.get("python", []), os_name)
    if python is not None:
        python = {key: value for key, value in python.items() if key != "os"}
    dependencies = dependencies_of(manifest, ctx.files, os_name, ctx.read)
    for dependency in dependencies:
        # Installing fetches packages: it needs the network, locked or not.
        weakenings.append(("deps-network", dependency["file"]))
        if dependency["lock"] is None:
            weakenings.append(("unlocked-dependencies", dependency["file"]))
    node = first_variant(runtime.get("node", []), os_name)
    if node is not None:
        node = {key: value for key, value in node.items() if key != "os"}
    interpreters = {s["interpreter"] for s in scripts or ()}
    if setup is not None:
        interpreters.add(setup["interpreter"])
    for target in manifest.get("scripts", {}).values():
        if isinstance(target, str) or (isinstance(target, dict) and os_matches(target, os_name)):
            found = script_interpreter(target)
            if found:
                interpreters.add(found)
    if python is not None:
        interpreters.add("python")
    if node is not None:
        interpreters.add("node")
    commands = set()
    for command in runtime.get("commands", []):
        if isinstance(command, str):
            commands.add(command)
        elif os_matches(command, os_name):
            commands.add(command["name"])
    for dependency in dependencies:
        interpreters.add(dependency["runtime"])
        if dependency["runtime"] == "node":
            commands.add("npm")
    secrets = ctx.secrets
    ungranted: "set[str]" = set()
    for server in manifest.get("mcp", {}).get("servers", []):
        for value in server.get("env", {}).values():
            if is_reference(value):
                if not secrets.granted(value):
                    ungranted.add(reference_name(value))
                elif value.get("provider") == "env":
                    weakenings.append(("env-passthrough", value["key"]))
                else:
                    weakenings.append(("secret-read", reference_name(value)))
        for name in list(server.get("env_passthrough", [])) + list(server.get("headers_env", {}).values()):
            if name in secrets.grants.get("env", ()):
                weakenings.append(("env-passthrough", name))
            else:
                ungranted.add("env:" + name)
        if insecure_url(server.get("url", "")):
            weakenings.append(("insecure-transport", server["name"]))
    for entry in manifest.get("env", {}).values():
        if is_reference(entry) and entry.get("optional") and not secrets.granted(entry):
            ungranted.add(reference_name(entry))
    return {
        "supported": "os" not in runtime or os_matches(runtime, os_name),
        "setup": setup,
        "python": python,
        "node": node,
        "dependencies": dependencies,
        "needs": {"interpreters": sorted(interpreters), "commands": sorted(commands)},
        "ungranted": sorted(ungranted),
        "weakenings": weakenings,
    }


class Activation(object):
    """What :func:`effective` found for one overlay: the ``error`` that
    refused it, or its environment, the env scripts to run, the rest of
    :func:`plan` and every weakening as ``(id, subject)``."""

    def __init__(self, *, error: "Optional[Refused]" = None, env: "Optional[Environment]" = None,
                 scripts: "Optional[list[dict[str, Any]]]" = None, plan: "Optional[dict[str, Any]]" = None,
                 weakenings: "Optional[list[tuple[str, str]]]" = None):
        self.error = error
        self.env = env
        self.scripts = scripts or []
        self.plan = plan or {}
        self.weakenings = weakenings or []


def effective(manifest: "dict[str, Any]", ctx: Context, *, base_env: "dict[str, str]", pinned: bool,
              enforces_network: bool = False) -> Activation:
    """One overlay applied alone over ``base_env``: its root (and state,
    ``ctx.state``) variable, then its declarations with their defaults, then
    :func:`plan`."""
    env = Environment(base_env, ctx.windows)
    env.set(root_variable(ctx.identity), ctx.root)
    if ctx.state is not None:
        env.set(state_variable(ctx.identity), ctx.state)
    weakenings: "list[tuple[str, str]]" = []
    try:
        scripts = apply_overlay(env, manifest, ctx, weakenings)
        found = plan(manifest, ctx, pinned=pinned, enforces_network=enforces_network, scripts=scripts)
    except Refused as refused:
        return Activation(error=refused)
    weakenings = found.pop("weakenings") + weakenings
    return Activation(env=env, scripts=scripts, plan=found, weakenings=weakenings)


#: What a consumer is doing to an overlay when it decides what to prepare:
#: adding it, a sync (a fetch of its source, a change of source, ref or pin,
#: or setup run again on request), activating a digest outside a sync, and
#: removing it.
SETUP_CHANGES = ("add", "sync", "activate", "remove")
#: The phases setup is told, first argument and ``AOS_SETUP_PHASE``.
SETUP_PHASES = ("install", "sync", "remove")


def setup_phase(change: str, recorded: "Optional[str]", digest: "Optional[str]") -> "Optional[str]":
    """The phase of an overlay's preparation for ``change`` (one of
    :data:`SETUP_CHANGES`), or None when nothing runs. ``recorded`` is the
    digest of the last preparation that succeeded on this OS, None when
    there is none; ``digest`` the one at hand. Add and sync run ``install``
    with no record, ``sync`` with any; activation runs them only for a digest
    other than the recorded one; removal runs ``remove`` after a record."""
    if change not in SETUP_CHANGES:
        raise ValueError(change)
    if change == "remove":
        return "remove" if recorded is not None else None
    if recorded is None:
        return "install"
    if change != "activate" or recorded.lower() != (digest or "").lower():
        return "sync"
    return None


def preparation(change: str, record: "Optional[dict[str, str]]", digest: "Optional[str]",
                setup: "Optional[dict[str, Any]]", dependencies: "list[dict[str, Any]]",
                fails: "Optional[str]" = None) -> "dict[str, Any]":
    """What preparing an overlay does: ``setup_phase``, ``setup_arguments``
    (the phase, then the variant's ``args``), the ``steps`` in order
    (``empty-state``, ``deps:<runtime>``, ``setup``, ``record`` or
    ``delete``), the ``record`` after them (``{"digest", "phase"}``) and
    whether the digest ``activates``. Nothing is prepared without a
    dependency file or a setup. ``fails`` names the step that fails
    (``deps`` or ``setup``): the steps stop there, except that a removal
    still deletes everything; only the recorded digest stays active."""
    recorded = record["digest"] if record else None
    phase = None
    if setup is not None or dependencies:
        phase = setup_phase(change, recorded, digest)
    steps: "list[str]" = []
    if phase == "install":
        steps.append("empty-state")
    if phase in ("install", "sync"):
        steps += ["deps:" + dependency["runtime"] for dependency in dependencies]
    if phase is not None and setup is not None:
        steps.append("setup")
    failed = False
    if fails is not None:
        for index, step in enumerate(steps):
            if step.split(":")[0] == fails:
                steps, failed = steps[:index + 1], True
                break
    if change == "remove":
        steps.append("delete")
        after, activates = None, False
    elif phase is None:
        after, activates = record, True
    elif failed:
        after = record
        activates = recorded is not None and recorded.lower() == (digest or "").lower()
    else:
        steps.append("record")
        after, activates = {"digest": digest, "phase": phase}, True
    return {
        "setup_phase": phase,
        "setup_arguments": None if phase is None or setup is None else [phase] + list(setup["args"]),
        "steps": steps,
        "record": after,
        "activates": activates,
    }


_PINNED_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(?:\[[^\]]*\])?\s*===?\s*([^\s;\\]+)")


def asked_versions(runtime: str, text: "Optional[str]") -> "dict[str, str]":
    """Package name to the version (Python, an ``==`` pin; names normalized
    as PEP 503 does) or range (Node.js, ``dependencies``) a dependency file
    asks for."""
    if text is None:
        return {}
    if runtime == "node":
        try:
            declared = json.loads(text).get("dependencies", {})
        except (ValueError, AttributeError):
            return {}
        return {name: str(spec) for name, spec in declared.items()} if isinstance(declared, dict) else {}
    pins = {}
    for line in re.sub(r"\\\r?\n", " ", text).splitlines():
        match = _PINNED_REQUIREMENT.match(line.strip())
        if match:
            pins[re.sub(r"[-_.]+", "-", match.group(1)).lower()] = match.group(2)
    return pins


def environment_key(dependency: "dict[str, Any]", identity: str) -> "tuple[str, str, str]":
    """``(runtime, environment, owner)``: the owner is the identity for an
    isolated environment, empty for a shared or named one."""
    isolated = dependency["environment"] == "isolated"
    return dependency["runtime"], dependency["environment"], identity if isolated else ""


def environments(members: "list[tuple[str, dict[str, Any], Optional[str]]]",
                 removed: "Iterable[tuple[str, dict[str, Any]]]" = ()) -> "dict[str, Any]":
    """The environments overlays install into. ``members`` are ``(identity,
    dependency, text of its file)`` in composition order; ``removed`` the
    ``(identity, dependency)`` of overlays being removed. Two members of one
    environment asking for one package at different versions conflict, and
    neither is installed or activated. Returns ``environments`` (each with
    its ``key``, ``overlays``, ``files`` and whether it is ``locked``: every
    file a lock for Python, one locked member for Node.js), ``conflicts``,
    ``blocked`` and ``left`` (what each removed dependency's environment
    becomes: ``rebuilt`` or ``deleted``)."""
    groups: "dict[tuple[str, str, str], list[tuple[str, dict[str, Any], Optional[str]]]]" = {}
    for identity, dependency, text in members:
        groups.setdefault(environment_key(dependency, identity), []).append((identity, dependency, text))
    conflicts = []
    blocked: "list[str]" = []
    for (runtime, name, _owner), group in groups.items():
        asked: "dict[str, dict[str, list[str]]]" = {}
        for identity, dependency, text in group:
            for package, version in asked_versions(runtime, text).items():
                asked.setdefault(package, {}).setdefault(version, []).append(identity)
        for package in sorted(asked, key=lambda p: p.encode("utf-8")):
            if len(asked[package]) > 1:
                involved = [i for i, _d, _t in group if any(i in v for v in asked[package].values())]
                conflicts.append({"runtime": runtime, "environment": name, "package": package,
                                  "overlays": involved})
                blocked += [i for i in involved if i not in blocked]
    built = []
    remaining = set()
    for key, group in groups.items():
        kept = [(i, d) for i, d, _t in group if i not in blocked]
        if not kept:
            continue
        remaining.add(key)
        locks = [d["lock"] for _i, d in kept]
        if key[0] == "node":
            # npm installs from one lock; several overlays' locks cannot be combined.
            locked = len(kept) == 1 and locks[0] is not None
        else:
            locked = all(lock is not None for lock in locks)
        built.append({"key": key, "runtime": key[0], "environment": key[1], "overlays": [i for i, _d in kept],
                      "files": [[i, d["file"]] for i, d in kept], "locked": locked})
    left = [{"overlay": identity, "runtime": dependency["runtime"], "environment": dependency["environment"],
             "then": "rebuilt" if environment_key(dependency, identity) in remaining else "deleted"}
            for identity, dependency in removed]
    return {"environments": built, "conflicts": conflicts, "blocked": blocked, "left": left}


def insecure_url(url: str) -> bool:
    """Plain ``http`` to a host other than loopback."""
    if url[:7].lower() != "http://":
        return False
    authority = url[7:].split("/", 1)[0].split("?", 1)[0].lower()
    if authority.startswith("["):
        host = authority.split("]", 1)[0] + "]"
    else:
        host = authority.rsplit(":", 1)[0]
    return host not in ("localhost", "[::1]") and not host.startswith("127.")


# --------------------------------------------------------------------------- #
# Resolution and composition order.
# --------------------------------------------------------------------------- #


class ResolveError(Exception):
    """A ``requires`` cycle; ``chain`` names it, ``a -> b -> a``."""

    def __init__(self, code: str, chain: "list[str]"):
        super().__init__("%s: %s" % (code, " -> ".join(chain)))
        self.code = code
        self.chain = chain


def resolve(requires_of: "Callable[[str], Optional[Iterable[str]]]",
            requested: "Iterable[str]") -> "tuple[list[str], list[list[str]]]":
    """The visiting order of the active overlays and the missing
    requirements ``[requirer, name]``. ``requires_of(identity)`` gives an
    overlay's ``requires``, or None when no source offers it. Raises
    :class:`ResolveError` on a cycle."""
    order: "list[str]" = []
    missing: "list[list[str]]" = []

    def visit(name: str, chain: "list[str]") -> None:
        if name in order:
            return
        if name in chain:
            raise ResolveError("cycle", chain[chain.index(name):] + [name])
        requires = requires_of(name)
        if requires is None:
            missing.append([chain[-1] if chain else "", name])
            return
        for dep in requires:
            visit(normalize_name(dep), chain + [name])
        order.append(name)

    for name in requested:
        visit(normalize_name(name), [])
    return order, missing


def order_key(priority: int, name: "Optional[str]", identity: str) -> "tuple[int, bytes, bytes]":
    """The composition sort key: ``(priority, name, identity)``, strings by
    their UTF-8 bytes; ``name`` falls back to the identity."""
    shown = name if isinstance(name, str) and name else identity
    return (priority, shown.encode("utf-8", "surrogatepass"), identity.encode("utf-8", "surrogatepass"))
