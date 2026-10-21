"""lib/curlshim, the launcher's choice: a URL hook's ``_CURL`` wrapper, else the
real curl with the agent proxy added, else curlike. The real curl is a fake
(``fake_curl``) that records its argv, its ``-K`` files and its environment;
curlike serves for real against loopback servers. Settings are read under the
overlay's prefix, ``SYSHTTP_PREFIX=AGENTS_``."""

import json
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytest.importorskip("curlike")
proxylib = pytest.importorskip("proxylib")

import curlshim  # noqa: E402  (lib/, via conftest)
import fake_curl  # noqa: E402  (tests/)
from syshttp import proxy as agent_proxy  # noqa: E402

BEARER = "Bearer eyJ0b2tlbiI6ICJhZ2VudCJ9"
_PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
               "HTTP_PROXY_AUTH")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """The overlay's prefix, and no agent setting, proxy variable or system
    proxy of the machine."""
    for name in list(os.environ):
        if name.upper().startswith(("SYSHTTP_", "AGENTS_")) or name in _PROXY_VARS:
            monkeypatch.delenv(name)
    monkeypatch.setenv("SYSHTTP_PREFIX", "AGENTS_")
    monkeypatch.setattr(agent_proxy, "system_map", lambda: None)
    proxylib.clear_caches()
    # The fake is no Schannel curl (curlike would ask it for -V).
    import curlike

    monkeypatch.setattr(curlike, "describe_curl", lambda path: (None, False))


class _Fake:
    """The fake real curl, alone on PATH; ``runs()`` are its records."""

    def __init__(self, tmp_path, monkeypatch):
        self.dir = tmp_path / "system"
        self.path = fake_curl.install(self.dir)
        self.log = tmp_path / "curl.jsonl"
        monkeypatch.setenv("PATH", str(self.dir))
        if os.name == "nt":
            monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
        for name, value in fake_curl.environment(self.log).items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("FAKE_CURL_ENV", "AGENTS_CURL_SHIM")
        self.entry = str(tmp_path / "launcher" / "curl")

    def runs(self):
        return fake_curl.records(self.log)

    def argv(self):
        """The last run's argv, each ``-K`` file of the launcher's shown as ``<K>``."""
        argv = list(self.runs()[-1]["argv"])
        return [("<K>" if i and argv[i - 1] == "-K" and "curlshim-" in a else a) for i, a in enumerate(argv)]

    def main(self, argv):
        return curlshim.main(argv, entry=self.entry)


@pytest.fixture()
def fake(tmp_path, monkeypatch):
    return _Fake(tmp_path, monkeypatch)


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


def _agent_proxy(monkeypatch, auth=BEARER):
    monkeypatch.setenv("AGENTS_SYSHTTP_PROXY", "http://proxy.example:3128")
    if auth:
        monkeypatch.setenv("AGENTS_SYSHTTP_PROXY_AUTH", auth)


# --------------------------------------------------------------------------
# the real curl first
# --------------------------------------------------------------------------


def test_the_real_curl_runs_with_argv_as_typed(fake):
    assert fake.main(["-s", "https://example.com/?a=1&b=2"]) == 0
    assert fake.argv() == ["-s", "https://example.com/?a=1&b=2"]


def test_the_real_curls_exit_code_propagates(fake, monkeypatch):
    monkeypatch.setenv("FAKE_CURL_RC", "22")
    assert fake.main(["https://example.com/"]) == 22


def test_a_signal_death_is_128_plus_the_signal(monkeypatch):
    class _Killed:
        returncode = -15

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Killed())
    assert curlshim._run(["curl"], "the real curl") == 143


def test_no_real_curl_means_curlike(tmp_path, monkeypatch, capsysbinary):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert curlshim.main(["--help"], entry=str(tmp_path / "curl")) == 0
    assert b"Pure Python curl-like tool" in capsysbinary.readouterr().out


def test_ctrl_c_is_130_without_a_traceback(fake, monkeypatch, capsys):
    def interrupted(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess, "run", interrupted)
    assert fake.main(["https://example.com/"]) == 130
    assert "Traceback" not in capsys.readouterr().err


# --------------------------------------------------------------------------
# finding the real curl
# --------------------------------------------------------------------------


