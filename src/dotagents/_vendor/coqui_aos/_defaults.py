"""What conventional files contribute unless ``[defaults]`` turns them off, computed from an
overlay's files, and the dependency files an overlay installs."""

from __future__ import annotations

import dataclasses
import enum
import re
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Sequence, Set, Tuple

from ._data import Document
from ._errors import Class, Error
from ._names import normalize_name, os_matches, valid_relative_path, valid_tool_name
from ._settings import Settings
from ._source import Source
from ._tree import OverlayTree

__all__ = [
    "DEFAULT_KEYS",
    "INTERPRETERS",
    "Dependency",
    "PartialLock",
    "Runtime",
    "SkillEntry",
    "SkillOrigin",
    "bundled_skills",
    "check_requirements",
    "context_ignored",
    "context_of",
    "default_on",
    "default_paths",
    "dependencies_of",
    "env_files_of",
    "harness_context_of",
    "paths_of",
    "requirements_locked",
    "rules_of",
    "skill_entries",
    "skills_of",
]

#: The keys of ``defaults``.
DEFAULT_KEYS = ("bin", "lib", "env", "env_scripts", "setup", "deps", "context", "skills", "rules", "hooks")

#: Every interpreter AOS names.
INTERPRETERS = ("python", "node", "powershell", "bash", "sh")

#: Default path additions as ``(key, variable, directory)``, in the order they apply.
_DEFAULT_PATHS = (
    ("bin", "PATH", "bin"),
    ("lib", "PYTHONPATH", "lib/python"),
    ("lib", "NODE_PATH", "lib/node"),
)

#: Default script extensions in the order a consumer tries them: extension, interpreter, and the
#: OS the interpreter applies on (``None``: every OS).
_DEFAULT_SCRIPTS = (
    (".py", "python", None),
    (".js", "node", None),
    (".mjs", "node", None),
    (".cjs", "node", None),
    (".ps1", "powershell", "windows"),
    (".bash", "bash", "unix"),
    (".sh", "sh", "unix"),
)

_BLANK = " \t"


class Runtime(str, enum.Enum):
    """A runtime whose packages an overlay installs."""

    PYTHON = "python"
    NODE = "node"

    @classmethod
    def named(cls, word: str) -> "Optional[Runtime]":
        """What a word stands for, or ``None``."""
        try:
            return cls(word)
        except ValueError:
            return None

    def __str__(self) -> str:
        return self.value


class SkillOrigin(str, enum.Enum):
    """Where a skill an overlay offers comes from."""

    BUNDLED = "bundled"
    SOURCE = "source"
    CATALOG = "catalog"

    def __str__(self) -> str:
        return self.value


@dataclasses.dataclass(frozen=True)
class SkillEntry:
    """A skill an overlay offers. ``source`` and ``key`` are for a ``SOURCE`` skill: the source as
    shown (no user information, no query) and in full as it is fetched; ``from_`` is the parsed
    source with the table's ``ref``. ``pin`` is the tree digest the table pins."""

    name: str
    origin: SkillOrigin
    source: Optional[str] = None
    key: Optional[str] = None
    pin: Optional[str] = None
    from_: Optional[Source] = None


@dataclasses.dataclass(frozen=True)
class Dependency:
    """A dependency file a consumer installs. ``lock`` is the file that pins it, when there is one:
    the file itself for a hashed requirements file; ``environment`` is ``shared``, ``isolated`` or
    a name."""

    runtime: Runtime
    file: str
    lock: Optional[str]
    environment: str


class PartialLock(Error):
    """A requirements file only some of whose requirements carry a hash; ``subject`` is its path."""

    def __init__(self, path: str) -> None:
        super().__init__(Class.PARTIAL_LOCK, path)


def _members(tree: OverlayTree) -> "List[str]":
    """The paths that make a path present, in byte order: digest members, so no excluded name or
    ``.pyc``."""
    return sorted(
        (p for p in tree.paths if valid_relative_path(p)), key=lambda p: p.encode("utf-8", "surrogatepass")
    )


def _has_member(tree: OverlayTree, path: str) -> bool:
    return path in tree and valid_relative_path(path)


def default_on(manifest: Document, key: str) -> bool:
    """Whether the default ``key`` is on: true unless ``defaults`` writes it ``false``."""
    defaults = manifest.get("defaults")
    return not (isinstance(defaults, dict) and defaults.get(key) is False)


