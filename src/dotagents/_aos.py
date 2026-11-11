"""What dotagents adds to the AOS reader: applying one overlay for this consumer.

Reading, validation, names, digests, substitution, the defaults an overlay's files give and the
setup phases are :mod:`coqui_aos`. This module is the consumer side: the environment an overlay
builds with this machine's secrets and files (:class:`Environment`, :class:`Secrets`,
:class:`Context`, :func:`apply_overlay`, :func:`plan`, :func:`effective`) and the package
environments overlays install into (:func:`environments`). :mod:`dotagents._overlays`,
:mod:`dotagents._env` and the ``overlays`` commands give it a filesystem, secrets and a policy.

The rules are those of AOS ``0.1.0-draft``
(https://boriken-dev.github.io/agent-overlay-spec/); the error codes are the classes its
conformance vectors name.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import coqui_aos
from coqui_aos import (
    EXCLUDED_COMPONENTS,
    Class,
    Error,
    OverlayTree,
    PathFS,
    default_on,
    is_reserved_variable,
    normalize_name,
    root_variable,
    state_variable,
    substitute,
)
from coqui_aos import is_sensitive_variable as is_sensitive
from coqui_aos import valid_relative_path as valid_relpath

#: The AOS edition this module implements.
EDITION = "0.1.0-draft"
PATH_VARIABLES = frozenset(("PATH", "PYTHONPATH", "NODE_PATH"))
#: A ``scripts`` path's interpreter, by extension.
SCRIPT_EXTENSIONS = {
    ".py": "python", ".sh": "sh", ".bash": "bash", ".ps1": "powershell",
    ".js": "node", ".mjs": "node", ".cjs": "node",
}
DIGEST_FORM = re.compile(r"^sha256:[0-9a-fA-F]{64}$")
_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: symlinks and junctions



# --------------------------------------------------------------------------- #
# Digests of an overlay on disk.
# --------------------------------------------------------------------------- #


def sha256_digest(data: bytes) -> str:
    return str(coqui_aos.Digest.of(data))


def same_digest(a: str, b: str) -> bool:
    """Digests compare by their hexadecimal digits, without regard to case."""
    if not (DIGEST_FORM.match(a) and DIGEST_FORM.match(b)):
        raise ValueError("not a sha256 digest")
    return a.lower() == b.lower()


def tree_digest(root: "str | os.PathLike[str]") -> str:
    """The tree digest of the overlay at ``root``. Raises :class:`coqui_aos.Error`."""
    return str(coqui_aos.tree_digest(PathFS(), Path(root)).digest)


def members(root: "str | os.PathLike[str]") -> "list[str]":
    """The relative paths of an overlay's members (the files the digest counts), sorted. Raises
    :class:`coqui_aos.Error` as the digest would."""
    return sorted(coqui_aos.tree_digest(PathFS(), Path(root)).files, key=lambda path: path.encode("utf-8"))


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)



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
    """Entries under ``root`` that are not part of the overlay (an excluded name, a ``.pyc``): in a
    verified copy each one is a mismatch."""
    out: "list[str]" = []

    def walk(directory: str, prefix: str) -> None:
        with os.scandir(directory) as it:
            found = sorted(it, key=lambda e: e.name)
        for entry in found:
            rel = prefix + entry.name
            if entry.name in EXCLUDED_COMPONENTS or entry.name.endswith(".pyc"):
                out.append(rel)
                continue
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode) and not _is_reparse(info):
                walk(entry.path, rel + "/")

    walk(str(root), "")
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
            raise Error(Class.SECRET_PLACEHOLDER, name)
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
            except Error as error:
                raise Refused(error.class_.value, name)
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
        except Error as error:
            raise Refused(error.class_.value, name)
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
    file's lines (:func:`coqui_aos.parse_dotenv`). Raises :class:`Refused`."""
    data = ctx.read(item["path"])
    if data is None:
        raise Refused("missing-file", item["path"])
    try:
        if item.get("format", "aos") == "dotenv":
            found: "dict[str, Any] | list[tuple[str, str, bool]]" = coqui_aos.parse_dotenv(data)
            names = [name for name, _text, _literal in found]
        else:
            found = coqui_aos.read_document(data)
            if coqui_aos.validate({"env": found}):
                raise Refused("invalid-env-file", item["path"])
            names = list(found)
    except Error as exc:
        raise Refused("invalid-env-file", item["path"], exc.class_.value)
    for name in names:
        if name.upper() in listed:
            raise Refused("env-path-conflict", name)
    return found


