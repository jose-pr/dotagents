"""Overlay identities, relative paths, variable names and operating systems, as AOS defines them.

Every comparison folds ASCII only: ``str.lower`` and ``str.upper`` also fold other scripts, which
would make two identities or variables equal that the specification keeps apart.
"""

from __future__ import annotations

import sys
from typing import Iterable, Optional

__all__ = [
    "EXCLUDED_COMPONENTS",
    "current_os",
    "is_reserved_variable",
    "is_sensitive_variable",
    "is_variable_name",
    "normalize_name",
    "os_matches",
    "root_variable",
    "state_variable",
    "valid_overlay_name",
    "valid_relative_path",
]

#: Directory names and files that are never part of an overlay: not digested, not read.
EXCLUDED_COMPONENTS = frozenset(
    (
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "node_modules",
    )
)

_RESERVED_NAMES = frozenset(("default", "overlay", "system", "user", "project", "project-root"))
_UPPER = {c: c - 32 for c in range(ord("a"), ord("z") + 1)}
_LOWER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}
_ASCII_ALNUM = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
_ASCII_LETTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")


def ascii_lower(text: str) -> str:
    """``text`` with ASCII capitals made lowercase and every other character left alone."""
    return text.translate(_LOWER)


def ascii_upper(text: str) -> str:
    """``text`` with ASCII lowercase letters made capitals and every other character left alone."""
    return text.translate(_UPPER)


def overlay_name_shaped(name: str) -> bool:
    """Whether ``name`` has the shape of an identity, reserved or not: a letter first, a letter
    or digit last, and letters, digits, ``_``, ``.`` and ``-`` between."""
    return (
        bool(name)
        and name[0] in _ASCII_LETTERS
        and name[-1] in _ASCII_ALNUM
        and all(c in _ASCII_ALNUM or c in "_.-" for c in name)
    )


def valid_overlay_name(name: str) -> bool:
    """Whether ``name`` may identify an overlay: well shaped, not a reserved word, and not a
    Windows device name before its first ``.``."""
    if not overlay_name_shaped(name):
        return False
    lower = ascii_lower(name)
    return lower not in _RESERVED_NAMES and not _is_device_stem(lower.split(".", 1)[0], False)


def normalize_name(name: str) -> str:
    """An identity as it is compared: ASCII lowercase, ``_`` and ``.`` made ``-``."""
    return ascii_lower(name).replace("_", "-").replace(".", "-")


def _variable_stem(identity: str) -> str:
    upper = ascii_upper(normalize_name(identity))
    return "".join(c if c in _ASCII_ALNUM else "_" for c in upper)


def root_variable(identity: str) -> str:
    """``<NAME>_OVERLAY_ROOT`` for an overlay identity."""
    return _variable_stem(identity) + "_OVERLAY_ROOT"


def state_variable(identity: str) -> str:
    """``<NAME>_OVERLAY_STATE`` for an overlay identity."""
    return _variable_stem(identity) + "_OVERLAY_STATE"


def is_variable_name(name: str) -> bool:
    """Whether ``name`` is an environment variable name: a letter or ``_``, then letters, digits
    and ``_``."""
    return (
        bool(name)
        and (name[0] in _ASCII_LETTERS or name[0] == "_")
        and all(c in _ASCII_ALNUM or c == "_" for c in name)
    )


def is_reserved_variable(name: str) -> bool:
    """Whether ``name`` is the consumer's: ``AOS_*``, ``*_OVERLAY_ROOT`` or ``*_OVERLAY_STATE``."""
    upper = ascii_upper(name)
    return upper.startswith("AOS_") or upper.endswith(("_OVERLAY_ROOT", "_OVERLAY_STATE"))


_SENSITIVE_NAMES = frozenset(
    """PATH PATHEXT HOME USERPROFILE SHELL COMSPEC SYSTEMROOT BASH_ENV ENV ZDOTDIR PROMPT_COMMAND
    IFS EDITOR VISUAL PAGER LESSOPEN LESSCLOSE XDG_CONFIG_HOME XDG_DATA_HOME APPDATA LOCALAPPDATA
    TMPDIR TEMP TMP PSMODULEPATH CLASSPATH JAVA_TOOL_OPTIONS _JAVA_OPTIONS JDK_JAVA_OPTIONS
    PERL5OPT PERL5LIB RUBYOPT RUBYLIB RUSTFLAGS GOPROXY GONOSUMDB GONOSUMCHECK GOPRIVATE GOFLAGS
    SSH_ASKPASS SSLKEYLOGFILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE CLAUDE_CONFIG_DIR CODEX_HOME""".split()
)
_SENSITIVE_PREFIXES = (
    "LD_",
    "DYLD_",
    "GIT_",
    "PYTHON",
    "NODE_",
    "NPM_CONFIG_",
    "PIP_",
    "UV_",
    "CARGO_",
    "RUSTC_",
    "DOTNET_",
    "COR_",
    "CORECLR_",
    "SSL_",
)


