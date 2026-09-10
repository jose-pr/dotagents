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
from .proxy import configured_authorization, prefix_url, proxy_type, resolve, should_bypass
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
    authorization: Optional[str] = None
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
    gateway: Optional[tuple] = None  # (clean proxy url, endpoint) in prefix mode
    if proxies is None:
        resolved = resolve()
        if resolved:
            clean_url, authorization = resolved
            kind, endpoint = proxy_type()
            if kind == "prefix":
                gateway = (clean_url, endpoint)
            else:
                proxies = {"http": clean_url, "https": clean_url}
    else:
        # Caller-supplied proxies keep their own userinfo (requests reads it);
        # a configured header value still applies to them.
        authorization = configured_authorization()

    class _ProxyAuthAdapter(HTTPAdapter):
        """Sends the configured ``Proxy-Authorization`` to the proxy -- on the
        plain-http request AND on the https ``CONNECT`` -- through the one hook
        requests offers for proxy-bound headers. A header set on the session
        would reach the ORIGIN inside the tunnel instead, and never the proxy."""

        def proxy_headers(self, proxy):
            headers = super().proxy_headers(proxy)
            if authorization:
                headers["Proxy-Authorization"] = authorization
            return headers

    class _PrefixGatewayAdapter(HTTPAdapter):
        """``AGENTS_PROXY_TYPE=prefix``: every request goes directly to
        ``<proxy><endpoint><url>`` with the ``Proxy-Authorization`` value as a
        header of that request; nothing is tunnelled. The prepared request's URL
        is rewritten for the wire only and restored afterwards, so redirects,
        cookies and history are still resolved against the URL the caller asked
        for. ``NO_PROXY`` hosts go direct, untouched."""

        def send(self, request, **kwargs):
            original = request.url
            if should_bypass(original):
                return super().send(request, **kwargs)
            request.url = prefix_url(original, gateway[0], gateway[1])
            if authorization:
                request.headers["Proxy-Authorization"] = authorization
            kwargs["proxies"] = {}  # the gateway IS the destination; no env proxy on top
            try:
                response = super().send(request, **kwargs)
            finally:
                request.url = original
            response.url = original
            return response

    adapter_cls = _PrefixGatewayAdapter if gateway else _ProxyAuthAdapter
    adapter = adapter_cls(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    if proxies:
        session.proxies = proxies
        # `requests` applies NO_PROXY only to proxies it discovers from the
        # environment itself; explicitly set `session.proxies` are used for
        # every host, loopback included. Honour the bypass list here: a
        # per-request `None` proxy removes the session-level one for that call.
        _proxied_request = session.request

        def _request_honouring_no_proxy(method, url, **kwargs):
            if should_bypass(url):
                kwargs.setdefault("proxies", {"http": None, "https": None})
            return _proxied_request(method, url, **kwargs)

        session.request = _request_honouring_no_proxy
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
