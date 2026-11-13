"""Resolving ``requires``, the composition order and what the active overlays contribute together."""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple, Union

from ._data import Document
from ._defaults import context_of, rules_of, skill_entries
from ._delivery import Delivery, bundled_held, elsewhere_held
from ._env import Environment, Facts, NonDefaultSetting, apply_overlay
from ._errors import Class, Error
from ._hooks import Decision, HookEvent, HookReport, HookRun, HookScope, fire_event
from ._names import normalize_name, root_variable, state_variable
from ._tree import OverlayTree

__all__ = [
    "Composition",
    "Cycle",
    "Resolution",
    "compose",
    "composed_environment",
    "composition_order",
    "hook_environment",
    "hook_order",
    "resolve",
    "ComposedEnvironment",
]

_NO_TREE = OverlayTree()


class Cycle(Error):
    """``requires`` leads back to an overlay already being resolved. ``chain`` runs from the
    first repeat through the overlay that closes it, which is that same name again."""

    def __init__(self, chain: "Sequence[str]") -> None:
        super().__init__(Class.CYCLE, " -> ".join(chain[:32]))
        self.chain: "List[str]" = list(chain)


@dataclasses.dataclass(frozen=True)
class Resolution:
    """Overlays in the order they install (a requirement before its requester), and each
    ``(requester, name)`` whose name is not available."""

    install_order: List[str] = dataclasses.field(default_factory=list)
    missing: List[Tuple[str, str]] = dataclasses.field(default_factory=list)


def _declared(manifest: Document, field: str) -> "List[Any]":
    value = manifest.get(field)
    return value if isinstance(value, list) else []


def _strings(manifest: Document, field: str) -> "Iterator[str]":
    return (item for item in _declared(manifest, field) if isinstance(item, str))


def resolve(available: "Mapping[str, Document]", requested: "Sequence[str]") -> Resolution:
    """The install order of ``requested`` and everything they require, names normalized.

    Raises ``Cycle`` when ``requires`` loops. The walk keeps its own stack, so a chain of any
    length is followed without recursion.
    """
    out = Resolution()
    done: "Set[str]" = set()
    for name in requested:
        chain: "List[str]" = []
        position: "Dict[str, int]" = {}
        frames: "List[Tuple[str, Iterator[str]]]" = []

        def visit(name: str) -> bool:
            if name in done:
                return False
            if name in position:
                raise Cycle(chain[position[name] :] + [name])
            manifest = available.get(name)
            if manifest is None:
                out.missing.append((chain[-1] if chain else "", name))
                return False
            position[name] = len(chain)
            chain.append(name)
            frames.append((name, _strings(manifest, "requires")))
            return True

        visit(normalize_name(name))
        while frames:
            current, required = frames[-1]
            for item in required:
                if visit(normalize_name(item)):
                    break
            else:
                frames.pop()
                chain.pop()
                del position[current]
                done.add(current)
                out.install_order.append(current)
    return out


def composition_order(overlays: "Mapping[str, Document]", active: "Sequence[str]") -> "List[str]":
    """``active`` ordered by ``priority`` (an integer, 500 when absent), then ``name`` (the
    identity when absent or empty), then identity."""

    def key(identity: str) -> "Tuple[int, str, str]":
        manifest = overlays.get(identity) or {}
        priority = manifest.get("priority")
        if isinstance(priority, bool) or not isinstance(priority, int):
            priority = 500
        name = manifest.get("name")
        if not isinstance(name, str) or not name:
            name = identity
        return priority, name, identity

    return sorted(active, key=key)


@dataclasses.dataclass(frozen=True)
class Composition:
    """What the active overlays contribute together, in composition order.

    ``skills`` maps each delivered skill to the overlay that holds it and ``skill_collisions``
    lists the lowercase names more than one holder offered.
    """

    install_order: List[str] = dataclasses.field(default_factory=list)
    active: List[str] = dataclasses.field(default_factory=list)
    missing: List[Tuple[str, str]] = dataclasses.field(default_factory=list)
    root_variables: Dict[str, str] = dataclasses.field(default_factory=dict)
    routing: List[str] = dataclasses.field(default_factory=list)
    rules: List[Tuple[str, str]] = dataclasses.field(default_factory=list)
    context: List[Tuple[str, str]] = dataclasses.field(default_factory=list)
    skills: Dict[str, str] = dataclasses.field(default_factory=dict)
    skill_collisions: List[str] = dataclasses.field(default_factory=list)