def test_the_launchers_own_directory_and_a_launcher_beside_curl_py_are_skipped(tmp_path, monkeypatch):
    own = tmp_path / "own"
    other = tmp_path / "other"
    real = tmp_path / "real"
    fake_curl.install(own)
    fake_curl.install(other)
    (other / "curl.py").write_text("", encoding="utf-8")
    found = fake_curl.install(real)
    monkeypatch.setenv("PATH", os.pathsep.join(map(str, (own, other, real))))
    assert curlshim.find_real_curl(str(own / "curl")) == str(found)


def test_the_current_directory_is_never_searched(tmp_path, monkeypatch):
    fake_curl.install(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "." + os.pathsep + str(tmp_path / "nothing"))
    assert curlshim.find_real_curl() is None


@pytest.mark.skipif(os.name != "nt", reason="PATHEXT")
def test_a_batch_file_is_never_the_real_curl(tmp_path, monkeypatch):
    (tmp_path / "curl.cmd").write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    assert curlshim.find_real_curl() is None


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlinked_real_curl_is_found(tmp_path, monkeypatch):
    real = fake_curl.install(tmp_path / "real")
    links = tmp_path / "links"
    links.mkdir()
    (links / "curl").symlink_to(real)
    monkeypatch.setenv("PATH", str(links))
    assert curlshim.find_real_curl() == str(links / "curl")


def test_the_real_curl_runs_with_the_recursion_marker(fake):
    fake.main(["https://example.com/"])
    assert fake.runs()[-1]["env"] == {"AGENTS_CURL_SHIM": "1"}


def test_a_launcher_started_by_the_real_curl_serves_with_curlike(fake, monkeypatch, capsysbinary):
    """A "real curl" that is a wrapper calling this launcher again ends in
    curlike, not in a chain of launchers."""
    monkeypatch.setenv("AGENTS_CURL_SHIM", "1")
    assert fake.main(["--help"]) == 0
    assert fake.runs() == [] and b"Pure Python curl-like tool" in capsysbinary.readouterr().out


# --------------------------------------------------------------------------
# the agent proxy, handed to the real curl
# --------------------------------------------------------------------------


def test_the_agent_proxys_credential_goes_in_a_config_file_not_argv(fake, monkeypatch):
    _agent_proxy(monkeypatch)
    assert fake.main(["-s", "https://api.example.com/"]) == 0
    run = fake.runs()[-1]
    assert fake.argv() == ["--proxy", "http://proxy.example:3128", "-K", "<K>", "-s", "https://api.example.com/"]
    assert BEARER not in " ".join(run["argv"])
    assert run["config"] == ['proxy-header = "Proxy-Authorization: %s"\n' % BEARER]
    assert not os.path.exists(run["argv"][3]), "the config file is removed afterwards"


def test_the_configured_header_name_and_quotes_are_kept(fake, monkeypatch):
    _agent_proxy(monkeypatch, auth='Token a"b\\c')
    monkeypatch.setenv("AGENTS_SYSHTTP_PROXY_AUTH_HEADER", "X-Proxy-Token")
    fake.main(["https://api.example.com/"])
    assert fake.runs()[-1]["config"] == ['proxy-header = "X-Proxy-Token: Token a\\"b\\\\c"\n']


def test_url_userinfo_becomes_a_proxy_user_config_line(fake, monkeypatch):
    monkeypatch.setenv("AGENTS_SYSHTTP_PROXY", "http://agent:s3cret@proxy.example:3128")
    fake.main(["https://api.example.com/"])
    run = fake.runs()[-1]
    assert fake.argv()[:4] == ["--proxy", "http://proxy.example:3128", "-K", "<K>"]
    assert "s3cret" not in " ".join(run["argv"]) and run["config"] == ['proxy-user = "agent:s3cret"\n']


@pytest.mark.parametrize("lead", ["-q", "--disable"])
def test_a_leading_q_stays_first(lead, fake, monkeypatch):
    _agent_proxy(monkeypatch, auth=None)
    fake.main([lead, "https://api.example.com/"])
    assert fake.argv() == [lead, "--proxy", "http://proxy.example:3128", "https://api.example.com/"]
    fake.main(["-s", "-q", "https://api.example.com/"])
    assert fake.argv() == ["--proxy", "http://proxy.example:3128", "-s", "-q", "https://api.example.com/"]


def test_a_q_cluster_ending_in_a_value_option_keeps_its_value(fake, monkeypatch):
    _agent_proxy(monkeypatch, auth=None)
    fake.main(["-qso", "out.txt", "https://api.example.com/"])
    assert fake.argv() == ["-q", "--proxy", "http://proxy.example:3128", "-so", "out.txt", "https://api.example.com/"]


