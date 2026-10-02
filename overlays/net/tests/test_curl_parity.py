"""The fallback against the real curl: the same command line, the same fake
servers, compared on what a caller sees -- the exit code, the ``curl: (N)``
error code and stdout (``-w`` included).

Both sides run through the shim, ``bin/curl.py``, as a subprocess: with the
real curl's directory on PATH it passes the command through (adding the agent
proxy), with an empty PATH it serves it itself. Skipped when no real curl is
installed. The servers are raw sockets on 127.0.0.1, so each failure is
exact: a reply cut short, a reset, a proxy refusing or dropping the CONNECT.

Where curl versions disagree, the allowance names the versions measured; the
fallback answers as the older, more widely installed ones do.
"""
import base64
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import curl  # noqa: E402  (bin/, via conftest)
from test_curl_options import pki  # noqa: F401  (fixture reused)

SHIM = Path(curl.__file__).resolve()
REAL_CURL = curl.find_real_curl()
pytestmark = pytest.mark.skipif(not REAL_CURL, reason="no real curl to compare the fallback with")

BASIC = "Basic " + base64.b64encode(b"agent:s3cret").decode()
_PROXY_VARS = re.compile(r"(?i)(.*_proxy|agents_proxy.*|no_proxy|curl_ca_bundle|net_.*)$")


# --------------------------------------------------------------------------- #
# Raw servers: each handler gets the connection and the request head.
# --------------------------------------------------------------------------- #
def _read_head(conn):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(65536)
        if not chunk:
            break
        buf += chunk
    return buf


def _header(head, name):
    m = re.search(rb"(?im)^" + name.encode() + rb":\s*(.*?)\r$", head)
    return m.group(1).decode() if m else None


def _respond(conn, status, body=b"", extra=()):
    head = "HTTP/1.1 %s\r\nContent-Length: %d\r\n%s\r\n" % (status, len(body), "".join(h + "\r\n" for h in extra))
    conn.sendall(head.encode() + body)


def _reset(conn):
    """Close with a TCP RST rather than a FIN."""
    conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")


def _origin(conn, head):
    path = head.split(b" ")[1] if b" " in head else b"/"
    if path.endswith(b"/404"):
        return _respond(conn, "404 Not Found", b"nope")
    if path.endswith(b"/500"):
        return _respond(conn, "500 Internal Server Error", b"boom")
    if path.endswith(b"/loop"):
        return _respond(conn, "302 Found", extra=["Location: /loop"])
    _respond(conn, "200 OK", b"hello")


def _empty(conn, head):
    pass


def _truncated(conn, head):
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nhello")


def _not_http(conn, head):
    conn.sendall(b"garbage here\r\n\r\n")


def _bad_status(conn, head):
    conn.sendall(b"HTTP/1.1 abc OK\r\nContent-Length: 2\r\n\r\nok")


def _reset_mid_body(conn, head):
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nhel")
    time.sleep(0.2)
    _reset(conn)


def _reset_at_once(conn, head):
    _reset(conn)


def _bad_chunk(conn, head):
    conn.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\nhello\r\n0\r\n\r\n")


def _chunks_cut(conn, head):
    conn.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n")


def _silent(conn, head):
    time.sleep(4)


