"""What a consumer applies for one overlay (environment, setup, runtime, needs, reports), when setup
runs, and the package environments the active overlays install into."""

from __future__ import annotations

import dataclasses
import enum
import json
import re
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

from ._data import Document
from ._defaults import (
    Dependency,
    Runtime,
    default_script,
    dependencies_of,
    entry_on,
    first_variant,
)
from ._env import (
    Environment,
    Facts,
    NonDefaultId,
    NonDefaultSetting,
    ScriptRun,
    _strings,
    _text,
    _unsigned,
    apply_overlay,
    require_valid,
    is_reference,
    reference_name,
)
from ._errors import Class, Error
from ._names import root_variable, state_variable
from ._tree import OverlayTree

__all__ = [
    "Change",
    "Effective",
    "Environments",
    "FailStep",
    "LeftEnvironment",
    "LeftState",
    "Needs",
    "PackageConflict",
    "PackageEnvironment",
    "Phase",
    "Preparation",
    "SetupRecord",
    "SetupRun",
    "Step",
    "effective",
    "environments",
    "preparation",
    "setup_phase",
]


class _Words(str, enum.Enum):
    """A closed set of words: a member equals its word."""

    @classmethod
    def named(cls, word: str):  # type: ignore[no-untyped-def]
        """What a word stands for, or ``None``."""
        try:
            return cls(word)
        except ValueError:
            return None

    def __str__(self) -> str:
        return self.value


class Change(_Words):
    """What a consumer is doing to an overlay when it decides whether setup runs."""

    #: Adding it.
    ADD = "add"
    #: A sync: a fetch of its source, a change of ref or pin, or setup run again on request.
    SYNC = "sync"
    #: Preparing a digest for activation outside a sync.
    ACTIVATE = "activate"
    #: Removing it.
    REMOVE = "remove"


class Phase(_Words):
    """A setup phase."""

    #: The first preparation of an identity on an OS.
    INSTALL = "install"
    #: Every later preparation.
    SYNC = "sync"
    #: Removal.
    REMOVE = "remove"


class FailStep(_Words):
    """The step of a preparation that fails."""

    DEPS = "deps"
    SETUP = "setup"


class LeftState(_Words):
    """What a removed overlay's dependencies leave of an environment."""

    REBUILT = "rebuilt"
    DELETED = "deleted"


class Step(str):
    """A step of a preparation, as the specification writes it: ``empty-state``, ``deps:<runtime>``,
    ``setup``, ``delete`` or ``record``. It equals its text."""

    __slots__ = ()

    @classmethod
    def deps(cls, runtime: Runtime) -> "Step":
        return cls("deps:" + runtime.value)

    @property
    def kind(self) -> str:
        """The step without its runtime: ``deps`` for ``deps:python``."""
        return self.split(":", 1)[0]


@dataclasses.dataclass(frozen=True)
class SetupRun:
    """The setup variant a consumer runs. ``timeout`` is seconds, 0 for no limit; ``network`` is
    whether it gets the network."""

    interpreter: str
    path: str
    args: List[str]
    timeout: int
    network: bool


@dataclasses.dataclass(frozen=True)
class SetupRecord:
    """The last preparation that succeeded for an overlay identity on one OS: its digest
    (``sha256:<hex>``) and the phase that prepared it."""

    digest: str
    phase: Phase


class Needs(NamedTuple):
    """What a target must have for an overlay on its OS: interpreters and commands, each sorted."""

    interpreters: List[str]
    commands: List[str]


@dataclasses.dataclass
class Effective:
    """What a consumer applies for one overlay on one OS.

    ``ungranted`` are the references that were not granted (``secret:<name>`` or ``<provider>:<key>``),
    ``supported`` whether ``runtime.os`` admits the OS, ``env`` the environment of the overlay's own
    processes, ``python`` and ``node`` the selected variants with ``os`` left out, and
    ``non_defaults`` what is reported, sorted, each once.
    """

    ungranted: List[str]
    supported: bool
    env: Environment
    env_scripts: List[ScriptRun]
    setup: Optional[SetupRun]
    python: Optional[Document]
    node: Optional[Document]
    dependencies: List[Dependency]
    needs: Needs
    non_defaults: List[NonDefaultSetting]


