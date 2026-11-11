"""Substitution in env values: ``${NAME}`` and its operators, a fixed set of string operations.

Nothing runs and no file is read; ``$NAME``, ``$(...)`` and backticks are characters.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from typing import Callable, Dict, List, Optional, Tuple

from ._errors import Class, Error
from ._settings import Settings

__all__ = ["Lookup", "substitute", "substitute_with"]

#: What a placeholder reads: the value, ``None`` for an unset variable, or an ``Error`` raised for
#: one the caller may not read (``Class.SECRET_PLACEHOLDER``).
Lookup = Callable[[str], Optional[str]]

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_BRACES = re.compile(r"\$\{|\}")
_NAME_START = frozenset("#abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_SPACE = " \t\n\r\x0b\x0c"
_OPERATORS = (":-", ":=", ":+", "-", "=", "+")
_CASE_OPERATORS = ("^", "^^", ",", ",,")


class _Run:
    """One substitution: its lookup, its limits and what it has spent of them."""

    def __init__(self, lookup: Lookup, limits: Settings) -> None:
        self.lookup = lookup
        self.limits = limits
        self.depth = 0
        self.steps = 0

    def step(self, cost: int) -> None:
        self.steps += cost
        if not self.limits.allows("substitution_steps", self.steps):
            raise Error(Class.TOO_COMPLEX, "a pattern")

    def fits(self, size: int) -> None:
        if not self.limits.allows("substitution_bytes", size):
            raise Error(Class.TOO_LARGE, "a substituted value")


def substitute(text: str, lookup: Lookup) -> str:
    """``text`` with every placeholder replaced, reading variables through ``lookup``.

    Raises ``Error``: ``undefined-variable`` for an unset name whose value is needed,
    ``invalid-pattern`` for a bracket expression with nothing in it or a range whose end is below
    its start, and whatever ``lookup`` raises. The subject is the placeholder's name; a
    pattern is never quoted. Past the default ``Settings``: ``too-large``, ``too-deep`` and
    ``too-complex``.
    """
    return substitute_with(text, lookup, Settings())


def substitute_with(text: str, lookup: Lookup, settings: Settings) -> str:
    """As ``substitute``, within the substitution limits of ``settings``."""
    try:
        return _substitute(text, _Run(lookup, settings))
    except RecursionError:
        raise Error(Class.TOO_DEEP, "an expression") from None


def _substitute(text: str, run: _Run) -> str:
    if not run.limits.allows("substitution_depth", run.depth):
        raise Error(Class.TOO_DEEP, "an expression")
    run.depth += 1
    try:
        return _substitute_inner(text, run)
    finally:
        run.depth -= 1


def _blen(text: str) -> int:
    return len(text.encode("utf-8", "surrogatepass"))


def _substitute_inner(text: str, run: _Run) -> str:
    out: "List[str]" = []
    size = 0
    index = 0
    length = len(text)
    closes: "Optional[Dict[int, int]]" = None
    while index < length:
        dollar = text.find("$", index)
        if dollar < 0:
            out.append(text[index:])
            size += _blen(out[-1])
            break
        out.append(text[index:dollar])
        index = dollar
        after = text[index + 1 : index + 2]
        if after == "$":
            out.append("$")
            size += _blen(out[-2]) + 1
            index += 2
            continue
        if after == "{":
            if closes is None:
                closes = _pair_braces(text)
            close = closes.get(index)
            # A body that cannot start an expression is not sliced out, so text made of nothing but
            # "${" costs one step per character; a body that can is charged for the slice.
            if close is not None and text[index + 2 : index + 3] in _NAME_START:
                run.step(close - index)
                found = _expression(text[index + 2 : close], run)
                if found is not None:
                    out.append(found)
                    size += _blen(out[-2]) + _blen(found)
                    run.fits(size)
                    index = close + 1
                    continue
        out.append("$")
        size += _blen(out[-2]) + 1
        index += 1
    run.fits(size)
    return "".join(out)


def _pair_braces(text: str) -> "Dict[int, int]":
    """The index of each ``${`` that has an end, to the ``}`` that ends it: the first that does not
    close a ``${`` opened inside it. One pass, whatever the nesting."""
    pairs: "Dict[int, int]" = {}
    open_at: "List[int]" = []
    for found in _BRACES.finditer(text):
        if found.group() == "${":
            open_at.append(found.start())
        elif open_at:
            pairs[open_at.pop()] = found.start()
    return pairs


def _required(name: str, run: _Run) -> str:
    value = run.lookup(name)
    if value is None:
        raise Error(Class.UNDEFINED_VARIABLE, name)
    return value


def _expression(body: str, run: _Run) -> "Optional[str]":
    """The text for ``${body}``, or ``None`` when it is no expression the specification defines."""
    if body.startswith("#"):
        named = _NAME.fullmatch(body, 1)
        if named is not None:
            return str(len(_required(named.group(), run)))
    named = _NAME.match(body)
    if named is None:
        return None
    key, rest = named.group(), body[named.end() :]
    if not rest:
        return _required(key, run)
    for operator in _OPERATORS:
        if rest.startswith(operator):
            word = rest[len(operator) :]
            value = run.lookup(key)
            present = value is not None and not (operator[0] == ":" and value == "")
            if operator.endswith("+"):
                return _substitute(word, run) if present else ""
            return value if present and value is not None else _substitute(word, run)
    lead = rest[0]
    if lead == ":":
        parsed = _slice_spec(rest[1:])
        if parsed is None:
            return None
        return _slice(_required(key, run), parsed[0], parsed[1])
    if lead == "/":
        spec = rest[1:]
        kind = spec[:1] if spec[:1] in ("/", "#", "%") else ""
        pattern, replacement = _split_pattern(spec[len(kind) :])
        return _replace(_required(key, run), kind, pattern, replacement, run)
    if lead in ("#", "%"):
        doubled = rest[1:2] == lead
        pattern = rest[2 if doubled else 1 :]
        return _trim(_required(key, run), lead, doubled, pattern, run)
    if rest not in _CASE_OPERATORS:
        return None
    value = _required(key, run)
    if rest == "^^":
        return value.upper()
    if rest == ",,":
        return value.lower()
    head = value[:1]
    return (head.upper() if rest == "^" else head.lower()) + value[1:]


def _number(digits: str, negative: bool) -> int:
    """A whole number of any size, saturated: past the length of any value it acts the same."""
    value = (1 << 62) if len(digits) > 19 else min(int(digits), 1 << 62)
    return -value if negative else value


def _slice_spec(text: str) -> "Optional[Tuple[int, Optional[int]]]":
    """The offset and length after ``${NAME:``, or ``None`` when the rest is no slice."""
    at = 0
    size = len(text)

    def skip() -> int:
        nonlocal at
        start = at
        while at < size and text[at] in _SPACE:
            at += 1
        return at - start

    def digits() -> str:
        nonlocal at
        start = at
        while at < size and "0" <= text[at] <= "9":
            at += 1
        return text[start:at]

    spaces = skip()
    char = text[at : at + 1]
    if char == "(":
        at += 1
        skip()
        negative = text[at : at + 1] == "-"
        at += negative
        found = digits()
        if not found:
            return None
        offset = _number(found, negative)
        skip()
        if text[at : at + 1] != ")":
            return None
        at += 1
    elif "0" <= char <= "9" and char:
        offset = _number(digits(), False)
    elif char == "-" and spaces > 0:
        at += 1
        found = digits()
        if not found:
            return None
        offset = _number(found, True)
    else:
        return None
    length: "Optional[int]" = None
    if text[at : at + 1] == ":":
        at += 1
        skip()
        negative = text[at : at + 1] == "-"
        at += negative
        found = digits()
        if not found:
            return None
        length = _number(found, negative)
    skip()
    return (offset, length) if at == size else None


def _slice(value: str, offset: int, length: "Optional[int]") -> str:
    size = len(value)
    if offset < 0:
        offset += size
        if offset < 0:
            return ""
    end = size if length is None else (offset + length if length >= 0 else size + length)
    return value[offset:end] if end > offset else ""


def _split_pattern(spec: str) -> "Tuple[str, str]":
    """A replacement's pattern and text: split at the first ``/`` that does not follow a ``\\``."""
    index = 0
    while index < len(spec):
        if spec[index] == "\\":
            index += 2
            continue
        if spec[index] == "/":
            return spec[:index], spec[index + 1 :]
        index += 1
    return spec, ""


# A pattern token: ("*",), ("?",), ("c", char) or ("[", negate, members).
_Token = Tuple

# One step of matching is charged this many units of ``substitution_steps``, so the default bound
# is reached in about a second of Python's time, not the tens of seconds a unit per step would take.
_COST = 4


class _Pattern:
    """A compiled glob: its tokens, and what must match at the ends of any text it matches."""

    def __init__(self, tokens: "List[_Token]") -> None:
        self.tokens = tokens
        stars = [i for i, token in enumerate(tokens) if token[0] == "*"]
        self.stars = bool(stars)
        self.head = tokens[: stars[0]] if stars else tokens
        self.tail = tokens[stars[-1] + 1 :] if stars else []
        self.fixed = len(self.head) + len(self.tail)


def _compile(pattern: str) -> _Pattern:
    invalid = Error(Class.INVALID_PATTERN, "a bracket expression")
    out: "List[_Token]" = []
    index = 0
    size = len(pattern)
    while index < size:
        char = pattern[index]
        if char == "\\" and index + 1 < size:
            out.append(("c", pattern[index + 1]))
            index += 2
            continue
        close = pattern.find("]", index + 2) if char == "[" else -1
        if char == "*":
            out.append(("*",))
        elif char == "?":
            out.append(("?",))
        elif close >= 0:
            body = pattern[index + 1 : close]
            negate = body[:1] in ("!", "^")
            items = body[1:] if negate else body
            if not items:
                raise invalid
            members: "List[Tuple[str, str]]" = []
            at = 0
            while at < len(items):
                if at + 2 < len(items) and items[at + 1] == "-":
                    low, high = items[at], items[at + 2]
                    if high < low:
                        raise invalid
                    members.append((low, high))
                    at += 3
                else:
                    members.append((items[at], items[at]))
                    at += 1
            out.append(("[", negate, _ranges(members)))
            index = close + 1
            continue
        else:
            out.append(("c", char))
        index += 1
    return _Pattern(out)


def _matches(token: _Token, char: str) -> bool:
    kind = token[0]
    if kind in ("*", "?"):
        return True
    if kind == "c":
        return token[1] == char
    lows, highs = token[2]
    at = bisect_right(lows, char) - 1
    return (at >= 0 and char <= highs[at]) != token[1]


def _ranges(members: "List[Tuple[str, str]]") -> "Tuple[List[str], List[str]]":
    """A bracket's members as sorted, disjoint ranges: their starts and their ends."""
    lows: "List[str]" = []
    highs: "List[str]" = []
    for low, high in sorted(members):
        if highs and low <= highs[-1]:
            highs[-1] = max(highs[-1], high)
        else:
            lows.append(low)
            highs.append(high)
    return lows, highs


def _whole(pattern: _Pattern, text: str, start: int, end: int, run: _Run) -> bool:
    """Whether the pattern matches ``text[start:end]`` whole; what it walks is spent from ``run``."""
    tokens = pattern.tokens
    size = end - start
    if (size < pattern.fixed) if pattern.stars else (size != len(tokens)):
        run.step(_COST)
        return False
    # Whatever lies before the first star and after the last must match the ends of the text, which
    # settles most mismatches without walking it.
    head, tail = pattern.head, pattern.tail
    if not (
        all(_matches(token, text[start + i]) for i, token in enumerate(head))
        and all(_matches(token, text[end - len(tail) + i]) for i, token in enumerate(tail))
    ):
        run.step(_COST * (pattern.fixed + 1))
        return False
    t = s = steps = 0
    star: "Optional[Tuple[int, int]]" = None
    count = len(tokens)
    while s < size:
        steps += 1
        if t < count and tokens[t][0] != "*" and _matches(tokens[t], text[start + s]):
            t += 1
            s += 1
        elif t < count and tokens[t][0] == "*":
            star = (t, s)
            t += 1
        elif star is not None:
            t = star[0] + 1
            s = star[1] + 1
            star = (star[0], star[1] + 1)
        else:
            run.step(_COST * (steps + pattern.fixed))
            return False
    run.step(_COST * (steps + 1 + pattern.fixed))
    return all(token[0] == "*" for token in tokens[t:])


def _ends(pattern: _Pattern, text: str, start: int, run: _Run) -> "List[int]":
    """Every end at which the pattern matches ``text`` from ``start``, shortest first."""
    return [end for end in range(start, len(text) + 1) if _whole(pattern, text, start, end, run)]


def _suffix_start(pattern: _Pattern, text: str, last: bool, run: _Run) -> "Optional[int]":
    """The first start from which the pattern matches the rest of ``text``, or the last with ``last``."""
    found = None
    for start in range(len(text) + 1):
        if _whole(pattern, text, start, len(text), run):
            found = start
            if not last:
                break
    return found


def _replace(value: str, kind: str, pattern: str, replacement: str, run: _Run) -> str:
    if not pattern:
        return value
    compiled = _compile(pattern)
    if kind == "#":
        found = _ends(compiled, value, 0, run)
        out = replacement + value[found[-1] :] if found else value
    elif kind == "%":
        start = _suffix_start(compiled, value, False, run)
        out = value if start is None else value[:start] + replacement
    else:
        pieces: "List[str]" = []
        size = position = 0
        while True:
            match: "Optional[Tuple[int, int]]" = None
            for start in range(position, len(value)):
                ends = _ends(compiled, value, start, run)
                if ends and ends[-1] > start:
                    match = (start, ends[-1])
                    break
            if match is None:
                break
            pieces.append(value[position : match[0]])
            pieces.append(replacement)
            size += _blen(pieces[-2]) + _blen(replacement)
            run.fits(size)
            position = match[1]
            if kind != "/":
                break
        pieces.append(value[position:])
        out = "".join(pieces)
    run.fits(_blen(out))
    return out


def _trim(value: str, operator: str, longest: bool, pattern: str, run: _Run) -> str:
    compiled = _compile(pattern)
    if operator == "#":
        found = _ends(compiled, value, 0, run)
        if not found:
            return value
        return value[(found[-1] if longest else found[0]) :]
    # The shortest suffix starts last, the longest first.
    start = _suffix_start(compiled, value, not longest, run)
    return value if start is None else value[:start]
