"""The pip wheel for interpreters without ensurepip (`dotagents._pipwheel`):
found in the cache, a find-links directory or the configured index (PEP 503
HTML or PEP 691 JSON), and accepted only with the pinned sha256."""
import http.server
import json
import threading

import pytest

from dotagents import _pipwheel


@pytest.fixture
def index(pip_wheel_file):
    """A one-project simple index on localhost; ``index.json = True`` serves
    PEP 691 JSON instead of PEP 503 HTML."""
    data = pip_wheel_file.read_bytes()
    state = {"json": False, "hits": []}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            state["hits"].append(self.path)
            if self.path.rstrip("/") == "/simple/pip":
                if state["json"]:
                    body = json.dumps({"files": [{"filename": _pipwheel.PIP_WHEEL,
                                                  "url": "../../files/" + _pipwheel.PIP_WHEEL}]}).encode()
                    ctype = "application/vnd.pypi.simple.v1+json"
                else:
                    body = ('<html><body><a href="../../files/%s#sha256=%s">%s</a></body></html>'
                            % (_pipwheel.PIP_WHEEL, _pipwheel.PIP_SHA256, _pipwheel.PIP_WHEEL)).encode()
                    ctype = "text/html"
            elif self.path == "/files/" + _pipwheel.PIP_WHEEL:
                body, ctype = data, "application/octet-stream"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = "http://127.0.0.1:%d/simple" % server.server_address[1]
    yield state
    server.shutdown()


def _env(**extra):
    return dict({"PIP_CONFIG_FILE": "/nonexistent/pip.conf"}, **extra)


@pytest.mark.parametrize("as_json", [False, True])
def test_the_index_supplies_the_pinned_wheel(tmp_path, index, as_json):
    index["json"] = as_json
    wheel = _pipwheel.pip_wheel(tmp_path, environ=_env(PIP_INDEX_URL=index["url"]))
    assert wheel == tmp_path / _pipwheel.PIP_WHEEL and wheel.stat().st_size > 1_000_000
    hits = len(index["hits"])
    assert _pipwheel.pip_wheel(tmp_path, environ=_env(PIP_INDEX_URL=index["url"])) == wheel
    assert len(index["hits"]) == hits, "the second call is served from the cache"


def test_the_index_comes_from_pip_config(tmp_path, index):
    conf = tmp_path / "pip.ini"
    conf.write_text("[global]\nindex-url = %s\n" % index["url"], encoding="utf-8")
    assert _pipwheel.pip_config({"PIP_CONFIG_FILE": str(conf)})["index-url"] == index["url"]
    assert _pipwheel.pip_wheel(tmp_path / "cache", environ={"PIP_CONFIG_FILE": str(conf)}).is_file()


def test_a_directory_of_wheels_needs_no_index(tmp_path, pip_wheel_file):
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    (wheels / _pipwheel.PIP_WHEEL).write_bytes(pip_wheel_file.read_bytes())
    env = _env(AGENTS_PIP_SOURCE=str(wheels), PIP_INDEX_URL="http://127.0.0.1:9/unreachable")
    assert _pipwheel.pip_wheel(tmp_path / "cache", environ=env).is_file()
    assert _pipwheel.find_links(env, cache_dir=tmp_path) == [str(wheels)]


def test_a_wheel_with_another_hash_is_refused(tmp_path, index, monkeypatch):
    monkeypatch.setattr(_pipwheel, "PIP_SHA256", "0" * 64)
    with pytest.raises(_pipwheel.PipUnavailable, match="expected 0000"):
        _pipwheel.pip_wheel(tmp_path, environ=_env(PIP_INDEX_URL=index["url"]))


def test_nothing_reachable_names_every_source(tmp_path):
    env = _env(AGENTS_PIP_SOURCE=str(tmp_path / "empty"), PIP_INDEX_URL="http://127.0.0.1:9/simple")
    with pytest.raises(_pipwheel.PipUnavailable) as caught:
        _pipwheel.pip_wheel(tmp_path / "cache", environ=env)
    message = str(caught.value.code)
    assert "empty" in message and "127.0.0.1:9" in message and "--with-pip" in message


def test_credentials_in_the_index_url_never_reach_the_message(tmp_path):
    env = _env(PIP_INDEX_URL="http://user:s3cret@127.0.0.1:9/simple")
    with pytest.raises(_pipwheel.PipUnavailable) as caught:
        _pipwheel.pip_wheel(tmp_path, environ=env)
    assert "s3cret" not in str(caught.value.code)