def declared(manifest: Document, field: str) -> "List[Any]":
    """The list ``field`` holds, or nothing."""
    value = manifest.get(field)
    return list(value) if isinstance(value, list) else []


def named(entry: Any) -> "Optional[str]":
    """The file or directory an entry names: a string entry, or a mapping's ``path``."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict) and isinstance(entry.get("path"), str):
        return entry["path"]
    return None


def _merge(defaults: "List[Any]", declared_entries: "List[Any]") -> "List[Any]":
    """Default entries first, then declared ones; a declared entry naming the same file or directory
    as a default one replaces it."""
    names = {n for n in map(named, declared_entries) if n is not None}
    return [entry for entry in defaults if named(entry) not in names] + declared_entries


def _strings(entries: "Iterable[Any]") -> "List[str]":
    return [entry for entry in entries if isinstance(entry, str)]


def os_list(entry: Any) -> "Optional[List[str]]":
    """An entry's ``os`` list, when it has one."""
    if isinstance(entry, dict) and isinstance(entry.get("os"), list):
        return [name for name in entry["os"] if isinstance(name, str)]
    return None


def entry_on(entry: Any, os: str) -> bool:
    """Whether an entry applies on ``os``: it names no ``os``, or names this one."""
    return os_matches(os_list(entry), os)


def first_variant(variants: "Iterable[Any]", os: str) -> Any:
    """The first variant that applies on ``os``, or ``None``."""
    for variant in variants:
        if entry_on(variant, os):
            return variant
    return None


def default_paths(manifest: Document, tree: OverlayTree) -> "List[Document]":
    """The default path additions that are on, present and not replaced by a declared entry, as
    ``{variable, path}`` mappings."""
    replaced = {n for n in map(named, declared(manifest, "paths")) if n is not None}
    members = _members(tree)
    return [
        {"variable": variable, "path": directory}
        for key, variable, directory in _DEFAULT_PATHS
        if default_on(manifest, key)
        and directory not in replaced
        and any(path.startswith(directory + "/") for path in members)
    ]


def paths_of(manifest: Document, tree: OverlayTree) -> "List[Any]":
    """The ``bin`` and ``lib`` defaults, then ``paths``."""
    return list(default_paths(manifest, tree)) + declared(manifest, "paths")


def rules_of(manifest: Document, tree: OverlayTree) -> "List[str]":
    """Every ``rules/<name>.md`` directly in ``rules/`` by path bytes, then ``rules``."""
    found: "List[Any]" = []
    if default_on(manifest, "rules"):
        found = [
            path
            for path in _members(tree)
            if path.startswith("rules/") and "/" not in path[6:] and path.endswith(".md")
        ]
    return _strings(_merge(found, declared(manifest, "rules")))


def _has_body(tree: OverlayTree) -> bool:
    """Whether the tree's ``OVERLAY.md``, whose bytes were read, has a body: anything but white
    space after its front matter."""
    from ._front_matter import split_front_matter

    data = tree.bytes("OVERLAY.md")
    if data is None:
        return False
    body = split_front_matter(data, Settings().replace(manifest_bytes=None)).body(data)
    return bool(body.strip(b" \t\r\n"))


def _body_is_context(manifest: Document, tree: OverlayTree) -> bool:
    """Whether the body of ``OVERLAY.md`` is the overlay's context: it has one, and ``defaults``
    leaves ``context`` on or ``context`` names ``OVERLAY.md``."""
    return _has_body(tree) and (
        default_on(manifest, "context") or "OVERLAY.md" in declared(manifest, "context")
    )


def context_of(manifest: Document, tree: OverlayTree) -> "List[str]":
    """The body of ``OVERLAY.md`` else the root ``CONTEXT.md``, then ``context``; while a body is
    context, ``CONTEXT.md`` is not, wherever it is named."""
    found: "List[Any]" = []
    if default_on(manifest, "context"):
        if _has_body(tree):
            found = ["OVERLAY.md"]
        elif _has_member(tree, "CONTEXT.md"):
            found = ["CONTEXT.md"]
    merged = _strings(_merge(found, declared(manifest, "context")))
    if _body_is_context(manifest, tree):
        return [entry for entry in merged if entry != "CONTEXT.md"]
    return merged