# --------------------------------------------------------------------------- #
# What an overlay's files give: coqui_aos reads them from a tree of its paths.
# --------------------------------------------------------------------------- #

#: The list variable each runtime's installed packages are added to.
PACKAGE_VARIABLES = {"python": "PYTHONPATH", "node": "NODE_PATH"}
#: Default script extensions in the order a consumer tries them, with the OSes each applies on.
DEFAULT_SCRIPTS = (
    (".py", "python", None),
    (".js", "node", None),
    (".mjs", "node", None),
    (".cjs", "node", None),
    (".ps1", "powershell", ["windows"]),
    (".bash", "bash", ["unix"]),
    (".sh", "sh", ["unix"]),
)


def _tree(files: "Iterable[str]") -> OverlayTree:
    return OverlayTree(files)


def _named(entry: Any) -> Any:
    return entry.get("path") if isinstance(entry, dict) else entry


def _declared(manifest: "dict[str, Any]", field: str) -> "list[Any]":
    value = manifest.get(field)
    return list(value) if isinstance(value, list) else []


def paths_of(manifest: "dict[str, Any]", files: "Iterable[str]") -> "list[dict[str, Any]]":
    """The ``bin`` and ``lib`` defaults, then ``paths``."""
    return coqui_aos.paths_of(manifest, _tree(files))


def dependencies_of(
    manifest: "dict[str, Any]",
    files: "Iterable[str]",
    os_name: str,
    read: "Optional[Callable[[str], Optional[bytes]]]" = None,
) -> "list[dict[str, Any]]":
    """The dependency files installed for this OS, one per runtime, as ``{"runtime", "file", "lock",
    "environment"}``. ``read`` gives a file's bytes, so a default requirements file is a lock when
    every requirement has a hash; without it the default file counts as unlocked. Raises
    :class:`Refused` (``partial-lock``)."""
    tree = _tree(files)
    path = "lib/python/requirements.txt"
    if read is not None and path in tree:
        data = read(path)
        if data is not None:
            tree.with_bytes(path, data)
    try:
        found = coqui_aos.dependencies_of(manifest, tree, os_name)
    except coqui_aos.PartialLock as error:
        raise Refused("partial-lock", error.subject)
    return [
        {"runtime": d.runtime.value, "file": d.file, "lock": d.lock, "environment": d.environment} for d in found
    ]


def overlay_paths(manifest: "dict[str, Any]", ctx: "Context") -> "list[dict[str, Any]]":
    """What :func:`apply_paths` applies for one overlay: the defaults, then each runtime's installed
    packages directory (``ctx.packages``, an entry with ``packages`` instead of ``path``), then
    ``paths``."""
    packages = [
        {"variable": PACKAGE_VARIABLES[dep["runtime"]], "packages": ctx.packages[dep["runtime"]]}
        for dep in dependencies_of(manifest, ctx.files, ctx.os)
        if dep["runtime"] in ctx.packages
    ]
    return coqui_aos.default_paths(manifest, _tree(ctx.files)) + packages + _declared(manifest, "paths")


def env_files_of(manifest: "dict[str, Any]", files: "Iterable[str]") -> "list[Any]":
    return coqui_aos.env_files_of(manifest, _tree(files))



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
    members = {path for path in files if valid_relpath(path)}
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
# Hooks: ``coqui_aos`` decides, these give its answers as the dicts the hook
# modules use, start a run only when its turn comes, and apply ``pre-env``
# to this module's :class:`Environment`.
# --------------------------------------------------------------------------- #

