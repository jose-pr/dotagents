"""Build a configured ``requests.Session`` with retry/backoff, proxy-from-env,
and transparent cookie/token load-save + per-host auth.

``requests`` + ``urllib3`` are this toolkit's **optional dependency** (the net
overlay does NOT vendor them — see ``lib/VENDORED.md``). ``new_session`` imports
them lazily and raises a clear, actionable error if they are absent; the curl
shim and the ``certifi`` shim work with zero dependencies regardless.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, Union
from urllib.parse import urlparse

from .auth import AuthProvider
from .cookies import CookieSpec, apply_to_session, merge_set_cookie_headers
from .jar import CookieJar, FileCookieJar, FileTokenJar, TokenJar
from .proxy import proxies_from_env
from .warnings import disable_insecure_request_warnings

VerifyT = Union[bool, str, Path]


def _import_requests():
    try:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
    except ImportError as exc:  # pragma: no cover - exercised via message assert
        raise ImportError(
            "httplib.new_session needs 'requests' (with urllib3), the net "
            "overlay's optional dependency. Install it (`pip install requests`) "
            "or use the curl shim, which needs nothing. See kb/NET.md."
        ) from exc
    return requests, HTTPAdapter, Retry


def new_session(
    *,
    user_agent: Optional[str] = None,
    proxies: Optional[dict] = None,
    verify: VerifyT = True,
    retries: int = 3,
    backoff: float = 1,
    status_forcelist: Iterable[int] = (429, 500, 502, 503, 504),
    cookies: Union[bool, str, "CookieJar"] = False,
    tokens: Union[bool, str, "TokenJar"] = False,
    auth_provider: Optional["AuthProvider"] = None,
):
    disable_insecure_request_warnings()
    requests, HTTPAdapter, Retry = _import_requests()
    session = requests.Session()
    cookie_jar: Optional[CookieJar] = None
    cookie_key: Optional[str] = None
    if cookies is True:
        cookie_jar = FileCookieJar()
        cookie_key = "__by_host__"
    elif isinstance(cookies, str):
        cookie_jar = FileCookieJar()
        cookie_key = cookies
    elif cookies:
        cookie_jar = cookies
        cookie_key = "__by_host__"
    token_jar: Optional[TokenJar] = None
    token_key: Optional[str] = None
    if tokens is True:
        token_jar = FileTokenJar()
        token_key = "__by_host__"
    elif isinstance(tokens, str):
        token_jar = FileTokenJar()
        token_key = tokens
    elif tokens:
        token_jar = tokens
        token_key = "__by_host__"
    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=list(status_forcelist),
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    if proxies is None:
        proxies = proxies_from_env()
    if proxies:
        session.proxies = proxies
    if isinstance(verify, Path):
        session.verify = str(verify)
    else:
        session.verify = verify
    if user_agent:
        session.headers.setdefault("User-Agent", user_agent)
    if cookie_jar or token_jar or auth_provider:
        _orig_request = session.request
        loaded_keys = set()

        def _key_for(url: str) -> str:
            return urlparse(url).hostname or ""

        def _cookie_keys_for(url: str) -> list:
            host = _key_for(url)
            if cookie_key and cookie_key != "__by_host__":
                return [cookie_key]
            return [host] if host else []

        def _load_cookies_for(url: str) -> None:
            if not cookie_jar:
                return
            for key in _cookie_keys_for(url):
                if not key or key in loaded_keys:
                    continue
                try:
                    apply_to_session(session, cookie_jar.get(key, []))
                except Exception:
                    pass
                loaded_keys.add(key)

        def _save_cookies_for(url: str, response) -> None:
            if not cookie_jar:
                return
            try:
                merge_set_cookie_headers(session, response)
            except Exception:
                pass
            specs = []
            try:
                for c in session.cookies:
                    specs.append(
                        CookieSpec(
                            domain=getattr(c, "domain", "") or "",
                            path=getattr(c, "path", "/") or "/",
                            secure=bool(getattr(c, "secure", False)),
                            expires=getattr(c, "expires", None),
                            name=getattr(c, "name", ""),
                            value=getattr(c, "value", ""),
                        )
                    )
            except Exception:
                specs = []
            for key in _cookie_keys_for(url):
                if not key:
                    continue
                try:
                    cookie_jar[key] = specs
                except Exception:
                    pass

        _ensuring = False

        def _load_state(url: str) -> None:
            _load_cookies_for(url)
            nonlocal _ensuring
            if auth_provider and not _ensuring:
                try:
                    _ensuring = True
                    auth_provider.ensure(session, url)
                except Exception:
                    pass
                finally:
                    _ensuring = False

        def request(method, url, **kwargs):
            _load_state(url)
            resp = _orig_request(method, url, **kwargs)
            _save_cookies_for(getattr(resp, "url", url), resp)
            return resp

        session.request = request
        if cookie_jar and cookie_key and cookie_key != "__by_host__":
            try:
                apply_to_session(session, cookie_jar.get(cookie_key, []))
                loaded_keys.add(cookie_key)
            except Exception:
                pass
    return session
