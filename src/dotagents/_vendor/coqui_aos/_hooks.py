"""Hooks: what runs at a session's events, what it is told and what of its output applies.

Nothing here starts a process. The caller runs a hook, describes what the process did as a
``HookRun`` and hands it back; this module checks it, combines the runs of one event to the
tightest decision and reports what could not be applied.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ._data import Document
from ._errors import Class, Error
from ._settings import Settings
from ._tree import OverlayTree

__all__ = [
    "Decision",
    "FiredEvent",
    "Hook",
    "HookError",
    "HookEvent",
    "HookFacts",
    "HookFailure",
    "HookOutput",
    "HookPlan",
    "HookReport",
    "HookRun",
    "HookScope",
    "OnFailure",
    "fire_event",
    "hook_matches",
    "hook_payload",
    "hook_plan",
    "hook_result",
    "hooks_declared",
    "hooks_of",
    "read_hook_output",
]

INTERPRETERS = ("python", "node", "powershell", "bash", "sh")
_DEFAULT_TIMEOUT = 30
#: ``hook.<ext>`` in the order a consumer tries them, with the OS names each applies on.
_DEFAULT_SCRIPTS = (
    (".py", "python", None),
    (".js", "node", None),
    (".mjs", "node", None),
    (".cjs", "node", None),
    (".ps1", "powershell", ("windows",)),
    (".bash", "bash", ("unix",)),
    (".sh", "sh", ("unix",)),
)
_EXTENSION_INTERPRETERS = {
    ".py": "python",
    ".sh": "sh",
    ".bash": "bash",
    ".ps1": "powershell",
    ".js": "node",
    ".mjs": "node",
    ".cjs": "node",
}
_UNIX = frozenset(("macos", "linux", "freebsd", "openbsd", "netbsd"))
#: Controls other than tab, line feed and carriage return, and the bidirectional overrides and
#: isolates, which change how the text around them reads.
_REFUSED_TEXT = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f‪-‮⁦-⁩]")
_VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INT_MIN, _INT_MAX = -(2**63), 2**63 - 1
_UNSET: Any = object()


class _Word(str, enum.Enum):
    """A closed set of words; a member equals the word the vectors write."""

    @classmethod
    def named(cls, word: str) -> "Optional[Any]":
        """The member a word stands for, or ``None``."""
        try:
            return cls(word)
        except ValueError:
            return None

    def __str__(self) -> str:
        return self.value


class HookEvent(_Word):
    """A moment of a session at which a hook may run."""

    PRE_ENV = "pre-env"
    SESSION_START = "session-start"
    SESSION_END = "session-end"
    PROMPT = "prompt"
    TURN_END = "turn-end"
    CD = "cd"
    PRE_COMPACT = "pre-compact"
    PRE_TOOL = "pre-tool"
    POST_TOOL = "post-tool"

    @property
    def by_default(self) -> bool:
        """Whether ``hook.<ext>`` gets this event when the manifest names none."""
        return self.value in _BY_DEFAULT

    @property
    def outputs(self) -> "Tuple[str, ...]":
        """What a hook of this event may output: ``context``, ``env``, ``block``, ``deny``, ``ask``, ``allow``, ``input``."""
        return _OUTPUTS[self.value]

    @property
    def refusal(self) -> "Optional[Decision]":
        """What a refusal is for this event; ``None`` when it cannot be refused."""
        word = _REFUSAL[self.value]
        return None if word is None else Decision(word)

    @property
    def selects(self) -> "Optional[str]":
        """What ``match`` selects: ``reason`` or ``kind``; ``None`` when it selects nothing."""
        return _SELECTS[self.value]

    @property
    def payload_keys(self) -> "Tuple[str, ...]":
        """The keys of the payload a hook of this event is told."""
        return ("event", "harness", "cwd") + _PAYLOAD[self.value]


_BY_DEFAULT = frozenset(("pre-env", "session-start", "session-end", "turn-end", "cd", "pre-compact"))
_OUTPUTS = {
    "pre-env": ("env",),
    "session-start": ("context", "env"),
    "session-end": (),
    "prompt": ("context", "block", "env"),
    "turn-end": (),
    "cd": ("env",),
    "pre-compact": ("context", "block"),
    "pre-tool": ("context", "deny", "ask", "allow", "input", "env"),
    "post-tool": ("context",),
}
_REFUSAL = {
    "pre-env": "refuse",
    "session-start": "refuse",
    "session-end": None,
    "prompt": "block",
    "turn-end": None,
    "cd": None,
    "pre-compact": "block",
    "pre-tool": "deny",
    "post-tool": None,
}
_SELECTS = {
    "pre-env": None,
    "session-start": "reason",
    "session-end": None,
    "prompt": None,
    "turn-end": None,
    "cd": None,
    "pre-compact": None,
    "pre-tool": "kind",
    "post-tool": "kind",
}
_PAYLOAD = {
    "pre-env": (),
    "session-start": ("session", "reason"),
    "session-end": ("session",),
    "prompt": ("session", "prompt"),
    "turn-end": ("session",),
    "cd": ("session", "previous"),
    "pre-compact": ("session", "trigger"),
    "pre-tool": ("session", "tool"),
    "post-tool": ("session", "tool", "status"),
}
_TOOL_KEYS = ("kind", "name", "input")


class Decision(_Word):
    """What a hook may ask of its event."""

    BLOCK = "block"
    DENY = "deny"
    ASK = "ask"
    ALLOW = "allow"
    REFUSE = "refuse"

    @property
    def rank(self) -> int:
        """How tight it is: ``allow`` 1, ``ask`` 2, every other 3."""
        if self is Decision.ALLOW:
            return 1
        return 2 if self is Decision.ASK else 3


def _rank(decision: "Optional[Decision]") -> int:
    return 0 if decision is None else decision.rank


class OnFailure(_Word):
    """What a hook's failure does."""

    REPORT = "report"
    REFUSE = "refuse"


