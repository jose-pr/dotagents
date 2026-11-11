"""Validating a manifest: the shape of every field, then the rules a shape cannot state.

A problem is an ``Issue``: a ``Class`` and an RFC 6901 pointer to where. Nothing here recurses
over the manifest's own nesting, so a hostile document cannot exhaust the stack.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, NamedTuple, Optional, Set, Tuple

from ._data import Document
from ._errors import Class, Error
from ._names import (
    is_reserved_variable,
    is_variable_name,
    overlay_name_shaped,
    valid_os_name,
    valid_overlay_name,
    valid_relative_path,
    valid_tool_name,
)
from ._source import _WHITE_SPACE, Source, SourceKind
from ._tree import OverlayTree

__all__ = ["Issue", "applies", "named_files", "skill_flags", "valid_license", "validate"]

_I64 = (-(2**63), 2**63 - 1)
_SPACE = frozenset(_WHITE_SPACE)
_DEFAULT_KEYS = ("bin", "lib", "env", "env_scripts", "setup", "deps", "context", "skills", "rules", "hooks")
_DEFAULT_PATHS = (
    ("bin", "PATH", "bin"),
    ("lib", "PYTHONPATH", "lib/python"),
    ("lib", "NODE_PATH", "lib/node"),
)
_SCRIPT_EXTENSIONS = (".py", ".js", ".mjs", ".cjs", ".ps1", ".bash", ".sh")
_ALNUM = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
_LOWER = frozenset("abcdefghijklmnopqrstuvwxyz")
_DIGITS = frozenset("0123456789")
_HEX = frozenset("0123456789abcdefABCDEF")
_SESSION_REASONS = ("new", "resume", "clear", "compact", "fork")
_TOOL_KINDS = (
    "read",
    "edit",
    "delete",
    "move",
    "search",
    "execute",
    "think",
    "fetch",
    "switch_mode",
    "other",
)
_EVENTS = (
    "pre-env",
    "session-start",
    "session-end",
    "prompt",
    "turn-end",
    "cd",
    "pre-compact",
    "pre-tool",
    "post-tool",
)


class Issue(NamedTuple):
    """One problem with a manifest: what (``code``) and where (``at``, a JSON pointer such as
    ``/env/PATH`` or ``/setup/0/timeout``; empty for the manifest as a whole)."""

    code: Class
    at: str


def applies(issues: "Iterable[Issue]") -> bool:
    """Whether a manifest with these problems is still applied: a problem under ``/about``,
    which is informational, never stops it."""
    return all(issue.at == "/about" or issue.at.startswith("/about/") for issue in issues)


def _token(text: str) -> str:
    """One reference token of a JSON Pointer: ``~`` is ``~0`` and ``/`` is ``~1``."""
    return text.replace("~", "~0").replace("/", "~1")


def _join(at: str, key: Any) -> str:
    return "%s/%s" % (at, _token(str(key)))


class _Mark:
    """A literal piece of `_canonical`'s output, told apart from a value still to visit."""

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


def _canonical(value: Any) -> str:
    """``value`` as text that is equal for equal JSON values, built without recursion. Mapping
    keys are sorted, and ``1``, ``1.0`` and ``true`` stay different."""
    parts: "List[str]" = []
    stack: "List[Any]" = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, _Mark):
            parts.append(item.text)
        elif isinstance(item, dict):
            parts.append("{")
            stack.append(_Mark("}"))
            for key in sorted(item, key=str, reverse=True):
                stack.append(item[key])
                stack.append(_Mark("%r:" % (key,)))
        elif isinstance(item, list):
            parts.append("[")
            stack.append(_Mark("]"))
            for entry in reversed(item):
                stack.append(entry)
                stack.append(_Mark(","))
        else:
            parts.append("%s:%r" % (type(item).__name__, item))
    return "".join(parts)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _no_nul(text: str) -> bool:
    return "\0" not in text


def _is_line(text: str) -> bool:
    return bool(text) and not any(
        ord(c) <= 0x1F or 0x7F <= ord(c) <= 0x9F or c in "  " or "‪" <= c <= "‮" or "⁦" <= c <= "⁩"
        for c in text
    )


def _is_web_url(text: str) -> bool:
    if not text.startswith("https://"):
        return False
    rest = text[8:]
    cut = len(rest)
    for char in "/?#":
        found = rest.find(char)
        if 0 <= found < cut:
            cut = found
    host, tail = rest[:cut], rest[cut:]

    def control(c: str) -> bool:
        return ord(c) <= 0x20 or 0x7F <= ord(c) <= 0x9F

    return (
        bool(host) and not any(control(c) or c in "@\\" for c in host) and not any(control(c) for c in tail)
    )


def _is_mcp_url(text: str) -> bool:
    lower = text[:8].lower()
    if lower.startswith("https://"):
        rest = text[8:]
    elif lower.startswith("http://"):
        rest = text[7:]
    else:
        return False
    cut = len(rest)
    for char in "/?":
        found = rest.find(char)
        if 0 <= found < cut:
            cut = found
    host, tail = rest[:cut], rest[cut:]
    return (
        bool(host)
        and not any(c in "#@" or c in _SPACE for c in host)
        and not any(c == "#" or c in _SPACE for c in tail)
    )


