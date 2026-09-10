"""Build a configured ``requests.Session`` with retry/backoff, the agent proxy
(credential, kind and ``NO_PROXY`` bypass, all decided in ONE adapter), and
transparent cookie/token load-save + per-host auth.

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


def _agent_proxy_adapter_class(HTTPAdapter):
    """The one adapter that owns every proxy decision, built lazily on the
    lazily imported ``HTTPAdapter``. ``HTTPAdapter.send(request, proxies=...)``
    is the single point every request -- ``session.get``, a bare
    ``session.send``, and every redirect hop -- passes through, so this is the
    layer requests designed for it; a ``session.request`` wrapper would miss
    the last two."""

    class AgentProxyAdapter(HTTPAdapter):
        """``proxy`` (userinfo-free URL) + ``authorization`` (the
        ``Proxy-Authorization`` value) + ``endpoint`` (``None`` for a
        ``connect`` proxy, the ``/endpoint`` of a prefix gateway).

        * ``NO_PROXY`` hosts go direct, and never see the credential.
        * ``connect``: the proxy is passed EXPLICITLY per request, so the global
          ``HTTP_PROXY``/``HTTPS_PROXY`` that ``trust_env`` would otherwise merge
          in cannot win over the agent proxy -- and the credential is added by
          ``proxy_headers`` only for THIS proxy, so it can never reach another.
          That hook covers the plain-http request and the https ``CONNECT``; a
          header on the session would reach the origin inside the tunnel instead.
        * ``prefix``: the request goes directly to ``<proxy><endpoint><url>`` with
          the credential as a header of that request.

        Every send works on a COPY of the prepared request: requests builds
        redirect hops from the caller's object, so mutating it would carry the
        gateway URL and the credential into the next hop -- which may be a
        ``NO_PROXY`` origin. The response reports the caller's URL, so
        redirects, cookies and history speak the URL the caller asked for.
        """

        def __init__(self, *, proxy=None, authorization=None, endpoint=None, **kwargs):
            super().__init__(**kwargs)
            self.proxy = proxy
            self.authorization = authorization
            self.endpoint = endpoint
            self._gateway_base = prefix_url("", proxy, endpoint) if endpoint is not None and proxy else None

        def proxy_headers(self, proxy):
            headers = super().proxy_headers(proxy)
            if self.authorization and self.proxy and proxy.rstrip("/") == self.proxy.rstrip("/"):
                headers["Proxy-Authorization"] = self.authorization
            return headers

        def send(self, request, **kwargs):
            if not self.proxy:
                return super().send(request, **kwargs)
            original = request.url
            request = request.copy()
            if should_bypass(original):
                kwargs["proxies"] = {}
                return super().send(request, **kwargs)
            if self.endpoint is None:
                kwargs["proxies"] = {"http": self.proxy, "https": self.proxy}
                return super().send(request, **kwargs)
            # Prefix gateway. A Location the gateway wrote in its own namespace
            # is already prefixed: never wrap it twice.
            if not original.startswith(self._gateway_base):
                request.url = prefix_url(original, self.proxy, self.endpoint)
            if self.authorization:
                request.headers["Proxy-Authorization"] = self.authorization
            kwargs["proxies"] = {}
            response = super().send(request, **kwargs)
            response.url = original
            return response

    return AgentProxyAdapter


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
    proxy_url: Optional[str] = None
    authorization: Optional[str] = None
    endpoint: Optional[str] = None
    if proxies is None:
        resolved = resolve()
        if resolved:
            proxy_url, authorization = resolved
            endpoint = proxy_type()[1]
    else:
        # Caller-supplied proxies are requests' business (userinfo included);
        # the configured credential still applies if one of them IS the agent proxy.
        session.proxies = proxies
        configured = resolve()
        if configured:
            proxy_url, authorization = configured[0], configured_authorization()
            proxy_url = proxy_url if proxy_url in proxies.values() else None
    adapter = _agent_proxy_adapter_class(HTTPAdapter)(
        proxy=proxy_url, authorization=authorization, endpoint=endpoint, max_retries=retry_strategy,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
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