class HookScope(_Word):
    """Whose hooks they are."""

    OVERLAY = "overlay"
    USER = "user"
    PROJECT = "project"


class HookFailure(_Word):
    """Why a run of a hook failed."""

    NOT_RUN = "not-run"
    PAYLOAD_TOO_LARGE = "payload-too-large"
    TIMEOUT = "timeout"
    TOO_LARGE = "too-large"
    EXIT = "exit"
    OUTPUT = "output"
    EVENT_TIMEOUT = "event-timeout"


class HookError(Exception):
    """A run of a hook that failed; ``failure`` says why."""

    def __init__(self, failure: HookFailure) -> None:
        super().__init__(failure)
        self.failure = failure

    def __str__(self) -> str:
        return self.failure.value


@dataclasses.dataclass(frozen=True)
class Hook:
    """A hook an overlay has."""

    event: HookEvent
    #: The script; ``None`` for a ``hook.<ext>`` none of which can run here.
    path: Optional[str]
    #: The interpreter that follows from the script's extension.
    interpreter: Optional[str]
    #: Whether the interpreter is one the consumer has.
    runs: bool
    #: The reasons or tool kinds it runs for; ``None`` for every one.
    match: Optional[Tuple[str, ...]]
    #: The variables its ``env`` output may set.
    sets: Tuple[str, ...]
    #: Seconds before it is stopped.
    timeout: int
    on_failure: OnFailure
    #: The keys of the payload it is told.
    payload: Tuple[str, ...]


def _os_matches(names: "Optional[Sequence[str]]", current: str) -> bool:
    if names is None:
        return True
    return current in names or ("unix" in names and current != "windows" and current in _UNIX)


def _members(tree: OverlayTree) -> "List[str]":
    """The paths that make a file present: digest members, with no excluded name or ``.pyc``."""
    from ._names import valid_relative_path

    return [path for path in tree.paths if valid_relative_path(path)]