#: Every hook event, in the order they are fired.
HOOK_EVENTS = tuple(event.value for event in coqui_aos.HookEvent)
#: The consumer's bound on one tree it reads to keep (a hook's snapshot).
MAX_TREE_BYTES = 256 * 1024 * 1024
_DEFAULTS = coqui_aos.Settings()
#: The bounds on every hook, by the name the hook modules use; ``max_stderr`` is this consumer's.
DEFAULT_HOOK_LIMITS = {
    "timeout": _DEFAULTS.hook_timeout,
    "event_timeout": _DEFAULTS.hook_event_timeout,
    "max_output": _DEFAULTS.hook_output_bytes,
    "max_stderr": _DEFAULTS.hook_output_bytes,
    "max_payload": _DEFAULTS.hook_payload_bytes,
}
_LIMIT_SETTINGS = {
    "timeout": "hook_timeout",
    "event_timeout": "hook_event_timeout",
    "max_output": "hook_output_bytes",
    "max_payload": "hook_payload_bytes",
}


def hook_settings(limits: "Optional[dict[str, int]]" = None) -> "coqui_aos.Settings":
    """``coqui_aos`` settings holding the bounds ``limits`` names (:data:`DEFAULT_HOOK_LIMITS`)."""
    return _DEFAULTS.replace(**{_LIMIT_SETTINGS[key]: value for key, value in (limits or {}).items()
                                if key in _LIMIT_SETTINGS})


def read_tree(root: "str | os.PathLike[str]") -> "tuple[coqui_aos.OverlayTree, str]":
    """The tree at ``root`` with the bytes of each member, and the digest of those same bytes.
    Raises :class:`coqui_aos.Error` (a link, a special file, a refused name, more than
    :data:`MAX_TREE_BYTES`) or ``OSError``."""
    tree, digest = coqui_aos.read_tree(PathFS(), Path(root), _DEFAULTS.replace(tree_bytes=MAX_TREE_BYTES))
    return tree, str(digest.digest)


def hooks_of(manifest: "dict[str, Any]", files: "Iterable[str]", os_name: str,
             interpreters: "Iterable[str]" = INTERPRETERS) -> "tuple[list[dict[str, Any]], list[str]]":
    """Every hook the overlay has, in event order, as dicts (``event``, ``path``, ``interpreter``,
    ``runs``, ``match``, ``sets``, ``timeout``, ``on_failure``, ``payload``), and the ``hook.<ext>``
    files reported as ``script-not-run``."""
    hooks, not_run = coqui_aos.hooks_of(manifest, _tree(files), os_name, list(interpreters))
    return [coqui_aos.to_data(hook) for hook in hooks], not_run


def hook_plan(manifest: "dict[str, Any]", facts: "dict[str, Any]") -> "dict[str, Any]":
    """:func:`coqui_aos.hook_plan` with its facts and its answer as plain data.

    ``facts``: ``os``, ``interpreters``, ``files``, ``identity``, ``scope``, ``digest``,
    ``allowed``, ``tree`` (path to text), ``fires``, ``partial`` and ``limits`` (``timeout``)."""
    tree = facts.get("tree")
    known = coqui_aos.HookFacts(
        os=facts["os"], identity=facts.get("identity", ""), interpreters=facts.get("interpreters"),
        scope=coqui_aos.HookScope(facts.get("scope", "overlay")), digest=facts.get("digest"),
        tree=None if tree is None else {path: text.encode("utf-8") for path, text in tree.items()},
        allowed=facts.get("allowed"),
        fires=None if "fires" not in facts else [coqui_aos.HookEvent(e) for e in facts["fires"]],
        partial=[coqui_aos.HookEvent(e) for e in facts.get("partial", [])],
        timeout=facts.get("limits", {}).get("timeout"),
    )
    return coqui_aos.to_data(coqui_aos.hook_plan(manifest, _tree(facts.get("files", [])), known))


