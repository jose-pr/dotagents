"""The environment an overlay builds: variables, secrets, and applying an overlay's declarations to
them (``env``, env files, path additions and env scripts)."""

from __future__ import annotations

import dataclasses
import enum
from typing import Any, Dict, Iterable, List, Mapping, NamedTuple, Optional, Set, Tuple, Union

from ._data import Document
from ._defaults import (
    INTERPRETERS,
    Runtime,
    declared,
    default_paths,
    default_script,
    dependencies_of,
    entry_on,
    env_files_of,
    named,
)
from ._dotenv import parse_dotenv
from ._errors import Class, Error
from ._names import (
    ascii_upper,
    is_path_variable,
    is_variable_name,
    is_reserved_variable,
    is_sensitive_variable,
    normalize_name,
    valid_relative_path,
)
from ._settings import Settings
from ._subst import substitute_with
from ._tree import OverlayTree

__all__ = [
    "Environment",
    "Facts",
    "NonDefaultId",
    "NonDefaultSetting",
    "ScriptRun",
    "apply_overlay",
    "read_dotenv",
]


class NonDefaultId(str, enum.Enum):
    """What a consumer reports as a non-default setting, by its AOS identifier."""

    DEPS_NETWORK = "deps-network"
    ENV_PASSTHROUGH = "env-passthrough"
    ENV_SCRIPT = "env-script"
    ENV_SENSITIVE = "env-sensitive"
    HOOK = "hook"
    INSECURE_TRANSPORT = "insecure-transport"
    NETWORK_NOT_ENFORCED = "network-not-enforced"
    NO_TIMEOUT = "no-timeout"
    PRIVATE_SOURCE = "private-source"
    SCRIPT_NOT_RUN = "script-not-run"
    SECRET_READ = "secret-read"
    SETUP_NETWORK = "setup-network"
    UNLOCKED_DEPENDENCIES = "unlocked-dependencies"
    UNPINNED = "unpinned"

    @classmethod
    def named(cls, word: str) -> "Optional[NonDefaultId]":
        """What a word stands for, or ``None``."""
        try:
            return cls(word)
        except ValueError:
            return None

    def __str__(self) -> str:
        return self.value


class NonDefaultSetting(NamedTuple):
    """A non-default setting a consumer reports: its identifier and its subject (the variable, path,
    file or reference it is about; empty for ``unpinned``), never a value. Sorts by identifier word,
    then subject."""

    id: "Union[NonDefaultId, str]"
    subject: str

    @classmethod
    def host(cls, id: str, subject: str = "") -> "NonDefaultSetting":
        """A setting a host defines by its own ``id`` (``"read-only-not-enforced"``): the id as
        given, or the ``NonDefaultId`` when AOS defines the word."""
        return cls(NonDefaultId.named(id) or id, subject)


class Environment:
    """An environment being built.

    Names compare without regard to ASCII case on Windows, and a variable whose value came from a
    secret reference is never read by a placeholder. ``repr`` shows names only.
    """

    def __init__(
        self,
        base: "Union[Mapping[str, str], Iterable[Tuple[str, str]], None]" = None,
        windows: bool = False,
        settings: "Optional[Settings]" = None,
    ) -> None:
        self.windows = windows
        self.settings = settings or Settings()
        self._values: "Dict[str, str]" = {}
        self._folded: "Dict[str, str]" = {}
        self._secret: "Set[str]" = set()
        self._extra: "Dict[str, str]" = {}
        items = base.items() if isinstance(base, Mapping) else (base or ())
        for name, value in items:
            self.set(name, value)

    def __repr__(self) -> str:
        return "Environment(%s)" % ", ".join(self._values)

    def copy(self) -> "Environment":
        """An independent copy, secrets and settings included."""
        other = Environment(None, self.windows, self.settings)
        other._values = dict(self._values)
        other._folded = dict(self._folded)
        other._secret = set(self._secret)
        other._extra = dict(self._extra)
        return other

    def _key(self, name: str) -> "Optional[str]":
        if name in self._values:
            return name
        return self._folded.get(ascii_upper(name)) if self.windows else None

    def get(self, name: str) -> "Optional[str]":
        """The value of ``name``, or ``None``."""
        key = self._key(name)
        return None if key is None else self._values[key]

    def set(self, name: str, value: str, secret: bool = False) -> None:
        """Set ``name``, keeping the name already there under another case on Windows. A secret is
        never read by a placeholder."""
        self._put(name, value, secret)

    def _put(self, name: str, value: str, secret: bool) -> None:
        key = self._key(name)
        if key is None:
            key = name
            if self.windows:
                self._folded[ascii_upper(name)] = name
        self._values[key] = value
        if secret:
            self._secret.add(key)
        else:
            self._secret.discard(key)

    def unset(self, name: str) -> None:
        """Remove ``name``."""
        key = self._key(name)
        if key is not None:
            del self._values[key]
            self._secret.discard(key)
            self._folded.pop(ascii_upper(key), None)

    def values(self) -> "Dict[str, str]":
        """The variables, in the order they were first set, as a copy."""
        return dict(self._values)

    def is_secret(self, name: str) -> bool:
        """Whether ``name`` holds a resolved secret, which must never be shown."""
        key = self._key(name)
        return key is not None and key in self._secret

    def _lookup(self, name: str) -> "Optional[str]":
        """What a placeholder reads: the overlay's own ``AOS_OVERLAY_*`` first, then the variable;
        a secret is refused."""
        for extra, value in self._extra.items():
            if extra == name or (self.windows and ascii_upper(extra) == ascii_upper(name)):
                return value
        key = self._key(name)
        if key is None:
            return None
        if key in self._secret:
            raise Error(Class.SECRET_PLACEHOLDER, name)
        return self._values[key]

    def _substitute(self, text: str, variable: str) -> str:
        try:
            return substitute_with(text, self._lookup, self.settings)
        except Error as error:
            raise Error(error.class_, variable) from None


