"""``OVERLAY.md`` and the front matter of a context file or ``SKILL.md``, and an overlay's manifest."""

from __future__ import annotations

import dataclasses
import enum
from typing import Dict, List, Optional

from ._data import Document
from ._document import Format, read_document, read_text, split_format_line
from ._errors import Class, Error
from ._settings import Settings
from ._tree import OverlayTree

__all__ = [
    "Case",
    "FrontMatter",
    "ManifestFile",
    "OverlayRead",
    "Split",
    "SplitCase",
    "context_file_text",
    "read_front_matter",
    "read_overlay",
    "split_front_matter",
]

_BYTE_ORDER_MARK = b"\xef\xbb\xbf"
_WHITE_SPACE = b" \t\r\n"
_FENCES = (b"---", b"...")


class _Words(str, enum.Enum):
    @classmethod
    def named(cls, word: str):  # type: ignore[no-untyped-def]
        """The member a vector's word stands for, or ``None``."""
        try:
            return cls(word)
        except ValueError:
            return None

    def __str__(self) -> str:
        return self.value


class SplitCase(_Words):
    """What ``split_front_matter`` found."""

    NONE = "none"
    UNTERMINATED = "unterminated"
    TOO_LARGE = "too-large"
    EMPTY = "empty"
    PRESENT = "present"


class Case(_Words):
    """What ``read_front_matter`` found, so a viewer can show the text."""

    NONE = "none"
    MALFORMED = "malformed"
    UNTERMINATED = "unterminated"
    TOO_LARGE = "too-large"
    EMPTY = "empty"
    UNKNOWN_FORMAT = "unknown-format"
    DOES_NOT_PARSE = "does-not-parse"
    NOT_A_MAPPING = "not-a-mapping"
    MAPPING = "mapping"


class ManifestFile(_Words):
    """The file an overlay's manifest came from."""

    OVERLAY_MD = "OVERLAY.md"
    OVERLAY_CFG = "overlay.cfg"


@dataclasses.dataclass(frozen=True)
class Split:
    """A document cut at its front matter, unchanged.

    Offsets count bytes from the first byte of the file, lines from 1. ``metadata`` and the two
    ``metadata_*`` fields are set only for ``PRESENT`` and ``EMPTY`` blocks; the ``body_*`` fields
    are ``None`` for ``TOO_LARGE``.
    """

    case: SplitCase
    metadata: Optional[bytes] = None
    metadata_offset: Optional[int] = None
    metadata_line: Optional[int] = None
    body_offset: Optional[int] = None
    body_line: Optional[int] = None

    def body(self, data: bytes) -> bytes:
        """The bytes after the front matter of ``data``, the document this split was made from."""
        return data[self.body_offset :] if self.body_offset is not None else b""


@dataclasses.dataclass(frozen=True)
class FrontMatter:
    """A split and the reading of its metadata.

    ``error`` is what a gate refuses with, its ``line`` counted from the file's first line;
    ``case`` is what a viewer shows. ``data`` is the manifest where there is one (an empty block
    or none is the empty mapping).
    """

    case: Case
    data: Optional[Document] = None
    error: Optional[Error] = None
    format: Optional[Format] = None
    metadata: Optional[bytes] = None
    metadata_offset: Optional[int] = None
    metadata_line: Optional[int] = None
    body_offset: Optional[int] = None
    body_line: Optional[int] = None


@dataclasses.dataclass(frozen=True)
class OverlayRead:
    """What a gate reads from an overlay's manifest file. ``issues`` are JSON pointers."""

    manifest_file: Optional[ManifestFile]
    manifest: Document
    context: List[str]
    context_ignored: List[str]
    issues: List[str]
    harness_context: Optional[Dict[str, List[str]]] = None


def _fence(line: bytes, ended: bool) -> "Optional[bytes]":
    """``---`` or ``...`` when ``line`` (without its LF) is one; ``ended`` says an LF followed,
    since a CR is part of a line end only before a LF."""
    if ended and line.endswith(b"\r"):
        line = line[:-1]
    line = line.rstrip(b" \t")
    return line if line in _FENCES else None


