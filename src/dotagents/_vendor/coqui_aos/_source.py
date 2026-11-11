"""Where an overlay comes from: ``[git+]<location>[@<ref>][#<path>]``."""

from __future__ import annotations

import enum
import ipaddress
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ._errors import Class, Error
from ._names import valid_relative_path

__all__ = ["Source", "SourceKind"]

#: What Unicode calls White_Space, which ``trim`` strips; ``str.strip`` also strips ``\x1c``-``\x1f``,
#: which a source must refuse as controls instead.
_WHITE_SPACE = "\t\n\x0b\x0c\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"

#: Characters no source holds: controls, line separators and what reorders displayed text.
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_PERCENT = re.compile(rb"%([0-9A-Fa-f]{2})")
_GIT_SCHEMES = ("https", "http", "ssh", "git", "file")


class SourceKind(str, enum.Enum):
    """What a source names."""

    DIRECTORY = "directory"
    GIT = "git"
    URL = "url"

    @classmethod
    def named(cls, word: str) -> "Optional[SourceKind]":
        """The kind a vector's word stands for, or ``None``."""
        try:
            return cls(word)
        except ValueError:
            return None


def _is_git_location(location: str) -> bool:
    return location.endswith(".git") or location.startswith(("git@", "ssh://", "git://"))


def _has_drive(location: str) -> bool:
    return (
        len(location) >= 2
        and location[1] == ":"
        and location[0] in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    )


def _authority_of(location: str) -> "Optional[Tuple[str, str, str]]":
    """A URL's scheme, authority and the rest; the authority ends at the first ``/`` or ``?``."""
    scheme, sep, rest = location.partition("://")
    if not sep:
        return None
    cut = len(rest)
    for char in "/?":
        found = rest.find(char)
        if 0 <= found < cut:
            cut = found
    return scheme, rest[:cut], rest[cut:]


def _host_and_path(location: str) -> "Optional[Tuple[str, str]]":
    """``host:path`` as scp-style Git locations write it, or ``None`` for a path or a drive."""
    if _has_drive(location):
        return None
    host, sep, rest = location.partition(":")
    if sep and host and "/" not in host and "\\" not in host:
        return host, rest
    return None


def _percent_decoded(text: str) -> str:
    data = text.encode("utf-8", "surrogatepass")
    return _PERCENT.sub(lambda m: bytes([int(m.group(1), 16)]), data).decode("utf-8", "replace")


def _refuse(why: str) -> Error:
    return Error(Class.INVALID_SOURCE, why)