def _run_bytes(run: "dict[str, Any]") -> bytes:
    if "stdout_base64" in run:
        import base64

        return base64.b64decode(run["stdout_base64"])
    if "stdout_parts" in run:
        return "".join(text * times for text, times in run["stdout_parts"]).encode("utf-8")
    if "stdout_bytes" in run:
        return run["stdout_bytes"]
    return run.get("stdout", "").encode("utf-8")


def hook_run(run: "dict[str, Any]", output_bytes: "Optional[int]" = None) -> "coqui_aos.HookRun":
    """One run as ``coqui_aos`` takes it. A run whose ``too_large`` is set was cut at the bound
    by the process driver; a byte more is how the model learns it went over."""
    data = _run_bytes(run)
    if run.get("too_large") and output_bytes is not None:
        data = data[:output_bytes] + b"\0"
    return coqui_aos.HookRun(
        source=run.get("source", ""), stdout=data,
        on_failure=coqui_aos.OnFailure.REFUSE if run.get("on_failure") == "refuse" else coqui_aos.OnFailure.REPORT,
        seconds=run.get("seconds", 0), not_run=run.get("not_run", False),
        payload_size=run.get("payload_size", 0), timed_out=run.get("timed_out", False),
        exit=run.get("exit", 0), sets=tuple(run.get("sets", ())),
    )


def _started(runs: "list[dict[str, Any]]", settings: "coqui_aos.Settings") -> "Iterable[coqui_aos.HookRun]":
    """The runs as ``coqui_aos`` takes them, each made only when its loop turn comes: a run that
    holds ``start``, a function returning its own keys, is started then, so one after a block or a
    deny, or after the event's bound, never is. ``fire_event`` reads the event's bound before a
    run's content; this holds back the start the same way."""
    spent = 0.0
    bound = settings.hook_event_timeout
    for run in runs:
        if bound is not None and spent >= bound:
            yield hook_run({"source": run["source"], "on_failure": run.get("on_failure")})
            continue
        if "start" in run:
            run = dict(run, **run["start"]())
        held = hook_run(run, settings.hook_output_bytes)
        spent += held.seconds
        yield held


def fire_event(event: str, runs: "list[dict[str, Any]]", applies: "Optional[Iterable[str]]" = None,
               limits: "Optional[dict[str, int]]" = None, windows: bool = False) -> "dict[str, Any]":
    """:func:`coqui_aos.fire_event` over ``runs`` (``source``, ``on_failure``, ``seconds``, the
    run's keys as :func:`hook_run` reads them, optionally ``start``), its result as plain data:
    ``ran``, ``decision``, ``by``, ``reason``, ``context``, ``env``, ``reports`` (lists of id and
    what it is about) and ``non_defaults``."""
    settings = hook_settings(limits)
    fired = coqui_aos.fire_event(coqui_aos.HookEvent(event), _started(runs, settings), applies, settings, windows)
    data = coqui_aos.to_data(fired)
    data["reports"] = [[report["id"], *report["about"]] for report in data["reports"]]
    return data


def apply_pre_env(env: Environment, identities: "list[str]", runs: "dict[str, dict[str, Any]]",
                  limits: "Optional[dict[str, int]]", report: "list[list[str]]",
                  non_defaults: "list[list[str]]") -> None:
    """The ``pre-env`` hooks' output, every overlay's in composition order as one event (a run
    may be given lazily, see :func:`fire_event`), applied to ``env`` after the root variables and
    before any overlay's declarations. Reports and non-default settings are appended to the
    lists. Raises :class:`Refused` (``hook-refused``) when an overlay's guard fails."""
    ordered = [dict(runs[identity], source=identity) for identity in identities if identity in runs]
    fired = fire_event("pre-env", ordered, None, limits, env.windows)
    report.extend(fired["reports"])
    non_defaults.extend(fired["non_defaults"])
    if fired["decision"] == "refuse":
        raise Refused("hook-refused", fired["by"])
    for name, value in fired["env"].items():
        if value is None:
            env.unset(name)
        else:
            env.set(name, value)
