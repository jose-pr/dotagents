"""Catalogs: what a store reads from a catalog file and lists across several.

Nothing here fetches anything: a catalog's bytes and the source it came from are passed in.
"""

from __future__ import annotations

import dataclasses
import enum
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ._data import Document
from ._document import Format, read_document, read_text
from ._errors import Class, Error
from ._names import normalize_name, valid_overlay_name, valid_relative_path, valid_tool_name
from ._settings import Settings
from ._source import Source, SourceKind
from ._validate import about_field_valid, catalog_field_valid

__all__ = [
    "Catalog",
    "CatalogEntry",
    "CatalogForm",
    "CatalogSection",
    "EntryRefusal",
    "ListedEntry",
    "Listing",
    "catalog_format",
    "list_catalogs",
    "read_catalog",
    "read_catalog_document",
]

_DIGEST = re.compile(r"sha256:[0-9a-fA-F]{64}")
_REF = re.compile("[^-/:\\\\@#\x00-\x20\x7f-\x9f  ‎‏‪-‮⁦-⁩]" "[^/:\\\\@#\x00-\x20\x7f-\x9f  ‎‏‪-‮⁦-⁩]*")
_DRIVE = re.compile(r"[A-Za-z]:")
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]+:")
_ENTRY_KEYS = frozenset(("source", "ref", "pin", "description", "about"))


class CatalogForm(str, enum.Enum):
    """``minimal`` when every entry is a bare source string, else ``full``."""

    MINIMAL = "minimal"
    FULL = "full"

    @classmethod
    def named(cls, word: str) -> "Optional[CatalogForm]":
        """The form a word stands for, or ``None``."""
        try:
            return cls(word)
        except ValueError:
            return None

    def __str__(self) -> str:
        return self.value


class EntryRefusal(str, enum.Enum):
    """Why a store does not list an entry."""

    INVALID_NAME = "invalid-name"
    DUPLICATE_NAME = "duplicate-name"
    INVALID_ENTRY = "invalid-entry"
    INVALID_SOURCE = "invalid-source"
    INVALID_PIN = "invalid-pin"
    REF_NOT_ALLOWED = "ref-not-allowed"
    LEAVES_REPOSITORY = "leaves-repository"
    LOCAL_SOURCE = "local-source"
    SOURCE_CREDENTIALS = "source-credentials"

    @classmethod
    def named(cls, word: str) -> "Optional[EntryRefusal]":
        """The refusal a word stands for, or ``None``."""
        try:
            return cls(word)
        except ValueError:
            return None

    def __str__(self) -> str:
        return self.value


@dataclasses.dataclass(frozen=True)
class CatalogEntry:
    """One listed overlay or skill: its source (absolute where the catalog's own source allows),
    an optional pin (``sha256:<hex>``), a description and the valid fields of ``about``.

    ``description`` is a stranger's text: do not render it as markup.
    """

    source: Source
    pin: Optional[str] = None
    description: Optional[str] = None
    about: Optional[Document] = None


@dataclasses.dataclass(frozen=True)
class CatalogSection:
    """The listed entries by name, and the ``(name, reason)`` of those refused, in name order."""

    entries: Dict[str, CatalogEntry] = dataclasses.field(default_factory=dict)
    refused: List[Tuple[str, EntryRefusal]] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class Catalog:
    """What a store reads from one catalog; ``ignored`` are JSON pointers to the fields left out."""

    form: CatalogForm
    name: Optional[str]
    description: Optional[str]
    homepage: Optional[str]
    overlays: CatalogSection
    skills: Optional[CatalogSection]
    ignored: List[str]


@dataclasses.dataclass(frozen=True)
class ListedEntry:
    """One row of a listing; ``shown`` is ``<label>/<name>`` when another catalog offers the same identity."""

    catalog: str
    name: str
    identity: str
    shown: str