def test_every_next_group_gets_the_proxy(fake, monkeypatch):
    _agent_proxy(monkeypatch)
    fake.main(["https://a.example/", "--next", "-s", "https://b.example/"])
    assert fake.argv() == [
        "--proxy",
        "http://proxy.example:3128",
        "-K",
        "<K>",
        "https://a.example/",
        "--next",
        "--proxy",
        "http://proxy.example:3128",
        "-K",
        "<K>",
        "-s",
        "https://b.example/",
    ]
    assert len(fake.runs()[-1]["config"]) == 2


@pytest.mark.parametrize(
    "steer",
    [
        ["-x", "http://other:1"],
        ["--proxy=http://other:1"],
        ["-sx", "http://other:1"],
        ["--noproxy", "*"],
        ["--proxy1.0", "other:1"],
        ["--preproxy", "socks5://s:1"],
        ["-K", "mine.curlrc"],
        ["--config", "mine.curlrc"],
        ["-sK", "mine.curlrc"],
        ["--expand-proxy", "http://other:1"],
        ["--expand-preproxy", "socks5://s:1"],
    ],
)
def test_a_group_that_steers_its_proxy_gets_nothing_injected(steer, fake, monkeypatch):
    _agent_proxy(monkeypatch)
    fake.main([*steer, "https://api.example.com/"])
    assert fake.argv() == [*steer, "https://api.example.com/"]
    assert fake.runs()[-1]["config"] == [] or steer[0] in ("-K", "--config", "-sK")


def test_an_option_value_is_not_steering(fake, monkeypatch):
    _agent_proxy(monkeypatch, auth=None)
    fake.main(["-d", "--proxy=trap", "https://api.example.com/"])
    assert fake.argv()[:2] == ["--proxy", "http://proxy.example:3128"]


@pytest.mark.parametrize(
    "argv",
    [
        ["-H", "--next", "-x", "http://other:1", "https://api.example.com/"],
        ["--header", "--next", "-x", "http://other:1", "https://api.example.com/"],
        ["-o", "-:", "-x", "http://other:1", "https://api.example.com/"],
        ["-sH", "-:", "-x", "http://other:1", "https://api.example.com/"],
        ["--expand-header", "--next", "-x", "http://other:1", "https://api.example.com/"],
    ],
)
def test_a_next_that_is_an_option_value_starts_no_group(argv, fake, monkeypatch):
    """Split there, the part before would get the agent proxy and its
    credential, and curl would send both to the caller's later -x."""
    _agent_proxy(monkeypatch)
    fake.main(argv)
    assert fake.argv() == argv and fake.runs()[-1]["config"] == []


def test_a_short_cluster_holding_a_colon_starts_a_group(fake, monkeypatch):
    _agent_proxy(monkeypatch, auth=None)
    fake.main(["https://a.example/", "-s:", "https://b.example/"])
    proxy = ["--proxy", "http://proxy.example:3128"]
    assert fake.argv() == [*proxy, "https://a.example/", "-s:", *proxy, "https://b.example/"]


def test_after_a_double_dash_nothing_starts_a_group(fake, monkeypatch):
    _agent_proxy(monkeypatch, auth=None)
    argv = ["--", "https://a.example/", "--next", "https://b.example/"]
    fake.main(argv)
    assert fake.argv() == ["--proxy", "http://proxy.example:3128", *argv]


