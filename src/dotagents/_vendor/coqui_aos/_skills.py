"""Skills from elsewhere: what a consumer shows before installing the skills of the active
overlays and reports after, and the check of a skill that was fetched.

The library never fetches: the caller supplies a ``SkillSource`` to ``fetch_planned``, or
fetches on its own and calls ``read_fetched_skill``.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import (
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    Union,
    runtime_checkable,
)

from ._catalog import Catalog, list_catalogs
from ._compose import Cycle, composition_order, resolve
from ._data import Document
from ._defaults import skill_entries
from ._delivery import Delivery, bundled_held, elsewhere_held
from ._digest import Digest, TreeEntry, digest_entries
from ._errors import Class, Error
from ._front_matter import Case, read_front_matter, split_front_matter
from ._document import Format
from ._names import normalize_name
from ._settings import Settings
from ._source import Source, host_is_private, source_host
from ._tree import OverlayTree

__all__ = [
    "Fetch",
    "FetchedPlanned",
    "FetchedSkill",
    "PlannedOrigin",
    "PlannedSkill",
    "SkillPlan",
    "SkillSource",
    "fetch_planned",
    "read_fetched_skill",
    "resolve_skills",
]

_INERT_KEYS = ("name", "description", "license", "compatibility", "metadata")


class PlannedOrigin(str, enum.Enum):
    """Where a planned skill comes from."""

    BUNDLED = "bundled"
    SOURCE = "source"
    CATALOG = "catalog"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"

    @classmethod
    def named(cls, word: str) -> "Optional[PlannedOrigin]":
        """The origin a word stands for, or ``None``."""
        try:
            return cls(word)
        except ValueError:
            return None

    def __str__(self) -> str:
        return self.value


@dataclasses.dataclass(frozen=True)
class PlannedSkill:
    """One skill of an active overlay and where it comes from.

    ``source`` is as shown (no user information, no query); ``catalog`` the label of the one
    catalog that offered it; ``catalogs`` the labels of those that did when ``origin`` is
    ``AMBIGUOUS``.
    """

    name: str
    overlay: str
    origin: PlannedOrigin
    source: Optional[str] = None
    pin: Optional[str] = None
    catalog: Optional[str] = None
    catalogs: Optional[List[str]] = None


@dataclasses.dataclass(frozen=True)
class Fetch:
    """A skill a consumer has to fetch: from where, and the tree digest it must have."""

    name: str
    overlay: str
    from_: Source
    pin: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class SkillPlan:
    """What a consumer shows before installing the skills of the active overlays.

    The ``(name, overlay)`` lists are ``missing`` (no catalog offers the name),
    ``unpinned``, ``not_fetched`` (a consumer that does not fetch), ``name_mismatch`` (a bundled
    ``SKILL.md`` naming another skill) and ``shadowing`` (an earlier holder keeps the name).
    ``ambiguous`` adds the labels of the catalogs that offered it; ``non_defaults`` are
    ``(id, name, overlay)`` for ``insecure-transport`` and ``private-source``. ``delivered``
    maps each name to its overlay, ``collisions`` lists the lowercase names offered by more than
    one holder, and ``to_fetch`` is what the caller has to fetch.
    """

    skills: List[PlannedSkill] = dataclasses.field(default_factory=list)
    missing: List[Tuple[str, str]] = dataclasses.field(default_factory=list)
    ambiguous: List[Tuple[str, str, List[str]]] = dataclasses.field(default_factory=list)
    unpinned: List[Tuple[str, str]] = dataclasses.field(default_factory=list)
    non_defaults: List[Tuple[str, str, str]] = dataclasses.field(default_factory=list)
    not_fetched: List[Tuple[str, str]] = dataclasses.field(default_factory=list)
    name_mismatch: List[Tuple[str, str]] = dataclasses.field(default_factory=list)
    shadowing: List[Tuple[str, str]] = dataclasses.field(default_factory=list)
    delivered: Dict[str, str] = dataclasses.field(default_factory=dict)
    collisions: List[str] = dataclasses.field(default_factory=list)
    to_fetch: List[Fetch] = dataclasses.field(default_factory=list)


def _bundled_name(data: bytes, settings: Settings) -> "Optional[str]":
    found = read_front_matter(data, settings)
    value = (found.data or {}).get("name")
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _ranked(offers: "List[Tuple[str, object]]", rank: "List[str]") -> "List[Tuple[str, object]]":
    """The first catalog in the user's order that offers the name; without one, all of them."""
    for label in rank:
        for offer in offers:
            if normalize_name(offer[0]) == label:
                return [offer]
    return offers


