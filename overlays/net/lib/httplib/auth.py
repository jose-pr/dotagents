from __future__ import annotations

from typing import Protocol


class AuthProvider(Protocol):
    def ensure(self, session, url: str) -> None: ...
