"""Netscape cookie-file I/O and requests-session cookie merging."""
from __future__ import annotations

from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, List, Optional, Protocol
from urllib.parse import urlparse

if TYPE_CHECKING:  # requests is optional at runtime: types only
    import requests


class CookieLike(Protocol):
    """What :func:`save_netscape` reads off a cookie: ``http.cookiejar.Cookie``
    (a requests session's) or any object with these attributes."""

    domain: str
    path: str
    secure: bool
    expires: Optional[int]
    name: str
    value: str


@dataclass(frozen=True)
class CookieSpec:
    domain: str
    path: str
    secure: bool
    expires: Optional[int]
    name: str
    value: str


def load_netscape(path: Path) -> List[CookieSpec]:
    if not path.exists():
        return []
    cookies: List[CookieSpec] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        # curl writes an HttpOnly cookie's domain as `#HttpOnly_<domain>`; any
        # other `#` line is a comment.
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, cpath, secure, expires, name, value = parts[:7]
        if domain.startswith("#HttpOnly_"):
            domain = domain[len("#HttpOnly_"):]
        cookies.append(
            CookieSpec(
                domain=domain,
                path=cpath or "/",
                secure=str(secure).upper() == "TRUE",
                expires=int(expires) if str(expires).isdigit() else None,
                name=name,
                value=value,
            )
        )
    return cookies


def apply_to_session(session: "requests.Session", cookies: Iterable[CookieSpec]) -> None:
    """Load jar rows into the session with their flags: a ``Secure`` row
    stays secure (never sent over http), an expiry stays an expiry."""
    for c in cookies:
        try:
            session.cookies.set(
                c.name, c.value, domain=c.domain, path=c.path,
                secure=bool(c.secure), expires=c.expires or None,
            )
        except Exception:
            continue


def domain_covers(domain: str, host: str) -> bool:
    """Does a cookie ``domain`` apply to ``host``: the host itself, or a
    subdomain when the domain has a leading dot (the Netscape flag)."""
    d = (domain or "").lower()
    h = (host or "").lower()
    if not d or not h:
        return False
    bare = d.lstrip(".")
    return h == bare or (d.startswith(".") and h.endswith("." + bare))


def save_netscape(session_cookies: "Iterable[CookieLike]", path: Path) -> None:
    lines = ["# Netscape HTTP Cookie File\n"]
    for cookie in session_cookies:
        domain = getattr(cookie, "domain", "") or ""
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        cpath = getattr(cookie, "path", "/") or "/"
        secure = "TRUE" if bool(getattr(cookie, "secure", False)) else "FALSE"
        expires_val = getattr(cookie, "expires", None)
        expires = str(int(expires_val)) if expires_val else "0"
        name = getattr(cookie, "name", "")
        value = getattr(cookie, "value", "")
        if not name:
            continue
        lines.append(
            "%s\t%s\t%s\t%s\t%s\t%s\t%s\n"
            % (domain, flag, cpath, secure, expires, name, value)
        )
    path.write_text("".join(lines), encoding="utf-8")


def merge_set_cookie_headers(session: "requests.Session", response: "requests.Response") -> None:
    try:
        session.cookies.update(response.cookies)
    except Exception:
        pass
    raw_headers = getattr(response.raw, "headers", None)
    if raw_headers is None:
        return
    try:
        set_cookies = raw_headers.get_all("Set-Cookie") or []
    except Exception:
        set_cookies = []
    if not set_cookies:
        return
    resp_host = urlparse(getattr(response, "url", "") or "").hostname or ""
    for header in set_cookies:
        parsed = SimpleCookie()
        try:
            parsed.load(header)
        except Exception:
            continue
        for morsel in parsed.values():
            domain = morsel["domain"] or resp_host
            cpath = morsel["path"] or "/"
            if morsel["max-age"] == "0" or not morsel.value:
                try:
                    session.cookies.clear(domain=domain, path=cpath, name=morsel.key)
                except Exception:
                    pass
                continue
            secure = bool(morsel["secure"])
            expires = None
            try:
                session.cookies.set(
                    morsel.key,
                    morsel.value,
                    domain=domain,
                    path=cpath,
                    secure=secure,
                    expires=expires,
                )
            except Exception:
                continue