@dataclasses.dataclass(frozen=True)
class Listing:
    """Overlay entries across catalogs, and the catalog labels offering each colliding identity."""

    entries: List[ListedEntry] = dataclasses.field(default_factory=list)
    collisions: Dict[str, List[str]] = dataclasses.field(default_factory=dict)

    def choose(self, requested: str) -> ListedEntry:
        """The entry ``requested`` picks: ``<label>/<name>``, or a name one catalog alone offers.

        Raises ``Error`` with ``NOT_FOUND`` or ``AMBIGUOUS``.
        """
        label, slash, name = requested.rpartition("/")
        if not valid_overlay_name(name) or (slash and not valid_overlay_name(label)):
            raise Error(Class.NOT_FOUND)
        found = [
            entry
            for entry in self.entries
            if entry.identity == normalize_name(name)
            and (not slash or normalize_name(entry.catalog) == normalize_name(label))
        ]
        if not found:
            raise Error(Class.NOT_FOUND)
        if len(found) > 1:
            raise Error(Class.AMBIGUOUS)
        return found[0]


def catalog_format(name: str) -> "Optional[str]":
    """The format a catalog's file name selects (``toml``, ``json``, ``yaml``), or ``None`` for the AOS document rule."""
    last = name.replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in last:
        return None
    extension = "".join(c.lower() if "A" <= c <= "Z" else c for c in last.rsplit(".", 1)[1])
    return {"toml": "toml", "json": "json", "yaml": "yaml", "yml": "yaml"}.get(extension)


def read_catalog_document(data: bytes, name: str, settings: "Optional[Settings]" = None) -> Document:
    """A catalog's data model value, bounded by ``Settings.catalog_bytes``.

    A known extension of ``name`` names the format; any other name is read as an AOS document.
    """
    settings = settings or Settings()
    bounded = settings.replace(manifest_bytes=settings.catalog_bytes)
    kind = catalog_format(name)
    if kind is None:
        return read_document(data, bounded)
    if not bounded.allows("manifest_bytes", len(data)):
        raise Error(Class.TOO_LARGE, "a catalog")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return read_text(Format(kind), data, bounded)


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _absolute_directory(location: str) -> bool:
    return location.startswith(("/", "\\")) or bool(_DRIVE.match(location))


def _normpath(path: str) -> str:
    rooted = path.startswith("/")
    parts: "List[str]" = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            elif not rooted:
                parts.append("..")
        else:
            parts.append(part)
    joined = "/".join(parts)
    if rooted:
        return "/" + joined
    return joined or "."


def _join(*parts: str) -> str:
    out = ""
    for part in parts:
        if not part:
            continue
        if part.startswith("/"):
            out = part
        elif not out or out.endswith("/"):
            out += part
        else:
            out += "/" + part
    return _normpath(out or ".")


def _dirname(path: str) -> str:
    head = path[: path.rfind("/") + 1]
    if head and any(c != "/" for c in head):
        return head.rstrip("/")
    return head


def _remove_dots(path: str) -> str:
    out: "List[str]" = []
    segments = path.split("/")
    last = len(segments) - 1
    for index, segment in enumerate(segments):
        if segment == ".":
            if index == last:
                out.append("")
        elif segment == "..":
            if len(out) > 1:
                out.pop()
            if index == last:
                out.append("")
        else:
            out.append(segment)
    joined = "/".join(out)
    return joined if joined.startswith("/") else "/" + joined


def _url_join(base: str, reference: str) -> str:
    if not reference:
        return base
    if _SCHEME.match(reference):
        return reference
    if "://" not in base:
        return reference
    scheme, rest = base.split("://", 1)
    cut = next((i for i, c in enumerate(rest) if c in "/?#"), len(rest))
    authority, tail = rest[:cut], rest[cut:]
    if reference.startswith("//"):
        return "%s://%s" % (scheme, reference[2:])
    tail = tail.split("#", 1)[0]
    base_path, _, base_query = tail.partition("?")
    cut = next((i for i, c in enumerate(reference) if c in "?#"), None)
    ref_path, ref_suffix = (reference, "") if cut is None else (reference[:cut], reference[cut:])
    if not ref_path:
        path = base_path
        if ref_suffix.startswith("#") and base_query:
            path += "?" + base_query
        return "%s://%s%s%s" % (scheme, authority, path, ref_suffix)
    if ref_path.startswith("/"):
        path = _remove_dots(ref_path)
    else:
        head = base_path[: base_path.rfind("/") + 1]
        if not head and authority:
            head = "/"
        path = _remove_dots(head + ref_path)
    return "%s://%s%s%s" % (scheme, authority, path, ref_suffix)