def is_sensitive_variable(name: str) -> bool:
    """Whether a variable changes how a program loads code or where it talks: a value set for
    it is reported as ``env-sensitive``."""
    upper = ascii_upper(name)
    return (
        upper in _SENSITIVE_NAMES
        or upper.startswith(_SENSITIVE_PREFIXES)
        or upper.endswith(("_PROXY", "_BASE_URL"))
    )


def is_path_variable(name: str) -> bool:
    """Whether ``name`` is one a path addition exists for."""
    return ascii_upper(name) in ("PATH", "PYTHONPATH", "NODE_PATH")


def _refused_in_component(c: str) -> bool:
    code = ord(c)
    return (
        c in '\\:*?"<>|'
        or code <= 0x1F
        or 0x7F <= code <= 0x9F
        or 0x200C <= code <= 0x200F
        or 0x202A <= code <= 0x202E
        or 0x2066 <= code <= 0x206F
        or code == 0xFEFF
    )


def _is_device_stem(stem: str, console: bool) -> bool:
    if stem in ("con", "prn", "aux", "nul"):
        return True
    if console and stem in ("conin$", "conout$"):
        return True
    digits = "0123456789¹²³" if console else "123456789"
    return len(stem) == 4 and stem[:3] in ("com", "lpt") and stem[3] in digits


def _valid_component(component: str) -> bool:
    if not component or component.endswith((".", " ")) or any(_refused_in_component(c) for c in component):
        return False
    lower = ascii_lower(component)
    if lower in EXCLUDED_COMPONENTS or lower.endswith(".pyc"):
        return False
    return not _is_device_stem(lower.split(".", 1)[0], True)


def valid_relative_path(path: str) -> bool:
    """Whether ``path`` is a relative path as a manifest writes one: ``/``-separated components,
    none empty, ending in ``.`` or a space, holding a character Windows or a terminal reads
    differently, naming a device, an excluded directory or a ``.pyc`` file, and no ``~`` before a
    digit (an 8.3 short name)."""
    if not isinstance(path, str) or not path:
        return False
    if any(a == "~" and b in "0123456789" for a, b in zip(path, path[1:])):
        return False
    return all(_valid_component(component) for component in path.split("/"))


def valid_os_name(name: str) -> bool:
    """Whether ``name`` is shaped like an operating system name: lowercase letters, digits and
    ``-``, a letter first."""
    return (
        bool(name)
        and "a" <= name[0] <= "z"
        and all("a" <= c <= "z" or "0" <= c <= "9" or c == "-" for c in name)
    )


def valid_tool_name(name: str) -> bool:
    """Whether ``name`` is a skill, server or tool name: a letter or digit, then letters, digits,
    ``_``, ``.`` and ``-``."""
    return bool(name) and name[0] in _ASCII_ALNUM and all(c in _ASCII_ALNUM or c in "_.-" for c in name)


_UNIX_NAMES = frozenset(("macos", "linux", "freebsd", "openbsd", "netbsd"))


def os_matches(names: "Optional[Iterable[str]]", current: str) -> bool:
    """Whether an entry's ``os`` list selects ``current``: no list selects every OS, and
    ``unix`` selects every Unix-like one."""
    if names is None:
        return True
    return any(name == current or (name == "unix" and current in _UNIX_NAMES) for name in names)


def current_os() -> str:
    """The operating system this process runs on, as AOS names it."""
    platform = sys.platform
    if platform.startswith("win") or platform == "cygwin":
        return "windows"
    if platform == "darwin":
        return "macos"
    if platform.startswith("linux"):
        return "linux"
    for name in ("freebsd", "openbsd", "netbsd"):
        if platform.startswith(name):
            return name
    return platform