def _default_hook(
    tree: OverlayTree, os: str, interpreters: "Sequence[str]"
) -> "Tuple[Optional[Tuple[str, str]], List[str]]":
    members = set(_members(tree))
    present = ["hook" + ext for ext, _i, _o in _DEFAULT_SCRIPTS if "hook" + ext in members]
    for ext, interpreter, names in _DEFAULT_SCRIPTS:
        path = "hook" + ext
        if path in present and _os_matches(names, os) and interpreter in interpreters:
            return (path, interpreter), present
    return None, present


def _strings(value: Any) -> "Optional[Tuple[str, ...]]":
    if not isinstance(value, list):
        return None
    return tuple(item for item in value if isinstance(item, str))


def _natural(value: Any) -> "Optional[int]":
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def hooks_of(
    manifest: Document,
    tree: OverlayTree,
    os: str,
    interpreters: "Iterable[str]" = INTERPRETERS,
) -> "Tuple[List[Hook], List[str]]":
    """Every hook the overlay has, in event order, and the ``hook.<ext>`` files reported as
    ``script-not-run`` because an entry needs one and none can run here.

    An entry whose script cannot run here keeps its ``path`` (``None`` for a ``hook.<ext>`` none
    of which can) so that firing it fails, and an ``on_failure = "refuse"`` guard fails closed.
    ``hooks`` is not validated here: call ``validate`` first.
    """
    known = list(interpreters)
    declared = manifest.get("hooks")
    declared = declared if isinstance(declared, dict) else None
    chosen, present = _default_hook(tree, os, known)
    defaults = manifest.get("defaults")
    default_on = not (isinstance(defaults, dict) and defaults.get("hooks") is False)
    entries: "List[Hook]" = []
    needs_default = False
    for event in HookEvent:
        name = event.value
        if declared is not None and name in declared:
            value = declared[name]
            if value is False:
                continue
            table: "Dict[str, Any]" = value if isinstance(value, dict) else {"run": value}
        elif event.by_default and default_on and present:
            table = {}
        else:
            continue
        run = table.get("run")
        if isinstance(run, str):
            path: "Optional[str]" = run
            dot = run.rfind(".")
            interpreter = _EXTENSION_INTERPRETERS.get(run[dot:]) if dot >= 0 else None
        elif chosen is not None:
            path, interpreter = chosen
        else:
            needs_default = True
            if declared is None or name not in declared:
                continue
            path = interpreter = None
        timeout = _natural(table.get("timeout"))
        entries.append(
            Hook(
                event=event,
                path=path,
                interpreter=interpreter,
                runs=interpreter is not None and interpreter in known,
                match=_strings(table.get("match")),
                sets=_strings(table.get("sets")) or (),
                timeout=_DEFAULT_TIMEOUT if timeout is None else timeout,
                on_failure=OnFailure.REFUSE if table.get("on_failure") == "refuse" else OnFailure.REPORT,
                payload=event.payload_keys,
            )
        )
    return entries, (present if needs_default else [])


def hooks_declared(manifest: Document, tree: OverlayTree) -> bool:
    """Whether the overlay declares a hook on any OS with any interpreter: ``hooks_of`` finds an
    entry or a ``hook.<ext>`` that cannot run, for some OS, were every interpreter AOS names
    present. Entries an overlay switches off (``false``, or ``defaults.hooks = false``) are not
    declared. For a consumer that runs no hooks and refuses an overlay that asks for any, whatever
    it runs on.
    """
    for os in ("windows", "linux"):
        hooks, not_run = hooks_of(manifest, tree, os, INTERPRETERS)
        if hooks or not_run:
            return True
    return False