def _resolve_entry_source(entry: Source, origin: "Optional[Source]") -> Source:
    location = entry.location
    relative = (
        entry.kind == SourceKind.DIRECTORY
        and not _absolute_directory(location)
        and not _SCHEME.match(location)
    )
    local = not relative and entry.is_local()
    if origin is None or origin.kind == SourceKind.DIRECTORY:
        if relative and origin is not None:
            folder = _dirname(origin.location.replace("\\", "/"))
            return dataclasses.replace(entry, location=_join(folder, location))
        return entry
    if local:
        raise _Refused(EntryRefusal.LOCAL_SOURCE)
    if not relative:
        if entry.kind != SourceKind.GIT and not entry.is_web_url():
            raise _Refused(EntryRefusal.INVALID_SOURCE)
        if entry.holds_credentials():
            raise _Refused(EntryRefusal.SOURCE_CREDENTIALS)
        return entry
    if origin.kind == SourceKind.GIT:
        folder = _dirname(origin.path or "")
        path = _join(folder, location, entry.path or "")
        if path == ".." or path.startswith("../"):
            raise _Refused(EntryRefusal.LEAVES_REPOSITORY)
        if path != "." and not valid_relative_path(path):
            raise _Refused(EntryRefusal.INVALID_SOURCE)
        inherited = dataclasses.replace(origin, path=None if path == "." else path)
    else:
        inherited = dataclasses.replace(
            entry, kind=SourceKind.URL, location=_url_join(origin.location, location), ref=None
        )
    if inherited.holds_credentials():
        raise _Refused(EntryRefusal.SOURCE_CREDENTIALS)
    return inherited


class _Refused(Exception):
    """An entry a store does not list; never leaves this module."""

    def __init__(self, why: EntryRefusal) -> None:
        super().__init__(why)
        self.why = why


def _store_about(about: Any, at: str, ignored: "List[str]") -> "Optional[Document]":
    if not isinstance(about, dict):
        ignored.append(at)
        return None
    shown: Document = {}
    for field, value in about.items():
        if about_field_valid(field, value):
            shown[field] = value
        else:
            ignored.append(at + "/" + _escape(field))
    return shown or None


def _entry(value: Any, at: str, origin: "Optional[Source]", ignored: "List[str]") -> CatalogEntry:
    if isinstance(value, str):
        fields: Dict[str, Any] = {"source": value}
    elif isinstance(value, dict):
        if set(value) - _ENTRY_KEYS:
            raise _Refused(EntryRefusal.INVALID_ENTRY)
        fields = value
    else:
        raise _Refused(EntryRefusal.INVALID_ENTRY)
    text = fields.get("source")
    if not isinstance(text, str):
        raise _Refused(EntryRefusal.INVALID_ENTRY)
    try:
        parsed = Source.parse(text)
    except Error:
        raise _Refused(EntryRefusal.INVALID_SOURCE) from None
    source = _resolve_entry_source(parsed, origin)
    if "ref" in fields:
        ref = fields["ref"]
        if not isinstance(ref, str) or not _REF.fullmatch(ref):
            raise _Refused(EntryRefusal.INVALID_ENTRY)
        if parsed.kind != SourceKind.GIT or parsed.ref is not None:
            raise _Refused(EntryRefusal.REF_NOT_ALLOWED)
        source = dataclasses.replace(source, ref=ref)
    pin = description = about = None
    if "pin" in fields:
        pin = fields["pin"]
        if not isinstance(pin, str) or not _DIGEST.fullmatch(pin):
            raise _Refused(EntryRefusal.INVALID_PIN)
    if "description" in fields:
        if isinstance(fields["description"], str):
            description = fields["description"]
        else:
            ignored.append(at + "/description")
    if "about" in fields:
        about = _store_about(fields["about"], at + "/about", ignored)
    return CatalogEntry(source, pin, description, about)


