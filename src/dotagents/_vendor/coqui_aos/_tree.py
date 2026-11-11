"""An overlay's files, as the paths it holds and the bytes of those a reader asked for."""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Optional, Union

__all__ = ["OverlayTree"]


class OverlayTree:
    """The relative paths (``/``-separated) of an overlay, and the bytes of the files read.

    A path is a member whether or not its bytes were read. ``with_text`` ignores a path the
    tree does not hold; ``with_bytes`` adds it.
    """

    def __init__(self, paths: "Iterable[str]" = (), contents: "Optional[Mapping[str, bytes]]" = None) -> None:
        self._paths: "Dict[str, None]" = dict.fromkeys(paths)
        self._bytes: "Dict[str, bytes]" = {}
        for path, data in (contents or {}).items():
            self.with_bytes(path, data)

    @classmethod
    def from_texts(cls, files: "Mapping[str, Union[str, bytes]]") -> "OverlayTree":
        """A tree whose every file's bytes are given (text is encoded as UTF-8)."""
        return cls(
            contents={p: v.encode("utf-8") if isinstance(v, str) else bytes(v) for p, v in files.items()}
        )

    def with_bytes(self, path: str, data: bytes) -> "OverlayTree":
        """Add ``path`` holding ``data``."""
        self._paths[path] = None
        self._bytes[path] = bytes(data)
        return self

    def with_text(self, path: str, text: str) -> "OverlayTree":
        """Give the bytes of ``path``, if the tree holds it."""
        if path in self._paths:
            self._bytes[path] = text.encode("utf-8")
        return self

    @property
    def paths(self) -> "List[str]":
        """Every path, in the order added."""
        return list(self._paths)

    def __contains__(self, path: object) -> bool:
        return path in self._paths

    def __len__(self) -> int:
        return len(self._paths)

    def bytes(self, path: str) -> "Optional[bytes]":
        """The bytes read of ``path``, or ``None`` when none were."""
        return self._bytes.get(path)

    def text(self, path: str) -> "Optional[str]":
        """The bytes read of ``path`` as UTF-8, or ``None`` when none were or they are not text."""
        data = self._bytes.get(path)
        if data is None:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None