def context_ignored(manifest: Document, tree: OverlayTree) -> "List[str]":
    """The file a consumer reports as ignored because a body is the context instead."""
    if _body_is_context(manifest, tree) and _has_member(tree, "CONTEXT.md"):
        return ["CONTEXT.md"]
    return []


def harness_context_of(manifest: Document, harness: str, tree: OverlayTree) -> "List[str]":
    """One harness's ``context``, without ``CONTEXT.md`` while a body is context."""
    hints = manifest.get("harness")
    hints = hints.get(harness) if isinstance(hints, dict) else None
    listed = hints.get("context") if isinstance(hints, dict) else None
    entries = _strings(listed) if isinstance(listed, list) else []
    if _body_is_context(manifest, tree):
        return [entry for entry in entries if entry != "CONTEXT.md"]
    return entries


def bundled_skills(tree: OverlayTree) -> "Set[str]":
    """The names with a ``skills/<name>/SKILL.md`` in the overlay."""
    found = set()
    for path in _members(tree):
        parts = path.split("/")
        if len(parts) == 3 and parts[0] == "skills" and parts[2] == "SKILL.md":
            if valid_tool_name(parts[1]):
                found.add(parts[1])
    return found


def skill_entries(manifest: Document, tree: OverlayTree) -> "List[SkillEntry]":
    """Every skill the overlay offers, by name bytes: bundled, from a source, or a name for the
    consumer's catalog to resolve. A table that names no source, or one that does not parse, is
    left out."""
    bundled = bundled_skills(tree)
    found: "Dict[str, SkillEntry]" = {}
    if default_on(manifest, "skills"):
        for name in bundled:
            found[name] = SkillEntry(name, SkillOrigin.BUNDLED)
    for entry in declared(manifest, "skills"):
        if isinstance(entry, dict):
            name, source = entry.get("name"), entry.get("source")
            if not isinstance(name, str) or not isinstance(source, str):
                continue
            try:
                parsed = Source.parse(source)
            except Error:
                continue
            ref = entry.get("ref") if isinstance(entry.get("ref"), str) else None
            pin = entry.get("pin")
            found[name] = SkillEntry(
                name,
                SkillOrigin.SOURCE,
                parsed.shown(ref),
                parsed.key(ref),
                pin if isinstance(pin, str) else None,
                dataclasses.replace(parsed, ref=ref) if ref is not None else parsed,
            )
        elif isinstance(entry, str):
            found[entry] = SkillEntry(entry, SkillOrigin.BUNDLED if entry in bundled else SkillOrigin.CATALOG)
    return [found[name] for name in sorted(found, key=lambda n: n.encode("utf-8", "surrogatepass"))]


def skills_of(manifest: Document, tree: OverlayTree) -> "List[str]":
    """The names of ``skill_entries``."""
    return [entry.name for entry in skill_entries(manifest, tree)]


def env_files_of(manifest: Document, tree: OverlayTree) -> "List[Any]":
    """The root file ``env`` read as dotenv, then ``env_files``."""
    found: "List[Any]" = []
    if default_on(manifest, "env") and _has_member(tree, "env"):
        found = [{"path": "env", "format": "dotenv"}]
    return _merge(found, declared(manifest, "env_files"))


class DefaultScript(NamedTuple):
    """The default ``<stem>.<ext>`` a consumer runs for ``env_scripts`` or ``setup``.

    ``chosen`` is the entry with its settings as declared or by default, ``configured`` whether a
    declared entry configured it, ``present`` the candidate files in the tree (reported when none
    is chosen) and ``others`` the declared entries that name no candidate.
    """

    chosen: "Optional[Document]"
    configured: bool
    present: "List[str]"
    others: "List[Any]"


def default_script(
    manifest: Document, field: str, stem: str, tree: OverlayTree, os: str, interpreters: "Sequence[str]"
) -> DefaultScript:
    """The default script of ``field`` named ``stem`` that runs on ``os`` with one of ``interpreters``."""
    entries = declared(manifest, field)
    if not default_on(manifest, field):
        return DefaultScript(None, False, [], entries)
    present = [stem + ext for ext, _i, _o in _DEFAULT_SCRIPTS if _has_member(tree, stem + ext)]
    configured = {
        entry["path"]: entry for entry in entries if isinstance(entry, dict) and entry.get("path") in present
    }
    others = [entry for entry in entries if named(entry) not in configured]
    for ext, interpreter, on in _DEFAULT_SCRIPTS:
        path = stem + ext
        if path not in present:
            continue
        entry = configured.get(path)
        if entry is None:
            entry = {"interpreter": interpreter, "path": path}
            if on is not None:
                entry["os"] = [on]
        runs = isinstance(entry.get("interpreter"), str) and entry["interpreter"] in interpreters
        if entry_on(entry, os) and runs:
            return DefaultScript(entry, path in configured, present, others)
    return DefaultScript(None, False, present, others)


