"""An icon's format and size, judged from its header alone."""

from __future__ import annotations

import dataclasses
import enum
from typing import Optional, Tuple

from ._errors import Class, Error
from ._settings import Settings

__all__ = ["Icon", "IconFormat", "read_icon"]

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class IconFormat(str, enum.Enum):
    """An icon format a store shows."""

    PNG = "png"
    WEBP = "webp"

    @classmethod
    def named(cls, word: str) -> "Optional[IconFormat]":
        """The format a word stands for, or ``None``."""
        try:
            return cls(word)
        except ValueError:
            return None

    def __str__(self) -> str:
        return self.value


@dataclasses.dataclass(frozen=True)
class Icon:
    """What a header says: the format and the pixel dimensions."""

    format: IconFormat
    width: int
    height: int


def _png_size(data: bytes) -> "Tuple[int, int]":
    if len(data) < 24 or data[8:16] != b"\x00\x00\x00\x0dIHDR":
        raise Error(Class.ICON_UNREADABLE)
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _webp_size(data: bytes) -> "Tuple[int, int]":
    if len(data) < 20 or int.from_bytes(data[4:8], "little") + 8 > len(data):
        raise Error(Class.ICON_UNREADABLE)
    chunk = data[12:16]
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        return (
            int.from_bytes(data[26:28], "little") & 0x3FFF,
            int.from_bytes(data[28:30], "little") & 0x3FFF,
        )
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X" and len(data) >= 30:
        return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
    raise Error(Class.ICON_UNREADABLE)


def read_icon(data: bytes, name: str, settings: "Optional[Settings]" = None) -> Icon:
    """The format and dimensions an icon's header gives, or ``Error`` with the reason a store refuses it.

    The checks run in order: the extension of ``name`` (``png`` or ``webp``), the size in
    bytes, the signature against the extension, the header, the dimensions. Nothing past the
    header is decoded. The bounds are ``Settings.icon_bytes``, ``icon_width`` and ``icon_height``.
    """
    settings = settings or Settings()
    last = name.rsplit("/", 1)[-1]
    extension = (
        "".join(c.lower() if "A" <= c <= "Z" else c for c in last.rsplit(".", 1)[1]) if "." in last else ""
    )
    if extension not in ("png", "webp"):
        raise Error(Class.ICON_FORMAT)
    if not settings.allows("icon_bytes", len(data)):
        raise Error(Class.ICON_TOO_LARGE).over("icon_bytes")
    if data.startswith(_PNG_SIGNATURE):
        found = IconFormat.PNG
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        found = IconFormat.WEBP
    else:
        raise Error(Class.ICON_FORMAT)
    if found.value != extension:
        raise Error(Class.ICON_MISMATCH)
    width, height = _png_size(data) if found is IconFormat.PNG else _webp_size(data)
    if not width or not height:
        raise Error(Class.ICON_UNREADABLE)
    if not settings.allows("icon_width", width):
        raise Error(Class.ICON_DIMENSIONS).over("icon_width")
    if not settings.allows("icon_height", height):
        raise Error(Class.ICON_DIMENSIONS).over("icon_height")
    return Icon(found, width, height)
