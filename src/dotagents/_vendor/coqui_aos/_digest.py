"""The tree digest an overlay is pinned by: one line per member, ``path NUL sha256:<hex> LF``,
sorted by path bytes, and the SHA-256 of those lines."""

from __future__ import annotations

import enum
import hashlib
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from pathlib import PurePath
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ._errors import Class, Error
from ._fs import FileKind, FileSystem
from ._names import EXCLUDED_COMPONENTS, valid_relative_path
from ._settings import Settings
from ._tree import OverlayTree

__all__ = ["Digest", "EntryKind", "TreeDigest", "TreeEntry", "digest_entries", "read_tree", "tree_digest"]

_FORM = re.compile(r"sha256:[0-9A-Fa-f]{64}")
_EXCLUDED = frozenset(name.encode("ascii") for name in EXCLUDED_COMPONENTS)


class Digest(str):
    """A SHA-256 digest written ``sha256:<64 lowercase hexadecimal digits>``.

    It is a ``str`` and compares equal to its text. ``Digest.parse`` reads either case of
    hexadecimal and keeps lowercase, so two digests that differ only in case are equal.
    """

    __slots__ = ()

    @classmethod
    def of(cls, data: bytes) -> "Digest":
        """The digest of ``data``."""
        return cls("sha256:" + hashlib.sha256(data).hexdigest())

    @classmethod
    def parse(cls, text: str) -> "Digest":
        """``text`` as a digest; ``Class.INVALID_DIGEST`` unless it is ``sha256:`` and 64 digits."""
        if not isinstance(text, str) or not _FORM.fullmatch(text):
            raise Error(Class.INVALID_DIGEST, "a digest is sha256: and 64 hexadecimal digits")
        return cls("sha256:" + text[7:].lower())

    @property
    def bytes(self) -> bytes:
        """The 32 bytes of the digest."""
        return bytes.fromhex(self[7:])


class EntryKind(str, enum.Enum):
    """What an entry of a tree is."""

    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    SPECIAL = "special"


@dataclass(frozen=True)
class TreeEntry:
    """One entry of a tree handed to ``digest_entries``: a file with its bytes, or a directory,
    a symbolic link or a special file (a device, a pipe), which have none."""

    kind: EntryKind
    data: bytes = b""

    @classmethod
    def file(cls, data: bytes) -> "TreeEntry":
        return cls(EntryKind.FILE, bytes(data))

    @classmethod
    def directory(cls) -> "TreeEntry":
        return cls(EntryKind.DIRECTORY)

    @classmethod
    def symlink(cls) -> "TreeEntry":
        return cls(EntryKind.SYMLINK)

    @classmethod
    def special(cls) -> "TreeEntry":
        return cls(EntryKind.SPECIAL)


@dataclass(frozen=True)
class TreeDigest:
    """The digest of a tree and the digest of each member, by path."""

    digest: Digest
    files: "Mapping[str, Digest]" = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", MappingProxyType(dict(self.files)))

    def lines(self) -> "List[bytes]":
        """The lines that were hashed, in order."""
        return [
            path.encode("utf-8") + b"\0" + digest.encode("ascii") + b"\n"
            for path, digest in self.files.items()
        ]


def _shown(components: "Sequence[bytes]") -> str:
    return "/".join(c.decode("utf-8", "replace") for c in components)


def _excluded(components: "Sequence[bytes]", directory: bool) -> bool:
    return any(c in _EXCLUDED for c in components) or (
        not directory and bool(components) and components[-1].endswith(b".pyc")
    )


def _member_path(components: "Sequence[bytes]") -> str:
    try:
        names = [c.decode("utf-8") for c in components]
    except UnicodeDecodeError:
        raise Error(Class.NON_UTF8_NAME, _shown(components)) from None
    path = "/".join(names)
    if not valid_relative_path(path):
        raise Error(Class.INVALID_NAME, _shown(components))
    return path


def _fold(path: str) -> str:
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in path)


def _finish(found: "Dict[str, Digest]") -> TreeDigest:
    folded: "Dict[str, str]" = {}
    for path in found:
        other = folded.setdefault(_fold(path), path)
        if other != path:
            raise Error(Class.CASE_COLLISION, path)
    files = {path: found[path] for path in sorted(found)}
    tree = TreeDigest(Digest.of(b""), files)
    return TreeDigest(Digest.of(b"".join(tree.lines())), files)


def digest_entries(entries: "Iterable[Tuple[Sequence[bytes], TreeEntry]]") -> TreeDigest:
    """The digest of abstract entries, each a path as its components (raw name bytes) and a
    ``TreeEntry``; order does not matter.

    An excluded name, and what is inside an excluded directory, is skipped. A symbolic link is
    ``Class.SYMLINK``, a special file ``Class.SPECIAL_FILE``, a name that is not UTF-8
    ``Class.NON_UTF8_NAME``, a path that is no relative path ``Class.INVALID_NAME``, two paths
    equal under ASCII case ``Class.CASE_COLLISION`` and one path twice ``Class.DUPLICATE_MEMBER``.
    """
    found: "Dict[str, Digest]" = {}
    for components, entry in entries:
        directory = entry.kind is EntryKind.DIRECTORY
        if directory or _excluded(components, directory):
            continue
        if entry.kind is EntryKind.SYMLINK:
            raise Error(Class.SYMLINK, _shown(components))
        if entry.kind is not EntryKind.FILE:
            raise Error(Class.SPECIAL_FILE, _shown(components))
        path = _member_path(components)
        if path in found:
            raise Error(Class.DUPLICATE_MEMBER, path)
        found[path] = Digest.of(entry.data)
    return _finish(found)


