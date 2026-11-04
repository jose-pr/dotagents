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


def read_dotenv(data: bytes) -> "dict[str, str]":
    """A ``dotenv`` env file: literal values, no references, escapes or
    expansion. Raises :class:`ReadError`."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ReadError("invalid-utf8")
    out: "dict[str, str]" = {}
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
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name.upper() in folded:
            raise ReadError("duplicate-key", name)
        folded.add(name.upper())
        out[name] = value
    return out


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
PATH_VARIABLES = frozenset(("PATH", "PYTHONPATH"))


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
        c.keys(entry, ("version", "requirements", "lock", "lock_digest", "os"), here)
        if "version" in entry:
            c.pattern(entry["version"], _VERSION, here + "/version")
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
            c.keys(entry, ("version", "os"), here)
            if "version" in entry:
                c.pattern(entry["version"], _VERSION, here + "/version")
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
            if key in ("bin", "lib", "env", "setup"):
                c.type(value, "boolean", "/defaults/" + key)
            else:
                c.any_value(value, "/defaults/" + key)
    known = {"name", "description", "requires", "routing", "rules", "priority", "context", "skills", "env",
             "env_files", "paths", "env_scripts", "runtime", "setup", "scripts", "mcp", "harness", "defaults"}
    for key, value in doc.items():
        if key not in known:
            c.any_value(value, "/" + key)
    if files is not None:
        have = set(files)
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
    case, and setting one that exists under another case keeps that name."""

    def __init__(self, values: "dict[str, str]", windows: bool):
        self.windows = windows
        self.values = dict(values)

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

    def set(self, name: str, value: str) -> None:
        self.values[self.key(name) or name] = value

    def unset(self, name: str) -> None:
        key = self.key(name)
        if key is not None:
            del self.values[key]

    def copy(self) -> "Environment":
        return Environment(self.values, self.windows)


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
    ``None`` when absent), the :class:`Secrets`, and whether env scripts are
    enabled."""

    def __init__(
        self,
        *,
        os_name: str,
        root: str,
        identity: str,
        read: "Callable[[str], Optional[bytes]]",
        secrets: "Optional[Secrets]" = None,
        env_scripts_enabled: bool = False,
        default_paths: "Iterable[dict[str, Any]]" = (),
        default_env_files: "Iterable[dict[str, Any]]" = (),
        default_env_scripts: "Iterable[dict[str, Any]]" = (),
    ):
        self.os = os_name
        self.windows = os_name == "windows"
        self.root = root
        self.identity = identity
        self.read = read
        self.secrets = secrets or Secrets(windows=self.windows)
        self.env_scripts_enabled = env_scripts_enabled
        #: Path additions the overlay makes without declaring them
        #: (:func:`default_paths`), applied before its ``paths``.
        self.default_paths = list(default_paths)
        #: The env file and env script the overlay has without declaring
        #: them (:func:`default_env_file`, :func:`default_script`), applied
        #: before its declared ones.
        self.default_env_files = list(default_env_files)
        self.default_env_scripts = list(default_env_scripts)


def _apply_mapping(env: Environment, mapping: "dict[str, Any]", ctx: Context,
                   weakenings: "list[tuple[str, str]]") -> None:
    before = env.copy()
    for name, entry in mapping.items():
        if is_reserved_variable(name):
            raise Refused("reserved-variable", name)
        if isinstance(entry, str):
            entry = {"value": entry}
        if entry.get("if_unset") and before.get(name) is not None:
            continue
        if "value" in entry:
            value: "Optional[str]" = entry["value"]
        elif "path" in entry:
            value = resolve_path(ctx.root, entry["path"], ctx.windows)
        else:
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
        env.set(name, value)  # type: ignore[arg-type]


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
            resolved = resolve_path(ctx.root, entry["path"], ctx.windows)
            if resolved in seen:
                continue
            seen.add(resolved)
            (append if entry.get("position") == "append" else prepend).append(resolved)
        kept = [c for c in (env.get(variable) or "").split(separator) if c and c not in seen]
        env.set(variable, separator.join(prepend + kept + append))


def read_env_file(item: "dict[str, Any]", ctx: Context, listed: "set[str]") -> "dict[str, Any]":
    """One env file's mapping, checked. Raises :class:`Refused`."""
    data = ctx.read(item["path"])
    if data is None:
        raise Refused("missing-file", item["path"])
    try:
        if item.get("format", "aos") == "dotenv":
            mapping: "dict[str, Any]" = dict(read_dotenv(data))
        else:
            mapping = read_document(data)
    except ReadError as exc:
        raise Refused("invalid-env-file", item["path"], exc.code)
    if validate({"env": mapping}):
        raise Refused("invalid-env-file", item["path"])
    for name in mapping:
        if name.upper() in listed:
            raise Refused("env-path-conflict", name)
    return mapping