def _variant(runtime: Any, name: str, os: str) -> "Optional[Document]":
    variants = runtime.get(name) if isinstance(runtime, dict) else None
    chosen = first_variant(variants, os) if isinstance(variants, list) else None
    if not isinstance(chosen, dict):
        return None
    return {key: value for key, value in chosen.items() if key != "os"}


_SCRIPT_INTERPRETERS = {".py": "python", ".sh": "sh", ".bash": "bash", ".ps1": "powershell"}


def effective(manifest: Document, facts: Facts) -> Effective:
    """What a consumer applies for one overlay: its environment, setup, runtime, needs and reports,
    from its manifest and the consumer's facts.

    Raises ``Error`` when the overlay's environment cannot be applied (see ``apply_overlay``), and
    ``partial-lock`` for a requirements file only some of whose requirements are hashed.
    """
    require_valid(manifest)
    os = facts.os
    non_defaults: "List[NonDefaultSetting]" = []
    if not facts.pinned:
        non_defaults.append(NonDefaultSetting(NonDefaultId.UNPINNED, ""))
    runtime = manifest.get("runtime")
    supported = not (isinstance(runtime, dict) and "os" in runtime) or entry_on(runtime, os)
    env = Environment(facts.base_env, facts.windows, facts.settings)
    env.set(root_variable(facts.identity), facts.root)
    if facts.state is not None:
        env.set(state_variable(facts.identity), facts.state)
    scripts = apply_overlay(env, manifest, facts, non_defaults)
    choice = default_script(manifest, "setup", "setup", facts.tree, os, facts.known_interpreters())
    # Declared variants come first; the default setup is the last one.
    network = False
    chosen = first_variant(choice.others, os)
    if chosen is None:
        chosen = choice.chosen
        # A default setup that declares nothing is taken to need the network.
        network = not choice.configured
        if chosen is None:
            non_defaults.extend(
                NonDefaultSetting(NonDefaultId.SCRIPT_NOT_RUN, path) for path in choice.present
            )
    setup: "Optional[SetupRun]" = None
    if chosen is not None:
        flag = chosen.get("network") if isinstance(chosen, dict) else None
        setup = SetupRun(
            _text(chosen, "interpreter"),
            _text(chosen, "path"),
            _strings(chosen, "args"),
            _unsigned(chosen, "timeout", 300),
            flag if isinstance(flag, bool) else network,
        )
        if setup.network:
            non_defaults.append(NonDefaultSetting(NonDefaultId.SETUP_NETWORK, setup.path))
        elif facts.enforces_network is False:
            non_defaults.append(NonDefaultSetting(NonDefaultId.NETWORK_NOT_ENFORCED, setup.path))
        if setup.timeout == 0:
            non_defaults.append(NonDefaultSetting(NonDefaultId.NO_TIMEOUT, setup.path))
    python = _variant(runtime, "python", os)
    dependencies = dependencies_of(manifest, facts.tree, os)
    for dependency in dependencies:
        # Installing fetches packages: it needs the network, locked or not.
        non_defaults.append(NonDefaultSetting(NonDefaultId.DEPS_NETWORK, dependency.file))
        if dependency.lock is None:
            non_defaults.append(NonDefaultSetting(NonDefaultId.UNLOCKED_DEPENDENCIES, dependency.file))
    node = _variant(runtime, "node", os)
    interpreters: "Set[str]" = {script.interpreter for script in scripts}
    if setup is not None:
        interpreters.add(setup.interpreter)
    targets = manifest.get("scripts")
    for target in targets.values() if isinstance(targets, dict) else ():
        if isinstance(target, str):
            extension = target[target.rfind(".") :] if "." in target else ""
            interpreters.add(_SCRIPT_INTERPRETERS.get(extension, "node"))
        elif isinstance(target, dict) and entry_on(target, os):
            interpreters.add(_text(target, "interpreter"))
    if python is not None:
        interpreters.add("python")
    if node is not None:
        interpreters.add("node")
    commands: "Set[str]" = set()
    listed = runtime.get("commands") if isinstance(runtime, dict) else None
    for command in listed if isinstance(listed, list) else ():
        if isinstance(command, str):
            commands.add(command)
        elif entry_on(command, os):
            commands.add(_text(command, "name"))
    for dependency in dependencies:
        interpreters.add(dependency.runtime.value)
        if dependency.runtime is Runtime.NODE:
            commands.add("npm")
    ungranted: "Set[str]" = set()
    host_grants = facts.grants.get("env", [])
    mcp = manifest.get("mcp")
    servers = mcp.get("servers") if isinstance(mcp, dict) else None
    for server in servers if isinstance(servers, list) else ():
        if not isinstance(server, dict):
            continue
        server_env = server.get("env")
        for value in server_env.values() if isinstance(server_env, dict) else ():
            if not is_reference(value):
                continue
            if not facts.granted(value):
                ungranted.add(reference_name(value))
            elif value.get("provider") == "env":
                non_defaults.append(NonDefaultSetting(NonDefaultId.ENV_PASSTHROUGH, _text(value, "key")))
            else:
                non_defaults.append(NonDefaultSetting(NonDefaultId.SECRET_READ, reference_name(value)))
        headers = server.get("headers_env")
        header_names = (
            [v for v in headers.values() if isinstance(v, str)] if isinstance(headers, dict) else []
        )
        for name in _strings(server, "env_passthrough") + header_names:
            if name in host_grants:
                non_defaults.append(NonDefaultSetting(NonDefaultId.ENV_PASSTHROUGH, name))
            else:
                ungranted.add("env:" + name)
        url = _text(server, "url")
        if url[:7].lower() == "http://":
            authority = url[7:].split("/", 1)[0].split("?", 1)[0]
            host = (authority.rsplit(":", 1)[0] if ":" in authority else authority).lower()
            if host not in ("localhost", "[::1]") and not host.startswith("127."):
                non_defaults.append(NonDefaultSetting(NonDefaultId.INSECURE_TRANSPORT, _text(server, "name")))
    own_env = manifest.get("env")
    for entry in own_env.values() if isinstance(own_env, dict) else ():
        if is_reference(entry) and entry.get("optional") is True and not facts.granted(entry):
            ungranted.add(reference_name(entry))
    return Effective(
        ungranted=sorted(ungranted),
        supported=supported,
        env=env,
        env_scripts=scripts,
        setup=setup,
        python=python,
        node=node,
        dependencies=dependencies,
        needs=Needs(sorted(interpreters), sorted(commands)),
        non_defaults=sorted(set(non_defaults)),
    )