def _name_bytes(name: str) -> bytes:
    """A listed name's bytes: its UTF-8, or the bytes an operating system's undecodable names
    carry, or one invalid byte for a name that is neither."""
    try:
        return name.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        return b"\xff"


def _io(error: BaseException, subject: str = "") -> Error:
    return Error(Class.IO, subject or "the filesystem")


def _walk(
    files: FileSystem, root: PurePath, settings: Settings, keep: bool
) -> "Tuple[TreeDigest, Dict[str, bytes]]":
    try:
        info = files.metadata(root)
    except OSError as error:
        raise _io(error) from None
    if info.kind is not FileKind.DIRECTORY:
        raise Error(Class.IO, "the root is not a directory (a link is never followed)")
    found: "Dict[str, Digest]" = {}
    contents: "Dict[str, bytes]" = {}
    entries = 0
    total = 0

    def listing(path: PurePath, depth: int) -> "List[Tuple[bytes, str]]":
        if not settings.allows("tree_depth", depth):
            raise Error(Class.TOO_LARGE, "directories nested deeper than %s" % settings.tree_depth)
        try:
            names = files.list(path)
        except OSError as error:
            raise _io(error) from None
        named = sorted((_name_bytes(name), name) for name in names)
        return named

    # An explicit stack of (directory, components, remaining names, depth) keeps depth-first order
    # without recursion, so a lifted ``tree_depth`` cannot exhaust the interpreter's stack.
    stack = [
        (root, (), iter(listing(root, 1)), 1)
    ]  # type: List[Tuple[PurePath, Tuple[bytes, ...], object, int]]
    while stack:
        path, prefix, names, depth = stack[-1]
        step = next(names, None)  # type: ignore[call-overload]
        if step is None:
            stack.pop()
            continue
        raw, name = step
        if raw in (b"", b".", b"..") or any(b in raw for b in (b"/", b"\\", b"\0")):
            raise Error(Class.INVALID_NAME, _shown([raw]))
        components = prefix + (raw,)
        child = path / name
        try:
            info = files.metadata(child)
        except OSError as error:
            raise _io(error) from None
        directory = info.kind is FileKind.DIRECTORY
        if _excluded(components, directory):
            continue
        if info.kind is FileKind.SYMLINK:
            raise Error(Class.SYMLINK, _shown(components))
        too_many = Error(Class.TOO_LARGE, "more than %s files and directories" % settings.tree_files)
        if directory:
            entries += 1
            if not settings.allows("tree_files", entries):
                raise too_many
            stack.append((child, components, iter(listing(child, depth + 1)), depth + 1))
            continue
        if info.kind is not FileKind.FILE:
            raise Error(Class.SPECIAL_FILE, _shown(components))
        member = _member_path(components)
        entries += 1
        if not settings.allows("tree_files", entries):
            raise too_many
        most = settings.file_bytes
        try:
            data = files.read(child, (1 << 62) if most is None else most + 1)
        except OSError as error:
            raise _io(error, member) from None
        if not settings.allows("file_bytes", len(data)):
            raise Error(Class.TOO_LARGE, "%s is larger than %s bytes" % (member, settings.file_bytes))
        total += len(data)
        if not settings.allows("tree_bytes", total):
            raise Error(Class.TOO_LARGE, "more than %s bytes" % settings.tree_bytes)
        found[member] = Digest.of(data)
        if keep:
            contents[member] = data
    return _finish(found), contents


def tree_digest(files: FileSystem, root: PurePath, settings: "Optional[Settings]" = None) -> TreeDigest:
    """The digest of the overlay at ``root``, read through ``files`` and bounded by the
    ``tree_*`` and ``file_bytes`` settings (``Class.TOO_LARGE``).

    Every entry is checked with ``files.metadata`` before it is read or entered; ``root`` itself
    must be a directory, not a link. A listed name that is empty, ``.``, ``..`` or holds ``/``,
    ``\\`` or NUL is ``Class.INVALID_NAME``; ``Class.IO`` is a path ``files`` could not serve.
    """
    return _walk(files, root, settings or Settings(), False)[0]


def read_tree(
    files: FileSystem, root: PurePath, settings: "Optional[Settings]" = None
) -> "Tuple[OverlayTree, TreeDigest]":
    """Every member's bytes and the digest of one walk: what was digested is what was read."""
    digest, contents = _walk(files, root, settings or Settings(), True)
    return OverlayTree(digest.files, contents), digest
