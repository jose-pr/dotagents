"""Proxy credentials, kind and bypass, end to end: httplib.proxy, the curl shim
(both the real-curl passthrough and the stdlib fallback) and the requests
session.

No external network: a fake authenticating forward proxy, a fake prefix gateway
and a plain target server run on 127.0.0.1. The proxy answers 407 without the
right Proxy-Authorization and never forwards -- a body of ``via-proxy`` proves
the request went through it with the credential, ``via-gateway`` that it went to
the prefix gateway, ``hello`` that it went direct.
"""
import base64
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import curl  # noqa: E402  (bin/, via conftest)
from httplib import proxy as agent_proxy  # noqa: E402  (lib/, via conftest)

_ALL_VARS = [
    "AGENTS_PROXY", "AGENTS_PROXY_AUTH", "AGENTS_PROXY_TYPE", "HTTP_PROXY_AUTH",
    "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
    "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
]

BASIC = "Basic " + base64.b64encode(b"agent:s3cret").decode()
BEARER = "Bearer eyJ0b2tlbiI6ICJhZ2VudCJ9"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for v in _ALL_VARS:
        monkeypatch.delenv(v, raising=False)


class _Target(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"hello"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class _Proxy(BaseHTTPRequestHandler):
    """A forward proxy that only checks the credential, then answers itself."""

    expected = BASIC
    seen = []

    def do_GET(self):
        _Proxy.seen.append((self.path, self.headers.get("Proxy-Authorization")))
        if self.headers.get("Proxy-Authorization") != _Proxy.expected:
            self.send_response(407)
            self.send_header("Proxy-Authenticate", 'Basic realm="agent"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"via-proxy"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class _Gateway(BaseHTTPRequestHandler):
    """A prefix gateway: records the path it was asked for and the header."""

    seen = []

    def do_GET(self):
        _Gateway.seen.append((self.path, self.headers.get("Proxy-Authorization")))
        body = b"via-gateway"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _serve(handler):
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.fixture()
def target():
    httpd = _serve(_Target)
    try:
        yield "http://127.0.0.1:%d" % httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture()
def proxy():
    _Proxy.seen, _Proxy.expected = [], BASIC
    httpd = _serve(_Proxy)
    try:
        yield "127.0.0.1:%d" % httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture()
def gateway():
    _Gateway.seen = []
    httpd = _serve(_Gateway)
    try:
        yield "http://127.0.0.1:%d" % httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


# --------------------------------------------------------------------------
# httplib.proxy: the header value, its sources, redaction, kind, bypass
# --------------------------------------------------------------------------

def test_agents_proxy_auth_is_the_header_value_verbatim(monkeypatch):
    monkeypatch.setenv("AGENTS_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("AGENTS_PROXY_AUTH", BEARER)
    assert agent_proxy.proxy_authorization() == BEARER
    assert agent_proxy.resolve() == ("http://proxy.example:3128", BEARER)


def test_http_proxy_auth_is_the_fallback(monkeypatch):
    monkeypatch.setenv("AGENTS_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("HTTP_PROXY_AUTH", BASIC)
    assert agent_proxy.proxy_authorization() == BASIC
    monkeypatch.setenv("AGENTS_PROXY_AUTH", BEARER)
    assert agent_proxy.proxy_authorization() == BEARER, "AGENTS_PROXY_AUTH wins"


def test_url_userinfo_becomes_basic_when_no_header_is_configured(monkeypatch):
    monkeypatch.setenv("AGENTS_PROXY", "http://agent:s3cret@proxy.example:3128")
    assert agent_proxy.proxy_authorization() == BASIC
    # ...and the stack gets a URL WITHOUT the userinfo, so nothing adds a second header.
    assert agent_proxy.resolve() == ("http://proxy.example:3128", BASIC)


def test_configured_header_beats_url_userinfo(monkeypatch):
    monkeypatch.setenv("AGENTS_PROXY", "http://agent:s3cret@proxy.example:3128")
    monkeypatch.setenv("AGENTS_PROXY_AUTH", BEARER)
    assert agent_proxy.resolve() == ("http://proxy.example:3128", BEARER)


def test_reserved_characters_in_userinfo_decode_before_encoding(monkeypatch):
    monkeypatch.setenv("AGENTS_PROXY", "http://dom%5Cuser:p%40ss%3Aw%2Frd@proxy.example:3128")
    assert agent_proxy.userinfo(agent_proxy.proxy_url()) == ("dom\\user", "p@ss:w/rd")
    assert agent_proxy.proxy_authorization() == agent_proxy.basic_authorization("dom\\user", "p@ss:w/rd")


def test_no_credential_at_all(monkeypatch):
    monkeypatch.setenv("AGENTS_PROXY", "proxy.example:3128")
    assert agent_proxy.resolve() == ("http://proxy.example:3128", None), "bare host:port gets a scheme"


def test_redact_hides_only_the_password():
    assert agent_proxy.redact("http://agent:s3cret@proxy.example:3128") == "http://agent:***@proxy.example:3128"
    assert agent_proxy.redact("http://proxy.example:3128") == "http://proxy.example:3128"
    assert agent_proxy.redact(None) is None


def test_proxy_type_parsing(monkeypatch):
    assert agent_proxy.proxy_type() == ("connect", None)
    monkeypatch.setenv("AGENTS_PROXY_TYPE", "connect")
    assert agent_proxy.proxy_type() == ("connect", None)
    monkeypatch.setenv("AGENTS_PROXY_TYPE", "prefix")
    assert agent_proxy.proxy_type() == ("prefix", "/")
    monkeypatch.setenv("AGENTS_PROXY_TYPE", "prefix:/fetch/")
    assert agent_proxy.proxy_type() == ("prefix", "/fetch/")
    monkeypatch.setenv("AGENTS_PROXY_TYPE", "prefix:fetch")
    assert agent_proxy.proxy_type() == ("prefix", "/fetch")
    monkeypatch.setenv("AGENTS_PROXY_TYPE", "socks5")
    with pytest.raises(ValueError):
        agent_proxy.proxy_type()


def test_prefix_url_is_proxy_endpoint_url(monkeypatch):
    monkeypatch.setenv("AGENTS_PROXY", "https://agent:s3cret@gw.example/")
    monkeypatch.setenv("AGENTS_PROXY_TYPE", "prefix:/fetch/")
    assert agent_proxy.prefix_url("https://api.example.com/x?q=1") == "https://gw.example/fetch/https://api.example.com/x?q=1"
    monkeypatch.setenv("AGENTS_PROXY_TYPE", "prefix")
    assert agent_proxy.prefix_url("https://api.example.com/x") == "https://gw.example/https://api.example.com/x"


def test_no_proxy_bypass(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "localhost,.internal.example,127.0.0.1:8080")
    assert agent_proxy.should_bypass("http://localhost/x")
    assert agent_proxy.should_bypass("https://db.internal.example/")
    assert agent_proxy.should_bypass("http://127.0.0.1:8080/")
    assert not agent_proxy.should_bypass("http://127.0.0.1:9090/")
    assert not agent_proxy.should_bypass("https://api.example.com/")
    # Lowercase form (on Windows the two names are one variable, so clear first).
    monkeypatch.delenv("NO_PROXY")
    monkeypatch.setenv("no_proxy", "*")
    assert agent_proxy.should_bypass("https://api.example.com/")


# --------------------------------------------------------------------------
# curl shim, real-curl passthrough: the agent proxy is handed to curl
# --------------------------------------------------------------------------

def _capture_real_curl(monkeypatch):
    calls = []
    monkeypatch.setattr(curl, "find_real_curl", lambda: "/usr/bin/curl")

    class _Res:
        returncode = 0

    monkeypatch.setattr(curl.subprocess, "run", lambda argv, *a, **k: calls.append(list(argv)) or _Res())
    return calls


def test_real_curl_gets_the_agent_proxy_and_the_header(monkeypatch):
    calls = _capture_real_curl(monkeypatch)
    monkeypatch.setenv("AGENTS_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("AGENTS_PROXY_AUTH", BEARER)
    assert curl.main(["-s", "https://api.example.com/"]) == 0
    assert calls[0][1:] == [
        "--proxy", "http://proxy.example:3128",
        "--proxy-header", "Proxy-Authorization: " + BEARER,
        "-s", "https://api.example.com/",
    ]


def test_real_curl_url_userinfo_becomes_proxy_user(monkeypatch):
    calls = _capture_real_curl(monkeypatch)
    monkeypatch.setenv("AGENTS_PROXY", "http://agent:s3cret@proxy.example:3128")
    curl.main(["https://api.example.com/"])
    assert calls[0][1:5] == ["--proxy", "http://proxy.example:3128", "--proxy-user", "agent:s3cret"]


def test_real_curl_prefix_rewrites_the_url(monkeypatch):
    calls = _capture_real_curl(monkeypatch)
    monkeypatch.setenv("AGENTS_PROXY", "https://gw.example")
    monkeypatch.setenv("AGENTS_PROXY_TYPE", "prefix:/fetch/")
    monkeypatch.setenv("AGENTS_PROXY_AUTH", BEARER)
    curl.main(["-s", "-H", "Accept: application/json", "https://api.example.com/x"])
    assert calls[-1][1:] == [
        "-H", "Proxy-Authorization: " + BEARER,
        "-s", "-H", "Accept: application/json", "https://gw.example/fetch/https://api.example.com/x",
    ]
    curl.main(["--url", "https://api.example.com/y"])
    assert calls[-1][-1] == "https://gw.example/fetch/https://api.example.com/y"
    monkeypatch.setenv("NO_PROXY", "api.example.com")
    curl.main(["https://api.example.com/z"])
    assert calls[-1][1:] == ["https://api.example.com/z"], "NO_PROXY: untouched"


def test_real_curl_untouched_without_agents_proxy_or_when_caller_steers(monkeypatch):
    calls = _capture_real_curl(monkeypatch)
    # The global vars are curl's own business -- never injected.
    monkeypatch.setenv("HTTPS_PROXY", "http://global.example:3128")
    curl.main(["https://api.example.com/"])
    assert calls[-1][1:] == ["https://api.example.com/"]
    monkeypatch.setenv("AGENTS_PROXY", "http://proxy.example:3128")
    for steer in (["-x", "http://other:1"], ["--proxy=http://other:1"], ["--noproxy", "*"], ["-U", "a:b"]):
        curl.main([*steer, "https://api.example.com/"])
        assert calls[-1][1:] == [*steer, "https://api.example.com/"], steer


# --------------------------------------------------------------------------
# curl shim, stdlib fallback: authenticates to the proxy, honours kind + bypass
# --------------------------------------------------------------------------

def _fallback(monkeypatch, capsysbinary, argv):
    monkeypatch.setattr(curl, "find_real_curl", lambda: None)
    rc = curl.main(argv)
    return rc, capsysbinary.readouterr()


def test_fallback_sends_the_configured_header(target, proxy, monkeypatch, capsysbinary):
    _Proxy.expected = BEARER
    monkeypatch.setenv("AGENTS_PROXY", "http://" + proxy)
    monkeypatch.setenv("AGENTS_PROXY_AUTH", BEARER)
    rc, out = _fallback(monkeypatch, capsysbinary, ["-s", target + "/x"])
    assert rc == 0 and out.out == b"via-proxy"
    assert _Proxy.seen[-1] == (target + "/x", BEARER)


def test_fallback_http_proxy_auth_fallback(target, proxy, monkeypatch, capsysbinary):
    monkeypatch.setenv("AGENTS_PROXY", "http://" + proxy)
    monkeypatch.setenv("HTTP_PROXY_AUTH", BASIC)
    rc, out = _fallback(monkeypatch, capsysbinary, ["-s", target + "/x"])
    assert rc == 0 and out.out == b"via-proxy"


def test_fallback_url_userinfo_as_basic(target, proxy, monkeypatch, capsysbinary):
    monkeypatch.setenv("AGENTS_PROXY", "http://agent:s3cret@" + proxy)
    rc, out = _fallback(monkeypatch, capsysbinary, ["-s", target + "/x"])
    assert rc == 0 and out.out == b"via-proxy"
    assert _Proxy.seen[-1][1] == BASIC


def test_fallback_without_credential_is_refused_by_the_proxy(target, proxy, monkeypatch, capsysbinary):
    monkeypatch.setenv("AGENTS_PROXY", "http://" + proxy)
    rc, out = _fallback(monkeypatch, capsysbinary, ["-s", target + "/x"])
    assert rc == 407 and out.out == b""


def test_fallback_proxy_user_flag_wins(target, proxy, monkeypatch, capsysbinary):
    monkeypatch.setenv("AGENTS_PROXY", "http://" + proxy)
    monkeypatch.setenv("AGENTS_PROXY_AUTH", "Bearer wrong")
    rc, out = _fallback(monkeypatch, capsysbinary, ["-s", "-U", "agent:s3cret", target + "/x"])
    assert rc == 0 and out.out == b"via-proxy"


def test_fallback_explicit_proxy_never_borrows_the_agent_credential(target, proxy, monkeypatch, capsysbinary):
    monkeypatch.setenv("AGENTS_PROXY", "http://elsewhere.example:1")
    monkeypatch.setenv("AGENTS_PROXY_AUTH", BASIC)
    rc, out = _fallback(monkeypatch, capsysbinary, ["-s", "-x", "http://" + proxy, target + "/x"])
    assert rc == 407, "-x names a different proxy; the agent proxy's credential is not its"


def test_fallback_no_proxy_goes_direct(target, proxy, monkeypatch, capsysbinary):
    monkeypatch.setenv("AGENTS_PROXY", "http://agent:s3cret@" + proxy)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    rc, out = _fallback(monkeypatch, capsysbinary, ["-s", target + "/x"])
    assert rc == 0 and out.out == b"hello" and _Proxy.seen == []


def test_fallback_noproxy_flag_replaces_no_proxy(target, proxy, monkeypatch, capsysbinary):
    monkeypatch.setenv("AGENTS_PROXY", "http://agent:s3cret@" + proxy)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    # --noproxy REPLACES the env list (curl semantics): 127.0.0.1 is proxied again.
    rc, out = _fallback(monkeypatch, capsysbinary, ["-s", "--noproxy", "example.com", target + "/x"])
    assert rc == 0 and out.out == b"via-proxy"
    rc, out = _fallback(monkeypatch, capsysbinary, ["-s", "--noproxy", "*", target + "/x"])
    assert rc == 0 and out.out == b"hello"


def test_fallback_prefix_gateway(target, gateway, monkeypatch, capsysbinary):
    monkeypatch.setenv("AGENTS_PROXY", gateway)
    monkeypatch.setenv("AGENTS_PROXY_TYPE", "prefix:/fetch/")
    monkeypatch.setenv("AGENTS_PROXY_AUTH", BEARER)
    rc, out = _fallback(monkeypatch, capsysbinary, ["-s", target + "/x"])
    assert rc == 0 and out.out == b"via-gateway"
    assert _Gateway.seen[-1] == ("/fetch/" + target + "/x", BEARER)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    rc, out = _fallback(monkeypatch, capsysbinary, ["-s", target + "/x"])
    assert rc == 0 and out.out == b"hello"


def test_fallback_verbose_never_prints_the_credential(target, proxy, monkeypatch, capsysbinary):
    monkeypatch.setenv("AGENTS_PROXY", "http://agent:s3cret@" + proxy)
    rc, out = _fallback(monkeypatch, capsysbinary, ["-v", target + "/x"])
    assert rc == 0
    assert b"Proxy: http://" + proxy.encode() + b" (with Proxy-Authorization)" in out.err
    assert b"s3cret" not in out.err and BASIC.encode() not in out.err


# --------------------------------------------------------------------------
# requests session: same credential, same kind, same bypass
# --------------------------------------------------------------------------

def test_session_sends_the_configured_header_to_the_proxy(target, proxy, monkeypatch):
    pytest.importorskip("requests")
    from httplib.session import new_session

    _Proxy.expected = BEARER
    monkeypatch.setenv("AGENTS_PROXY", "http://" + proxy)
    monkeypatch.setenv("AGENTS_PROXY_AUTH", BEARER)
    resp = new_session(retries=0).get(target + "/x", timeout=5)
    assert resp.status_code == 200 and resp.text == "via-proxy"
    assert _Proxy.seen[-1][1] == BEARER


def test_session_url_userinfo_authenticates(target, proxy, monkeypatch):
    pytest.importorskip("requests")
    from httplib.session import new_session

    monkeypatch.setenv("AGENTS_PROXY", "http://agent:s3cret@" + proxy)
    resp = new_session(retries=0).get(target + "/x", timeout=5)
    assert resp.text == "via-proxy" and _Proxy.seen[-1][1] == BASIC


def test_session_adapter_puts_the_header_on_the_connect_too(monkeypatch):
    """https goes through CONNECT; requests only forwards `proxy_headers()`
    there, so that hook must carry the credential (a session header would reach
    the origin instead)."""
    pytest.importorskip("requests")
    from httplib.session import new_session

    monkeypatch.setenv("AGENTS_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("AGENTS_PROXY_AUTH", BEARER)
    session = new_session()
    adapter = session.get_adapter("https://api.example.com/")
    assert adapter.proxy_headers("http://proxy.example:3128") == {"Proxy-Authorization": BEARER}
    assert "Proxy-Authorization" not in session.headers


def test_session_honours_no_proxy(target, proxy, monkeypatch):
    pytest.importorskip("requests")
    from httplib.session import new_session

    monkeypatch.setenv("AGENTS_PROXY", "http://agent:s3cret@" + proxy)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    resp = new_session(retries=0).get(target + "/x", timeout=5)
    assert resp.text == "hello" and _Proxy.seen == []


def test_session_prefix_gateway(target, gateway, monkeypatch):
    pytest.importorskip("requests")
    from httplib.session import new_session

    monkeypatch.setenv("AGENTS_PROXY", gateway)
    monkeypatch.setenv("AGENTS_PROXY_TYPE", "prefix:/fetch/")
    monkeypatch.setenv("AGENTS_PROXY_AUTH", BEARER)
    session = new_session(retries=0)
    assert not session.proxies, "a prefix gateway is the destination, not a proxy"
    resp = session.get(target + "/x", timeout=5)
    assert resp.text == "via-gateway"
    assert _Gateway.seen[-1] == ("/fetch/" + target + "/x", BEARER)
    assert resp.url == target + "/x", "the caller's URL, not the gateway's, is what the response reports"
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    assert session.get(target + "/x", timeout=5).text == "hello"