def setup_phase(change: Change, recorded: "Optional[str]", digest: "Optional[str]") -> "Optional[Phase]":
    """The phase a change runs, or ``None`` when nothing runs. ``recorded`` is the digest of the last
    preparation of this overlay that succeeded on this OS, which the consumer keeps per identity and
    forgets when the overlay is removed."""
    if change is Change.REMOVE:
        return Phase.REMOVE if recorded is not None else None
    if recorded is None:
        return Phase.INSTALL
    if change is Change.ACTIVATE and digest is not None and digest.lower() == recorded.lower():
        return None
    return Phase.SYNC


@dataclasses.dataclass(frozen=True)
class Preparation:
    """What a preparation does: its phase (``None`` when nothing runs), setup's arguments (the phase,
    then the variant's own), the steps in order, the record afterwards and whether the digest may be
    activated."""

    phase: Optional[Phase]
    arguments: Optional[List[str]]
    steps: List[Step]
    record: Optional[SetupRecord]
    activates: bool


def preparation(
    change: Change,
    record: "Optional[SetupRecord]",
    digest: "Optional[str]",
    setup: "Optional[SetupRun]",
    dependencies: "Sequence[Dependency]",
    fails: "Optional[FailStep]" = None,
) -> Preparation:
    """The preparation of ``digest`` for ``change``, given the ``record`` kept for the overlay and the
    step that ``fails``, if one does: the steps stop there, except that a removal still deletes
    everything."""
    recorded = record.digest if record is not None else None
    phase = setup_phase(change, recorded, digest) if setup is not None or dependencies else None
    steps: "List[Step]" = []
    if phase is Phase.INSTALL:
        steps.append(Step("empty-state"))
    if phase in (Phase.INSTALL, Phase.SYNC):
        steps.extend(Step.deps(dependency.runtime) for dependency in dependencies)
    if phase is not None and setup is not None:
        steps.append(Step("setup"))
    failed = False
    if fails is not None:
        for index, step in enumerate(steps):
            if step.kind == fails.value:
                del steps[index + 1 :]
                failed = True
                break
    after: "Optional[SetupRecord]"
    if change is Change.REMOVE:
        steps.append(Step("delete"))
        after, activates = None, False
    elif phase is None:
        after, activates = record, True
    elif failed:
        # The record still names the last digest prepared; only that one stays usable.
        after, activates = record, recorded is not None and recorded == digest
    else:
        steps.append(Step("record"))
        after, activates = SetupRecord(digest or "", phase), True
    arguments = [phase.value] + list(setup.args) if phase is not None and setup is not None else None
    return Preparation(phase, arguments, steps, after, activates)


