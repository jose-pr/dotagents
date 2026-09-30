"""``.gitignore`` / ``.ignore`` rules for a directory tree, as git reads them
-- what an installed overlay keeps as its own (setup output, caches, local
state) when ``overlays add`` / ``sync`` make the rest of it match upstream.

``.ignore`` is the ripgrep / fd convention, same syntax; in one directory it
is read after ``.gitignore``, so its rules win (ripgrep's precedence).

Pure stdlib. Supported: ignore files in any directory (their patterns are
relative to that directory; a deeper file's rules come later and win), blank
lines and ``#`` comments, ``\\#`` / ``\\!`` escapes, ``!`` negation, a
leading or middle ``/`` anchoring the pattern to its directory, a trailing
``/`` matching directories only, ``*``, ``?``, ``[...]`` and ``**``. As in
git, a file under an ignored directory stays ignored whatever a later ``!``
says, and ``.git/info/exclude`` / ``core.excludesFile`` are not read.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

#: The ignore files read in each directory, in order: a later one's rules win.
IGNORE_FILES = (".gitignore", ".ignore")


def _translate(pattern: str) -> str:
    """A gitignore glob (without anchoring) as a regex for a ``/``-joined path."""
    out, i, n = [], 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern[i:i + 2] == "**":
                i += 2
                if i < n and pattern[i] == "/":  # "**/": zero or more directories
                    out.append("(?:.*/)?")
                    i += 1
                else:  # a trailing "**": everything below
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = pattern.find("]", i + 1)
            if j < 0:
                out.append(re.escape(c))
            else:
                body = pattern[i + 1:j]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[%s]" % body.replace("\\", "\\\\"))
                i = j
        elif c == "\\" and i + 1 < n:
            i += 1
            out.append(re.escape(pattern[i]))
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


class _Rule:
    __slots__ = ("base", "regex", "negate", "dir_only")

    def __init__(self, base: str, regex: "re.Pattern[str]", negate: bool, dir_only: bool) -> None:
        self.base = base          # the .gitignore's directory, "" or "a/b"
        self.regex = regex
        self.negate = negate
        self.dir_only = dir_only

    def matches(self, rel: str, is_dir: bool) -> bool:
        if self.dir_only and not is_dir:
            return False
        if self.base:
            if not rel.startswith(self.base + "/"):
                return False
            rel = rel[len(self.base) + 1:]
        return bool(self.regex.fullmatch(rel))


def parse(text: str, base: str = "") -> "List[_Rule]":
    """The rules of one ``.gitignore`` whose directory is ``base``."""
    rules: "List[_Rule]" = []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        # Trailing spaces are ignored unless escaped.
        stripped = line.rstrip(" ")
        if stripped.endswith("\\") and len(line) > len(stripped):
            stripped += " "
        line = stripped
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        if negate:
            line = line[1:]
        if line.startswith(("\\#", "\\!")):
            line = line[1:]
        dir_only = line.endswith("/")
        line = line.rstrip("/")
        if not line:
            continue
        anchored = "/" in line
        line = line.lstrip("/")
        body = _translate(line)
        regex = re.compile(body if anchored else "(?:.*/)?" + body)
        rules.append(_Rule(base, regex, negate, dir_only))
    return rules


class IgnoreRules:
    """Every ``.gitignore`` / ``.ignore`` under a tree, applied as git applies
    them."""

    def __init__(self, rules: "Iterable[_Rule]" = ()) -> None:
        self.rules = list(rules)

    @classmethod
    def from_tree(cls, root: Path, skip_parts: "Iterable[str]" = ()) -> "IgnoreRules":
        """The rules of every ignore file under ``root`` (shallower files
        first, so a deeper one's rules win; in one directory ``.gitignore``
        then ``.ignore``), not descending into ``skip_parts`` directories
        (``.git``, caches)."""
        skip = frozenset(skip_parts)
        found: "List[Tuple[int, str, int, Path]]" = []
        for dirpath, dirnames, filenames in os.walk(str(root)):
            dirnames[:] = sorted(d for d in dirnames if d not in skip)
            rel = Path(dirpath).relative_to(root).as_posix()
            rel = "" if rel == "." else rel
            for order, name in enumerate(IGNORE_FILES):
                if name in filenames:
                    found.append((rel.count("/") + (1 if rel else 0), rel, order, Path(dirpath) / name))
        rules: "List[_Rule]" = []
        for _depth, base, _order, path in sorted(found):
            try:
                rules.extend(parse(path.read_text(encoding="utf-8", errors="replace"), base))
            except OSError:
                continue
        return cls(rules)

    def _decide(self, rel: str, is_dir: bool) -> Optional[bool]:
        verdict = None
        for rule in self.rules:
            if rule.matches(rel, is_dir):
                verdict = not rule.negate
        return verdict

    def ignored(self, rel: str, is_dir: bool = False) -> bool:
        """Is ``rel`` (``/``-separated, relative to the root) ignored? A path
        under an ignored directory is, whatever a later ``!`` says."""
        parts = rel.strip("/").split("/")
        for depth in range(1, len(parts)):
            if self._decide("/".join(parts[:depth]), True):
                return True
        return bool(self._decide("/".join(parts), is_dir))