def _pipe(a, b):
    def forward(src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    back = threading.Thread(target=forward, args=(b, a), daemon=True)
    back.start()
    forward(a, b)
    back.join(5)


def _proxy(require=BASIC, connect_status=None, plain_status=None, drop_connect=False):
    """A forward proxy: ``require`` is the Proxy-Authorization it wants (407
    otherwise); ``connect_status`` answers every CONNECT, ``drop_connect``
    closes on it; a CONNECT it accepts is tunnelled to the target."""
    def handle(conn, head):
        method, target = head.split(b"\r\n", 1)[0].decode().split(" ")[:2]
        auth = _header(head, "Proxy-Authorization")
        if method == "CONNECT":
            if drop_connect:
                return
            if connect_status:
                return _respond(conn, connect_status, b"denied")
            if require and auth != require:
                return _respond(conn, "407 Proxy Authentication Required", b"auth", ['Proxy-Authenticate: Basic realm="x"'])
            host, port = target.rsplit(":", 1)
            try:
                upstream = socket.create_connection((host, int(port)), timeout=5)
            except OSError:
                return  # as a proxy that cannot reach the target drops the CONNECT
            conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            return _pipe(conn, upstream)
        if plain_status:
            return _respond(conn, plain_status, b"proxy-says-no")
        if require and auth != require:
            return _respond(conn, "407 Proxy Authentication Required", b"auth", ['Proxy-Authenticate: Basic realm="x"'])
        _respond(conn, "200 OK", b"via-proxy")
    return handle


class _Servers(object):
    def __init__(self):
        self.sockets = []

    def serve(self, handler, tls=None):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(50)
        self.sockets.append(listener)

        def one(conn):
            try:
                if tls is not None:
                    conn = tls.wrap_socket(conn, server_side=True)
                handler(conn, _read_head(conn))
            except (OSError, ValueError):
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

        def accept():
            while True:
                try:
                    conn, _ = listener.accept()
                except OSError:
                    return
                threading.Thread(target=one, args=(conn,), daemon=True).start()

        threading.Thread(target=accept, daemon=True).start()
        return "http://127.0.0.1:%d" % listener.getsockname()[1]

    def close(self):
        for s in self.sockets:
            s.close()


def _closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def world(request, tmp_path_factory):
    """Every server the scenarios name, by name; ``tls`` is the https
    origin (``None`` without openssl to make its certificate), ``ca`` the
    flags that trust it."""
    servers = _Servers()
    w = {name: servers.serve(handler) for name, handler in (
        ("origin", _origin), ("empty", _empty), ("truncated", _truncated), ("not_http", _not_http),
        ("bad_status", _bad_status), ("reset_mid_body", _reset_mid_body), ("reset_at_once", _reset_at_once),
        ("bad_chunk", _bad_chunk), ("chunks_cut", _chunks_cut), ("silent", _silent),
        ("proxy", _proxy()), ("open_proxy", _proxy(require=None)),
        ("proxy_403", _proxy(connect_status="403 Forbidden")),
        ("proxy_502", _proxy(connect_status="502 Bad Gateway", plain_status="502 Bad Gateway")),
        ("proxy_drops", _proxy(drop_connect=True)),
    )}
    w["dead"] = "http://127.0.0.1:%d" % _closed_port()
    w["tmp"] = tmp_path_factory.mktemp("parity")
    try:
        certs = request.getfixturevalue("pki")
    except pytest.skip.Exception:
        certs = None
    if certs is not None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(certs / "server.pem"), str(certs / "server.key"))
        w["tls"] = servers.serve(_origin, tls=context).replace("http://", "https://")
        w["ca"] = ["--cacert", str(certs / "ca.pem")]
    else:
        w["tls"], w["ca"] = None, []
    yield w
    servers.close()


