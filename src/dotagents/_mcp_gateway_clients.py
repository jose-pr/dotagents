"""MCP stdio and Streamable HTTP client transports."""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading

from dotagents import __version__
from dotagents._mcp_gateway_config import (
    _HTTP_PROTOCOLS,
    _MAX_LINE,
    _PROTOCOLS,
    _ServerSpec,
    _valid_name,
)

_BASE_ENV = ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TEMP", "TMP")
_STDERR_LINES = 20
_STDERR_LINE_BYTES = 2048
_SHUTDOWN_TIMEOUT = 1


class _StdioClient:
    def __init__(self, spec):
        self.spec = spec
        self.process = None
        self.messages = queue.Queue(maxsize=256)
        self.lock = threading.Lock()
        self.stderr_lock = threading.Lock()
        self.stderr_lines = []
        self.request_id = 0
        self.tools = []

    def start(self):
        env = {key: os.environ[key] for key in _BASE_ENV if key in os.environ}
        env.update({key: os.environ[key] for key in self.spec.env_passthrough if key in os.environ})
        env.update(self.spec.env)
        command = shutil.which(self.spec.command, path=env.get("PATH"))
        if command is None:
            raise RuntimeError(
                "MCP command %r was not found on PATH; install it or fix overlay.toml" % self.spec.command
            )
        if os.name == "nt" and command.lower().endswith((".cmd", ".bat")):
            raise RuntimeError("MCP command %r cannot use a .cmd or .bat shim" % self.spec.command)
        options = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": env,
        }
        if os.name == "nt":
            options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            options["start_new_session"] = True
        try:
            self.process = subprocess.Popen([command] + self.spec.args, **options)
        except OSError as exc:
            raise RuntimeError(
                "could not start MCP command %r (%s)" % (self.spec.command, type(exc).__name__)
            ) from None
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        try:
            result = self._request(
                "initialize",
                {"protocolVersion": _PROTOCOLS[0], "capabilities": {},
                 "clientInfo": {"name": "dotagents", "version": __version__}},
            )
            self._check_initialize(result, _PROTOCOLS)
            self._notify("notifications/initialized")
            self.tools = self._list_tools()
        except Exception as exc:
            diagnostic = self._stderr_text()
            if diagnostic:
                raise RuntimeError("%s; server stderr: %s" % (exc, diagnostic)) from None
            raise

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc_info):
        self.close()

    def _read(self):
        assert self.process is not None and self.process.stdout is not None
        try:
            while True:
                line = self.process.stdout.readline(_MAX_LINE + 1)
                if not line:
                    self.messages.put(None)
                    return
                if len(line) > _MAX_LINE or not line.endswith(b"\n"):
                    self.messages.put(ValueError("MCP response exceeded the line limit"))
                    return
                self.messages.put(line)
        except Exception as exc:
            self.messages.put(exc)

    def _read_stderr(self):
        assert self.process is not None and self.process.stderr is not None
        while True:
            line = self.process.stderr.readline(_STDERR_LINE_BYTES + 1)
            if not line:
                return
            text = line.decode("utf-8", "replace").rstrip("\r\n")[:_STDERR_LINE_BYTES]
            with self.stderr_lock:
                self.stderr_lines.append(text)
                del self.stderr_lines[:-_STDERR_LINES]

    def _stderr_text(self):
        with self.stderr_lock:
            return "\n".join(self.stderr_lines)

    def _write(self, message):
        assert self.process is not None and self.process.stdin is not None
        data = (json.dumps(message, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")
        self.process.stdin.write(data)
        self.process.stdin.flush()

    def _notify(self, method):
        self._write({"jsonrpc": "2.0", "method": method})

    def _request(self, method, params):
        with self.lock:
            self.request_id += 1
            request_id = self.request_id
            self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            while True:
                try:
                    raw = self.messages.get(timeout=self.spec.timeout)
                except queue.Empty:
                    self.close()
                    raise TimeoutError("MCP server request timed out") from None
                if raw is None:
                    raise RuntimeError("MCP server closed its output")
                if isinstance(raw, BaseException):
                    raise RuntimeError(str(raw)) from None
                try:
                    response = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    raise RuntimeError("MCP server returned invalid JSON") from None
                if not isinstance(response, dict):
                    continue
                if response.get("id") == request_id:
                    if "error" in response:
                        error = response["error"]
                        message = error.get("message") if isinstance(error, dict) else None
                        raise RuntimeError("MCP server request failed: %s" % (message or "unknown error"))
                    if "result" not in response:
                        raise RuntimeError("MCP server response has no result")
                    return response["result"]
                if response.get("method") and "id" in response:
                    self._write({
                        "jsonrpc": "2.0", "id": response["id"],
                        "error": {"code": -32601, "message": "method not supported by gateway"},
                    })

    def _list_tools(self):
        return _list_tools(self)

    @staticmethod
    def _check_initialize(result, versions):
        if not isinstance(result, dict) or result.get("protocolVersion") not in versions:
            raise RuntimeError("MCP server negotiated an unsupported protocol version")

    def call(self, name, arguments):
        return self._request("tools/call", {"name": name, "arguments": arguments})

    def close(self):
        process = self.process
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=_SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=_SHUTDOWN_TIMEOUT)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def _list_tools(client):
    tools, cursor, pages = [], None, 0
    while True:
        listing = client._request("tools/list", {"cursor": cursor} if cursor else {})
        if not isinstance(listing, dict) or not isinstance(listing.get("tools"), list):
            raise RuntimeError("MCP tools/list returned an invalid result")
        for tool in listing["tools"]:
            if not isinstance(tool, dict) or not _valid_name(tool.get("name")):
                raise RuntimeError("MCP tools/list returned an invalid tool")
            if len(tool["name"]) > 128 or any(item["name"] == tool["name"] for item in tools):
                raise RuntimeError("MCP tools/list returned an invalid or duplicate tool name")
            if not isinstance(tool.get("inputSchema"), dict):
                raise RuntimeError("MCP tools/list returned a tool without an input schema")
            tools.append(dict(tool))
            if len(tools) > 10000:
                raise RuntimeError("MCP tools/list exceeded the tool limit")
        cursor = listing.get("nextCursor")
        pages += 1
        if cursor is None:
            return tools
        if not isinstance(cursor, str) or pages >= 100:
            raise RuntimeError("MCP tools/list pagination did not finish")


class _HTTPClient:
    def __init__(self, spec):
        self.spec = spec
        self.lock = threading.Lock()
        self.request_id = 0
        self.session_id = None
        self.protocol_version = None
        self.tools = []
        self.headers = {}
        for header, var in spec.headers_env.items():
            value = os.environ.get(var)
            if value is None:
                raise RuntimeError("required HTTP header environment variable %s is unset" % var)
            self.headers[header] = value
        try:
            from syshttp.client import HTTPClient
        except ImportError:
            raise ImportError(
                "Streamable HTTP MCP requires the dotagents-cli[mcp-http] extra"
            ) from None
        self.http = HTTPClient(
            timeout=(spec.timeout, spec.timeout),
            retries=0,
            user_agent="dotagents/%s" % __version__,
        )
        self.closed = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc_info):
        self.close()

    def start(self):
        result = self._request(
            "initialize",
            {"protocolVersion": _PROTOCOLS[0], "capabilities": {},
             "clientInfo": {"name": "dotagents", "version": __version__}},
        )
        _StdioClient._check_initialize(result, _HTTP_PROTOCOLS)
        self.protocol_version = result["protocolVersion"]
        self._notify("notifications/initialized")
        self.tools = _list_tools(self)

    def _send(self, message):
        data = json.dumps(message, ensure_ascii=True, allow_nan=False).encode("utf-8")
        headers = dict(self.headers)
        headers.update({
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        })
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        try:
            with self.http.request(
                "POST",
                self.spec.url,
                data=data,
                headers=headers,
                stream=True,
                allow_redirects=False,
                timeout=(self.spec.timeout, self.spec.timeout),
            ) as response:
                self.session_id = response.headers.get("Mcp-Session-Id", self.session_id)
                if response.status == 202:
                    return None
                if response.status >= 400:
                    raise RuntimeError("HTTP MCP server returned status %d" % response.status)
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                body = response.read(_MAX_LINE + 1)
                if len(body) > _MAX_LINE:
                    raise RuntimeError("HTTP MCP response exceeded the size limit")
        except TimeoutError:
            raise TimeoutError("HTTP MCP server request timed out") from None
        except OSError as exc:
            raise RuntimeError("HTTP MCP server request failed (%s)" % type(exc).__name__) from None
        try:
            if content_type == "application/json":
                return json.loads(body.decode("utf-8"))
            if content_type == "text/event-stream":
                return self._read_sse(body)
            raise RuntimeError("HTTP MCP server returned an unsupported content type")
        except (UnicodeDecodeError, ValueError):
            raise RuntimeError("HTTP MCP server returned invalid JSON") from None

    def _read_sse(self, body):
        data = []
        for line in body.decode("utf-8").splitlines():
            if not line:
                if data:
                    message = json.loads("\n".join(data))
                    if isinstance(message, dict) and message.get("id") == self.request_id:
                        return message
                    data = []
                continue
            if line.startswith("data:"):
                data.append(line[5:].lstrip())
        raise RuntimeError("HTTP MCP event stream ended before its response")

    def _request(self, method, params):
        with self.lock:
            self.request_id += 1
            req_id = self.request_id
            response = self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            if not isinstance(response, dict) or response.get("id") != req_id:
                raise RuntimeError("HTTP MCP server returned an invalid response")
            if "error" in response:
                error = response["error"]
                message = error.get("message") if isinstance(error, dict) else None
                raise RuntimeError("HTTP MCP server request failed: %s" % (message or "unknown error"))
            if "result" not in response:
                raise RuntimeError("HTTP MCP server response has no result")
            return response["result"]

    def _notify(self, method):
        self._send({"jsonrpc": "2.0", "method": method})

    def call(self, name, arguments):
        return self._request("tools/call", {"name": name, "arguments": arguments})

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.session_id:
                headers = dict(self.headers)
                headers["Mcp-Session-Id"] = self.session_id
                if self.protocol_version:
                    headers["MCP-Protocol-Version"] = self.protocol_version
                with self.http.request(
                    "DELETE", self.spec.url, headers=headers, stream=True,
                    allow_redirects=False, timeout=(self.spec.timeout, self.spec.timeout),
                ):
                    pass
        except (OSError, TimeoutError, ValueError):
            pass
        finally:
            self.http.close()


def _prefix_tools(spec, client, prefix):
    output = []
    for tool in client.tools:
        name = tool["name"]
        exposed = prefix + name
        if len(exposed) > 128 or not _valid_name(exposed):
            raise RuntimeError("forwarded MCP tool name is invalid or exceeds 128 characters")
        item = dict(tool)
        item["name"] = exposed
        output.append((item, name))
    return output


def _server_error(exc, spec):
    message = str(exc)
    secret_names = list(spec.env_passthrough) + list(spec.headers_env.values())
    for name in secret_names:
        value = os.environ.get(name)
        if value:
            message = message.replace(value, "[redacted]")
    for value in spec.env.values():
        if value:
            message = message.replace(value, "[redacted]")
    return message