def environment_name(variant: Any) -> str:
    """The environment a runtime variant installs into: ``shared``, ``isolated`` or a name."""
    name = variant.get("environment") if isinstance(variant, dict) else None
    return normalize_name(name if isinstance(name, str) else "shared")


_CONTINUATION = re.compile(r"\\\r?\n")


def _lines(text: str) -> "List[str]":
    """The lines of ``text`` once continuations are joined: split at ``\\n`` only, a ``\\r\\n`` ending cut whole."""
    return [line[:-1] if line.endswith("\r") else line for line in _CONTINUATION.sub(" ", text).split("\n")]


def requirements_locked(text: str, path: str) -> bool:
    """Whether a requirements file is a lock: every requirement in it carries ``--hash``.

    A file where only some do raises ``PartialLock``: pip would check hashes for all of them.
    """
    hashed = []
    for line in _lines(text):
        line = line.strip()
        if line and not line.startswith("#"):
            hashed.append(re.search(r"(?:^|\s)--hash(?:=|\s)", line) is not None)
    if hashed and all(hashed):
        return True
    if any(hashed):
        raise PartialLock(path)
    return False


def check_requirements(text: str, locked: bool) -> None:
    """Refuse a requirements file before pip reads it: it holds only requirement lines and comments,
    since pip would otherwise follow ``-r`` and ``-c``, change the index or install a direct URL, and
    in a ``locked`` file every requirement is pinned with ``==``.

    Raises ``invalid-requirements`` on the first line refused, with the reason as its subject and the
    line's number; the line's text, which may hold a credential, is never quoted.
    """
    for number, line in enumerate(_lines(text), 1):

        def refused(why: str) -> Error:
            return Error(Class.INVALID_REQUIREMENTS, why).on_line(number)

        line = line.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        requirement, _, options = line.partition(" --")
        if requirement.startswith("-"):
            raise refused("an option, and only requirements are read")
        if "://" in requirement or " @ " in requirement:
            raise refused("a direct URL")
        if options and any(not o.lstrip("-").startswith("hash") for o in options.split(" --")):
            raise refused("an option other than --hash")
        if locked and "==" not in requirement:
            raise refused("a requirement not pinned with ==")


def dependencies_of(manifest: Document, tree: OverlayTree, os: str) -> "List[Dependency]":
    """The dependency files a consumer installs on ``os``, one per runtime.

    A selected variant that names a file replaces the default one; one that names none still
    chooses its environment. Raises ``PartialLock`` for a requirements file only some of whose
    requirements are hashed.
    """
    runtime = manifest.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    found: "List[Dependency]" = []
    for name, default, beside in (
        ("python", "lib/python/requirements.txt", None),
        ("node", "lib/node/package.json", "lib/node/package-lock.json"),
    ):
        variants = runtime.get(name)
        variant = first_variant(variants if isinstance(variants, list) else [], os)

        def field(key: str) -> "Optional[str]":
            value = variant.get(key) if isinstance(variant, dict) else None
            return value if isinstance(value, str) else None

        lock: "Optional[str]"
        if name == "python" and (field("lock") is not None or field("requirements") is not None):
            file = field("lock") or field("requirements") or ""
            lock = field("lock")
        elif name == "node" and field("package") is not None:
            file = field("package") or ""
            candidate = (
                file[: -len("package.json")] + "package-lock.json" if file.endswith("package.json") else None
            )
            lock = candidate if candidate is not None and _has_member(tree, candidate) else None
        elif default_on(manifest, "deps") and _has_member(tree, default):
            file = default
            if beside is None:
                text = tree.text(default)
                lock = default if text is not None and requirements_locked(text, default) else None
            else:
                lock = beside if _has_member(tree, beside) else None
        else:
            continue
        found.append(Dependency(Runtime(name), file, lock, environment_name(variant)))
    return found
