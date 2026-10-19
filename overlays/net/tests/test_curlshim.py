"""lib/curlshim, the launcher's choice: a URL hook's ``_CURL`` wrapper, else the
real curl with the agent proxy added, else curlike. The real curl is faked
(``subprocess.run`` captured); curlike serves for real against loopback
servers. Settings are read under the default prefix (``SYSHTTP_*``) here."""

import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytest.importorskip("curlike")
proxylib = pytest.importorskip("proxylib")

import curlshim  # noqa: E402  (lib/, via conftest)
from syshttp import proxy as agent_proxy  # noqa: E402

BEARER = "Bearer eyJ0b2tlbiI6ICJhZ2VudCJ9"
_PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """No SYSHTTP_* setting, proxy variable or system proxy of the machine."""
    for name in list(os.environ):
        if name.startswith("SYSHTTP_") or name in _PROXY_VARS:
            monkeypatch.delenv(name)
    monkeypatch.setattr(agent_proxy, "system_map", lambda: None)
    proxylib.clear_caches()
    # The faked real curl is no Schannel curl (it would be asked for -V).
    import curlike

    monkeypatch.setattr(curlike, "describe_curl", lambda path: (None, False))


def _capture_real_curl(monkeypatch):
    calls = []
    monkeypatch.setattr(curlshim, "find_real_curl", lambda *a: "/usr/bin/curl")

    class _Res:
        returncode = 0

    monkeypatch.setattr(curlshim.subprocess, "run", lambda argv, *a, **k: calls.append(list(argv)) or _Res())
    return calls


class _Gateway(BaseHTTPRequestHandler):
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


@pytest.fixture()
def gateway():
    _Gateway.seen = []
    httpd = HTTPServer(("127.0.0.1", 0), _Gateway)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield "http://127.0.0.1:%d" % httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def _dead_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _FakeSystem:
    """The system's answer: a PAC result for every URL."""

    def __init__(self, route):
        self.route = route
        self.asked = []

    def __getitem__(self, url):
        self.asked.append(url)
        return proxylib.Proxy.parse_pac_result(self.route or "DIRECT")


# --------------------------------------------------------------------------
# the real curl first
# --------------------------------------------------------------------------


def test_prefers_the_real_curl_with_argv_as_typed(monkeypatch):
    calls = _capture_real_curl(monkeypatch)
    assert curlshim.main(["https://example.com"]) == 0
    assert calls == [["/usr/bin/curl", "https://example.com"]]


def test_the_real_curls_exit_code_propagates(monkeypatch):
    monkeypatch.setattr(curlshim, "find_real_curl", lambda *a: "/usr/bin/curl")

    class _Res:
        returncode = 22

    monkeypatch.setattr(curlshim.subprocess, "run", lambda *a, **k: _Res())
    assert curlshim.main(["https://example.com"]) == 22


def test_no_real_curl_means_curlike(monkeypatch, capsysbinary):
    monkeypatch.setattr(curlshim, "find_real_curl", lambda *a: None)
    assert curlshim.main(["--help"]) == 0
    assert b"Pure Python curl-like tool" in capsysbinary.readouterr().out


# --------------------------------------------------------------------------
# the agent proxy, handed to the real curl
# --------------------------------------------------------------------------