def compose(
    overlays: "Mapping[str, Document]",
    requested: "Sequence[str]",
    trees: "Optional[Mapping[str, OverlayTree]]" = None,
) -> Composition:
    """Resolution, composition order and what the active overlays contribute together.

    ``trees`` gives each overlay's files by identity; an overlay without one has none. Raises
    ``Cycle``.
    """
    trees = trees or {}
    resolution = resolve(overlays, requested)
    active = composition_order(overlays, resolution.install_order)
    composition = Composition(
        install_order=resolution.install_order,
        active=active,
        missing=resolution.missing,
        root_variables={identity: root_variable(identity) for identity in active},
    )
    delivery = Delivery()
    for identity in active:
        manifest = overlays[identity]
        tree = trees.get(identity, _NO_TREE)
        composition.routing.extend(_strings(manifest, "routing"))
        composition.rules.extend((identity, path) for path in rules_of(manifest, tree))
        composition.context.extend((identity, path) for path in context_of(manifest, tree))
        for entry in skill_entries(manifest, tree):
            bundled = entry.origin == "bundled"
            held = bundled_held(identity) if bundled else elsewhere_held(entry.key, entry.pin)
            delivery.offer(entry.name, identity, bundled, held)
    composition.skills.update(delivery.delivered())
    composition.skill_collisions.extend(delivery.collisions())
    return composition


@dataclasses.dataclass
class ComposedEnvironment:
    """The environment the active overlays build together, or the refusal that stopped it.

    Exactly one of ``env`` and ``error`` is set. ``hook_reports`` and ``hook_non_defaults`` are
    what the ``pre-env`` hooks reported (see ``HookReport`` and ``FiredEvent.non_defaults``),
    kept when a refusal follows.
    """

    env: "Optional[Environment]" = None
    error: "Optional[Error]" = None
    hook_reports: "List[HookReport]" = dataclasses.field(default_factory=list)
    hook_non_defaults: "List[Tuple[str, str]]" = dataclasses.field(default_factory=list)


def composed_environment(
    active: "Sequence[Tuple[Document, Facts]]",
    base: Environment,
    pre_env: "Mapping[str, HookRun]",
    non_defaults: "List[NonDefaultSetting]",
) -> ComposedEnvironment:
    """The environment of the ``(manifest, facts)`` of each active overlay, in composition order.

    ``base`` is what the consumer starts from. Each overlay's root (and state) variable is set
    first, then the ``pre-env`` hooks' output, every overlay's in order and keyed by identity
    (the caller ran them), then each overlay's own declarations. A guard hook that refuses is
    ``HOOK_REFUSED`` naming the overlay. What a declaration reports is appended to
    ``non_defaults``.
    """
    env = base.copy()
    for _manifest, facts in active:
        env.set(root_variable(facts.identity), facts.root)
        if facts.state is not None:
            env.set(state_variable(facts.identity), facts.state)
    composed = ComposedEnvironment()
    for _manifest, facts in active:
        run = pre_env.get(facts.identity)
        if run is None:
            continue
        fired = fire_event(
            HookEvent.PRE_ENV,
            [dataclasses.replace(run, source=facts.identity)],
            None,
            facts.settings,
            env.windows,
        )
        composed.hook_reports.extend(fired.reports)
        composed.hook_non_defaults.extend(fired.non_defaults)
        if fired.decision is Decision.REFUSE:
            composed.error = Error(Class.HOOK_REFUSED, facts.identity)
            return composed
        for name, value in fired.env.items():
            if value is None:
                env.unset(name)
            else:
                env.set(name, value)
    for manifest, facts in active:
        try:
            apply_overlay(env, manifest, facts, non_defaults)
        except Error as error:
            composed.error = error
            return composed
    composed.env = env
    return composed