@dataclasses.dataclass
class HookFacts:
    """What a consumer knows when it plans an overlay's hooks on one harness."""

    #: The OS AOS names it.
    os: str
    #: The identity the overlay is installed under.
    identity: str = ""
    #: The interpreters the consumer can run; ``None`` is every one AOS names.
    interpreters: Optional[Sequence[str]] = None
    scope: HookScope = HookScope.OVERLAY
    #: The overlay's tree digest, when known.
    digest: Optional[str] = None
    #: For a project, every file of the directory that holds its configuration, by path and
    #: bytes; its tree digest replaces ``digest``.
    tree: Optional[Mapping[str, bytes]] = None
    #: The one digest at which the user allowed these hooks.
    allowed: Optional[str] = None
    #: The events the consumer can fire on this harness; ``None`` is every one.
    fires: Optional[Sequence[HookEvent]] = None
    #: The events it fires for only some occurrences.
    partial: Sequence[HookEvent] = ()
    #: The consumer's bound on one run, in seconds; ``None`` is the setting's.
    timeout: Optional[int] = None


@dataclasses.dataclass(frozen=True)
class HookPlan:
    """What a consumer would do with an overlay's hooks on one harness."""

    #: The hooks, in event order, each with its timeout held to the bound.
    hooks: Tuple[Hook, ...]
    #: Whether they are on.
    on: bool
    #: Whether the overlay activates: a guard that is off would fail open, so it waits.
    activates: bool
    #: The interpreters a probe needs, sorted.
    needs: Tuple[str, ...]
    #: ``(id, subject)`` reports, sorted: ``hook-not-allowed``, ``hook-unsupported``, ``hook-partial``.
    reports: Tuple[Tuple[str, str], ...]
    #: ``(id, subject)`` non-default settings, sorted: ``script-not-run``, ``hook``.
    non_defaults: Tuple[Tuple[str, str], ...]


def _digest_of(tree: "Mapping[str, bytes]", settings: Settings) -> str:
    from ._digest import TreeEntry, digest_entries

    entries = [
        ([part.encode("utf-8") for part in path.split("/")], TreeEntry.file(bytes(data)))
        for path, data in tree.items()
    ]
    return str(digest_entries(entries).digest)


def _same_digest(a: str, b: str) -> bool:
    from ._digest import Digest

    try:
        return Digest.parse(a) == Digest.parse(b)
    except Error:
        return False


def hook_plan(
    manifest: Document,
    files: OverlayTree,
    facts: HookFacts,
    settings: Optional[Settings] = None,
) -> HookPlan:
    """What a consumer would do with an overlay's hooks, or a scope's own, on one harness.

    Raises ``Error`` with the digest's classes when ``facts.tree`` is not a tree that has a
    digest.
    """
    settings = settings or Settings()
    known = list(INTERPRETERS if facts.interpreters is None else facts.interpreters)
    hooks, not_run = hooks_of(manifest, files, facts.os, known)
    if facts.timeout is not None:
        bound = facts.timeout
    else:
        bound = settings.hook_timeout
    if bound is not None:
        hooks = [dataclasses.replace(hook, timeout=min(hook.timeout, bound)) for hook in hooks]
    digest = facts.digest
    if facts.tree is not None:
        # Every file of the directory, not only those the hooks name: a module beside a script
        # is loaded by it.
        digest = _digest_of(facts.tree, settings)
    on = facts.scope is HookScope.USER or (
        digest is not None and facts.allowed is not None and _same_digest(digest, facts.allowed)
    )
    fires = list(HookEvent) if facts.fires is None else list(facts.fires)
    non_defaults = [("script-not-run", path) for path in not_run]
    reports: "List[Tuple[str, str]]" = []
    if hooks and not on:
        reports.append(("hook-not-allowed", facts.identity))
    elif on:
        for hook in hooks:
            if hook.event not in fires:
                reports.append(("hook-unsupported", hook.event.value))
                continue
            non_defaults.append(("hook", hook.event.value))
            if hook.event in facts.partial:
                reports.append(("hook-partial", hook.event.value))
    guarded = any(hook.on_failure is OnFailure.REFUSE for hook in hooks)
    return HookPlan(
        hooks=tuple(hooks),
        on=on and bool(hooks),
        activates=on or not guarded,
        needs=tuple(sorted({hook.interpreter for hook in hooks if hook.interpreter})),
        reports=tuple(sorted(reports)),
        non_defaults=tuple(sorted(non_defaults)),
    )