def resolve_skills(
    overlays: "Mapping[str, Document]",
    requested: "Sequence[str]",
    trees: "Optional[Mapping[str, OverlayTree]]" = None,
    catalogs: "Sequence[Tuple[str, Catalog]]" = (),
    order: "Sequence[str]" = (),
    fetches: bool = True,
    settings: "Optional[Settings]" = None,
) -> SkillPlan:
    """What a consumer shows before installing the skills of the active overlays.

    Every skill of the active overlays comes with where it is from; a name no catalog offers is
    ``missing``, one more than one offers is ``ambiguous`` unless ``order`` (the catalog labels
    in the user's order) picks one. A consumer that does not fetch (``fetches`` false) delivers
    only bundled skills and reports the rest as ``not_fetched``. Raises ``Error`` for a cycle in
    ``requires`` or an invalid catalog label.
    """
    settings = settings or Settings()
    trees = trees or {}
    try:
        resolution = resolve(overlays, requested)
    except Cycle as cycle:
        raise Error(Class.CYCLE, cycle.subject) from None
    active = composition_order(overlays, resolution.install_order)
    list_catalogs(catalogs)
    rank = [normalize_name(label) for label in order]
    empty = OverlayTree()
    plan = SkillPlan()
    delivery = Delivery()
    for identity in active:
        tree = trees.get(identity, empty)
        for entry in skill_entries(overlays[identity], tree):
            origin = PlannedOrigin(entry.origin)
            item = {
                "name": entry.name,
                "overlay": identity,
                "origin": origin,
                "source": entry.source,
                "pin": entry.pin,
                "catalog": None,
                "catalogs": None,
            }
            key, source = entry.key, entry.from_
            if origin is PlannedOrigin.CATALOG:
                wanted = entry.name.lower()
                offers: "List[Tuple[str, object]]" = []
                for label, catalog in catalogs:
                    if catalog.skills is None:
                        continue
                    for listed, offer in catalog.skills.entries.items():
                        if listed.lower() == wanted:
                            offers.append((label, offer))
                offers = _ranked(offers, rank)
                if not offers:
                    item["origin"] = PlannedOrigin.MISSING
                    plan.missing.append((entry.name, identity))
                elif len(offers) == 1:
                    label, offer = offers[0]
                    item["catalog"] = label
                    item["source"] = offer.source.shown()
                    key, source, item["pin"] = offer.source.key(), offer.source, offer.pin
                else:
                    labels = sorted(label for label, _offer in offers)
                    item["origin"] = PlannedOrigin.AMBIGUOUS
                    item["catalogs"] = labels
                    plan.ambiguous.append((entry.name, identity, labels))
            planned = PlannedSkill(**item)  # type: ignore[arg-type]
            plan.skills.append(planned)
            if planned.origin in (PlannedOrigin.MISSING, PlannedOrigin.AMBIGUOUS):
                continue
            bundled = planned.origin is PlannedOrigin.BUNDLED
            if bundled:
                text = tree.bytes("skills/%s/SKILL.md" % entry.name)
                if text is not None:
                    named = _bundled_name(text, settings)
                    if named is not None and named != entry.name:
                        plan.name_mismatch.append((entry.name, identity))
                        continue
            else:
                if not fetches:
                    plan.not_fetched.append((entry.name, identity))
                    continue
                if planned.pin is None:
                    plan.unpinned.append((entry.name, identity))
                location = (key or "").split("#", 1)[0]
                lowered = location.lower()
                if lowered.startswith("git+"):
                    lowered = lowered[4:]
                if lowered.startswith(("http://", "git://")):
                    plan.non_defaults.append(("insecure-transport", entry.name, identity))
                if host_is_private(source_host(location)):
                    plan.non_defaults.append(("private-source", entry.name, identity))
            held = bundled_held(identity) if bundled else elsewhere_held(key, planned.pin)
            if not delivery.offer(entry.name, identity, bundled, held):
                plan.shadowing.append((entry.name, identity))
            elif not bundled and source is not None:
                plan.to_fetch.append(Fetch(entry.name, identity, source, planned.pin))
    plan.delivered.update(delivery.delivered())
    plan.collisions.extend(delivery.collisions())
    # A skill later displaced by a bundled one of the same name is not fetched.
    plan.to_fetch[:] = [fetch for fetch in plan.to_fetch if delivery.holds(fetch.name, fetch.overlay)]
    return plan


@dataclasses.dataclass(frozen=True)
class FetchedSkill:
    """A fetched skill that passed its checks: its tree digest, and the front matter keys beyond
    the inert ones (``name``, ``description``, ``license``, ``compatibility``, ``metadata``),
    sorted, which a consumer reports before delivery. ``files`` are the paths the digest covers:
    a caller writes those and nothing else, since the tree may also carry entries under an excluded
    name (``node_modules``, ``__pycache__``, ``*.pyc``, ``.git``), which the digest leaves out and
    Python, Node.js and Git load code from."""

    digest: Digest
    reported: List[str]
    files: "Tuple[str, ...]" = ()