def _is_version(text: str) -> bool:
    parts = text.split(".")
    return len(parts) <= 3 and all(1 <= len(p) <= 9 and all(c in _DIGITS for c in p) for p in parts)


def _is_environment_name(text: str) -> bool:
    return (
        bool(text)
        and text[0] in _ALNUM
        and text[-1] in _ALNUM
        and all(c in _ALNUM or c in "_.-" for c in text)
    )


def _is_digest(text: str) -> bool:
    return text.startswith("sha256:") and len(text) == 71 and all(c in _HEX for c in text[7:])


def _is_lowercase_id(text: str, first_digit: bool, extra: str = "") -> bool:
    return (
        bool(text)
        and (text[0] in _LOWER or (first_digit and text[0] in _DIGITS))
        and all(c in _LOWER or c in _DIGITS or c == "-" or c in extra for c in text)
    )


def _is_script_path(text: str) -> bool:
    return (
        valid_relative_path(text)
        and all(component[0] in _ALNUM or component[0] in "_+-" for component in text.split("/"))
        and all(c in _ALNUM or c in "_+-." for c in text.replace("/", ""))
    )


def _is_hook_run(text: str) -> bool:
    return _is_script_path(text) and text.endswith(_SCRIPT_EXTENSIONS)


def _is_command_name(text: str) -> bool:
    return bool(text) and text[0] in _ALNUM and all(c in _ALNUM or c in ".+_-" for c in text)


def _is_script_name(text: str) -> bool:
    return bool(text) and text[0] in _ALNUM and all(c in _ALNUM or c in "._-" for c in text)


def _is_header_name(text: str) -> bool:
    return bool(text) and all(c in _ALNUM or c in "!#$%&'*+.^_`|~-" for c in text)


def _is_git_ref(text: str) -> bool:
    def bad(c: str) -> bool:
        return (
            c in "/:\\@#"
            or c <= " "
            or "\x7f" <= c <= "\x9f"
            or c in "  ‎‏"
            or "‪" <= c <= "‮"
            or "⁦" <= c <= "⁩"
        )

    return bool(text) and text[0] != "-" and not any(bad(c) for c in text)


def _os_list_ok(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(n, str) and valid_os_name(n) for n in value)


def _object_form(entry: Any, source: str, flags: "Tuple[str, ...]", ok: "Callable[[Any], bool]") -> bool:
    if not isinstance(entry, dict):
        return False
    return ok(entry.get(source)) and all(
        key == source or (key in flags and isinstance(value, bool)) for key, value in entry.items()
    )