def hook_matches(event: HookEvent, select: "Optional[Sequence[str]]", value: str) -> bool:
    """Whether a hook whose ``match`` is ``select`` runs for this reason or tool kind.

    A tool the consumer could not place (``other``) runs every ``pre-tool`` and ``post-tool``
    hook, so a tool of an unknown kind never slips past a guard.
    """
    if select is None:
        return True
    if event.selects == "kind" and value == "other":
        return True
    return value in select


def hook_payload(event: HookEvent, known: "Mapping[str, Any]") -> "Dict[str, Any]":
    """The payload a hook of ``event`` is told: the event's own keys and nothing else of what
    the consumer knows. A key the consumer does not know is absent, never ``None``."""
    payload: "Dict[str, Any]" = {"event": event.value}
    for key in event.payload_keys[1:]:
        if key not in known:
            continue
        value = known[key]
        if key == "tool" and isinstance(value, dict):
            value = {field: value[field] for field in _TOOL_KEYS if field in value}
        payload[key] = value
    return payload


@dataclasses.dataclass(frozen=True)
class HookOutput:
    """What a successful run asks for. ``repr`` shows which parts are present, never their text."""

    #: What it asks the event to do.
    decision: Optional[Decision] = None
    #: Why, in its own words.
    reason: Optional[str] = dataclasses.field(default=None, repr=False)
    #: Text for the agent.
    context: Optional[str] = dataclasses.field(default=None, repr=False)
    #: The call as rewritten, with ``Decision.ALLOW``: the whole object that replaces its input.
    input: Optional[Dict[str, Any]] = dataclasses.field(default=None, repr=False)
    #: Variables to set, or ``None`` to unset, in the order given.
    env: Optional[Dict[str, Optional[str]]] = dataclasses.field(default=None, repr=False)

    def __repr__(self) -> str:
        parts = [name for name in ("reason", "context", "input", "env") if getattr(self, name) is not None]
        return "HookOutput(decision=%s, has=%s)" % (self.decision, parts)


def _text_ok(value: str) -> bool:
    return _REFUSED_TEXT.search(value) is None


def _read_json_object(text: str, depth: "Optional[int]") -> "Dict[str, Any]":
    """One JSON object: no duplicate key, no ``NaN``, nothing deeper than ``depth``.

    Raises ``Error``; nesting is measured before the parser sees it, so no input recurses.
    """
    if depth is not None:
        level, quoted, escaped = 0, False, False
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                level += 1
                if level > depth:
                    raise Error(Class.TOO_DEEP).over("manifest_depth")
            elif char in "]}":
                level -= 1

    def pairs(items: "List[Tuple[str, Any]]") -> "Dict[str, Any]":
        out: "Dict[str, Any]" = {}
        for key, value in items:
            if key in out:
                raise Error(Class.DUPLICATE_KEY, "a key set twice")
            out[key] = value
        return out

    def constant(name: str) -> Any:
        raise Error(Class.PARSE_ERROR)

    def integer(digits: str) -> int:
        number = int(digits)
        if not _INT_MIN <= number <= _INT_MAX:
            raise Error(Class.INTEGER_RANGE)
        return number

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_int=integer)
    except (ValueError, RecursionError):
        raise Error(Class.PARSE_ERROR)
    if not isinstance(value, dict):
        raise Error(Class.NOT_A_MAPPING)
    return value