def _may_open(head: bytes) -> bool:
    """Whether a first line cut off at the bound could still be an opening fence."""
    tail = head[3:]
    if tail.endswith(b"\r"):
        tail = tail[:-1]
    return b"---".startswith(head[:3]) and not tail.strip(b" \t")


def split_front_matter(data: bytes, settings: "Optional[Settings]" = None) -> Split:
    """Split ``data`` on bytes, without parsing or changing it.

    The head, from after a byte-order mark through the closing fence's line end, is bounded by
    ``manifest_bytes``, and nothing past it is read.
    """
    settings = settings or Settings()
    start = 3 if data.startswith(_BYTE_ORDER_MARK) else 0
    limit = len(data) if settings.manifest_bytes is None else start + settings.manifest_bytes
    plain = Split(SplitCase.NONE, body_offset=start, body_line=1)
    end = data.find(b"\n", start, limit)
    if end < 0 and len(data) > limit:
        if _may_open(data[start:limit]):
            return Split(SplitCase.TOO_LARGE)
        return plain
    first = data[start:] if end < 0 else data[start:end]
    if _fence(first, end >= 0) != b"---":
        return plain
    if end < 0:
        return dataclasses.replace(plain, case=SplitCase.UNTERMINATED)
    metadata_offset = at = end + 1
    while True:
        lf = data.find(b"\n", at, limit)
        if lf < 0:
            if len(data) > limit:
                return Split(SplitCase.TOO_LARGE)
            if _fence(data[at:], False) is None:
                return dataclasses.replace(plain, case=SplitCase.UNTERMINATED)
            body_offset = len(data)
        elif _fence(data[at:lf], True) is None:
            at = lf + 1
            continue
        else:
            body_offset = lf + 1
        metadata = data[metadata_offset:at]
        return Split(
            SplitCase.PRESENT if metadata.strip(_WHITE_SPACE) else SplitCase.EMPTY,
            metadata,
            metadata_offset,
            2,
            body_offset,
            1 + data.count(b"\n", start, body_offset),
        )


def _almost_a_fence(data: bytes) -> bool:
    """A document that is no block but that a person, or a laxer reader, would take for one: a
    fence after white space or a second byte-order mark, or a format line before one."""
    lead = data
    while lead[:1] in (b" ", b"\t", b"\r", b"\n") or lead.startswith(_BYTE_ORDER_MARK):
        lead = lead[3:] if lead.startswith(_BYTE_ORDER_MARK) else lead[1:]
    if lead.startswith((b"---", b"...")):
        return True
    start = 3 if data.startswith(_BYTE_ORDER_MARK) else 0
    if data.startswith(b"#!", start):
        end = data.find(b"\n", start)
        if end >= 0:
            second = data.find(b"\n", end + 1)
            line = data[end + 1 :] if second < 0 else data[end + 1 : second]
            return _fence(line, second >= 0) is not None
    return False


def read_front_matter(data: bytes, settings: "Optional[Settings]" = None) -> FrontMatter:
    """The split, then the metadata read: YAML unless its first line names another format."""
    settings = settings or Settings()
    found = split_front_matter(data, settings)
    shown = {
        "metadata": found.metadata,
        "metadata_offset": found.metadata_offset,
        "metadata_line": found.metadata_line,
        "body_offset": found.body_offset,
        "body_line": found.body_line,
    }
    if found.case is SplitCase.NONE and _almost_a_fence(data):
        return FrontMatter(Case.MALFORMED, error=Error(Class.MALFORMED_FENCE, "", 1), **shown)
    if found.case in (SplitCase.NONE, SplitCase.EMPTY):
        return FrontMatter(Case(found.case.value), data={}, **shown)
    if found.case is SplitCase.UNTERMINATED:
        return FrontMatter(Case.UNTERMINATED, error=Error(Class.UNTERMINATED_FRONT_MATTER, "", 1), **shown)
    if found.case is SplitCase.TOO_LARGE:
        return FrontMatter(Case.TOO_LARGE, error=Error(Class.TOO_LARGE, "", 1), **shown)
    metadata = found.metadata or b""
    first_line = found.metadata_line or 2
    named = metadata.startswith(b"#!")
    kind: "Optional[Format]" = None
    payload = b""
    try:
        word, payload = split_format_line(metadata, "yaml")
        kind = Format.named(word)
        if kind is None:
            raise Error(Class.UNSUPPORTED_FORMAT)
        parsed = read_text(kind, payload, settings)
    except Error as error:
        if error.class_ in (Class.UNSUPPORTED_FORMAT, Class.NO_FORMAT_NAMED):
            case = Case.UNKNOWN_FORMAT
        elif error.class_ is Class.NOT_A_MAPPING:
            case = Case.NOT_A_MAPPING
        else:
            case = Case.DOES_NOT_PARSE
        line = first_line + (1 if named else 0) + error.line - 1 if error.line else first_line
        return FrontMatter(case, error=Error(error.class_, error.subject, line), format=kind, **shown)
    case = Case.EMPTY if not payload.strip(_WHITE_SPACE) else Case.MAPPING
    return FrontMatter(case, data=parsed, format=kind, **shown)