def apply_overlay(env: Environment, manifest: "dict[str, Any]", ctx: Context,
                  weakenings: "list[tuple[str, str]]") -> "list[dict[str, Any]]":
    """Steps 2.1 to 2.3 of the order of application -- the overlay's env
    files for this OS, its ``env``, its ``paths`` -- applied to ``env``.
    Returns the env scripts that run next (none unless enabled), each
    reported. Raises :class:`Refused`; ``env`` may then hold part of the
    overlay's changes, so a caller applies to a copy."""
    listed = {entry["variable"].upper() for entry in manifest.get("paths", [])}
    for item in ctx.default_env_files + list(manifest.get("env_files", [])):
        item = {"path": item} if isinstance(item, str) else item
        if os_matches(item, ctx.os):
            _apply_mapping(env, read_env_file(item, ctx, listed), ctx, weakenings)
    _apply_mapping(env, manifest.get("env", {}), ctx, weakenings)
    apply_paths(env, ctx.default_paths + list(manifest.get("paths", [])), ctx, weakenings)
    scripts: "list[dict[str, Any]]" = []
    if ctx.env_scripts_enabled:
        for script in ctx.default_env_scripts + list(manifest.get("env_scripts", [])):
            if os_matches(script, ctx.os):
                selected = {"interpreter": script["interpreter"], "path": script["path"],
                            "args": list(script.get("args", [])), "timeout": script.get("timeout", 60)}
                scripts.append(selected)
                weakenings.append(("env-script", script["path"]))
                if selected["timeout"] == 0:
                    weakenings.append(("no-timeout", script["path"]))
    return scripts


#: The path additions an overlay makes by default: ``bin`` prepended to
#: ``PATH`` and ``lib`` appended to ``PYTHONPATH``, each when that directory
#: exists, unless ``[defaults]`` turns it off (``bin = false``, ``lib = false``).
DEFAULT_PATHS = (
    ("bin", {"variable": "PATH", "path": "bin"}),
    ("lib", {"variable": "PYTHONPATH", "path": "lib", "position": "append"}),
)


def default_paths(manifest: "dict[str, Any]", is_dir: "Callable[[str], bool]") -> "list[dict[str, Any]]":
    """The default path additions of an overlay (:data:`DEFAULT_PATHS`):
    ``is_dir(relpath)`` says whether that directory exists under the root."""
    defaults = manifest.get("defaults", {})
    return [dict(entry) for key, entry in DEFAULT_PATHS if defaults.get(key, True) and is_dir(entry["path"])]


#: The order a default script (``env.<ext>``, ``setup.<ext>``) is chosen in:
#: the first that exists and that this OS can run.
DEFAULT_SCRIPT_ORDER = ((".py", "python"), (".ps1", "powershell"), (".bash", "bash"), (".sh", "sh"),
                        (".js", "node"), (".mjs", "node"))


def default_env_file(manifest: "dict[str, Any]", is_file: "Callable[[str], bool]") -> "list[dict[str, Any]]":
    """An overlay's root ``env`` file, read as ``dotenv``, unless
    ``[defaults] env = false``."""
    if manifest.get("defaults", {}).get("env", True) and is_file("env"):
        return [{"path": "env", "format": "dotenv"}]
    return []


def default_script(manifest: "dict[str, Any]", stem: str, is_file: "Callable[[str], bool]",
                   can_run: "Callable[[str], bool]") -> "tuple[Optional[dict[str, Any]], list[str]]":
    """``(script, skipped)``: the first ``<stem>.<ext>`` at the root (in
    :data:`DEFAULT_SCRIPT_ORDER`) whose interpreter ``can_run``, as
    ``{interpreter, path}``, and the ones present that cannot run here.
    ``stem`` is ``env`` or ``setup``; ``[defaults] <stem> = false`` turns it off."""
    if not manifest.get("defaults", {}).get(stem, True):
        return None, []
    skipped = []
    for ext, interpreter in DEFAULT_SCRIPT_ORDER:
        path = stem + ext
        if is_file(path):
            if can_run(interpreter):
                return {"interpreter": interpreter, "path": path}, skipped
            skipped.append(path)
    return None, skipped