def read_hook_output(
    event: HookEvent,
    data: bytes,
    sets: "Sequence[str]" = (),
    settings: Optional[Settings] = None,
) -> HookOutput:
    """A successful run's standard output: what it asks for. Raises ``HookError``.

    ``pre-env`` reads as an env script's output. Every other event reads one JSON object, whose
    keys are those the event takes; a ``decision`` that is not one of the event's own is an
    ``output`` failure, so is an ``input`` that does not come with an allow, and ``env`` may name only the variables ``sets`` lists.
    """
    settings = settings or Settings()
    outputs = event.outputs
    if not settings.allows("hook_output_bytes", len(data)):
        raise HookError(HookFailure.TOO_LARGE)
    if event is HookEvent.PRE_ENV:
        from ._dotenv import read_env_script_output

        try:
            return HookOutput(env=dict(read_env_script_output(data, settings)))
        except Error:
            raise HookError(HookFailure.OUTPUT)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise HookError(HookFailure.OUTPUT)
    whole = text.strip(" \t\r\n")
    if not whole:
        return HookOutput()
    try:
        value = _read_json_object(whole, settings.manifest_depth)
    except Error:
        raise HookError(HookFailure.OUTPUT)
    decision: "Optional[Decision]" = None
    if "decision" in value:
        word = value["decision"]
        if isinstance(word, str) and word in ("block", "deny", "ask", "allow") and word in outputs:
            decision = Decision(word)
        else:
            raise HookError(HookFailure.OUTPUT)
    if any(key not in ("context", "decision", "reason", "input", "env") for key in value):
        raise HookError(HookFailure.OUTPUT)
    reason = context = None
    if "reason" in value:
        reason = value["reason"]
        if (
            not isinstance(reason, str)
            or decision is None
            or not _text_ok(reason)
            or not settings.allows("hook_reason_chars", len(reason))
        ):
            raise HookError(HookFailure.OUTPUT)
    rewritten: "Optional[Dict[str, Any]]" = None
    if "input" in value:
        rewritten = value["input"]
        if "input" not in outputs or decision is not Decision.ALLOW or not isinstance(rewritten, dict):
            raise HookError(HookFailure.OUTPUT)
    if "context" in value:
        context = value["context"]
        if not isinstance(context, str) or "context" not in outputs or not _text_ok(context):
            raise HookError(HookFailure.OUTPUT)
    env: "Optional[Dict[str, Optional[str]]]" = None
    if "env" in value:
        given = value["env"]
        if not isinstance(given, dict) or "env" not in outputs:
            raise HookError(HookFailure.OUTPUT)
        from ._names import is_reserved_variable

        env, folded = {}, set()
        for name, item in given.items():
            upper = _ascii_upper(name)
            if (
                _VARIABLE.match(name) is None
                or is_reserved_variable(name)
                or name not in sets
                or upper in folded
            ):
                raise HookError(HookFailure.OUTPUT)
            folded.add(upper)
            if item is not None and not (isinstance(item, str) and "\0" not in item):
                raise HookError(HookFailure.OUTPUT)
            env[name] = item
    return HookOutput(decision=decision, reason=reason, context=context, input=rewritten, env=env)


def _ascii_upper(name: str) -> str:
    return "".join(c.upper() if "a" <= c <= "z" else c for c in name)


@dataclasses.dataclass
class HookRun:
    """One run of a hook, as the consumer saw it. ``repr`` omits the output."""

    #: Who the hook belongs to: an overlay's identity, or a scope.
    source: str
    #: What it wrote to standard output.
    stdout: bytes = dataclasses.field(default=b"", repr=False)
    #: What its failure does; ``None`` is ``OnFailure.REPORT``.
    on_failure: Optional[OnFailure] = None
    #: Seconds it took.
    seconds: int = 0
    #: Whether it did not start.
    not_run: bool = False
    #: Bytes of the payload it was sent.
    payload_size: int = 0
    #: Whether it ran past its timeout.
    timed_out: bool = False
    #: Its exit status.
    exit: int = 0
    #: The variables its entry declares.
    sets: Tuple[str, ...] = ()
    #: Its entry's ``match``, or ``None`` for every call: on a call of kind ``other`` only a hook
    #: with none, or one naming ``other``, may allow.
    select: Optional[Tuple[str, ...]] = None

    def __repr__(self) -> str:
        return "HookRun(source=%r, exit=%d, stdout=<%d bytes>)" % (self.source, self.exit, len(self.stdout))


