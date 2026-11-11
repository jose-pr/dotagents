"""Which overlay's skill of a name is the one delivered, and which names collide."""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

__all__: "List[str]" = []

#: What a delivered skill is held as: bundled in an overlay, or from elsewhere (key, pin).
Held = Tuple[str, ...]


def bundled_held(identity: str) -> Held:
    return ("bundled", identity)


def elsewhere_held(key: "Optional[str]", pin: "Optional[str]") -> Held:
    return ("elsewhere", key or "", "" if pin is None else "pin:" + pin)


class Delivery:
    """Skill names compare by Unicode lowercase. A name offered twice with different holders is
    a collision; the bundled skill of an overlay later in composition order replaces the earlier
    holder, and any other later offer is shadowed."""

    def __init__(self) -> None:
        self._winners: "Dict[str, Tuple[str, str, Held]]" = {}
        self._collisions: "Set[str]" = set()

    def offer(self, name: str, identity: str, bundled: bool, held: Held) -> bool:
        """Offer ``name`` from ``identity``; ``False`` when an earlier holder keeps it."""
        folded = name.lower()
        earlier = self._winners.get(folded)
        if earlier is not None and earlier[2] != held:
            self._collisions.add(folded)
            if not bundled:
                return False
        self._winners[folded] = (name, identity, held)
        return True

    def delivered(self) -> "Dict[str, str]":
        """Each delivered name, with the overlay that holds it."""
        return {name: identity for name, identity, _held in self._winners.values()}

    def collisions(self) -> "List[str]":
        """The lowercase names offered by more than one holder, sorted."""
        return sorted(self._collisions)

    def holds(self, name: str, identity: str) -> bool:
        """Whether ``identity`` holds the delivered skill called ``name``."""
        winner = self._winners.get(name.lower())
        return winner is not None and winner[1] == identity
