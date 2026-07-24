"""Netscape cookie-file I/O and requests-session cookie merging."""
from __future__ import annotations

from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import urlparse


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
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, cpath, secure, expires, name, value = parts[:7]
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


def apply_to_session(session, cookies: Iterable[CookieSpec]) -> None:
    for c in cookies:
        try:
            session.cookies.set(c.name, c.value, domain=c.domain, path=c.path)
        except Exception:
            continue


def save_netscape(session_cookies, path: Path) -> None:
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


def merge_set_cookie_headers(session, response) -> None:
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