def _manifest_bytes(tree: OverlayTree, name: str) -> bytes:
    data = tree.bytes(name)
    if data is None:
        raise Error(Class.IO, name)
    return data


def read_overlay(tree: OverlayTree, settings: "Optional[Settings]" = None) -> OverlayRead:
    """The manifest of an overlay from the bytes of ``OVERLAY.md`` or ``overlay.cfg`` in ``tree``,
    its context entries, the context files it ignores and the problems of the manifest.

    Refused: two manifests, a manifest that does not read, and a body of ``OVERLAY.md`` that is
    context but is past ``context_bytes`` or not UTF-8.
    """
    from ._defaults import context_ignored, context_of, harness_context_of
    from ._validate import validate

    settings = settings or Settings()
    present = [name for name in ("OVERLAY.md", "overlay.cfg") if name in tree]
    if len(present) > 1:
        raise Error(Class.TWO_MANIFESTS, "OVERLAY.md and overlay.cfg")
    manifest: Document = {}
    body = b""
    body_line = 1
    if present == ["overlay.cfg"]:
        manifest = read_document(_manifest_bytes(tree, "overlay.cfg"), settings)
    elif present:
        data = _manifest_bytes(tree, "OVERLAY.md")
        found = read_front_matter(data, settings)
        if found.error is not None:
            raise Error(found.error.class_, "OVERLAY.md", found.error.line)
        manifest = found.data or {}
        body = data[found.body_offset or 0 :]
        body_line = found.body_line or 1
    context = context_of(manifest, tree)
    harness = manifest.get("harness") if isinstance(manifest.get("harness"), dict) else {}
    named = list(context)
    for harness_id, hints in harness.items():
        if isinstance(hints, dict) and "context" in hints:
            named += harness_context_of(manifest, harness_id, tree)
    if "OVERLAY.md" in named and body.strip(_WHITE_SPACE):
        if settings.context_bytes is not None and len(body) > settings.context_bytes:
            raise Error(Class.TOO_LARGE, "the body of OVERLAY.md", body_line)
        try:
            body.decode("utf-8")
        except UnicodeDecodeError:
            raise Error(Class.INVALID_UTF8, "the body of OVERLAY.md", body_line) from None
    harness_context = None
    if isinstance(manifest.get("harness"), dict):
        harness_context = {
            harness_id: harness_context_of(manifest, harness_id, tree)
            for harness_id, hints in manifest["harness"].items()
            if isinstance(hints, dict) and "context" in hints
        }
    return OverlayRead(
        ManifestFile(present[0]) if present else None,
        manifest,
        context,
        context_ignored(manifest, tree),
        [issue.at for issue in validate(manifest, tree)],
        harness_context,
    )


def context_file_text(data: bytes, settings: "Optional[Settings]" = None) -> bytes:
    """A context file's bytes as context: a closed front matter block is dropped unread, the
    rest kept. A head past ``manifest_bytes`` is ``too-large``."""
    found = split_front_matter(data, settings)
    if found.case is SplitCase.TOO_LARGE:
        raise Error(Class.TOO_LARGE, "front matter", 1)
    return found.body(data)