@dataclass(frozen=True)
class Source:
    """A parsed source. ``ref`` is the Git ref after ``@`` (a Git source only), ``path`` the
    directory inside it after ``#``. ``repr`` shows ``shown()``, never a user or a query."""

    kind: SourceKind
    location: str
    ref: Optional[str] = None
    path: Optional[str] = None

    def __repr__(self) -> str:
        return "Source(kind=%s, shown=%r)" % (self.kind.value, self.shown())

    @classmethod
    def parse(cls, text: str) -> "Source":
        """``text`` as a source; ``Class.INVALID_SOURCE`` for one the specification refuses
        (empty, a control character, a ``#path`` that is no relative path, a user, host or ref
        that reads as an option, a Git remote helper or a scheme Git is not given)."""
        raw = text.strip(_WHITE_SPACE)
        if not raw:
            raise _refuse("empty")
        if _CONTROL.search(raw):
            raise _refuse("control character")
        explicit_git = raw.startswith("git+")
        if explicit_git:
            raw = raw[len("git+") :]
        body, hash_sep, tail = raw.partition("#")
        path: "Optional[str]" = None
        if hash_sep:
            path = tail.strip(_WHITE_SPACE).strip("/") or None
        location = body.strip(_WHITE_SPACE)
        ref: "Optional[str]" = None
        head, at, after = location.rpartition("@")
        if at:
            parts = _authority_of(location)
            after_authority = len(location) - len(parts[2]) if parts else len(location)
            # An @ inside a URL's authority is user information, never the start of a ref.
            in_authority = "://" in location and len(head) < after_authority
            if (
                head
                and after
                and not in_authority
                and not any(c in after for c in "/:\\")
                and not head.endswith(("://", ":"))
                and (explicit_git or _is_git_location(head))
            ):
                location, ref = head, after
        if path is not None and not valid_relative_path(path):
            raise _refuse("the #path is not a relative path")
        pieces: "List[str]" = []
        authority = _authority_of(location)
        if authority is not None:
            if "\\" in authority[1]:
                raise _refuse("backslash in the authority")
            user, _, hostport = authority[1].rpartition("@")
            if hostport.endswith("]"):
                host, port = hostport, ""
            else:
                host, _, port = hostport.rpartition(":") if ":" in hostport else (hostport, "", "")
            pieces = [user, host, port]
        else:
            scp = _host_and_path(location)
            if scp is not None:
                user, _, host = scp[0].rpartition("@")
                pieces = [user, host, scp[1]]
        if any(_percent_decoded(piece).startswith("-") for piece in pieces):
            raise _refuse("a user, host or port that reads as an option")
        if explicit_git or _is_git_location(location):
            if location.startswith("-") or (ref or "").startswith("-"):
                raise _refuse("a location or ref that reads as an option")
            if "::" in location:
                raise _refuse("a Git remote helper")
            scheme, sep, _ = location.partition("://")
            if sep and scheme.lower() not in _GIT_SCHEMES:
                raise _refuse("a scheme Git is not given")
            return cls(SourceKind.GIT, location, ref, path)
        return cls(SourceKind.URL if "://" in location else SourceKind.DIRECTORY, location, None, path)

    def shown(self, ref: "Optional[str]" = None) -> str:
        """The source as a consumer shows it: no user information and no query in a URL.
        ``ref`` replaces the source's own."""
        location = self.location
        parts = _authority_of(location)
        if parts is not None:
            scheme, authority, rest = parts
            location = "%s://%s%s" % (scheme, authority.rpartition("@")[2], rest.split("?", 1)[0])
        ref = ref if ref is not None else self.ref
        return location + ("@" + ref if ref else "") + ("#" + self.path if self.path else "")

    def key(self, ref: "Optional[str]" = None) -> str:
        """The source in full, as it is fetched: what two entries must share to be one."""
        ref = ref if ref is not None else self.ref
        return self.location + ("@" + ref if ref else "") + ("#" + self.path if self.path else "")

    def is_local(self) -> bool:
        """Whether the source names this machine: a directory, a ``file:`` URL, a Git path, or a
        host that is loopback, link-local, unspecified or ``localhost``."""
        location = self.location
        if location[:5].lower() == "file:" or self.kind is SourceKind.DIRECTORY:
            return True
        if self.kind is SourceKind.GIT and _local_git(location):
            return True
        return host_is_local(source_host(location))

    def holds_credentials(self) -> bool:
        """Whether the location carries a secret: a query, a password, or a user over a web URL.
        ``ssh://git@host`` and ``git@host:path`` name an account, not a secret."""
        parts = _authority_of(self.location)
        if parts is None:
            return False
        scheme, authority, rest = parts
        user = authority.rpartition("@")[0] if "@" in authority else ""
        return "?" in rest or ":" in user or bool(user and scheme.lower() in ("http", "https"))

    def is_web_url(self) -> bool:
        """Whether the location is an ``http://`` or ``https://`` URL."""
        return self.location[:8].lower() == "https://" or self.location[:7].lower() == "http://"


def _local_git(location: str) -> bool:
    scheme, sep, _ = location.partition("://")
    if sep:
        return scheme.lower() == "file"
    return _has_drive(location) or _host_and_path(location) is None