def _provider_form(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    provider, key = entry.get("provider"), entry.get("key")
    if not isinstance(provider, str) or not isinstance(key, str):
        return False
    provider_ok = (
        provider[:1] in _LOWER
        and bool(provider)
        and all(c in _LOWER or c in _DIGITS or c in "_-" for c in provider)
    )
    key_ok = bool(key) and _no_nul(key)
    if provider == "file":
        key_ok = key_ok and valid_relative_path(key)
    elif provider == "env":
        key_ok = key_ok and is_variable_name(key)

    def field_ok(field: str, value: Any) -> bool:
        if field in ("provider", "key"):
            return True
        if field in ("if_unset", "optional"):
            return isinstance(value, bool)
        if field in ("value", "path", "secret"):
            return False
        return isinstance(value, (str, bool)) or _is_int(value)

    return provider_ok and key_ok and all(field_ok(field, value) for field, value in entry.items())


def _env_value_ok(entry: Any) -> bool:
    """Whether ``entry`` is exactly one form of env value."""
    forms = (
        isinstance(entry, str) and _no_nul(entry),
        _object_form(entry, "value", ("if_unset",), lambda v: isinstance(v, str) and _no_nul(v)),
        _object_form(entry, "path", ("if_unset",), lambda v: isinstance(v, str) and valid_relative_path(v)),
        _object_form(entry, "secret", ("if_unset", "optional"), lambda v: isinstance(v, str) and bool(v)),
        _provider_form(entry),
    )
    return sum(forms) == 1


class _Check:
    def __init__(self) -> None:
        self.issues: "List[Issue]" = []

    def fail(self, code: Class, at: str) -> None:
        self.issues.append(Issue(code, at))

    def require(self, ok: bool, at: str) -> None:
        if not ok:
            self.fail(Class.SCHEMA, at)

    def string(self, value: Any, at: str, ok: "Callable[[str], bool]" = lambda _text: True) -> None:
        self.require(isinstance(value, str) and ok(value), at)

    def boolean(self, value: Any, at: str) -> None:
        self.require(isinstance(value, bool), at)

    def integer(self, value: Any, at: str, low: int, high: int) -> None:
        """An integer in ``low..=high``; a float of any value is ``integer-required``."""
        if isinstance(value, float):
            self.fail(Class.INTEGER_REQUIRED, at)
        else:
            self.require(_is_int(value) and low <= value <= high, at)

    def array(self, value: Any, at: str) -> "List[Any]":
        if isinstance(value, list):
            return value
        self.fail(Class.SCHEMA, at)
        return []

    def each(self, value: Any, at: str, item: "Callable[[Any, str], None]") -> None:
        for index, entry in enumerate(self.array(value, at)):
            item(entry, _join(at, index))

    def unique(self, value: Any, at: str) -> None:
        if isinstance(value, list):
            seen: "Set[str]" = set()
            for item in value:
                key = _canonical(item)
                if key in seen:
                    self.fail(Class.SCHEMA, at)
                    return
                seen.add(key)

    def object(self, value: Any, at: str, known: "Iterable[str]") -> "Optional[Dict[str, Any]]":
        """A mapping whose keys are only ``known``; ``None`` for something that is not one."""
        if not isinstance(value, dict):
            self.fail(Class.SCHEMA, at)
            return None
        known = tuple(known)
        for key in value:
            if key not in known:
                self.fail(Class.SCHEMA, _join(at, key))
        return value

    def required(self, mapping: "Dict[str, Any]", at: str, keys: "Iterable[str]") -> None:
        for key in keys:
            if key not in mapping:
                self.fail(Class.SCHEMA, at)

    def rel_path(self, value: Any, at: str) -> None:
        self.string(value, at, valid_relative_path)

    def os_list(self, value: Any, at: str) -> None:
        self.each(value, at, lambda name, here: self.string(name, here, valid_os_name))

    def variable(self, name: str, at: str) -> None:
        self.require(is_variable_name(name) and not is_reserved_variable(name), at)

    def document(self, doc: Document) -> None:
        for key, value in doc.items():
            at = "/" + _token(key)
            if key in ("name", "description"):
                self.string(value, at)
            elif key == "requires":
                self.each(value, at, lambda name, here: self.string(name, here, overlay_name_shaped))
            elif key == "routing":
                self.each(value, at, lambda line, here: self.string(line, here))
            elif key in ("rules", "context"):
                self.each(value, at, self.rel_path)
            elif key == "priority":
                self.integer(value, at, *_I64)
            elif key == "defaults":
                mapping = self.object(value, at, _DEFAULT_KEYS)
                for flag_key, flag in (mapping or {}).items():
                    self.boolean(flag, _join(at, flag_key))
            elif key == "skills":
                self.each(value, at, self.skill_entry)
                self.unique(value, at)
            elif key == "env":
                self.env_map(value, at)
            elif key == "env_files":
                self.each(value, at, self.env_file)
            elif key == "paths":
                self.each(value, at, self.path_addition)
            elif key == "env_scripts":
                self.each(value, at, lambda entry, here: self.script(entry, here, False))
            elif key == "setup":
                self.each(value, at, lambda entry, here: self.script(entry, here, True))
            elif key == "runtime":
                self.runtime(value, at)
            elif key == "scripts":
                self.scripts(value, at)
            elif key == "mcp":
                self.mcp(value, at)
            elif key == "harness":
                self.harness(value, at)
            elif key == "hooks":
                self.hooks(value, at)
            elif key == "about":
                self.about(value, at)

    def skill_entry(self, entry: Any, at: str) -> None:
        """A skill's name, or a table naming its source."""
        if isinstance(entry, str):
            self.require(valid_tool_name(entry), at)
        elif isinstance(entry, dict):
            mapping = self.object(entry, at, ("name", "source", "ref", "pin"))
            assert mapping is not None
            self.required(mapping, at, ("name", "source"))
            for key, value in mapping.items():
                here = _join(at, key)
                if key == "name":
                    self.string(value, here, valid_tool_name)
                elif key == "source":
                    self.string(value, here, lambda text: any(c not in " \t\r\n" for c in text))
                elif key == "ref":
                    self.string(value, here, _is_git_ref)
                elif key == "pin":
                    self.string(value, here, _is_digest)
        else:
            self.fail(Class.SCHEMA, at)

    def hooks(self, value: Any, at: str) -> None:
        """What runs at each event: a script, a table, or ``false`` for nothing."""
        if not isinstance(value, dict):
            self.fail(Class.SCHEMA, at)
            return
        for name, entry in value.items():
            if name not in _EVENTS:
                self.fail(Class.SCHEMA, at)
                continue
            here = _join(at, name)
            if entry is False:
                continue
            if isinstance(entry, str):
                self.require(_is_hook_run(entry), here)
            elif isinstance(entry, dict):
                self.hook_table(name, entry, here)
            else:
                self.fail(Class.SCHEMA, here)

    def hook_table(self, event: str, entry: "Dict[str, Any]", at: str) -> None:
        if event in ("pre-env", "pre-compact"):
            selects, sets, refuses = False, False, True
        elif event in ("session-start", "pre-tool"):
            selects, sets, refuses = True, True, True
        elif event == "prompt":
            selects, sets, refuses = False, True, True
        elif event == "post-tool":
            selects, sets, refuses = True, False, False
        else:
            selects, sets, refuses = False, event == "cd", False
        known = ["run", "timeout", "on_failure"] + (["match"] if selects else []) + (["sets"] if sets else [])
        mapping = self.object(entry, at, known)
        assert mapping is not None
        choices = _SESSION_REASONS if event == "session-start" else _TOOL_KINDS
        for key, value in mapping.items():
            here = _join(at, key)
            if key == "run":
                self.string(value, here, _is_hook_run)
            elif key == "timeout":
                self.integer(value, here, 1, 3600)
            elif key == "on_failure":
                self.string(value, here, lambda text: text == "report" or (refuses and text == "refuse"))
            elif key == "match":
                self.hook_list(value, here, lambda text: text in choices)
            elif key == "sets":
                self.hook_list(
                    value, here, lambda text: is_variable_name(text) and not is_reserved_variable(text)
                )

    def hook_list(self, value: Any, at: str, ok: "Callable[[str], bool]") -> None:
        """A list of one or more distinct strings that pass ``ok``."""
        if not isinstance(value, list):
            self.fail(Class.SCHEMA, at)
            return
        self.require(bool(value), at)
        for index, item in enumerate(value):
            self.string(item, _join(at, index), ok)
        self.unique(value, at)

    def env_map(self, value: Any, at: str) -> None:
        if not isinstance(value, dict):
            self.fail(Class.SCHEMA, at)
            return
        for name, entry in value.items():
            here = _join(at, name)
            self.variable(name, here)
            self.require(_env_value_ok(entry), here)

    def env_file(self, entry: Any, at: str) -> None:
        if isinstance(entry, str):
            self.rel_path(entry, at)
            return
        mapping = self.object(entry, at, ("path", "format", "os"))
        if mapping is None:
            return
        self.required(mapping, at, ("path",))
        for key, value in mapping.items():
            here = _join(at, key)
            if key == "path":
                self.rel_path(value, here)
            elif key == "format":
                self.string(value, here, lambda text: text in ("aos", "dotenv"))
            elif key == "os":
                self.os_list(value, here)

    def path_addition(self, entry: Any, at: str) -> None:
        mapping = self.object(entry, at, ("variable", "path", "position", "os"))
        if mapping is None:
            return
        self.required(mapping, at, ("variable", "path"))
        for key, value in mapping.items():
            here = _join(at, key)
            if key == "variable":
                self.require(
                    isinstance(value, str) and is_variable_name(value) and not is_reserved_variable(value),
                    here,
                )
            elif key == "path":
                self.rel_path(value, here)
            elif key == "position":
                self.string(value, here, lambda text: text in ("prepend", "append"))
            elif key == "os":
                self.os_list(value, here)

    def script(self, entry: Any, at: str, setup: bool) -> None:
        known = ("interpreter", "path", "args", "timeout", "os") + (("network",) if setup else ())
        mapping = self.object(entry, at, known)
        if mapping is None:
            return
        self.required(mapping, at, ("interpreter", "path"))
        for key, value in mapping.items():
            here = _join(at, key)
            if key == "interpreter":
                self.string(value, here, lambda name: _is_lowercase_id(name, False))
            elif key == "path":
                self.rel_path(value, here)
            elif key == "args":
                self.each(value, here, lambda arg, spot: self.string(arg, spot, _no_nul))
            elif key == "timeout":
                self.integer(value, here, 0, 2_147_483_647)
            elif key == "network":
                self.boolean(value, here)
            elif key == "os":
                self.os_list(value, here)

    def runtime(self, value: Any, at: str) -> None:
        mapping = self.object(value, at, ("os", "commands", "python", "node"))
        if mapping is None:
            return
        for key, item in mapping.items():
            here = _join(at, key)
            if key == "os":
                self.os_list(item, here)
            elif key == "commands":
                self.each(item, here, self.command)
            elif key == "python":
                self.each(item, here, lambda variant, spot: self.variant(variant, spot, True))
            elif key == "node":
                self.each(item, here, lambda variant, spot: self.variant(variant, spot, False))

    def command(self, command: Any, at: str) -> None:
        if isinstance(command, str):
            self.string(command, at, _is_command_name)
            return
        mapping = self.object(command, at, ("name", "os"))
        if mapping is None:
            return
        self.required(mapping, at, ("name",))
        if "name" in mapping:
            self.string(mapping["name"], _join(at, "name"), _is_command_name)
        if "os" in mapping:
            self.os_list(mapping["os"], _join(at, "os"))

    def variant(self, value: Any, at: str, python: bool) -> None:
        known = (
            ("version", "requirements", "lock", "lock_digest", "environment", "os")
            if python
            else ("version", "package", "environment", "os")
        )
        mapping = self.object(value, at, known)
        if mapping is None:
            return
        if "requirements" in mapping and "lock" in mapping:
            self.fail(Class.SCHEMA, at)
        if "lock_digest" in mapping and "lock" not in mapping:
            self.fail(Class.SCHEMA, at)
        for key, item in mapping.items():
            here = _join(at, key)
            if key == "version":
                self.string(item, here, _is_version)
            elif key in ("requirements", "lock"):
                self.rel_path(item, here)
            elif key == "lock_digest":
                self.string(item, here, _is_digest)
            elif key == "environment":
                self.string(item, here, _is_environment_name)
            elif key == "package":
                self.string(
                    item,
                    here,
                    lambda path: valid_relative_path(path)
                    and (path == "package.json" or path.endswith("/package.json")),
                )
            elif key == "os":
                self.os_list(item, here)

    def scripts(self, value: Any, at: str) -> None:
        if not isinstance(value, dict):
            self.fail(Class.SCHEMA, at)
            return
        for name, target in value.items():
            here = _join(at, name)
            self.require(_is_script_name(name) and not name.lower().endswith(".exe"), here)
            if target is False:
                fine = True
            elif isinstance(target, str):
                fine = _is_script_path(target) and target.endswith(_SCRIPT_EXTENSIONS)
            elif isinstance(target, dict):
                path, interpreter = target.get("path"), target.get("interpreter")
                fine = (
                    all(key in ("path", "interpreter", "os") for key in target)
                    and isinstance(path, str)
                    and _is_script_path(path)
                    and isinstance(interpreter, str)
                    and _is_lowercase_id(interpreter, False)
                    and ("os" not in target or _os_list_ok(target["os"]))
                )
            else:
                fine = False
            self.require(fine, here)

    def mcp(self, value: Any, at: str) -> None:
        mapping = self.object(value, at, ("servers", "skills"))
        if mapping is None:
            return
        if "servers" in mapping:
            self.each(mapping["servers"], _join(at, "servers"), self.server)
        if "skills" in mapping:
            self.each(mapping["skills"], _join(at, "skills"), self.skill_link)

    def skill_link(self, link: Any, at: str) -> None:
        mapping = self.object(link, at, ("name", "server"))
        if mapping is None:
            return
        self.required(mapping, at, ("name", "server"))
        for key, value in mapping.items():
            self.string(value, _join(at, key), valid_tool_name)

    def server(self, value: Any, at: str) -> None:
        known = (
            "name",
            "transport",
            "timeout",
            "command",
            "args",
            "env",
            "env_passthrough",
            "url",
            "headers_env",
        )
        mapping = self.object(value, at, known)
        if mapping is None:
            return
        self.required(mapping, at, ("name", "transport"))
        transport = mapping.get("transport")
        if transport == "stdio":
            misshapen = "command" not in mapping or "url" in mapping or "headers_env" in mapping
        elif transport == "streamable-http":
            misshapen = "url" not in mapping or any(
                key in mapping for key in ("command", "args", "env", "env_passthrough")
            )
        else:
            misshapen = False
        if misshapen:
            self.fail(Class.SCHEMA, at)
        for key, item in mapping.items():
            here = _join(at, key)
            if key == "name":
                self.string(item, here, valid_tool_name)
            elif key == "transport":
                self.string(item, here, lambda text: text in ("stdio", "streamable-http"))
            elif key == "timeout":
                number = isinstance(item, (int, float)) and not isinstance(item, bool)
                self.require(number and 0.001 <= item <= 2_147_483_647.0, here)
            elif key == "command":
                self.string(item, here, lambda command: bool(command) and _no_nul(command))
            elif key == "args":
                self.each(item, here, lambda arg, spot: self.string(arg, spot, _no_nul))
            elif key == "env":
                self.env_map(item, here)
            elif key == "env_passthrough":
                self.each(item, here, lambda name, spot: self.string(name, spot, is_variable_name))
                self.unique(item, here)
            elif key == "url":
                self.string(item, here, _is_mcp_url)
            elif key == "headers_env":
                if isinstance(item, dict):
                    for header, variable in item.items():
                        self.require(
                            _is_header_name(header)
                            and isinstance(variable, str)
                            and is_variable_name(variable),
                            _join(here, header),
                        )
                else:
                    self.fail(Class.SCHEMA, here)

    def harness(self, value: Any, at: str) -> None:
        if not isinstance(value, dict):
            self.fail(Class.SCHEMA, at)
            return
        for ident, hints in value.items():
            here = _join(at, ident)
            self.require(_is_lowercase_id(ident, True), here)
            if not isinstance(hints, dict):
                self.fail(Class.SCHEMA, here)
                continue
            for key, item in hints.items():
                there = _join(here, key)
                if key == "context":
                    self.each(item, there, self.rel_path)
                elif key == "skills":
                    self.boolean(item, there)
                elif key in ("model", "effort"):
                    self.string(item, there, bool)

    def about(self, value: Any, at: str) -> None:
        known = ("summary", "icon", "homepage", "repository", "license", "authors", "tags")
        mapping = self.object(value, at, known)
        if mapping is None:
            return
        for key, item in mapping.items():
            here = _join(at, key)
            if key == "summary":
                self.string(item, here, _is_line)
            elif key == "icon":
                self.string(
                    item,
                    here,
                    lambda path: valid_relative_path(path) and path.lower().endswith((".png", ".webp")),
                )
            elif key in ("homepage", "repository"):
                self.string(item, here, _is_web_url)
            elif key == "license":
                self.string(item, here, _license_chars)
                if isinstance(item, str) and not valid_license(item):
                    self.fail(Class.LICENSE_EXPRESSION, here)
            elif key == "authors":
                self.each(item, here, lambda author, spot: self.string(author, spot, _is_line))
            elif key == "tags":
                self.each(item, here, lambda tag, spot: self.string(tag, spot, _is_tag))
                self.unique(item, here)


def _is_tag(tag: str) -> bool:
    return (
        len(tag) <= 32
        and bool(tag)
        and all(bool(run) and all(c in _LOWER or c in _DIGITS for c in run) for run in tag.split("-"))
    )


def _license_chars(text: str) -> bool:
    return bool(text) and all(c in _ALNUM or c in ".+:() -" for c in text)


def _declared(doc: Document, field: str) -> "List[Any]":
    value = doc.get(field)
    return value if isinstance(value, list) else []


def _get(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def _skill_name(entry: Any) -> "Optional[str]":
    name = entry.get("name") if isinstance(entry, dict) else entry
    return name if isinstance(name, str) else None


def _skill_at(entry: Any, index: int) -> str:
    """The pointer of an entry's name: the entry, or the ``name`` of a table."""
    return "/skills/%d/name" % index if isinstance(entry, dict) else "/skills/%d" % index


def _semantic(doc: Document, check: _Check) -> None:
    for index, name in enumerate(_declared(doc, "requires")):
        if isinstance(name, str) and overlay_name_shaped(name) and not valid_overlay_name(name):
            check.fail(Class.RESERVED_NAME, "/requires/%d" % index)
    env_names: "Set[str]" = set()
    env = doc.get("env")
    if isinstance(env, dict):
        for name in env:
            if _upper(name) in env_names:
                check.fail(Class.ENV_NAME_CASE_DUPLICATE, "/env/" + _token(name))
            env_names.add(_upper(name))
            if is_reserved_variable(name):
                check.fail(Class.RESERVED_VARIABLE, "/env/" + _token(name))
    for index, entry in enumerate(_declared(doc, "paths")):
        variable = _get(entry, "variable")
        if isinstance(variable, str):
            if _upper(variable) in env_names:
                check.fail(Class.ENV_PATH_CONFLICT, "/paths/%d/variable" % index)
            if is_reserved_variable(variable):
                check.fail(Class.RESERVED_VARIABLE, "/paths/%d/variable" % index)
    _skills(doc, check)
    _manifest_named(doc, check)
    mcp = doc.get("mcp")
    if not isinstance(mcp, dict):
        return
    servers = mcp.get("servers") if isinstance(mcp.get("servers"), list) else []
    names: "Set[Optional[str]]" = set()
    for index, server in enumerate(servers):
        if not isinstance(server, dict):
            continue
        name = _canonical(server["name"]) if "name" in server else None
        if name in names:
            check.fail(Class.MCP_DUPLICATE_SERVER, "/mcp/servers/%d/name" % index)
        names.add(name)
        command = server.get("command")
        if (
            isinstance(command, str)
            and ("/" in command or "\\" in command)
            and not valid_relative_path(command)
        ):
            check.fail(Class.MCP_COMMAND, "/mcp/servers/%d/command" % index)
        url = server.get("url")
        if isinstance(url, str):
            authority = url.partition("://")[2] if "://" in url else url
            authority = authority.split("/", 1)[0]
            if "#" in url or "@" in authority:
                check.fail(Class.MCP_URL, "/mcp/servers/%d/url" % index)
        server_env = server.get("env")
        if isinstance(server_env, dict):
            seen: "Set[str]" = set()
            for var in server_env:
                if is_reserved_variable(var):
                    check.fail(Class.RESERVED_VARIABLE, "/mcp/servers/%d/env/%s" % (index, _token(var)))
                if _upper(var) in seen:
                    check.fail(Class.ENV_NAME_CASE_DUPLICATE, "/mcp/servers/%d/env/%s" % (index, _token(var)))
                seen.add(_upper(var))
    links = mcp.get("skills") if isinstance(mcp.get("skills"), list) else []
    linked: "Set[Optional[str]]" = set()
    for index, link in enumerate(links):
        if not isinstance(link, dict):
            continue
        name = _canonical(link["name"]) if "name" in link else None
        if name in linked:
            check.fail(Class.MCP_DUPLICATE_SKILL, "/mcp/skills/%d/name" % index)
        linked.add(name)
        server = _canonical(link["server"]) if "server" in link else None
        if server not in names:
            check.fail(Class.MCP_UNKNOWN_SERVER, "/mcp/skills/%d/server" % index)


def _upper(text: str) -> str:
    return "".join(chr(ord(c) - 32) if "a" <= c <= "z" else c for c in text)


def _skills(doc: Document, check: _Check) -> None:
    """The rules of ``skills`` that need no files: names, and what a table's source may be."""
    seen: "Set[str]" = set()
    for index, entry in enumerate(_declared(doc, "skills")):
        name = _skill_name(entry)
        if name is None:
            continue
        at = _skill_at(entry, index)
        if valid_tool_name(name) and not valid_relative_path(name):
            check.fail(Class.SKILL_NAME, at)
        if name.lower() in seen:
            check.fail(Class.SKILL_DUPLICATE, at)
        seen.add(name.lower())
        source = entry.get("source") if isinstance(entry, dict) else None
        if not isinstance(source, str):
            continue
        there = "/skills/%d" % index
        try:
            parsed = Source.parse(source)
        except Error:
            check.fail(Class.INVALID_SOURCE, there + "/source")
            continue
        if parsed.is_local():
            check.fail(Class.LOCAL_SOURCE, there + "/source")
        elif parsed.kind is SourceKind.URL and not parsed.is_web_url():
            check.fail(Class.INVALID_SOURCE, there + "/source")
        elif parsed.holds_credentials():
            check.fail(Class.SOURCE_CREDENTIALS, there + "/source")
        if "ref" in entry and (parsed.kind is not SourceKind.GIT or parsed.ref is not None):
            check.fail(Class.REF_NOT_ALLOWED, there + "/ref")


def _manifest_named(doc: Document, check: _Check) -> None:
    """A manifest is never another field's file: only ``context`` names ``OVERLAY.md``, for its
    body, and nothing names ``overlay.cfg``."""
    for at, path in named_files(doc):
        is_context = at.startswith("/context/") or (at.startswith("/harness/") and "/context/" in at)
        if path == "overlay.cfg" or (path == "OVERLAY.md" and not is_context):
            check.fail(Class.MANIFEST_FILE, at)


def named_files(doc: Document) -> "List[Tuple[str, str]]":
    """Every ``(pointer, relative path)`` of a file a field requires to exist."""
    out: "List[Tuple[str, str]]" = []

    def each(key: str, field: "Optional[str]") -> None:
        for index, item in enumerate(_declared(doc, key)):
            if field is None and isinstance(item, str):
                out.append(("/%s/%d" % (key, index), item))
            elif isinstance(item, dict):
                name = field or "path"
                if isinstance(item.get(name), str):
                    out.append(("/%s/%d/%s" % (key, index, name), item[name]))

    each("rules", None)
    each("context", None)
    each("env_files", None)
    each("env_scripts", "path")
    each("setup", "path")
    runtime = doc.get("runtime")
    if isinstance(runtime, dict):
        for runtime_name, fields in (("python", ("requirements", "lock")), ("node", ("package",))):
            variants = runtime.get(runtime_name)
            for index, variant in enumerate(variants if isinstance(variants, list) else []):
                for field in fields:
                    if isinstance(_get(variant, field), str):
                        out.append(("/runtime/%s/%d/%s" % (runtime_name, index, field), variant[field]))
    hooks = doc.get("hooks")
    if isinstance(hooks, dict):
        for event, entry in hooks.items():
            if isinstance(entry, str):
                out.append(("/hooks/" + _token(event), entry))
            elif isinstance(entry, dict) and isinstance(entry.get("run"), str):
                out.append(("/hooks/%s/run" % _token(event), entry["run"]))
    scripts = doc.get("scripts")
    if isinstance(scripts, dict):
        for name, target in scripts.items():
            if isinstance(target, str):
                out.append(("/scripts/" + _token(name), target))
            elif isinstance(target, dict) and isinstance(target.get("path"), str):
                out.append(("/scripts/%s/path" % _token(name), target["path"]))
    harness = doc.get("harness")
    if isinstance(harness, dict):
        for ident, hints in harness.items():
            context = _get(hints, "context")
            for index, path in enumerate(context if isinstance(context, list) else []):
                if isinstance(path, str):
                    out.append(("/harness/%s/context/%d" % (_token(ident), index), path))
    icon = _get(doc.get("about"), "icon")
    if isinstance(icon, str):
        out.append(("/about/icon", icon))
    return out


def _bundled_skills(tree: OverlayTree) -> "Set[str]":
    found: "Set[str]" = set()
    for path in tree.paths:
        parts = path.split("/")
        if len(parts) == 3 and parts[0] == "skills" and parts[2] == "SKILL.md" and valid_tool_name(parts[1]):
            found.add(parts[1])
    return found


def _default_path_variables(doc: Document, tree: OverlayTree) -> "Set[str]":
    """The variables the overlay's default ``bin`` and ``lib`` path additions would set."""
    declared: "Set[str]" = set()
    for entry in _declared(doc, "paths"):
        path = entry if isinstance(entry, str) else _get(entry, "path")
        if isinstance(path, str):
            declared.add(path)
    defaults = doc.get("defaults")
    paths = tree.paths
    found: "Set[str]" = set()
    for key, variable, directory in _DEFAULT_PATHS:
        if _get(defaults, key) is False or directory in declared:
            continue
        if any(path.startswith(directory + "/") for path in paths):
            found.add(variable)
    return found


def _files(doc: Document, tree: OverlayTree, check: _Check) -> None:
    for at, path in named_files(doc):
        if path not in tree:
            check.fail(Class.MISSING_FILE, at)
    env = doc.get("env")
    if isinstance(env, dict):
        added = _default_path_variables(doc, tree)
        for name in env:
            if _upper(name) in added:
                check.fail(Class.ENV_PATH_CONFLICT, "/env/" + _token(name))
    hooks = doc.get("hooks")
    if isinstance(hooks, dict) and not any("hook" + ext in tree for ext in _SCRIPT_EXTENSIONS):
        # A table with no run runs hook.<ext>, which has to be there.
        for event, entry in hooks.items():
            if isinstance(entry, dict) and "run" not in entry:
                check.fail(Class.MISSING_FILE, "/hooks/" + _token(event))
    bundled = _bundled_skills(tree)
    folded = {name.lower() for name in bundled}
    for index, entry in enumerate(_declared(doc, "skills")):
        name = _skill_name(entry)
        if name is None or name.lower() not in folded:
            continue
        if isinstance(entry, dict):
            check.fail(Class.SKILL_AMBIGUOUS, _skill_at(entry, index))
        elif name not in bundled:
            check.fail(Class.SKILL_CASE, _skill_at(entry, index))
    if "OVERLAY.md" in tree and "overlay.cfg" in tree:
        check.fail(Class.TWO_MANIFESTS, "")
    mcp = doc.get("mcp")
    links = mcp.get("skills") if isinstance(mcp, dict) else None
    for index, link in enumerate(links if isinstance(links, list) else []):
        name = _get(link, "name")
        if isinstance(name, str) and "skills/%s/SKILL.md" % name not in tree:
            check.fail(Class.MISSING_SKILL, "/mcp/skills/%d/name" % index)


def validate(manifest: Document, tree: "Optional[OverlayTree]" = None) -> "List[Issue]":
    """Every problem with ``manifest``, sorted by pointer then class; empty when it is valid.

    With ``tree``, every file a field names must be one of the overlay's files, ``env`` may set
    no variable a default path addition adds to, and the overlay may hold one manifest only.
    """
    check = _Check()
    check.document(manifest)
    _semantic(manifest, check)
    if tree is not None:
        _files(manifest, tree, check)
    return sorted(set(check.issues), key=lambda issue: (issue.at, issue.code.value))


def skill_flags(doc: Document, tree: OverlayTree) -> "List[str]":
    """What a validator flags without refusing: the pointer of each skill name that is neither
    bundled nor given a source, which only a consumer's catalogs can resolve, so a typo is seen
    before it is looked up."""
    folded = {name.lower() for name in _bundled_skills(tree)}
    return [
        "/skills/%d" % index
        for index, entry in enumerate(_declared(doc, "skills"))
        if isinstance(entry, str) and entry.lower() not in folded
    ]


def _license_id(text: str) -> bool:
    return bool(text) and all(c in _ALNUM or c in ".-" for c in text)


def _license_reference(text: str, kind: str) -> bool:
    if text.startswith("DocumentRef-") and ":" in text:
        document, _, text = text[len("DocumentRef-") :].partition(":")
        if not _license_id(document):
            return False
    return text.startswith(kind) and _license_id(text[len(kind) :])


#: How deep parentheses may nest in a license expression: past it the text is no expression.
_LICENSE_DEPTH = 128


def valid_license(expression: str) -> bool:
    """Whether ``expression`` is an SPDX license expression: identifiers and ``LicenseRef-``
    references, ``+``, ``WITH``, ``AND`` and ``OR`` in upper case, and parentheses nested at most
    128 deep."""
    if not _license_chars(expression):
        return False
    tokens: "List[str]" = []
    word: "List[str]" = []
    for c in expression:
        if c in "( )":
            if word:
                tokens.append("".join(word))
                word = []
            if c != " ":
                tokens.append(c)
        else:
            word.append(c)
    if word:
        tokens.append("".join(word))
    depth = 0
    expect_operand = True
    at = 0
    while at < len(tokens):
        token = tokens[at]
        if expect_operand:
            if token == "(":
                depth += 1
                if depth > _LICENSE_DEPTH:
                    return False
                at += 1
                continue
            if token in ("AND", "OR", "WITH", ")"):
                return False
            plain = token[:-1] if token.endswith("+") else token
            if not (_license_reference(token, "LicenseRef-") or _license_id(plain)):
                return False
            at += 1
            if at < len(tokens) and tokens[at] == "WITH":
                at += 1
                if at >= len(tokens):
                    return False
                exception = tokens[at]
                if not (_license_id(exception) or _license_reference(exception, "AdditionRef-")):
                    return False
                at += 1
            expect_operand = False
        elif token in ("AND", "OR"):
            expect_operand = True
            at += 1
        elif token == ")" and depth > 0:
            depth -= 1
            at += 1
        else:
            return False
    return depth == 0 and not expect_operand


def about_field_valid(field: str, value: Any) -> bool:
    """Whether one informational field of ``about`` holds a valid value; an unknown field does not."""
    check = _Check()
    check.about({field: value}, "/about")
    return not check.issues


def catalog_field_valid(field: str, value: Any) -> bool:
    """Whether a catalog's own ``name``, ``description`` or ``homepage`` holds a valid value."""
    if not isinstance(value, str):
        return False
    if field == "name":
        return _is_line(value)
    if field == "description":
        return True
    if field == "homepage":
        return _is_web_url(value)
    return False


is_digest = _is_digest
is_git_ref = _is_git_ref
env_value_ok = _env_value_ok
