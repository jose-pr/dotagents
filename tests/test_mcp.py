"""The dotagents MCP server and command surface.

`env` prints resolved, possibly secret values (its own docstring's "Output is
sensitive" note) -- exactly what an MCP client should never be handed as a
callable tool. It is a compiled, module-level command CLASS (`Env`, one of
`dotagents.cli._BUILTIN_COMMANDS`, dispatched via `duho.app`'s `commands=`),
not a discovered `.py` module command, so the opt-out is the class-attribute
form: `_mcp_ = False` set directly on `Env` (`dotagents/cli/env.py`) -- NOT a
module-level `_mcp_ = False` in a `cmds/` file, which is the form for a
discovered module command instead.

The subprocess tests use isolated project directories and local MCP peers; they
do not contact a remote service.
"""

import json
import http.server
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from dotagents.cli.env import Env


def test_env_command_is_hidden_from_mcp_tools():
    requests = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ]
    env = dict(os.environ)
    env["DOTAGENTS_MCP"] = "stdio"

    proc = subprocess.run(
        [sys.executable, "-m", "dotagents"],
        input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr

    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 2, proc.stdout
    response = json.loads(lines[-1])
    names = {tool["name"] for tool in response["result"]["tools"]}

    assert "dotagents.about" in names, names
    assert "dotagents.env" not in names, names
    assert not any(name.startswith("dotagents.env.") for name in names), names
    assert "dotagents.mcp" not in names
    assert "dotagents.mcp.refresh" in names


def test_env_still_works_on_the_cli(capsys):
    """The MCP opt-out is MCP-only: `env` dispatches normally on the CLI."""
    command = Env()
    command.format = "json"

    assert command() == 0
    assert isinstance(json.loads(capsys.readouterr().out), dict)


def _serve(requests, cwd, *args):
    proc = subprocess.run(
        [sys.executable, "-m", "dotagents", "mcp", *args],
        input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=_mcp_environment(cwd),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return {
        response["id"]: response
        for response in map(json.loads, filter(str.strip, proc.stdout.splitlines()))
    }


def _initialize(protocol):
    return {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": protocol, "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    }}


def test_mcp_gateway_answers_bad_calls_as_duho_does(tmp_path):
    def call(req_id, name, arguments):
        return {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}}

    list_tools = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    # Before `initialize`, a request is refused.
    early = _serve([list_tools], tmp_path)
    assert early[1]["error"]["code"] == -32002

    # No `notifications/initialized` is sent: the `initialize` exchange suffices.
    older = _serve([
        _initialize("2025-06-18"),
        list_tools,
        call(2, "dotagents.no-such-tool", {}),
        call(3, "dotagents.about", {"no_such_field": True}),
    ], tmp_path)
    assert "dotagents.about" in {tool["name"] for tool in older[1]["result"]["tools"]}
    assert older[2]["error"]["code"] == -32602
    assert older[3]["error"]["code"] == -32602

    newer = _serve([
        _initialize("2025-11-25"),
        call(2, "dotagents.no-such-tool", {}),
        call(3, "dotagents.about", {"no_such_field": True}),
    ], tmp_path)
    assert newer[2]["error"]["code"] == -32602
    assert newer[3]["result"]["isError"] is True


def _peer_script():
    return (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        " request=json.loads(line)\n"
        " if 'id' not in request: continue\n"
        " method=request['method']\n"
        " if method == 'initialize':\n"
        "  result={'protocolVersion':request['params']['protocolVersion'],'capabilities':{},'serverInfo':{'name':'peer','version':'1'}}\n"
        " elif method == 'tools/list':\n"
        "  result={'tools':[{'name':'echo','description':'Echo input','inputSchema':{'type':'object','properties':{},'additionalProperties':False}}]}\n"
        " elif method == 'tools/call':\n"
        "  result={'content':[{'type':'text','text':'ok'}]}\n"
        " else: result={}\n"
        " print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)\n"
    )