def source_host(location: str) -> "Optional[str]":
    """The host a location reaches, lowercased and without brackets, or ``None`` for a path."""
    parts = _authority_of(location)
    if parts is not None:
        hostport = parts[1].rpartition("@")[2]
    else:
        scp = _host_and_path(location)
        if scp is None:
            return None
        host = scp[0].rpartition("@")[2]
        return _ascii_lower(host) if host else None
    if hostport.startswith("["):
        return _ascii_lower(hostport[1:].split("]", 1)[0])
    host = hostport.rpartition(":")[0] if ":" in hostport else hostport
    return _ascii_lower(host)


def _ascii_lower(text: str) -> str:
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in text)


def _number(part: str) -> "Optional[int]":
    """One part of a shorthand IPv4 host: decimal, ``0x`` hexadecimal or a leading-zero octal."""
    if part[:2] in ("0x", "0X"):
        digits, base = part[2:] or "0", 16
    elif len(part) > 1 and part[0] == "0":
        digits, base = part[1:], 8
    else:
        digits, base = part, 10
    allowed = {8: "01234567", 10: "0123456789", 16: "0123456789abcdefABCDEF"}[base]
    if len(digits) > 12 or not all(c in allowed for c in digits):
        return None
    return int(digits, base)


def _shorthand_ipv4(host: str) -> "Optional[ipaddress.IPv4Address]":
    """The address a client such as ``curl`` or ``git`` reaches for ``127.1``, ``2130706433``,
    ``0x7f.0.0.1`` or ``0177.0.0.1``, the way the WHATWG URL standard parses a host: one to four
    parts, each decimal, hexadecimal or octal, the last filling the bytes the others leave."""
    parts = host.split(".")
    if parts and parts[-1] == "" and len(parts) > 1:
        parts.pop()
    if not 1 <= len(parts) <= 4 or "" in parts:
        return None
    numbers = [_number(part) for part in parts]
    if any(number is None for number in numbers):
        return None
    *head, last = numbers
    if any(number > 255 for number in head) or last >= 256 ** (5 - len(parts)):
        return None
    value = last
    for position, number in enumerate(head):
        value += number * 256 ** (3 - position)
    return ipaddress.IPv4Address(value)


def _address(host: str) -> "Optional[ipaddress._BaseAddress]":
    if "%" in host:
        return None
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    return _shorthand_ipv4(host)


def host_is_local(host: "Optional[str]") -> bool:
    """Whether ``host`` is this machine on its face: ``localhost``, loopback, link-local or
    unspecified, judged on the text alone."""
    if host is None:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    address = _address(host)
    if address is None:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    address = mapped or address
    return address.is_loopback or address.is_link_local or address.is_unspecified


def host_is_private(host: "Optional[str]") -> bool:
    """Whether ``host`` is a private, reserved or documentation address that is not already
    local, judged on the text alone."""
    if host is None or host_is_local(host):
        return False
    address = _address(host)
    if address is None:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    if isinstance(address, ipaddress.IPv4Address):
        a, b, c, _ = address.packed
        return (
            a == 0
            or a == 10
            or (a == 100 and 64 <= b < 128)
            or (a == 172 and 16 <= b < 32)
            or (a == 192 and b == 168)
            or (a == 169 and b == 254)
            or (a == 192 and b == 0 and c in (0, 2))
            or (a == 198 and b in (18, 19))
            or (a == 198 and b == 51 and c == 100)
            or (a == 203 and b == 0 and c == 113)
            or a >= 240
        )
    words = [int.from_bytes(address.packed[i : i + 2], "big") for i in range(0, 8, 2)]
    return (
        (words[0] & 0xFE00) == 0xFC00
        or (words[0] == 0x2001 and words[1] == 0x0DB8)
        or (words[0] == 0x0100 and words[1] == words[2] == words[3] == 0)
        or words[0] == 0x2002
    )