def _section(
    table: Dict[str, Any],
    prefix: str,
    name_ok: Any,
    fold: Any,
    origin: "Optional[Source]",
    ignored: "List[str]",
) -> CatalogSection:
    holders: Dict[str, int] = {}
    for key in table:
        if name_ok(key):
            holders[fold(key)] = holders.get(fold(key), 0) + 1
    out = CatalogSection()
    for key in sorted(table):
        if not name_ok(key):
            out.refused.append((key, EntryRefusal.INVALID_NAME))
        elif holders[fold(key)] > 1:
            out.refused.append((key, EntryRefusal.DUPLICATE_NAME))
        else:
            try:
                out.entries[key] = _entry(table[key], prefix + "/" + _escape(key), origin, ignored)
            except _Refused as refusal:
                out.refused.append((key, refusal.why))
    return out


def read_catalog(
    data: bytes, name: str, base: "Optional[str]" = None, settings: "Optional[Settings]" = None
) -> Catalog:
    """What a store reads from one catalog: its form, its own fields, the entries it lists with
    sources made absolute, the entries it refuses and the fields it ignores.

    ``name`` is the catalog's file name, whose extension picks the format (any other is an AOS
    document); ``base`` is the source string the file came from, against which relative sources
    resolve (a directory, a Git repository or a URL). A catalog with neither an ``overlays`` nor
    a ``skills`` table is ``NOT_A_CATALOG``. Nothing is fetched.
    """
    document = read_catalog_document(data, name, settings)
    overlays = document.get("overlays")
    skills = document.get("skills")
    overlays = overlays if isinstance(overlays, dict) else None
    skills = skills if isinstance(skills, dict) else None
    if overlays is None and skills is None:
        raise Error(Class.NOT_A_CATALOG)
    origin: "Optional[Source]" = None
    if base:
        try:
            origin = Source.parse(base)
        except Error:
            raise Error(Class.INVALID_SOURCE, "the catalog's base") from None
    ignored: "List[str]" = []
    if overlays is None and "overlays" in document:
        ignored.append("/overlays")
    listed = list((overlays or {}).values()) + list((skills or {}).values())
    form = CatalogForm.MINIMAL if all(isinstance(v, str) for v in listed) else CatalogForm.FULL
    own: "Dict[str, Optional[str]]" = {}
    for field in ("name", "description", "homepage"):
        if field in document:
            if catalog_field_valid(field, document[field]):
                own[field] = document[field]
            else:
                ignored.append("/" + field)
    overlay_section = _section(
        overlays or {}, "/overlays", valid_overlay_name, normalize_name, origin, ignored
    )
    skill_section = None
    if skills is not None:
        skill_section = _section(
            skills,
            "/skills",
            lambda n: valid_tool_name(n) and valid_relative_path(n),
            str.lower,
            origin,
            ignored,
        )
    elif "skills" in document:
        ignored.append("/skills")
    ignored.sort()
    return Catalog(
        form,
        own.get("name"),
        own.get("description"),
        own.get("homepage"),
        overlay_section,
        skill_section,
        ignored,
    )


def list_catalogs(catalogs: "Sequence[Tuple[str, Catalog]]") -> Listing:
    """A store's listing of several catalogs, each ``(label, catalog)``.

    An identity two catalogs offer is shown once per catalog, qualified ``<label>/<name>``;
    entries are never merged. A label that is no overlay name, or two that normalize alike, is
    ``INVALID_LABEL``.
    """
    seen = set()
    for label, _catalog in catalogs:
        if not valid_overlay_name(label) or normalize_name(label) in seen:
            raise Error(Class.INVALID_LABEL)
        seen.add(normalize_name(label))
    offered: "Dict[str, List[Tuple[str, str]]]" = {}
    for label, catalog in catalogs:
        for name in catalog.overlays.entries:
            offered.setdefault(normalize_name(name), []).append((label, name))
    listing = Listing()
    for identity in sorted(offered):
        holders = sorted(offered[identity])
        qualified = len(holders) > 1
        if qualified:
            listing.collisions[identity] = [label for label, _name in holders]
        for label, name in holders:
            shown = "%s/%s" % (label, name) if qualified else name
            listing.entries.append(ListedEntry(label, name, identity, shown))
    return listing