def hook_result(event: HookEvent, run: HookRun, settings: Optional[Settings] = None) -> HookOutput:
    """One run, checked in a fixed order: started, payload, time, size, exit status, then the
    output itself. Raises ``HookError`` with the first failure in that order."""
    settings = settings or Settings()
    if run.not_run:
        raise HookError(HookFailure.NOT_RUN)
    if not settings.allows("hook_payload_bytes", run.payload_size):
        raise HookError(HookFailure.PAYLOAD_TOO_LARGE)
    if run.timed_out:
        raise HookError(HookFailure.TIMEOUT)
    if not settings.allows("hook_output_bytes", len(run.stdout)):
        raise HookError(HookFailure.TOO_LARGE)
    if run.exit != 0:
        raise HookError(HookFailure.EXIT)
    return read_hook_output(event, run.stdout, run.sets, settings)


@dataclasses.dataclass(frozen=True)
class HookReport:
    """What a hook did that the consumer should say: ``hook-failed``, ``hook-ask-denied`` or
    ``hook-not-applied``, then the source, the event and for some the cause or what was not applied."""

    id: str
    about: Tuple[str, ...]


@dataclasses.dataclass
class FiredEvent:
    """What the hooks of one event amount to. ``repr`` shows counts, never text or values."""

    #: The sources of the hooks that were started, those that failed to start excepted.
    ran: List[str] = dataclasses.field(default_factory=list)
    #: The tightest decision any asked for: a deny, then an ask, then an allow.
    decision: Optional[Decision] = None
    #: The source of that decision.
    by: Optional[str] = None
    #: Its hook's own words; ``None`` for a refusal by failure.
    reason: Optional[str] = dataclasses.field(default=None, repr=False)
    #: ``(source, text)`` for the agent.
    context: List[Tuple[str, str]] = dataclasses.field(default_factory=list, repr=False)
    #: The variables to set, or ``None`` to unset.
    env: Dict[str, Optional[str]] = dataclasses.field(default_factory=dict, repr=False)
    reports: List[HookReport] = dataclasses.field(default_factory=list)
    #: ``(id, subject)``: ``env-sensitive``.
    non_defaults: List[Tuple[str, str]] = dataclasses.field(default_factory=list)
    #: With a ``tool_input``: ``(source, input)`` for what each hook that ran was given.
    seen: Optional[List[Tuple[str, Any]]] = dataclasses.field(default=None, repr=False)
    #: With a ``tool_input``: the input the call runs with, ``None`` for the one it was given. Set
    #: only for a combined allow.
    input: Optional[Dict[str, Any]] = dataclasses.field(default=None, repr=False)

    def __repr__(self) -> str:
        return "FiredEvent(ran=%r, decision=%s, by=%r, context=%d, env=%d, reports=%d)" % (
            self.ran,
            self.decision,
            self.by,
            len(self.context),
            len(self.env),
            len(self.reports),
        )