@dataclasses.dataclass(frozen=True)
class PackageEnvironment:
    """One environment the active overlays install packages into. ``files`` are ``(overlay,
    dependency file)`` pairs in composition order; ``locked`` is whether it installs pinned: every
    member's file locked (one member, for Node.js)."""

    runtime: Runtime
    environment: str
    overlays: List[str]
    files: List[Tuple[str, str]]
    locked: bool


@dataclasses.dataclass(frozen=True)
class PackageConflict:
    """Two members of one environment asking for one package at different versions; ``package`` is
    the name normalized."""

    runtime: Runtime
    environment: str
    package: str
    overlays: List[str]


@dataclasses.dataclass(frozen=True)
class LeftEnvironment:
    """What a removed overlay's dependencies leave: ``then`` is ``rebuilt`` while another member
    remains, else ``deleted``."""

    overlay: str
    runtime: Runtime
    environment: str
    then: LeftState


@dataclasses.dataclass
class Environments:
    """The environments of the active overlays, the conflicts that block overlays (``blocked``, in the
    order given), and what each removed overlay leaves."""

    environments: List[PackageEnvironment] = dataclasses.field(default_factory=list)
    conflicts: List[PackageConflict] = dataclasses.field(default_factory=list)
    blocked: List[str] = dataclasses.field(default_factory=list)
    left: List[LeftEnvironment] = dataclasses.field(default_factory=list)


_CONTINUATION = re.compile(r"\\\r?\n")


def _pinned_requirement(line: str) -> "Optional[Tuple[str, str]]":
    """``name[extras] == version`` at the start of a requirement line: the name normalized as PEP 503
    does, and the version."""
    if not line or not (line[0].isascii() and line[0].isalnum()):
        return None
    end = 0
    while end < len(line) and line[end].isascii() and (line[end].isalnum() or line[end] in "._-"):
        end += 1
    name = line[:end].rstrip("._-")
    after = line[len(name) :]
    if not after or not (after[0].isspace() or after[0] in "[="):
        return None
    rest = after.lstrip()
    if rest.startswith("["):
        close = rest.find("]")
        if close < 0:
            return None
        rest = rest[close + 1 :].lstrip()
    if rest.startswith("==="):
        rest = rest[3:]
    elif rest.startswith("=="):
        rest = rest[2:]
    else:
        return None
    rest = rest.lstrip()
    version = ""
    for char in rest:
        if char.isspace() or char in ";\\":
            break
        version += char
    if not version:
        return None
    normalized = ""
    for char in name:
        if char in "-_.":
            if not normalized.endswith("-"):
                normalized += "-"
        else:
            normalized += char.lower() if char.isascii() else char
    return normalized, version


