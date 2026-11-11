"""The filesystem a reader reads through, which the caller supplies."""

from __future__ import annotations

import enum
import os
import stat
from pathlib import PurePath
from typing import List, NamedTuple, Protocol, runtime_checkable

__all__ = ["FileKind", "FileSystem", "Metadata", "PathFS"]


class FileKind(enum.Enum):
    """What a path is, without following a link."""

    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    OTHER = "other"


class Metadata(NamedTuple):
    """What a path is and how many bytes it holds (0 for anything but a regular file)."""

    kind: FileKind
    size: int = 0


@runtime_checkable
class FileSystem(Protocol):
    """Reads files, lists directories and says what a path is.

    A path is as the caller built it from the root it handed the reader. Each method raises
    ``OSError`` for a path it cannot serve; the reader reports that as ``Class.IO``.
    """

    def metadata(self, path: PurePath) -> Metadata:
        """What ``path`` is, **without following a link**: a link is ``FileKind.SYMLINK``."""
        ...

    def read(self, path: PurePath, limit: int) -> bytes:
        """The first ``limit`` bytes of the file at ``path``, fewer when it is shorter."""
        ...

    def list(self, path: PurePath) -> "List[str]":
        """The names in the directory at ``path``, in any order."""
        ...


class PathFS:
    """A ``FileSystem`` over ``pathlib`` and the operating system.

    ``read`` and ``list`` open through links, as the operating system does: a link swapped in
    between ``metadata`` and ``read`` is followed, so read only trees no one else can write. On
    Windows a reparse point (a junction, for one) is a ``SYMLINK``.
    """

    def metadata(self, path: PurePath) -> Metadata:
        info = os.lstat(path)
        mode = info.st_mode
        if stat.S_ISLNK(mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            return Metadata(FileKind.SYMLINK)
        if stat.S_ISDIR(mode):
            return Metadata(FileKind.DIRECTORY)
        if stat.S_ISREG(mode):
            return Metadata(FileKind.FILE, info.st_size)
        return Metadata(FileKind.OTHER)

    def read(self, path: PurePath, limit: int) -> bytes:
        # Chunked, because ``read(n)`` allocates ``n`` bytes up front, which a lifted limit makes huge.
        out = bytearray()
        with open(path, "rb") as handle:
            while len(out) < limit:
                chunk = handle.read(min(1 << 20, limit - len(out)))
                if not chunk:
                    break
                out += chunk
        return bytes(out)

    def list(self, path: PurePath) -> "List[str]":
        return os.listdir(path)