def fire_event(
    event: HookEvent,
    runs: "Sequence[HookRun]",
    applies: "Optional[Iterable[str]]" = None,
    settings: Optional[Settings] = None,
    windows: bool = False,
    context_used: int = 0,
    tool_input: Any = _UNSET,
    tool_kind: Optional[str] = None,
) -> FiredEvent:
    """Every hook of one event, in the order given, combined to the tightest result.

    ``applies`` are the outputs the consumer can apply on this harness (``context``, ``env``,
    ``block``, ``deny``, ``ask``, ``allow``, ``input``, ``refuse``); ``None`` is all the event takes, and refuse. A
    hook that would start after the event's own bound has passed fails as ``event-timeout``.
    ``windows`` makes a variable's name compare without case, keeping the spelling it was
    first given. ``context_used`` is what the session's context already holds, in bytes: a part
    of a hook's context that would take the total past ``settings.context_bytes`` is not added,
    whole, and is reported as ``hook-not-applied``; a later part that fits still is.
    ``tool_input`` is the call's input as the harness gave it (a ``pre-tool`` event that may be
    rewritten): ``seen`` then says what each hook was given, a hook's ``input`` replaces it for
    the hooks after, and ``input`` is what the call runs with, set only by a combined allow. An
    allow whose ``input`` the consumer cannot apply (``input`` not in ``applies``) is not applied
    at all; one that an ask beat reports its rewrite as ``hook-not-applied``. ``tool_kind`` is the
    call's kind: on ``other``, a run whose ``select`` is given and does not name ``other`` still
    runs and its deny and ask count, but its allow and input do not (``hook-not-applied``
    ``allow``).
    """
    from ._names import is_sensitive_variable

    settings = settings or Settings()
    can = set(applies) if applies is not None else set(event.outputs) | {"refuse"}
    bound = settings.hook_event_timeout
    spent = 0
    fired = FiredEvent()
    current, rewriter = tool_input, None
    rewriting = tool_input is not _UNSET
    if rewriting:
        fired.seen = []
    for run in runs:
        source = run.source
        try:
            if bound is not None and spent >= bound:
                raise HookError(HookFailure.EVENT_TIMEOUT)
            fired.ran.append(source)
            if rewriting:
                fired.seen.append((source, current))
            spent += run.seconds
            out = hook_result(event, run, settings)
        except HookError as failure:
            fired.reports.append(HookReport("hook-failed", (source, event.value, failure.failure.value)))
            refusal = event.refusal
            if run.on_failure is not OnFailure.REFUSE or refusal is None:
                continue
            out = HookOutput(decision=refusal)
        decision = out.decision
        if decision is Decision.ASK and "ask" not in can:
            fired.reports.append(HookReport("hook-ask-denied", (source, event.value)))
            decision = Decision.DENY
        if decision is not None and decision.value not in can:
            fired.reports.append(HookReport("hook-not-applied", (source, event.value, decision.value)))
            decision = None
        if (
            decision is Decision.ALLOW
            and tool_kind == "other"
            and run.select is not None
            and "other" not in run.select
        ):
            fired.reports.append(HookReport("hook-not-applied", (source, event.value, "allow")))
            decision = None
            out = dataclasses.replace(out, input=None)
        if out.input is not None and decision is Decision.ALLOW:
            if "input" in can:
                current, rewriter = out.input, source
            else:
                fired.reports.append(HookReport("hook-not-applied", (source, event.value, "input")))
                decision = None
        if _rank(decision) > _rank(fired.decision):
            fired.decision, fired.by, fired.reason = decision, source, out.reason
        if _rank(decision) == 3:
            # The prompt, compaction, call or session does not happen: nothing else applies.
            fired.context.clear()
            fired.env.clear()
            break
        if out.context:
            size = len(out.context.encode("utf-8"))
            if "context" in can and not settings.allows("context_bytes", context_used + size):
                fired.reports.append(HookReport("hook-not-applied", (source, event.value, "context")))
            elif "context" in can:
                context_used += size
                fired.context.append((source, out.context))
            else:
                fired.reports.append(HookReport("hook-not-applied", (source, event.value, "context")))
        if out.env:
            if "env" not in can:
                fired.reports.append(HookReport("hook-not-applied", (source, event.value, "env")))
                continue
            for name, value in out.env.items():
                key = name
                if windows:
                    folded = _ascii_upper(name)
                    key = next((held for held in fired.env if _ascii_upper(held) == folded), name)
                fired.env[key] = value
    fired.non_defaults = sorted(("env-sensitive", name) for name in fired.env if is_sensitive_variable(name))
    if rewriting and rewriter is not None:
        if fired.decision is Decision.ALLOW:
            fired.input = current
        elif fired.decision is Decision.ASK:
            fired.reports.append(HookReport("hook-not-applied", (rewriter, event.value, "input")))
    return fired