def _pins(runtime: Runtime, text: "Optional[str]") -> "Dict[str, str]":
    """Package name to the exact version or range a dependency file asks for."""
    if text is None:
        return {}
    if runtime is Runtime.NODE:
        try:
            asked = json.loads(text)
        except (ValueError, RecursionError):
            return {}
        declared = asked.get("dependencies") if isinstance(asked, dict) else None
        if not isinstance(declared, dict):
            return {}
        return {
            name: spec if isinstance(spec, str) else json.dumps(spec, separators=(",", ":"))
            for name, spec in declared.items()
        }
    out: "Dict[str, str]" = {}
    for line in _CONTINUATION.sub(" ", text).split("\n"):
        found = _pinned_requirement(line.strip())
        if found is not None:
            out[found[0]] = found[1]
    return out


def environments(
    overlays: "Dict[str, Document]",
    active: "Sequence[str]",
    trees: "Dict[str, OverlayTree]",
    os: str,
    removed: "Sequence[str]" = (),
) -> Environments:
    """The environments ``active`` install into on ``os``: a shared or named one holds every overlay
    that chose it, in composition order, and ``isolated`` is one per overlay. Two members asking for
    one package at different versions conflict, and neither is activated. ``removed`` names the
    overlays whose environments are reported in ``left``.

    A name with no manifest in ``overlays`` is skipped. Raises ``partial-lock`` naming a requirements
    file only some of whose requirements are hashed.
    """
    empty = OverlayTree()

    def tree(identity: str) -> OverlayTree:
        return trees.get(identity, empty)

    def key(runtime: Runtime, environment: str, identity: str) -> "Tuple[Runtime, str, str]":
        return (runtime, environment, identity if environment == "isolated" else "")

    groups: "Dict[Tuple[Runtime, str, str], List[Tuple[str, Dependency]]]" = {}
    for identity in active:
        manifest = overlays.get(identity)
        if manifest is None:
            continue
        for dependency in dependencies_of(manifest, tree(identity), os):
            group = key(dependency.runtime, dependency.environment, identity)
            groups.setdefault(group, []).append((identity, dependency))
    out = Environments()
    blocked: "Set[str]" = set()
    for (runtime, name, _owner), members in groups.items():
        asked: "Dict[str, List[Tuple[str, List[str]]]]" = {}
        for identity, dependency in members:
            for package, version in _pins(runtime, tree(identity).text(dependency.file)).items():
                versions = asked.setdefault(package, [])
                for existing, askers in versions:
                    if existing == version:
                        askers.append(identity)
                        break
                else:
                    versions.append((version, [identity]))
        for package in sorted(asked, key=lambda n: n.encode("utf-8", "surrogatepass")):
            versions = asked[package]
            if len(versions) > 1:
                involved = [
                    identity
                    for identity, _dependency in members
                    if any(identity in askers for _version, askers in versions)
                ]
                blocked.update(involved)
                out.conflicts.append(PackageConflict(runtime, name, package, involved))
    remaining = set()
    for group, members in groups.items():
        kept = [(identity, dependency) for identity, dependency in members if identity not in blocked]
        if not kept:
            continue
        remaining.add(group)
        if group[0] is Runtime.NODE:
            # npm installs one lock file; several overlays' locks cannot be combined.
            locked = len(kept) == 1 and kept[0][1].lock is not None
        else:
            locked = all(dependency.lock is not None for _identity, dependency in kept)
        out.environments.append(
            PackageEnvironment(
                group[0],
                group[1],
                [identity for identity, _dependency in kept],
                [(identity, dependency.file) for identity, dependency in kept],
                locked,
            )
        )
    for identity in removed:
        manifest = overlays.get(identity)
        if manifest is None:
            continue
        for dependency in dependencies_of(manifest, tree(identity), os):
            group = key(dependency.runtime, dependency.environment, identity)
            out.left.append(
                LeftEnvironment(
                    identity,
                    dependency.runtime,
                    dependency.environment,
                    LeftState.REBUILT if group in remaining else LeftState.DELETED,
                )
            )
    out.blocked = [identity for identity in active if identity in blocked]
    return out