def env_script_changes(stdout: bytes, *, max_bytes: int = DEFAULT_MAX_BYTES) -> "list[tuple[str, Optional[str]]]":
    """An env script's output as ``(name, value or None to unset)`` in order.
    Raises :class:`Refused` (``env-script-output``) on anything else."""
    if len(stdout) > max_bytes:
        raise Refused("env-script-output", "too large")
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise Refused("env-script-output", "not UTF-8")
    out: "list[tuple[str, Optional[str]]]" = []
    for line in text.split("\n"):
        if line.endswith("\r"):
            line = line[:-1]
        if not line.strip(" \t"):
            continue
        try:
            obj = _read_json(line)
        except ReadError:
            raise Refused("env-script-output", "a line that is not a JSON object")
        if not isinstance(obj, dict):
            raise Refused("env-script-output", "a line that is not a JSON object")
        for name, value in obj.items():
            if not valid_variable_name(name) or is_reserved_variable(name):
                raise Refused("env-script-output", name)
            if value is not None and not (isinstance(value, str) and "\0" not in value):
                raise Refused("env-script-output", name)
            out.append((name, value))
    return out


def plan(manifest: "dict[str, Any]", ctx: Context, *, pinned: bool, enforces_network: bool = False,
         scripts: "Optional[list[dict[str, Any]]]" = None) -> "dict[str, Any]":
    """What one overlay needs and declares on ``ctx.os``, beyond its
    environment: whether the OS is supported, the selected setup, Python and
    Node variants, the interpreters and commands a probe looks for, the
    references not granted, and the weakenings (not counting the
    environment's own). ``scripts`` are the env scripts that will run."""
    os_name = ctx.os
    weakenings: "list[tuple[str, str]]" = []
    if not pinned:
        weakenings.append(("unpinned", ""))
    runtime = manifest.get("runtime", {})
    setup = first_variant(manifest.get("setup", []), os_name)
    if setup is not None:
        setup = {"interpreter": setup["interpreter"], "path": setup["path"], "args": list(setup.get("args", [])),
                 "timeout": setup.get("timeout", 300), "network": setup.get("network", False)}
        if setup["network"]:
            weakenings.append(("setup-network", setup["path"]))
        elif not enforces_network:
            weakenings.append(("network-not-enforced", setup["path"]))
        if setup["timeout"] == 0:
            weakenings.append(("no-timeout", setup["path"]))
    python = first_variant(runtime.get("python", []), os_name)
    if python is not None:
        python = {key: value for key, value in python.items() if key != "os"}
        if "requirements" in python:
            weakenings.append(("unlocked-dependencies", python["requirements"]))
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
              state: "Optional[str]" = None, enforces_network: bool = False) -> Activation:
    """One overlay applied alone over ``base_env``: its root (and state)
    variable, then its declarations, then :func:`plan`."""
    env = Environment(base_env, ctx.windows)
    env.set(root_variable(ctx.identity), ctx.root)
    if state is not None:
        env.set(state_variable(ctx.identity), state)
    weakenings: "list[tuple[str, str]]" = []
    try:
        scripts = apply_overlay(env, manifest, ctx, weakenings)
    except Refused as refused:
        return Activation(error=refused)
    found = plan(manifest, ctx, pinned=pinned, enforces_network=enforces_network, scripts=scripts)
    weakenings = found.pop("weakenings") + weakenings
    return Activation(env=env, scripts=scripts, plan=found, weakenings=weakenings)


def skills_of(manifest: "dict[str, Any]", files: "Iterable[str]") -> "list[str]":
    """An overlay's skills: its ``skills`` list, else every
    ``skills/<name>/SKILL.md`` among its ``files``."""
    if "skills" in manifest:
        return list(manifest["skills"])
    found = []
    for path in files:
        parts = path.split("/")
        if len(parts) == 3 and parts[0] == "skills" and parts[2] == "SKILL.md":
            found.append(parts[1])
    return sorted(found)


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