@dataclasses.dataclass
class Facts:
    """What a consumer knows when it applies one overlay: where it is exposed, the files it has, what
    is granted it, and its own policy.

    ``repr`` shows ``os``, ``root``, ``identity`` and ``pinned`` only, so no secret reaches a log.
    """

    #: The OS AOS names it (``linux``, ``windows``, ``macos``, ...).
    os: str
    #: The overlay root as exposed, absolute.
    root: str = ""
    #: The identity the overlay is installed under.
    identity: str = ""
    #: The state directory, when the overlay has one.
    state: Optional[str] = None
    #: Whether a pin covers it; without one ``unpinned`` is reported.
    pinned: bool = False
    #: The environment the consumer starts from.
    base_env: Dict[str, str] = dataclasses.field(default_factory=dict, repr=False)
    #: The consumer's own environment, which an ``env`` provider reference reads.
    host_env: Dict[str, str] = dataclasses.field(default_factory=dict, repr=False)
    #: What this overlay is granted, by provider (``env``, ``file``, ...) or ``secret``.
    grants: Dict[str, List[str]] = dataclasses.field(default_factory=dict, repr=False)
    #: The values of named secrets the consumer defines.
    secrets: Dict[str, str] = dataclasses.field(default_factory=dict, repr=False)
    #: The files of the consumer's secrets directory, by key; ``None`` when it has none.
    secret_files: Optional[Dict[str, str]] = dataclasses.field(default=None, repr=False)
    #: The providers the consumer has; ``None`` is ``env`` and ``file``.
    providers: Optional[List[str]] = dataclasses.field(default=None, repr=False)
    #: Further providers' values, by provider and key.
    provider_values: Dict[str, Dict[str, str]] = dataclasses.field(default_factory=dict, repr=False)
    #: Whether the consumer can deny setup the network; ``None`` is yes.
    enforces_network: Optional[bool] = dataclasses.field(default=None, repr=False)
    #: Whether env scripts run; ``None`` is yes.
    env_scripts_enabled: Optional[bool] = dataclasses.field(default=None, repr=False)
    #: Whether env scripts run only while ``digest`` is the ``allowed`` one, the allowance of the
    #: overlay's hooks; off, none is held back.
    gate_env_scripts: bool = False
    #: The overlay's tree digest, when the consumer has computed it.
    digest: Optional[str] = None
    #: The one digest at which the user allowed this overlay's hooks and env scripts.
    allowed: Optional[str] = None
    #: The interpreters the consumer can run; ``None`` is every one AOS names.
    interpreters: Optional[List[str]] = dataclasses.field(default=None, repr=False)
    #: Each runtime's installed packages directory, by runtime.
    packages: Dict[str, str] = dataclasses.field(default_factory=dict, repr=False)
    #: The overlay's files and the bytes of those read.
    tree: OverlayTree = dataclasses.field(default_factory=OverlayTree, repr=False)
    #: How much of an ``aos`` env file is read.
    settings: Settings = dataclasses.field(default_factory=Settings, repr=False)

    @property
    def windows(self) -> bool:
        return self.os == "windows"

    def known_interpreters(self) -> "List[str]":
        """The interpreters the consumer can run."""
        return list(INTERPRETERS) if self.interpreters is None else list(self.interpreters)

    def granted(self, entry: Document) -> bool:
        """Whether the consumer's configuration lets this overlay read this reference."""
        if isinstance(entry.get("secret"), str):
            return entry["secret"] in self.grants.get("secret", [])
        provider, key = _text(entry, "provider"), _text(entry, "key")
        return key in self.grants.get(provider, [])

    def secret(self, entry: Document) -> "Optional[str]":
        """A granted reference's value, or ``None`` when it cannot be resolved. A ``file`` key that is
        no relative path raises ``invalid-reference``."""
        provider = entry.get("provider") if isinstance(entry.get("provider"), str) else None
        key = _text(entry, "key")
        if provider == "file" and not valid_relative_path(key):
            raise Error(Class.INVALID_REFERENCE, key)
        if not self.granted(entry):
            return None
        if isinstance(entry.get("secret"), str):
            return self.secrets.get(entry["secret"])
        provider = provider or ""
        known = ("env", "file") if self.providers is None else self.providers
        if provider not in known:
            return None
        if provider == "env":
            return Environment(self.host_env, self.windows).get(key)
        if provider == "file":
            if self.secret_files is None or key not in self.secret_files:
                return None
            value = self.secret_files[key]
            for ending in ("\r\n", "\n"):
                if value.endswith(ending):
                    return value[: -len(ending)]
            return value
        return self.provider_values.get(provider, {}).get(key)