def hook_order(
    overlays: "Mapping[str, Document]",
    requested: "Sequence[str]",
    has: "Iterable[str]",
    scopes: "Iterable[Union[str, HookScope]]" = (),
) -> "List[Tuple[HookScope, str]]":
    """The order the hooks of one event run in, as ``(scope, source)`` pairs.

    Every active overlay that has a hook (``has``) in composition order, then the user's own, then
    the project's, whichever order ``scopes`` (the scopes that have one) names them in; a scope's
    own hook has the source ``""``. Raises ``Cycle``.
    """
    present = set(has)
    wanted = {HookScope(scope) for scope in scopes}
    order = [
        (HookScope.OVERLAY, identity)
        for identity in composition_order(overlays, resolve(overlays, requested).install_order)
        if identity in present
    ]
    return order + [(scope, "") for scope in (HookScope.USER, HookScope.PROJECT) if scope in wanted]


def _replay(owned: Environment, writes: "List[Tuple[str, str, bool, Any]]") -> None:
    for name, value, secret, join in writes:
        if join is None:
            owned.set(name, value, secret)
            continue
        separator, prepend, seen, append = join
        kept = [part for part in (owned.get(name) or "").split(separator) if part and part not in seen]
        owned.set(name, separator.join(prepend + kept + append))


def hook_environment(
    event: HookEvent,
    active: "Sequence[Tuple[Document, Facts]]",
    base: Environment,
    pre_env: "Mapping[str, HookRun]",
    identity: "Optional[str]" = None,
) -> Environment:
    """The environment a hook of ``event`` runs with.

    ``active`` and ``base`` are as ``composed_environment`` takes them. The hook of overlay
    ``identity`` gets what that overlay's own processes get: the base, every overlay's root (and
    state) variable, that overlay's own ``pre-env`` output and declarations with the values they had
    in the session's order of application, and ``AOS_OVERLAY_ROOT``, ``AOS_OVERLAY_NAME``,
    ``AOS_OVERLAY_STATE`` (when it has one) and ``AOS_HOOK_EVENT``; nothing another overlay's
    declarations or ``pre-env`` output set. A ``pre-env`` hook gets the base, the root and the
    state variables only. With ``identity`` ``None``, a user's or a project's own hook, it gets the
    base and ``AOS_HOOK_EVENT``. Raises what ``apply_overlay`` raises.
    """

    def with_roots() -> Environment:
        env = base.copy()
        for _manifest, facts in active:
            env.set(root_variable(facts.identity), facts.root)
            if facts.state is not None:
                env.set(state_variable(facts.identity), facts.state)
        return env

    if identity is None:
        own = base.copy()
        own.set("AOS_HOOK_EVENT", event.value)
        return own
    chosen = next(facts for _manifest, facts in active if facts.identity == identity)
    own = with_roots()
    if event is not HookEvent.PRE_ENV:
        session = with_roots()
        owned = {facts.identity: with_roots() for _manifest, facts in active}
        for _manifest, facts in active:
            run = pre_env.get(facts.identity)
            if run is None:
                continue
            fired = fire_event(
                HookEvent.PRE_ENV,
                [dataclasses.replace(run, source=facts.identity)],
                None,
                facts.settings,
                session.windows,
            )
            for name, value in fired.env.items():
                for target in (session, owned[facts.identity]):
                    if value is None:
                        target.unset(name)
                    else:
                        target.set(name, value)
        for manifest, facts in active:
            session._log = []
            try:
                apply_overlay(session, manifest, facts, [])
                _replay(owned[facts.identity], session._log)
            finally:
                session._log = None
        own = owned[identity]
    own.set("AOS_OVERLAY_ROOT", chosen.root)
    own.set("AOS_OVERLAY_NAME", normalize_name(chosen.identity))
    if chosen.state is not None:
        own.set("AOS_OVERLAY_STATE", chosen.state)
    own.set("AOS_HOOK_EVENT", event.value)
    return own