def _entries(
    files: "Iterable[Tuple[str, bytes]]", settings: Settings
) -> "List[Tuple[Tuple[bytes, ...], TreeEntry]]":
    entries = []
    total = 0
    for path, data in files:
        if not settings.allows("tree_files", len(entries) + 1):
            raise Error(Class.TOO_LARGE, "too many files")
        if not settings.allows("file_bytes", len(data)):
            raise Error(Class.TOO_LARGE, "a file past the limit")
        total += len(data)
        if not settings.allows("tree_bytes", total):
            raise Error(Class.TOO_LARGE, "more bytes than the limit")
        try:
            parts = tuple(part.encode("utf-8") for part in path.split("/"))
        except UnicodeEncodeError:
            raise Error(Class.NON_UTF8_NAME) from None
        entries.append((parts, TreeEntry.file(data)))
    return entries


def read_fetched_skill(
    name: str,
    files: "Union[Mapping[str, bytes], Iterable[Tuple[str, bytes]]]",
    pin: "Optional[str]" = None,
    settings: "Optional[Settings]" = None,
) -> FetchedSkill:
    """Checks a skill fetched under ``name`` before it is delivered; ``files`` are its relative
    paths and bytes.

    The tree digest is computed within the ``tree_*`` and ``file_bytes`` limits and compared
    with ``pin`` first, so nothing unverified is parsed. Then ``SKILL.md`` must have YAML front
    matter (no format line) whose ``name`` is ``name``. Errors: the digest's classes,
    ``PIN_MISMATCH``, ``NOT_A_SKILL``, ``SKILL_UNREADABLE`` and ``SKILL_NAME_MISMATCH``.
    """
    settings = settings or Settings()
    pairs = list(files.items() if isinstance(files, Mapping) else files)
    digest_tree = digest_entries(_entries(pairs, settings))
    digest = digest_tree.digest
    if pin is not None:
        try:
            pinned = Digest.parse(pin)
        except Error:
            raise Error(Class.PIN_MISMATCH, name) from None
        if pinned != digest:
            raise Error(Class.PIN_MISMATCH, name)
    skill = next((data for path, data in pairs if path == "SKILL.md"), None)
    if skill is None:
        raise Error(Class.NOT_A_SKILL, name)
    found = read_front_matter(skill, settings)
    if found.error is not None:
        refused = Error(Class.SKILL_UNREADABLE, found.error.class_.value)
        refused.line = found.error.line
        raise refused
    split = split_front_matter(skill, settings)
    if (
        found.case is not Case.MAPPING
        or found.format is not Format.YAML
        or (split.metadata is not None and split.metadata.startswith(b"#!"))
    ):
        raise Error(Class.SKILL_UNREADABLE, "front matter in YAML is required")
    data = found.data or {}
    if data.get("name") != name:
        raise Error(Class.SKILL_NAME_MISMATCH, name)
    return FetchedSkill(
        digest,
        sorted(key for key in data if key not in _INERT_KEYS),
        tuple(sorted(digest_tree.files, key=lambda p: p.encode("utf-8"))),
    )


@runtime_checkable
class SkillSource(Protocol):
    """Fetches the files of a skill from a source; the caller's, since the library holds no network."""

    def fetch(self, source: Source) -> "Sequence[Tuple[str, bytes]]":
        """The relative paths and bytes under ``source``. Raise ``Error`` (or ``OSError``) when it cannot."""
        ...


@dataclasses.dataclass(frozen=True)
class FetchedPlanned:
    """The outcome for one planned skill: the checked skill, or the ``Error`` for it alone."""

    name: str
    overlay: str
    outcome: "Union[FetchedSkill, Error]"


def fetch_planned(
    plan: SkillPlan, fetcher: SkillSource, settings: "Optional[Settings]" = None
) -> "List[FetchedPlanned]":
    """Fetches each of ``plan.to_fetch`` once through ``fetcher`` and checks it, pin first.

    A failure, the fetcher's or a check's, is that skill's outcome and stops no other.
    """
    settings = settings or Settings()
    out = []
    for fetch in plan.to_fetch:
        outcome: "Union[FetchedSkill, Error]"
        try:
            outcome = read_fetched_skill(fetch.name, fetcher.fetch(fetch.from_), fetch.pin, settings)
        except Error as error:
            outcome = error
        except OSError as error:
            outcome = Error(Class.IO, type(error).__name__)
        out.append(FetchedPlanned(fetch.name, fetch.overlay, outcome))
    return out