# --------------------------------------------------------------------------- #
# Running both sides.
# --------------------------------------------------------------------------- #
def _schannel():
    try:
        out = subprocess.run([REAL_CURL, "-V"], capture_output=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return b"Schannel" in out


SCHANNEL = bool(REAL_CURL) and _schannel()
EMPTY_PATH = None


def _run(argv, env, real):
    """``(exit code, curl: (N) code or None, stdout)`` from the shim."""
    global EMPTY_PATH
    if EMPTY_PATH is None:
        import tempfile
        EMPTY_PATH = tempfile.mkdtemp()
    e = {k: v for k, v in os.environ.items() if not _PROXY_VARS.match(k)}
    e.update(env)
    e["PATH"] = (os.path.dirname(REAL_CURL) + os.pathsep + os.environ.get("PATH", "")) if real else EMPTY_PATH
    if real and SCHANNEL:
        # Schannel cannot check revocation for a private test CA: a
        # certificate the CA signs would fail as "revocation status unknown".
        argv = ["--ssl-no-revoke"] + argv
    try:
        p = subprocess.run([sys.executable, str(SHIM)] + argv, capture_output=True, env=e, timeout=30,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "timeout", None, b"", b""
    m = re.search(rb"curl: \((\d+)\)", p.stderr)
    return p.returncode, int(m.group(1)) if m else None, p.stdout, p.stderr


# --------------------------------------------------------------------------- #
# Scenarios: (id, argv from the world, env, needs the TLS origin, allowed).
# ``allowed`` maps a real exit code to the fallback's where curl versions
# disagree: the comparison accepts the fallback's code, in stdout too.
# --------------------------------------------------------------------------- #
#: curl 8.21 (Windows, Schannel) says 7 for a CONNECT the proxy refuses;
#: 8.18 and earlier (8.18 measured on Linux, OpenSSL) say 56.
CONNECT_REFUSED = {7: 56}

S = lambda w: (w["tls"] or "https://127.0.0.1:1") + "/"  # noqa: E731
P = lambda w, name: w[name]  # noqa: E731
X = "http://example.invalid/"

SCENARIOS = [
    # Responses: a status is not an error unless -f.
    ("200", lambda w: [w["origin"] + "/"], {}, False, {}),
    ("404", lambda w: [w["origin"] + "/404"], {}, False, {}),
    ("404 -f", lambda w: ["-f", w["origin"] + "/404"], {}, False, {}),
    ("500 --fail-with-body", lambda w: ["--fail-with-body", w["origin"] + "/500"], {}, False, {}),
    ("redirect loop", lambda w: ["-L", "--max-redirs", "2", w["origin"] + "/loop"], {}, False, {}),
    # Connecting.
    ("refused", lambda w: [w["dead"] + "/"], {}, False, {}),
    ("no such host", lambda w: ["http://nonexistent.invalid/"], {}, False, {}),
    ("timeout", lambda w: ["-m", "1", w["silent"] + "/"], {}, False, {}),
    # The reply.
    ("empty reply", lambda w: [w["empty"] + "/"], {}, False, {}),
    ("not HTTP", lambda w: [w["not_http"] + "/"], {}, False, {}),
    ("bad status line", lambda w: [w["bad_status"] + "/"], {}, False, {}),
    ("reset before reply", lambda w: [w["reset_at_once"] + "/"], {}, False, {}),
    ("truncated body", lambda w: [w["truncated"] + "/"], {}, False, {}),
    ("truncated body -s", lambda w: ["-s", w["truncated"] + "/"], {}, False, {}),
    ("reset mid-body", lambda w: [w["reset_mid_body"] + "/"], {}, False, {}),
    ("bad chunk", lambda w: [w["bad_chunk"] + "/"], {}, False, {}),
    ("chunks cut", lambda w: [w["chunks_cut"] + "/"], {}, False, {}),
    ("-w on empty reply", lambda w: ["-w", "[%{http_code} %{exitcode}]", w["empty"] + "/"], {}, False, {}),
    ("-w on truncated", lambda w: ["-w", "[%{http_code} %{exitcode} %{size_download}]", w["truncated"] + "/"],
     {}, False, {}),
    # The URL and local files.
    ("malformed URL", lambda w: ["-w", "[%{exitcode}]", "http://[::1/"], {}, False, {}),
    ("malformed URL -g", lambda w: ["-g", "-w", "[%{exitcode}]", "http://[::1/"], {}, False, {}),
    ("IPv6 host", lambda w: ["-w", "[%{exitcode}]", "http://[::1]:%d/" % _closed_port()], {}, False, {}),
    ("glob: unmatched brace", lambda w: ["-w", "[%{exitcode}]", w["origin"] + "/{a,b"], {}, False, {}),
    ("glob: unmatched close", lambda w: ["-w", "[%{exitcode}]", w["origin"] + "/a]"], {}, False, {}),
    ("glob: bad range", lambda w: ["-w", "[%{exitcode}]", w["origin"] + "/[a"], {}, False, {}),
    ("glob off", lambda w: ["-g", w["origin"] + "/{a,b}"], {}, False, {}),
    ("port out of range", lambda w: ["-w", "[%{exitcode}]", "http://127.0.0.1:99999/"], {}, False, {}),
    ("unknown scheme", lambda w: ["-w", "[%{exitcode}]", "foo://127.0.0.1/"], {}, False, {}),
    ("no URL", lambda w: [], {}, False, {}),
    ("-o into a missing dir", lambda w: ["-o", str(w["tmp"] / "no" / "x"), w["origin"] + "/"], {}, False, {}),
    ("-T missing", lambda w: ["-T", str(w["tmp"] / "missing.bin"), w["origin"] + "/"], {}, False, {}),
    ("-d @missing", lambda w: ["-d", "@" + str(w["tmp"] / "missing.bin"), w["origin"] + "/"], {}, False, {}),
    ("-F @missing", lambda w: ["-F", "f=@" + str(w["tmp"] / "missing.bin"), w["origin"] + "/"], {}, False, {}),
    ("-H @missing", lambda w: ["-H", "@" + str(w["tmp"] / "missing.txt"), w["origin"] + "/"], {}, False, {}),
    ("-w @missing", lambda w: ["-w", "@" + str(w["tmp"] / "missing.txt"), w["origin"] + "/"], {}, False, {}),
    # TLS.
    ("TLS trusted", lambda w: w["ca"] + [S(w)], {}, True, {}),
    ("TLS untrusted", lambda w: [S(w)], {}, True, {}),
    ("TLS -k", lambda w: ["-k", S(w)], {}, True, {}),
    # A proxy, plain http: its answer is the response.
    ("proxy credential", lambda w: ["-x", P(w, "proxy"), "-U", "agent:s3cret", X], {}, False, {}),
    ("proxy credential in its URL", lambda w: ["-x", P(w, "proxy").replace("//", "//agent:s3cret@"), X],
     {}, False, {}),
    ("proxy 407", lambda w: ["-x", P(w, "proxy"), X], {}, False, {}),
    ("proxy 407 -f", lambda w: ["-f", "-x", P(w, "proxy"), X], {}, False, {}),
    ("proxy wrong credential", lambda w: ["-x", P(w, "proxy"), "-U", "agent:bad", X], {}, False, {}),
    ("proxy 502 -f", lambda w: ["-f", "-x", P(w, "proxy_502"), X], {}, False, {}),
    ("proxy refused", lambda w: ["-x", P(w, "dead"), X], {}, False, {}),
    ("proxy not resolvable", lambda w: ["-x", "http://nonexistent.invalid:3128", X], {}, False, {}),
    ("proxy empty reply", lambda w: ["-x", P(w, "empty"), X], {}, False, {}),
    ("proxy URL malformed", lambda w: ["-w", "[%{exitcode}]", "-x", "http://not a url", X], {}, False, {}),
    ("proxy port out of range", lambda w: ["-x", "http://127.0.0.1:99999", X], {}, False, {}),
    # A proxy, https: the CONNECT.
    ("CONNECT refused", lambda w: ["-x", P(w, "dead"), S(w)], {}, False, {}),
    ("CONNECT proxy not resolvable", lambda w: ["-x", "http://nonexistent.invalid:3128", S(w)], {}, False, {}),
    ("CONNECT 407", lambda w: w["ca"] + ["-x", P(w, "proxy"), S(w)], {}, False, CONNECT_REFUSED),
    ("CONNECT 407 -f", lambda w: w["ca"] + ["-f", "-x", P(w, "proxy"), S(w)], {}, False, {}),
    ("CONNECT 407 -w", lambda w: w["ca"] + ["-w", "[%{http_code} %{exitcode}]", "-x", P(w, "proxy"), S(w)],
     {}, False, CONNECT_REFUSED),
    ("CONNECT wrong credential", lambda w: w["ca"] + ["-x", P(w, "proxy"), "-U", "agent:bad", S(w)],
     {}, False, CONNECT_REFUSED),
    ("CONNECT 403", lambda w: w["ca"] + ["-x", P(w, "proxy_403"), S(w)], {}, False, CONNECT_REFUSED),
    ("CONNECT 403 -f", lambda w: w["ca"] + ["-f", "-x", P(w, "proxy_403"), S(w)], {}, False, {}),
    ("CONNECT 502", lambda w: w["ca"] + ["-x", P(w, "proxy_502"), S(w)], {}, False, CONNECT_REFUSED),
    ("CONNECT dropped", lambda w: w["ca"] + ["-x", P(w, "proxy_drops"), S(w)], {}, False, {}),
    ("CONNECT empty reply", lambda w: w["ca"] + ["-x", P(w, "empty"), S(w)], {}, False, {}),
    ("CONNECT target unreachable", lambda w: ["-x", P(w, "open_proxy"), "https://127.0.0.1:%d/" % _closed_port()],
     {}, False, {}),
    ("CONNECT tunnelled", lambda w: w["ca"] + ["-x", P(w, "proxy"), "-U", "agent:s3cret", S(w)], {}, True, {}),
    ("CONNECT tunnelled, untrusted", lambda w: ["-x", P(w, "proxy"), "-U", "agent:s3cret", S(w)], {}, True, {}),
    # The agent proxy (AGENTS_PROXY): the shim hands it to real curl as
    # --proxy / --proxy-header; the fallback applies it itself.
    ("agent proxy", lambda w: [X], {"AGENTS_PROXY": "@proxy", "AGENTS_PROXY_AUTH": BASIC}, False, {}),
    ("agent proxy, wrong credential", lambda w: [X], {"AGENTS_PROXY": "@proxy", "AGENTS_PROXY_AUTH": "Basic eA=="},
     False, {}),
    ("agent proxy, wrong credential -f", lambda w: ["-f", X],
     {"AGENTS_PROXY": "@proxy", "AGENTS_PROXY_AUTH": "Basic eA=="}, False, {}),
    ("agent proxy, no credential", lambda w: [X], {"AGENTS_PROXY": "@proxy"}, False, {}),
    ("agent proxy, userinfo credential", lambda w: [X], {"AGENTS_PROXY": "@proxy+userinfo"}, False, {}),
    ("agent proxy refused", lambda w: [X], {"AGENTS_PROXY": "@dead"}, False, {}),
    ("agent proxy not resolvable", lambda w: [X], {"AGENTS_PROXY": "http://nonexistent.invalid:3128"}, False, {}),
    ("agent proxy malformed", lambda w: [X], {"AGENTS_PROXY": "not a url"}, False, {}),
    ("agent proxy CONNECT 407", lambda w: w["ca"] + [S(w)], {"AGENTS_PROXY": "@proxy", "AGENTS_PROXY_AUTH": "Basic eA=="},
     False, CONNECT_REFUSED),
    ("agent proxy CONNECT 407 -f", lambda w: w["ca"] + ["-f", S(w)],
     {"AGENTS_PROXY": "@proxy", "AGENTS_PROXY_AUTH": "Basic eA=="}, False, {}),
    ("agent proxy bearer CONNECT 407", lambda w: w["ca"] + [S(w)], {"AGENTS_PROXY": "@proxy", "AGENTS_PROXY_AUTH": "Bearer t"},
     False, CONNECT_REFUSED),
    ("agent proxy CONNECT tunnelled", lambda w: w["ca"] + [S(w)], {"AGENTS_PROXY": "@proxy", "AGENTS_PROXY_AUTH": BASIC},
     True, {}),
    ("agent proxy dropped CONNECT", lambda w: w["ca"] + [S(w)], {"AGENTS_PROXY": "@proxy_drops"}, False, {}),
]


def _env(world, env):
    out = {}
    for key, value in env.items():
        if value == "@proxy+userinfo":
            value = world["proxy"].replace("//", "//agent:s3cret@")
        elif value.startswith("@"):
            value = world[value[1:]]
        out[key] = value
    return out


@pytest.mark.parametrize("name,argv,env,tls,allowed", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_the_fallback_answers_as_curl_does(world, name, argv, env, tls, allowed):
    if tls and not world["tls"]:
        pytest.skip("no openssl to make the TLS origin's certificate with")
    args = ["-sS"] + argv(world)
    if args[1:2] == ["-s"]:
        args = args[1:]  # the scenario asked for -s alone
    environment = _env(world, env)
    real = _run(args, environment, real=True)
    fallback = _run(args, environment, real=False)
    code, err_code, out = real[:3]
    if code in allowed:
        # A version difference: what the fallback answers instead.
        out = out.replace(b"%d]" % code, b"%d]" % allowed[code])
        code = allowed[code]
        err_code = code if err_code is not None else None
    assert (fallback[0], fallback[1], fallback[2]) == (code, err_code, out), (
        "%s\n real curl: rc=%s stderr=%r stdout=%r\n fallback:  rc=%s stderr=%r stdout=%r"
        % (name, real[0], real[3][:300], real[2][:120], fallback[0], fallback[3][-300:], fallback[2][:120]))


def test_a_url_glob_is_refused_not_sent_as_typed(world):
    """curl makes one transfer per expansion of ``{a,b}`` / ``[1-3]``; the
    fallback makes one, so it refuses the pattern (exit 2) instead of sending
    the braces literally. ``-g`` sends them as typed, in both."""
    for url in ("/{a,b}", "/file[1-3].txt", "/x[a-c:2]"):
        rc, code, out, err = _run(["-sS", world["origin"] + url], {}, real=False)
        assert rc == 2 and b"pass -g" in err, err