def test_the_variables_are_curls_own(fake, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://global.example:3128")
    fake.main(["https://api.example.com/"])
    assert fake.argv() == ["https://api.example.com/"]


def test_the_machines_unprefixed_settings_are_not_the_agents(fake, monkeypatch):
    monkeypatch.setenv("SYSHTTP_PROXY", "http://machine.example:3128")
    monkeypatch.setenv("SYSHTTP_PROXY_AUTH", BEARER)
    fake.main(["https://api.example.com/"])
    assert fake.argv() == ["https://api.example.com/"] and fake.runs()[-1]["config"] == []


def test_an_agent_proxy_variable_wins_over_curls_own(fake, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://global.example:3128")
    monkeypatch.setenv("AGENTS_HTTPS_PROXY", "http://agents.example:3128")
    monkeypatch.setenv("AGENTS_HTTP_PROXY_AUTH", BEARER)
    fake.main(["https://api.example.com/"])
    assert fake.argv() == ["--proxy", "http://agents.example:3128", "-K", "<K>", "https://api.example.com/"]
    assert fake.runs()[-1]["config"] == ['proxy-header = "Proxy-Authorization: %s"\n' % BEARER]


def test_the_agents_no_proxy_keeps_the_configured_proxy_off_a_group(fake, monkeypatch):
    """curl reads no AGENTS_NO_PROXY: the launcher applies it per group."""
    _agent_proxy(monkeypatch, auth=None)
    monkeypatch.setenv("AGENTS_NO_PROXY", "internal.example")
    fake.main(["https://api.internal.example/", "--next", "https://api.example.com/"])
    assert fake.argv() == [
        "https://api.internal.example/", "--next", "--proxy", "http://proxy.example:3128", "https://api.example.com/"
    ]


def test_the_callers_u_keeps_the_agent_proxy_without_its_credential(fake, monkeypatch):
    _agent_proxy(monkeypatch)
    fake.main(["-U", "me:pw", "https://api.example.com/"])
    assert fake.argv() == ["--proxy", "http://proxy.example:3128", "-U", "me:pw", "https://api.example.com/"]


@pytest.mark.parametrize("setting", [("AGENTS_SYSHTTP_PROXY", "http://[bad:3128"), ("AGENTS_SYSHTTP_PROXY_TYPE", "socks5")])
def test_a_malformed_agent_proxy_runs_the_real_curl_as_typed(setting, fake, monkeypatch, capsysbinary):
    _agent_proxy(monkeypatch)
    monkeypatch.setenv(*setting)
    assert fake.main(["https://api.example.com/"]) == 0
    assert fake.argv() == ["https://api.example.com/"]
    err = capsysbinary.readouterr().err
    assert err.startswith(b"curl: ignoring the configured proxy:") and b"Traceback" not in err


def test_a_prefix_gateway_is_served_by_curlike(fake, gateway, monkeypatch, capsysbinary):
    monkeypatch.setenv("AGENTS_SYSHTTP_PROXY", gateway)
    monkeypatch.setenv("AGENTS_SYSHTTP_PROXY_TYPE", "prefix:/fetch/")
    monkeypatch.setenv("AGENTS_SYSHTTP_PROXY_AUTH", BEARER)
    target = "http://origin.example/x"
    assert fake.main(["-s", target]) == 0
    assert fake.runs() == [] and capsysbinary.readouterr().out == b"via-gateway"
    assert _Gateway.seen[-1] == ("/fetch/" + target, BEARER)
    fake.main(["-x", "http://mine:1", "https://api.example.com/"])
    assert fake.argv() == ["-x", "http://mine:1", "https://api.example.com/"]


# --------------------------------------------------------------------------
# the system's proxy, handed to the real curl
# --------------------------------------------------------------------------


def test_the_systems_proxy_and_the_machine_credential(fake, monkeypatch):
    system = _FakeSystem("PROXY pac.example:3128")
    monkeypatch.setattr(agent_proxy, "system_map", lambda: system)
    monkeypatch.setenv("AGENTS_HTTP_PROXY_AUTH", BEARER)
    assert fake.main(["-s", "https://api.example.com/x"]) == 0
    assert fake.argv() == ["--proxy", "http://pac.example:3128", "-K", "<K>", "-s", "https://api.example.com/x"]
    assert fake.runs()[-1]["config"] == ['proxy-header = "Proxy-Authorization: %s"\n' % BEARER]
    assert system.asked == ["https://api.example.com/x"]


def test_each_next_group_asks_the_system_for_its_own_url(fake, monkeypatch):
    system = _FakeSystem("PROXY pac.example:3128")
    monkeypatch.setattr(agent_proxy, "system_map", lambda: system)
    fake.main(["https://a.example/", "--next", "https://b.example/"])
    assert system.asked == ["https://a.example/", "https://b.example/"]


def test_a_direct_answer_leaves_the_real_curl_alone(fake, monkeypatch):
    monkeypatch.setattr(agent_proxy, "system_map", lambda: _FakeSystem(None))
    fake.main(["-s", "https://api.example.com/"])
    assert fake.argv() == ["-s", "https://api.example.com/"]


def test_the_first_system_proxy_that_answers_is_handed_over(fake, monkeypatch):
    live = socket.socket()
    live.bind(("127.0.0.1", 0))
    live.listen(4)
    try:
        port = live.getsockname()[1]
        route = "PROXY 127.0.0.1:%d; PROXY 127.0.0.1:%d" % (_dead_port(), port)
        monkeypatch.setattr(agent_proxy, "system_map", lambda: _FakeSystem(route))
        assert fake.main(["-s", "https://api.example.com/x"]) == 0
        assert fake.argv()[:2] == ["--proxy", "http://127.0.0.1:%d" % port]
    finally:
        live.close()


def test_exit_7_when_no_system_proxy_answers(fake, monkeypatch, capsys):
    first, second = _dead_port(), _dead_port()
    route = "PROXY 127.0.0.1:%d; PROXY 127.0.0.1:%d" % (first, second)
    monkeypatch.setattr(agent_proxy, "system_map", lambda: _FakeSystem(route))
    assert fake.main(["-s", "https://api.example.com/x"]) == 7
    assert fake.runs() == [], "real curl is not run without a route"
    assert capsys.readouterr().err.startswith(
        "curl: (7) no proxy of the list is reachable: 127.0.0.1:%d, 127.0.0.1:%d" % (first, second)
    )


# --------------------------------------------------------------------------
# <P>_CURL
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["0", "n", "No", "false", "OFF"])
def test_curl_off_runs_the_real_curl_as_typed(value, fake, monkeypatch):
    _agent_proxy(monkeypatch)
    monkeypatch.setenv("AGENTS_SYSHTTP_HOOKS_ALL", ".")
    monkeypatch.setenv("AGENTS_SYSHTTP_HOOKS_ALL_CURL", "never-run-me")
    monkeypatch.setenv("AGENTS_CURL", value)
    assert fake.main(["-s", "https://api.example.com/"]) == 0
    assert fake.argv() == ["-s", "https://api.example.com/"]


@pytest.mark.parametrize("value", ["1", "y", "yes", "true", "on"])
def test_curl_on_values(value, monkeypatch):
    monkeypatch.setenv("AGENTS_CURL", value)
    assert curlshim.shim_enabled()


def test_an_unknown_curl_value_is_exit_2(fake, monkeypatch, capsys):
    monkeypatch.setenv("AGENTS_CURL", "disable")
    assert fake.main(["https://api.example.com/"]) == 2
    assert capsys.readouterr().err.startswith("curl: (2) AGENTS_CURL=disable") and fake.runs() == []


def test_curl_off_without_a_real_curl_is_exit_2(tmp_path, monkeypatch, capsysbinary):
    monkeypatch.setenv("AGENTS_CURL", "0")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert curlshim.main(["-s", "https://api.example.com/"], entry=str(tmp_path / "curl")) == 2
    assert capsysbinary.readouterr().err.startswith(b"curl: (2) AGENTS_CURL=0 asks for the real curl")


# --------------------------------------------------------------------------
# URL hooks: a _CURL wrapper in curl's place
# --------------------------------------------------------------------------

WRAPPER = """
import json, os, sys
from pathlib import Path
Path(os.environ["WRAPPER_LOG"]).write_text(json.dumps({
    "argv": sys.argv[1:], "curl": os.environ.get("AGENTS_SYSHTTP_HOOK_CURL"), "skip": os.environ.get("AGENTS_SYSHTTP_HOOK_SKIP"),
    "url": os.environ.get("AGENTS_SYSHTTP_HOOK_URL"), "key": os.environ.get("AGENTS_SYSHTTP_HOOK_KEY"),
}))
"""


def test_a_curl_hook_runs_the_wrapper_in_curls_place(fake, tmp_path, monkeypatch):
    wrapper = tmp_path / "wrap.py"
    wrapper.write_text(WRAPPER, encoding="utf-8")
    log = tmp_path / "log.json"
    monkeypatch.setenv("WRAPPER_LOG", str(log))
    monkeypatch.setenv("AGENTS_SYSHTTP_HOOKS_W", r"^https://api\.example\.com/hooked")
    monkeypatch.setenv("AGENTS_SYSHTTP_HOOKS_W_CURL", "'%s' fetch --" % wrapper)
    monkeypatch.setenv("AGENTS_PYTHON", sys.executable)
    assert fake.main(["-s", "https://api.example.com/hooked?a=1&calc"]) == 0
    seen = json.loads(log.read_text(encoding="utf-8"))
    assert seen["argv"] == ["fetch", "--", "-s", "https://api.example.com/hooked?a=1&calc"]
    assert seen["curl"] == fake.entry and seen["skip"] == "W" and seen["key"] == "W"
    assert fake.runs() == [], "the real curl is not run"


def test_a_wrapper_that_cannot_start_is_exit_2(fake, monkeypatch, capsys):
    monkeypatch.setenv("AGENTS_SYSHTTP_HOOKS_W", ".")
    monkeypatch.setenv("AGENTS_SYSHTTP_HOOKS_W_CURL", "no-such-wrapper-program")
    assert fake.main(["https://api.example.com/"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("curl: (2) AGENTS_SYSHTTP_HOOKS_W_CURL: cannot run") and "Traceback" not in err


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe")
@pytest.mark.parametrize("suffix", [".cmd", ".bat"])
def test_a_batch_wrapper_is_refused_on_windows(suffix, fake, tmp_path, monkeypatch, capsys):
    marker = tmp_path / "ran"
    wrapper = tmp_path / ("wrap" + suffix)
    wrapper.write_text("@echo off\necho ran > \"%s\"\n" % marker, encoding="utf-8")
    monkeypatch.setenv("AGENTS_SYSHTTP_HOOKS_W", ".")
    monkeypatch.setenv("AGENTS_SYSHTTP_HOOKS_W_CURL", str(wrapper))
    assert fake.main(["https://api.example.com/?x=1&calc"]) == 2
    assert "cannot take a URL safely" in capsys.readouterr().err and not marker.exists()


# --------------------------------------------------------------------------
# <P>_CURL_SSL_REVOKE: a Schannel curl's revocation check
# --------------------------------------------------------------------------


@pytest.fixture()
def schannel(monkeypatch):
    import curlike

    monkeypatch.setattr(curlshim.os, "name", "nt")
    monkeypatch.setattr(curlike, "describe_curl", lambda path: ((8, 21, 0), True))


@pytest.mark.skipif(os.name != "nt", reason="the fake curl is a Windows executable here")
def test_schannel_gets_best_effort_by_default(schannel, fake):
    assert fake.main(["-qs", "https://example.com/"]) == 0
    assert fake.argv() == ["-q", "--ssl-revoke-best-effort", "-s", "https://example.com/"], "after a leading -q"


@pytest.mark.skipif(os.name != "nt", reason="the fake curl is a Windows executable here")
@pytest.mark.parametrize(
    "value,added", [("false", ["--ssl-no-revoke"]), ("true", []), ("BEST-EFFORT", ["--ssl-revoke-best-effort"])]
)
def test_the_revoke_setting(schannel, fake, monkeypatch, value, added):
    monkeypatch.setenv("AGENTS_CURL_SSL_REVOKE", value)
    fake.main(["-s", "https://example.com/"])
    assert fake.argv() == added + ["-s", "https://example.com/"]


@pytest.mark.skipif(os.name != "nt", reason="the fake curl is a Windows executable here")
def test_the_revoke_flag_is_in_every_next_group(schannel, fake):
    fake.main(["https://a.example/", "--next", "https://b.example/"])
    assert fake.argv() == [
        "--ssl-revoke-best-effort",
        "https://a.example/",
        "--next",
        "--ssl-revoke-best-effort",
        "https://b.example/",
    ]


@pytest.mark.skipif(os.name != "nt", reason="the fake curl is a Windows executable here")
def test_the_callers_own_revoke_flag_wins(schannel, fake):
    fake.main(["--ssl-no-revoke", "https://example.com/"])
    assert fake.argv() == ["--ssl-no-revoke", "https://example.com/"]


@pytest.mark.skipif(os.name != "nt", reason="the fake curl is a Windows executable here")
@pytest.mark.parametrize("described", [((8, 21, 0), False), ((7, 69, 1), True), (None, True)])
def test_only_a_schannel_curl_that_has_the_flag(schannel, fake, monkeypatch, described):
    import curlike

    monkeypatch.setattr(curlike, "describe_curl", lambda path: described)
    fake.main(["-s", "https://example.com/"])
    assert fake.argv() == ["-s", "https://example.com/"]


def test_never_off_windows(monkeypatch):
    import curlike

    monkeypatch.setattr(curlike, "describe_curl", lambda path: ((8, 21, 0), True))
    monkeypatch.setattr(curlshim.os, "name", "posix")
    assert curlshim.revoke_argv("/usr/bin/curl", ["-s", "https://example.com/"]) == []


def test_an_unknown_revoke_value_is_exit_2(fake, monkeypatch, capsys):
    monkeypatch.setenv("AGENTS_CURL_SSL_REVOKE", "maybe")
    assert fake.main(["-s", "https://example.com/"]) == 2
    assert "AGENTS_CURL_SSL_REVOKE=maybe" in capsys.readouterr().err
