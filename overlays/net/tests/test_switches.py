"""AGENTS_PROXY_AUTH_HEADER names the header the proxy credential rides in
(session, fallback, real curl's --proxy-header); NET_CURL=0 runs the real curl
as typed; httplib's typing imports requests only under TYPE_CHECKING."""
import subprocess
import sys
from pathlib import Path

import pytest

import curl  # noqa: E402  (bin/, via conftest)
from httplib import proxy as agent_proxy
from test_jars_proxy import _Gateway, gateway, origin  # noqa: F401  (fixtures reused)
from test_proxy_auth import BEARER, _capture_real_curl, _clean_env, _fallback  # noqa: F401

LIB = Path(__file__).resolve().parents[1] / "lib"


@pytest.fixture(autouse=True)
def _no_shim_switch(monkeypatch):
    monkeypatch.delenv(curl.SHIM_ENV, raising=False)
    monkeypatch.delenv(agent_proxy.AUTH_HEADER_VAR, raising=False)


def test_auth_header_name_defaults_and_validates(monkeypatch):
    assert agent_proxy.auth_header() == "Proxy-Authorization"
    monkeypatch.setenv("AGENTS_PROXY_AUTH_HEADER", " X-Proxy-Token ")
    assert agent_proxy.auth_header() == "X-Proxy-Token"
    monkeypatch.setenv("AGENTS_PROXY_AUTH_HEADER", "")
    assert agent_proxy.auth_header() == "Proxy-Authorization"
    for bad in ("X-Token: x", "two words"):
        monkeypatch.setenv("AGENTS_PROXY_AUTH_HEADER", bad)
        with pytest.raises(ValueError, match="AGENTS_PROXY_AUTH_HEADER"):
            agent_proxy.auth_header()


def test_session_uses_the_configured_header_name(origin, gateway, monkeypatch):
    pytest.importorskip("requests")
    from httplib.session import new_session

    monkeypatch.setenv("AGENTS_PROXY_AUTH_HEADER", "X-Proxy-Token")
    session = new_session(retries=0)
    # connect: the hook that feeds the plain-http request and the CONNECT
    adapter = session.get_adapter("https://api.example.com/")
    headers = adapter.proxy_headers(adapter.proxy)
    assert headers == {"X-Proxy-Token": BEARER}
    # prefix: a header of the gateway request
    session.get(origin + "/x", timeout=5)
    seen = _Gateway.seen[-1][1]
    assert seen.get("X-Proxy-Token") == BEARER and "Proxy-Authorization" not in seen


def test_fallback_uses_the_configured_header_name(origin, gateway, monkeypatch, capsysbinary):
    monkeypatch.setenv("AGENTS_PROXY_AUTH_HEADER", "X-Proxy-Token")
    rc, out = _fallback(monkeypatch, capsysbinary, ["-s", origin + "/x"])
    assert rc == 0 and out.out == b"gateway"
    seen = _Gateway.seen[-1][1]
    assert seen.get("X-Proxy-Token") == BEARER and "Proxy-Authorization" not in seen


def test_real_curl_gets_the_configured_header_name(monkeypatch):
    monkeypatch.setenv("AGENTS_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("AGENTS_PROXY_AUTH", BEARER)
    monkeypatch.setenv("AGENTS_PROXY_AUTH_HEADER", "X-Proxy-Token")
    calls = _capture_real_curl(monkeypatch)
    assert curl.main(["https://api.example.com/"]) == 0
    assert calls[-1][1:] == ["--proxy", "http://proxy.example:3128", "--proxy-header", "X-Proxy-Token: " + BEARER, "https://api.example.com/"]


def test_net_curl_off_runs_the_real_curl_as_typed(monkeypatch):
    monkeypatch.setenv("AGENTS_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("AGENTS_PROXY_AUTH", BEARER)
    monkeypatch.setenv("NET_HOOKS_ALL", ".")
    monkeypatch.setenv("NET_HOOKS_ALL_CURL", "never-run-me")
    calls = _capture_real_curl(monkeypatch)
    for value in ("0", "n", "No", "false", "OFF"):
        monkeypatch.setenv("NET_CURL", value)
        assert curl.main(["-s", "https://api.example.com/"]) == 0
        assert calls[-1] == ["/usr/bin/curl", "-s", "https://api.example.com/"], value
    # The default, and any other value, is the shim: the proxy is applied.
    for value in ("1", "y", "yes", "on", "whatever"):
        monkeypatch.setenv("NET_CURL", value)
        assert curl.shim_enabled()
    monkeypatch.delenv("NET_CURL")
    assert curl.shim_enabled()


def test_net_curl_off_without_a_real_curl_is_exit_2(monkeypatch, capsysbinary):
    monkeypatch.setenv("NET_CURL", "0")
    monkeypatch.setattr(curl, "find_real_curl", lambda: None)
    assert curl.main(["-s", "https://api.example.com/"]) == 2
    assert capsysbinary.readouterr().err.startswith(b"curl: (2) NET_CURL=0 asks for the real curl")


def test_httplib_typing_never_imports_requests_at_runtime():
    """The annotations name requests' types under TYPE_CHECKING only: the
    dependency-free modules stay dependency-free, and session/fetch import
    requests lazily, inside new_session."""
    code = (
        "import sys; sys.path.insert(0, %r); "
        "import httplib.session, httplib.fetch, httplib.hooks, httplib.cookies, httplib.auth, httplib.jar; "
        "print('requests' in sys.modules)" % str(LIB)
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "False"


def test_httplib_type_checks_clean():
    """The annotations hold: mypy over lib/httplib reports nothing (skipped
    where mypy is not installed)."""
    mypy_api = pytest.importorskip("mypy.api")
    out, err, rc = mypy_api.run([
        "--python-version", "3.10", "--ignore-missing-imports", "--no-error-summary", str(LIB / "httplib"),
    ])
    assert rc == 0, out + err