def _text(entry: Any, key: str) -> str:
    value = entry.get(key) if isinstance(entry, dict) else None
    return value if isinstance(value, str) else ""


def _strings(entry: Any, key: str) -> "List[str]":
    value = entry.get(key) if isinstance(entry, dict) else None
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _unsigned(entry: Any, key: str, default: int) -> int:
    value = entry.get(key) if isinstance(entry, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else default


def reference_name(entry: Document) -> str:
    """How a report names a reference: ``secret:<name>`` or ``<provider>:<key>``, never a value."""
    if isinstance(entry.get("secret"), str):
        return "secret:" + entry["secret"]
    return "%s:%s" % (_text(entry, "provider"), _text(entry, "key"))


def is_reference(entry: Any) -> bool:
    """Whether a mapping is a secret reference: it names a ``provider`` or a ``secret``."""
    return isinstance(entry, dict) and ("provider" in entry or "secret" in entry)


def resolve_path(root: str, relative: str, windows: bool) -> str:
    """The absolute path of ``relative`` under ``root``, with the platform's separator."""
    separator = "\\" if windows else "/"
    return root.rstrip("/\\") + separator + relative.replace("/", separator)


def read_dotenv(data: bytes, env: Environment) -> "List[Tuple[str, str]]":
    """Apply a ``dotenv`` file to ``env`` line by line, each line substituted against the environment
    the earlier ones left, and return what it set, in order.

    Raises what ``parse_dotenv`` raises, and ``undefined-variable``, ``secret-placeholder`` or
    ``invalid-pattern`` naming the variable whose value failed.
    """
    done: "List[Tuple[str, str]]" = []
    for line in parse_dotenv(data):
        value = line.text if line.literal else env._substitute(line.text, line.name)
        env.set(line.name, value)
        done.append((line.name, value))
    return done


def _apply_mapping(
    env: Environment, mapping: Document, facts: Facts, root: str, non_defaults: "List[NonDefaultSetting]"
) -> None:
    before = env.copy()
    for name, entry in mapping.items():
        if is_reserved_variable(name):
            raise Error(Class.RESERVED_VARIABLE, name)
        if isinstance(entry, str):
            entry = {"value": entry}
        elif not isinstance(entry, dict):
            continue
        if entry.get("if_unset") is True and before.get(name) is not None:
            continue
        secret = False
        if isinstance(entry.get("value"), str):
            # Mapping order carries no meaning, so no entry sees another of the same mapping.
            value = before._substitute(entry["value"], name)
        elif isinstance(entry.get("path"), str):
            value = resolve_path(root, entry["path"], env.windows)
        else:
            resolved = facts.secret(entry)
            if resolved is None:
                if entry.get("optional") is True:
                    continue
                raise Error(Class.UNRESOLVED_REFERENCE, name)
            value, secret = resolved, True
            if entry.get("provider") == "env":
                non_defaults.append(NonDefaultSetting(NonDefaultId.ENV_PASSTHROUGH, _text(entry, "key")))
            else:
                non_defaults.append(NonDefaultSetting(NonDefaultId.SECRET_READ, reference_name(entry)))
        if is_sensitive_variable(name):
            non_defaults.append(NonDefaultSetting(NonDefaultId.ENV_SENSITIVE, name))
        env.set(name, value, secret)


def _apply_paths(
    env: Environment, paths: "List[Any]", root: str, os: str, non_defaults: "List[NonDefaultSetting]"
) -> None:
    windows = env.windows
    separator = ";" if windows else ":"
    grouped: "Dict[str, Tuple[str, List[Any]]]" = {}
    for entry in paths:
        if not entry_on(entry, os):
            continue
        variable = _text(entry, "variable")
        key = ascii_upper(variable) if windows else variable
        grouped.setdefault(key, (variable, []))[1].append(entry)
    for variable, entries in grouped.values():
        if is_sensitive_variable(variable) and not is_path_variable(variable):
            non_defaults.append(NonDefaultSetting(NonDefaultId.ENV_SENSITIVE, variable))
        prepend: "List[str]" = []
        append: "List[str]" = []
        seen: "Set[str]" = set()
        for entry in entries:
            if isinstance(entry.get("packages"), str):
                resolved = entry["packages"]
            else:
                resolved = resolve_path(root, _text(entry, "path"), windows)
            if resolved in seen:
                continue
            seen.add(resolved)
            (append if entry.get("position") == "append" else prepend).append(resolved)
        current = env.get(variable) or ""
        kept = [part for part in current.split(separator) if part and part not in seen]
        env._put(
            variable,
            separator.join(prepend + kept + append),
            env.is_secret(variable),
        )


@dataclasses.dataclass(frozen=True)
class ScriptRun:
    """An env script a consumer runs, with its settings. ``timeout`` is seconds; 0 is no limit."""

    interpreter: str
    path: str
    args: List[str]
    timeout: int


def apply_overlay(
    env: Environment,
    manifest: Document,
    facts: Facts,
    non_defaults: "List[NonDefaultSetting]",
    reports: "Optional[List[Tuple[str, str]]]" = None,
) -> "List[ScriptRun]":
    """Apply one overlay's ``env``, env files and path additions to ``env`` and return the env scripts
    that run, in order. What is reported is appended to ``non_defaults``. With ``facts.gate_env_scripts``
    and an overlay whose digest is not the allowed one, none runs, and ``(env-script-not-allowed,
    identity)`` is appended to ``reports`` when the overlay has an env script for this OS.

    While it runs, placeholders also see ``AOS_OVERLAY_ROOT``, ``AOS_OVERLAY_NAME`` and, when
    ``facts.state`` is set, ``AOS_OVERLAY_STATE``. Raises ``Error`` for a declaration the consumer
    refuses: ``env-path-conflict``, ``missing-file``, ``invalid-env-file``, ``reserved-variable``,
    ``unresolved-reference``, ``invalid-reference``, ``partial-lock`` and the substitution classes,
    each with the variable, path or reference it is about as its subject.
    """
    require_valid(manifest)
    env._extra = {"AOS_OVERLAY_ROOT": facts.root, "AOS_OVERLAY_NAME": normalize_name(facts.identity)}
    if facts.state is not None:
        env._extra["AOS_OVERLAY_STATE"] = facts.state
    try:
        return _apply_overlay(env, manifest, facts, non_defaults, reports)
    finally:
        env._extra = {}


def require_valid(manifest: Document) -> None:
    """Refuse a manifest that would make the consumer touch what a manifest may not name: an
    environment variable whose name is not a name (``A=B``, a line break), or a path that leaves the
    overlay (``../x``, an absolute path, a device name). Everything else a manifest gets wrong is
    reported where it is applied or by ``validate``. Raises ``invalid-reference`` with the name or
    path."""
    env = manifest.get("env")
    if isinstance(env, dict):
        for name in env:
            if not is_variable_name(name):
                raise Error(Class.INVALID_REFERENCE, name)

    def check(path: object) -> None:
        if isinstance(path, str) and not valid_relative_path(path):
            raise Error(Class.INVALID_REFERENCE, path)

    for field in ("paths", "env_files", "env_scripts", "setup"):
        entries = manifest.get(field)
        for entry in entries if isinstance(entries, list) else ():
            check(entry.get("path") if isinstance(entry, dict) else entry)
    scripts = manifest.get("scripts")
    for target in scripts.values() if isinstance(scripts, dict) else ():
        check(target.get("path") if isinstance(target, dict) else target)
    runtime = manifest.get("runtime")
    for variants in runtime.values() if isinstance(runtime, dict) else ():
        for variant in variants if isinstance(variants, list) else ():
            for key in ("lock", "requirements", "package"):
                check(variant.get(key) if isinstance(variant, dict) else None)


def _apply_overlay(
    env: Environment,
    manifest: Document,
    facts: Facts,
    non_defaults: "List[NonDefaultSetting]",
    reports: "Optional[List[Tuple[str, str]]]",
) -> "List[ScriptRun]":
    from ._hooks import _same_digest

    from ._document import read_document
    from ._validate import validate

    os, root, tree = facts.os, facts.root, facts.tree
    paths: "List[Any]" = list(default_paths(manifest, tree))
    for dependency in dependencies_of(manifest, tree, os):
        packages = facts.packages.get(dependency.runtime.value)
        if packages is not None:
            variable = "PYTHONPATH" if dependency.runtime is Runtime.PYTHON else "NODE_PATH"
            paths.append({"variable": variable, "packages": packages})
    paths.extend(declared(manifest, "paths"))
    listed = {
        ascii_upper(entry["variable"])
        for entry in paths
        if isinstance(entry, dict) and isinstance(entry.get("variable"), str)
    }
    own_env = manifest.get("env") if isinstance(manifest.get("env"), dict) else {}
    for name in own_env:
        if ascii_upper(name) in listed:
            raise Error(Class.ENV_PATH_CONFLICT, name)
    for item in env_files_of(manifest, tree):
        if not entry_on(item, os):
            continue
        path = named(item) or ""
        dotenv = isinstance(item, dict) and item.get("format") == "dotenv"
        text = tree.text(path)
        if text is None:
            raise Error(Class.MISSING_FILE, path)
        if dotenv:
            try:
                lines = parse_dotenv(text.encode("utf-8"))
            except Error:
                raise Error(Class.INVALID_ENV_FILE, path) from None
            for line in lines:
                if ascii_upper(line.name) in listed:
                    raise Error(Class.ENV_PATH_CONFLICT, line.name)
            for line in lines:
                if is_reserved_variable(line.name):
                    raise Error(Class.RESERVED_VARIABLE, line.name)
                value = line.text if line.literal else env._substitute(line.text, line.name)
                if is_sensitive_variable(line.name):
                    non_defaults.append(NonDefaultSetting(NonDefaultId.ENV_SENSITIVE, line.name))
                env.set(line.name, value)
        else:
            try:
                mapping = read_document(text.encode("utf-8"), facts.settings)
            except Error:
                raise Error(Class.INVALID_ENV_FILE, path) from None
            if validate({"env": mapping}, None):
                raise Error(Class.INVALID_ENV_FILE, path)
            for name in mapping:
                if ascii_upper(name) in listed:
                    raise Error(Class.ENV_PATH_CONFLICT, name)
            _apply_mapping(env, mapping, facts, root, non_defaults)
    _apply_mapping(env, own_env, facts, root, non_defaults)
    _apply_paths(env, paths, root, os, non_defaults)
    scripts: "List[ScriptRun]" = []
    if facts.env_scripts_enabled is not False:
        choice = default_script(manifest, "env_scripts", "env", tree, os, facts.known_interpreters())
        if facts.gate_env_scripts and not (
            facts.digest is not None
            and facts.allowed is not None
            and _same_digest(facts.digest, facts.allowed)
        ):
            if reports is not None and (
                choice.chosen is not None
                or choice.present
                or any(entry_on(script, os) for script in choice.others)
            ):
                reports.append(("env-script-not-allowed", facts.identity))
            return scripts
        if choice.chosen is None:
            non_defaults.extend(
                NonDefaultSetting(NonDefaultId.SCRIPT_NOT_RUN, path) for path in choice.present
            )
        for script in ([choice.chosen] if choice.chosen is not None else []) + choice.others:
            if not entry_on(script, os):
                continue
            run = ScriptRun(
                _text(script, "interpreter"),
                _text(script, "path"),
                _strings(script, "args"),
                _unsigned(script, "timeout", 60),
            )
            non_defaults.append(NonDefaultSetting(NonDefaultId.ENV_SCRIPT, run.path))
            if run.timeout == 0:
                non_defaults.append(NonDefaultSetting(NonDefaultId.NO_TIMEOUT, run.path))
            scripts.append(run)
    return scripts