def _write_overlay(root, skill):
    overlay = root / ".agents" / "overlays" / "sample"
    (overlay / "skills" / skill).mkdir(parents=True, exist_ok=True)
    (overlay / "skills" / skill / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    manifest = (
        "[mcp]\n\n"
        "[[mcp.servers]]\n"
        "name = \"builder\"\n"
        "transport = \"stdio\"\n"
        "command = %s\n"
        "args = [%s, %s]\n\n"
        "[[mcp.skills]]\n"
        "name = %s\n"
        "server = \"builder\"\n"
    ) % (
        json.dumps(sys.executable),
        json.dumps("-c"),
        json.dumps(_peer_script()),
        json.dumps(skill),
    )
    (overlay / "overlay.toml").write_bytes(manifest.encode("utf-8"))


def _mcp_environment(tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env.pop("AGENTS_PROJECT_ROOT", None)
    env.pop("CLAUDE_PROJECT_DIR", None)
    return env


class _McpHTTPHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        if request.get("method") == "initialize":
            result = {
                "protocolVersion": request["params"]["protocolVersion"],
                "capabilities": {},
                "serverInfo": {"name": "peer", "version": "1"},
            }
        elif request.get("method") == "notifications/initialized":
            self.send_response(202)
            self.send_header("Mcp-Session-Id", "local-test")
            self.end_headers()
            return
        elif request.get("method") == "tools/list":
            result = {"tools": [{
                "name": "echo", "description": "Echo input",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            }]}
        elif request.get("method") == "tools/call":
            result = {"content": [{"type": "text", "text": "http-ok"}]}
        else:
            result = {}
        body = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Mcp-Session-Id", "local-test")
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format, *args):
        return


def test_mcp_gateway_forwards_to_streamable_http_peer(tmp_path):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _McpHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    overlay = tmp_path / ".agents" / "overlays" / "sample"
    overlay.mkdir(parents=True)
    (overlay / "overlay.toml").write_text(
        "[mcp]\n\n[[mcp.servers]]\nname = \"builder\"\n"
        "transport = \"streamable-http\"\nurl = \"http://127.0.0.1:%d/mcp\"\n" % server.server_port,
        encoding="utf-8",
    )
    requests = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "builder.echo", "arguments": {},
        }},
    ]
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "dotagents", "mcp"],
            input="".join(json.dumps(request) + "\n" for request in requests),
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=_mcp_environment(tmp_path),
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        responses = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        assert len(responses) == 3, proc.stdout
        names = {tool["name"] for tool in responses[1]["result"]["tools"]}
        assert "builder.echo" in names
        assert responses[2]["result"]["content"][0]["text"] == "http-ok"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_mcp_command_prefixes_skill_tools_and_keeps_the_stdio_mode(tmp_path):
    _write_overlay(tmp_path, "code-search")
    requests = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "dotagents.code-search.echo", "arguments": {},
        }},
    ]
    proc = subprocess.run(
        [sys.executable, "-m", "dotagents", "mcp", "--prefix", "dotagents.{name}"],
        input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_mcp_environment(tmp_path),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    responses = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert len(responses) == 3, proc.stdout
    names = {tool["name"] for tool in responses[1]["result"]["tools"]}
    assert "dotagents.code-search.echo" in names
    assert "builder.echo" not in names
    assert responses[2]["result"]["content"][0]["text"] == "ok"


def test_mcp_refresh_reloads_skills_and_notifies_the_client(tmp_path):
    _write_overlay(tmp_path, "code-search")
    proc = subprocess.Popen(
        [sys.executable, "-m", "dotagents", "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=tmp_path,
        env=_mcp_environment(tmp_path),
    )
    messages = queue.Queue()

    def read_stdout():
        assert proc.stdout is not None
        for line in proc.stdout:
            messages.put(line)
        messages.put(None)

    threading.Thread(target=read_stdout, daemon=True).start()

    def send(request):
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()

    def read_message():
        try:
            line = messages.get(timeout=15)
        except queue.Empty:
            raise AssertionError("dotagents MCP server did not answer") from None
        if line is None:
            raise AssertionError("dotagents MCP server closed stdout")
        return json.loads(line)

    try:
        send({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        }})
        assert read_message()["id"] == 0
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        first_list = read_message()
        assert "code-search.echo" in {tool["name"] for tool in first_list["result"]["tools"]}

        _write_overlay(tmp_path, "lookup")
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "dotagents.mcp.refresh", "arguments": {},
        }})
        assert read_message()["method"] == "notifications/tools/list_changed"
        assert read_message()["id"] == 2
        send({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        refreshed = read_message()
        names = {tool["name"] for tool in refreshed["result"]["tools"]}
        assert "lookup.echo" in names
        assert "code-search.echo" not in names
    finally:
        if proc.stdin is not None:
            proc.stdin.close()
        proc.wait(timeout=15)
        if proc.stderr is not None:
            proc.stderr.read()