def test_real_curl_gets_the_agent_proxy_and_the_header(monkeypatch):
    calls = _capture_real_curl(monkeypatch)
    monkeypatch.setenv("SYSHTTP_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("SYSHTTP_PROXY_AUTH", BEARER)
    assert curlshim.main(["-s", "https://api.example.com/"]) == 0
    assert calls[0][1:] == [
        "--proxy",
        "http://proxy.example:3128",
        "--proxy-header",
        "Proxy-Authorization: " + BEARER,
        "-s",
        "https://api.example.com/",
    ]


def test_real_curl_gets_the_configured_header_name(monkeypatch):
    calls = _capture_real_curl(monkeypatch)
    monkeypatch.setenv("SYSHTTP_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("SYSHTTP_PROXY_AUTH", BEARER)
    monkeypatch.setenv("SYSHTTP_PROXY_AUTH_HEADER", "X-Proxy-Token")
    curlshim.main(["https://api.example.com/"])
    assert calls[-1][1:5] == ["--proxy", "http://proxy.example:3128", "--proxy-header", "X-Proxy-Token: " + BEARER]


def test_url_userinfo_becomes_proxy_user(monkeypatch):
    calls = _capture_real_curl(monkeypatch)
    monkeypatch.setenv("SYSHTTP_PROXY", "http://agent:s3cret@proxy.example:3128")
    curlshim.main(["https://api.example.com/"])
    assert calls[0][1:5] == ["--proxy", "http://proxy.example:3128", "--proxy-user", "agent:s3cret"]


@pytest.mark.parametrize("lead", ["-q", "--disable", "-qs"])
def test_a_leading_q_stays_first(lead, monkeypatch):
    calls = _capture_real_curl(monkeypatch)
    monkeypatch.setenv("SYSHTTP_PROXY", "http://proxy.example:3128")
    curlshim.main([lead, "https://api.example.com/"])
    assert calls[-1][1] == lead and calls[-1][2:4] == ["--proxy", "http://proxy.example:3128"]
    curlshim.main(["-s", "-q", "https://api.example.com/"])
    assert calls[-1][1] == "--proxy" and calls[-1][-3:] == ["-s", "-q", "https://api.example.com/"]


def test_untouched_without_the_agent_proxy_or_when_the_caller_steers(monkeypatch):
    calls = _capture_real_curl(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://global.example:3128")
    curlshim.main(["https://api.example.com/"])
    assert calls[-1][1:] == ["https://api.example.com/"], "curl reads the variables itself"
    monkeypatch.setenv("SYSHTTP_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("SYSHTTP_PROXY_AUTH", BEARER)
    for steer in (["-x", "http://other:1"], ["--proxy=http://other:1"], ["-sx", "http://other:1"], ["--noproxy", "*"]):
        curlshim.main([*steer, "https://api.example.com/"])
        assert calls[-1][1:] == [*steer, "https://api.example.com/"], steer
    curlshim.main(["-d", "--proxy=trap", "https://api.example.com/"])
    assert calls[-1][1:3] == ["--proxy", "http://proxy.example:3128"], "an option value is not steering"


def test_the_callers_u_keeps_the_agent_proxy(monkeypatch):
    calls = _capture_real_curl(monkeypatch)
    monkeypatch.setenv("SYSHTTP_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("SYSHTTP_PROXY_AUTH", BEARER)
    curlshim.main(["-U", "me:pw", "https://api.example.com/"])
    assert calls[-1][1:] == ["--proxy", "http://proxy.example:3128", "-U", "me:pw", "https://api.example.com/"]


@pytest.mark.parametrize("setting", [("SYSHTTP_PROXY", "http://[bad:3128"), ("SYSHTTP_PROXY_TYPE", "socks5")])
def test_a_malformed_agent_proxy_runs_the_real_curl_as_typed(setting, monkeypatch, capsysbinary):
    calls = _capture_real_curl(monkeypatch)
    monkeypatch.setenv("SYSHTTP_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv(*setting)
    assert curlshim.main(["https://api.example.com/"]) == 0
    assert calls[-1][1:] == ["https://api.example.com/"]
    err = capsysbinary.readouterr().err
    assert err.startswith(b"curl: ignoring the configured proxy:") and b"Traceback" not in err


def test_a_prefix_gateway_is_served_by_curlike(gateway, monkeypatch, capsysbinary):
    """curl has no notion of <proxy><endpoint><url> per hop (-L, several URLs,
    config files): curlike serves a gateway; a caller who steers gets curl."""
    calls = _capture_real_curl(monkeypatch)
    monkeypatch.setenv("SYSHTTP_PROXY", gateway)
    monkeypatch.setenv("SYSHTTP_PROXY_TYPE", "prefix:/fetch/")
    monkeypatch.setenv("SYSHTTP_PROXY_AUTH", BEARER)
    target = "http://origin.example/x"
    assert curlshim.main(["-s", target]) == 0
    assert calls == [] and capsysbinary.readouterr().out == b"via-gateway"
    assert _Gateway.seen[-1] == ("/fetch/" + target, BEARER)
    curlshim.main(["-x", "http://mine:1", "https://api.example.com/"])
    assert calls[-1][1:] == ["-x", "http://mine:1", "https://api.example.com/"]


# --------------------------------------------------------------------------
# the system's proxy, handed to the real curl
# --------------------------------------------------------------------------


def test_the_real_curl_gets_the_systems_proxy_and_the_machine_credential(monkeypatch):
    fake = _FakeSystem("PROXY pac.example:3128")
    monkeypatch.setattr(agent_proxy, "system_map", lambda: fake)
    monkeypatch.setenv("HTTP_PROXY_AUTH", BEARER)
    calls = _capture_real_curl(monkeypatch)
    assert curlshim.main(["-s", "https://api.example.com/x"]) == 0
    assert calls[-1][1:] == [
        "--proxy",
        "http://pac.example:3128",
        "--proxy-header",
        "Proxy-Authorization: " + BEARER,
        "-s",
        "https://api.example.com/x",
    ]
    assert fake.asked == ["https://api.example.com/x"], "asked for the URL the caller typed"


def test_a_direct_answer_or_a_variable_leaves_the_real_curl_alone(monkeypatch):
    calls = _capture_real_curl(monkeypatch)
    monkeypatch.setattr(agent_proxy, "system_map", lambda: _FakeSystem(None))
    curlshim.main(["-s", "https://api.example.com/"])
    assert calls[-1][1:] == ["-s", "https://api.example.com/"], "DIRECT"
    fake = _FakeSystem("PROXY pac.example:3128")
    monkeypatch.setattr(agent_proxy, "system_map", lambda: fake)
    monkeypatch.setenv("HTTPS_PROXY", "http://global.example:3128")
    curlshim.main(["-s", "https://api.example.com/"])
    assert calls[-1][1:] == ["-s", "https://api.example.com/"] and fake.asked == []


def test_the_first_system_proxy_that_answers_is_handed_over(monkeypatch):
    live = socket.socket()
    live.bind(("127.0.0.1", 0))
    live.listen(4)
    try:
        port = live.getsockname()[1]
        route = "PROXY 127.0.0.1:%d; PROXY 127.0.0.1:%d" % (_dead_port(), port)
        monkeypatch.setattr(agent_proxy, "system_map", lambda: _FakeSystem(route))
        calls = _capture_real_curl(monkeypatch)
        assert curlshim.main(["-s", "https://api.example.com/x"]) == 0
        assert calls[-1][1:3] == ["--proxy", "http://127.0.0.1:%d" % port]
    finally:
        live.close()


def test_exit_7_when_no_system_proxy_answers(monkeypatch, capsys):
    first, second = _dead_port(), _dead_port()
    route = "PROXY 127.0.0.1:%d; PROXY 127.0.0.1:%d" % (first, second)
    monkeypatch.setattr(agent_proxy, "system_map", lambda: _FakeSystem(route))
    calls = _capture_real_curl(monkeypatch)
    assert curlshim.main(["-s", "https://api.example.com/x"]) == 7
    assert calls == [], "real curl is not run without a route"
    assert capsys.readouterr().err.startswith(
        "curl: (7) no proxy of the list is reachable: 127.0.0.1:%d, 127.0.0.1:%d" % (first, second)
    )


# --------------------------------------------------------------------------
# <P>_CURL=0: the real curl as typed
# --------------------------------------------------------------------------


def test_curl_off_runs_the_real_curl_as_typed(monkeypatch):
    monkeypatch.setenv("SYSHTTP_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("SYSHTTP_HOOKS_ALL", ".")
    monkeypatch.setenv("SYSHTTP_HOOKS_ALL_CURL", "never-run-me")
    calls = _capture_real_curl(monkeypatch)
    for value in ("0", "n", "No", "false", "OFF"):
        monkeypatch.setenv("SYSHTTP_CURL", value)
        assert curlshim.main(["-s", "https://api.example.com/"]) == 0
        assert calls[-1] == ["/usr/bin/curl", "-s", "https://api.example.com/"], value
    for value in ("1", "y", "yes", "on", "whatever"):
        monkeypatch.setenv("SYSHTTP_CURL", value)
        assert curlshim.shim_enabled()


def test_curl_off_without_a_real_curl_is_exit_2(monkeypatch, capsysbinary):
    monkeypatch.setenv("SYSHTTP_CURL", "0")
    monkeypatch.setattr(curlshim, "find_real_curl", lambda *a: None)
    assert curlshim.main(["-s", "https://api.example.com/"]) == 2
    assert capsysbinary.readouterr().err.startswith(b"curl: (2) SYSHTTP_CURL=0 asks for the real curl")


# --------------------------------------------------------------------------
# URL hooks: a _CURL wrapper in curl's place
# --------------------------------------------------------------------------

WRAPPER = """
import json, os, sys
from pathlib import Path
Path(os.environ["WRAPPER_LOG"]).write_text(json.dumps({
    "argv": sys.argv[1:], "curl": os.environ.get("SYSHTTP_HOOK_CURL"), "skip": os.environ.get("SYSHTTP_HOOK_SKIP"),
    "url": os.environ.get("SYSHTTP_HOOK_URL"), "key": os.environ.get("SYSHTTP_HOOK_KEY"),
}))
"""


def test_a_curl_hook_runs_the_wrapper_in_curls_place(tmp_path, monkeypatch):
    wrapper = tmp_path / "wrap.py"
    wrapper.write_text(WRAPPER, encoding="utf-8")
    log = tmp_path / "log.json"
    monkeypatch.setenv("WRAPPER_LOG", str(log))
    monkeypatch.setenv("SYSHTTP_HOOKS_W", r"^https://api\.example\.com/hooked")
    monkeypatch.setenv("SYSHTTP_HOOKS_W_CURL", "'%s' fetch --" % wrapper)
    monkeypatch.setenv("SYSHTTP_PYTHON", sys.executable)
    monkeypatch.setattr(curlshim, "find_real_curl", lambda *a: pytest.fail("the real curl is not looked for"))
    entry = str(tmp_path / "curl")
    assert curlshim.main(["-s", "https://api.example.com/hooked"], entry=entry) == 0
    seen = json.loads(log.read_text(encoding="utf-8"))
    assert seen["argv"] == ["fetch", "--", "-s", "https://api.example.com/hooked"]
    assert seen["curl"] == entry and seen["skip"] == "W" and seen["key"] == "W"
    assert seen["url"] == "https://api.example.com/hooked"


# --------------------------------------------------------------------------
# <P>_CURL_SSL_REVOKE: a Schannel curl's revocation check
# --------------------------------------------------------------------------


@pytest.fixture()
def schannel(monkeypatch):
    import curlike

    monkeypatch.setattr(curlshim.os, "name", "nt")
    monkeypatch.setattr(curlike, "describe_curl", lambda path: ((8, 21, 0), True))


def test_schannel_gets_best_effort_by_default(schannel, monkeypatch):
    calls = _capture_real_curl(monkeypatch)
    assert curlshim.main(["-qs", "https://example.com/"]) == 0
    assert calls[0][1:] == ["-qs", "--ssl-revoke-best-effort", "https://example.com/"], "after a leading -q"


@pytest.mark.parametrize(
    "value,added", [("false", ["--ssl-no-revoke"]), ("true", []), ("BEST-EFFORT", ["--ssl-revoke-best-effort"])]
)
def test_the_revoke_setting(schannel, monkeypatch, value, added):
    calls = _capture_real_curl(monkeypatch)
    monkeypatch.setenv("SYSHTTP_CURL_SSL_REVOKE", value)
    curlshim.main(["-s", "https://example.com/"])
    assert calls[0][1:] == added + ["-s", "https://example.com/"]


def test_the_callers_own_revoke_flag_wins(schannel, monkeypatch):
    calls = _capture_real_curl(monkeypatch)
    curlshim.main(["--ssl-no-revoke", "https://example.com/"])
    assert calls[0][1:] == ["--ssl-no-revoke", "https://example.com/"]


@pytest.mark.parametrize("described", [((8, 21, 0), False), ((7, 69, 1), True), (None, True)])
def test_only_a_schannel_curl_that_has_the_flag(schannel, monkeypatch, described):
    import curlike

    calls = _capture_real_curl(monkeypatch)
    monkeypatch.setattr(curlike, "describe_curl", lambda path: described)
    curlshim.main(["-s", "https://example.com/"])
    assert calls[0][1:] == ["-s", "https://example.com/"]


def test_never_off_windows(schannel, monkeypatch):
    calls = _capture_real_curl(monkeypatch)
    monkeypatch.setattr(curlshim.os, "name", "posix")
    curlshim.main(["-s", "https://example.com/"])
    assert calls[0][1:] == ["-s", "https://example.com/"]


def test_an_unknown_revoke_value_is_exit_2(schannel, monkeypatch, capsys):
    _capture_real_curl(monkeypatch)
    monkeypatch.setenv("SYSHTTP_CURL_SSL_REVOKE", "maybe")
    assert curlshim.main(["-s", "https://example.com/"]) == 2
    assert "SYSHTTP_CURL_SSL_REVOKE=maybe" in capsys.readouterr().err
