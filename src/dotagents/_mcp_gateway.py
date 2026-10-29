"""Expose dotagents and overlay MCP tools on a shared stdio server."""

from __future__ import annotations

import json
import logging
import sys

from dotagents import __version__
from dotagents._mcp_gateway_config import (  # noqa: F401  (re-exported for the CLI)
    _MAX_LINE,
    _PROTOCOLS,
    GatewayConfigError,
    discover,
)
from dotagents._mcp_gateway_clients import (
    _HTTPClient,
    _StdioClient,
    _prefix_tools,
    _server_error,
)

_LOG = logging.getLogger("dotagents.mcp")
_REFRESH_TOOL = "dotagents.mcp.refresh"
#: From this revision on, bad arguments to a known tool are a tool result the
#: model can read, not a JSON-RPC error (as duho's own server answers).
_ARGUMENT_ERRORS_AS_RESULTS_FROM = "2025-11-25"


def _connect_specs(specs, state, name_prefix):
    for spec in specs:
        client = None
        try:
            client = _StdioClient(spec) if spec.transport == "stdio" else _HTTPClient(spec)
            client.start()
            exposures = spec.skills or [spec.name]
            candidates = [
                (tool, remote_name)
                for expose_as in exposures
                for tool, remote_name in _prefix_tools(
                    spec, client, spec.prefix(expose_as, name_prefix)
                )
            ]
        except Exception as exc:
            if client is not None:
                client.close()
            reason = _server_error(exc, spec)[:500]
            _LOG.warning("MCP server %s.%s unavailable (%s)", spec.overlay, spec.name, reason)
            continue
        for tool, remote_name in candidates:
            exposed_name = tool["name"]
            if exposed_name in state["local_names"] or exposed_name in state["tool_map"]:
                _LOG.warning("MCP server %s.%s has a colliding tool name", spec.overlay, spec.name)
                continue
            state["tool_map"][exposed_name] = (client, remote_name)
            state["tools"].append(tool)
        state["clients"].append(client)


def _error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _result(req_id, value):
    return {"jsonrpc": "2.0", "id": req_id, "result": value}


def _write_notification(stream, method):
    stream.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
    stream.flush()


def _handle(
    request, core, local_tools, specs, output_var, name_prefix,
    reload_specs, session, state,
):
    from duho import mcp

    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return _error(request.get("id") if isinstance(request, dict) else None, -32600, "invalid request")
    if "method" not in request and ("result" in request or "error" in request):
        return None
    method, req_id = request.get("method"), request.get("id")
    if not isinstance(method, str):
        return _error(req_id, -32600, "invalid request: method must be a string") if "id" in request else None
    if "id" in request and (isinstance(req_id, bool) or not isinstance(req_id, (str, int, float))):
        return _error(None, -32600, "invalid request id")
    params = request.get("params", {})
    if not isinstance(params, dict):
        return _error(req_id, -32602, "invalid params: expected an object") if "id" in request else None
    if "id" not in request:
        return None
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in _PROTOCOLS else _PROTOCOLS[0]
        session["protocolVersion"] = version
        return _result(req_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": True}},
            "serverInfo": {"name": "dotagents", "version": __version__},
        })
    if method == "ping":
        return _result(req_id, {})
    if "protocolVersion" not in session:
        return _error(req_id, -32002, "server has not been initialized")
    if method == "tools/list":
        if not state["connected"]:
            state["connected"] = True
            _connect_specs(specs, state, name_prefix)
        return _result(req_id, {"tools": local_tools + state["tools"]})
    if method != "tools/call":
        return _error(req_id, -32601, "method not found")
    name, arguments = params.get("name"), params.get("arguments", {})
    if not isinstance(name, str) or not isinstance(arguments, dict):
        return _error(req_id, -32602, "tools/call needs a name and object arguments")
    if name == _REFRESH_TOOL:
        try:
            updated_specs = reload_specs()
        except Exception as exc:
            return _result(req_id, {
                "content": [{"type": "text", "text": "Could not reload MCP configuration: %s" % exc}],
                "isError": True,
            })
        for client in state["clients"]:
            client.close()
        state["clients"] = []
        state["tools"] = []
        state["tool_map"] = {}
        specs[:] = updated_specs
        state["connected"] = False
        state["notify_tools_changed"]()
        return _result(req_id, {
            "content": [{
                "type": "text",
                "text": "Reloaded %d declared MCP server(s); request tools/list to reconnect them." % len(updated_specs),
            }],
        })
    target = state["tool_map"].get(name)
    if target is None:
        try:
            value = mcp.call_tool(core, name, arguments, output_var=output_var)
        except (mcp.UnknownToolError, mcp.InvalidArgumentsError) as exc:
            # ISO dates: string order is chronological order.
            if not (
                isinstance(exc, mcp.InvalidArgumentsError)
                and session["protocolVersion"] >= _ARGUMENT_ERRORS_AS_RESULTS_FROM
            ):
                return _error(req_id, exc.code, str(exc))
            value = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    else:
        try:
            value = target[0].call(target[1], arguments)
        except Exception as exc:
            value = {
                "content": [{"type": "text", "text": _server_error(exc, target[0].spec)}],
                "isError": True,
            }
    if not isinstance(value, dict) or "content" not in value:
        value = {"content": [{"type": "text", "text": "tool returned an invalid result"}], "isError": True}
    return _result(req_id, value)


def serve(
    core, specs, *, stdin=None, stdout=None, output_var=None, name_prefix=None,
    reload_specs=None,
):
    """Serve local and overlay-declared MCP tools on the existing stdio session."""
    from duho import mcp

    local_tools = mcp.describe_tools(core)
    stream_in = stdin if stdin is not None else sys.stdin
    stream_out = stdout if stdout is not None else sys.stdout
    local_tools = local_tools + [{
        "name": _REFRESH_TOOL,
        "description": "Reload active overlay MCP declarations and reconnect their servers.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    }]
    session = {}
    state = {
        "connected": False,
        "clients": [],
        "tools": [],
        "tool_map": {},
        "local_names": {tool.get("name") for tool in local_tools},
        "notify_tools_changed": lambda: _write_notification(
            stream_out, "notifications/tools/list_changed"
        ),
    }
    if reload_specs is None:
        reload_specs = lambda: specs

    def handle_one(request):
        try:
            return _handle(
                request, core, local_tools, specs, output_var, name_prefix,
                reload_specs, session, state,
            )
        except Exception as exc:
            req_id = request.get("id") if isinstance(request, dict) else None
            return _error(req_id, -32603, "internal error: %s" % exc)

    try:
        for raw in stream_in:
            if isinstance(raw, bytes):
                try:
                    line = raw.decode("utf-8")
                except UnicodeDecodeError:
                    stream_out.write(json.dumps(_error(None, -32700, "parse error: invalid utf-8")) + "\n")
                    stream_out.flush()
                    continue
            else:
                line = raw
            if len(line) > _MAX_LINE:
                response = _error(None, -32700, "parse error: message too large")
            else:
                try:
                    request = json.loads(line)
                except (ValueError, RecursionError):
                    response = _error(None, -32700, "parse error")
                else:
                    if isinstance(request, list):
                        responses = [handle_one(item) for item in request]
                        response = [item for item in responses if item is not None]
                        if not response:
                            continue
                    else:
                        response = handle_one(request)
                        if response is None:
                            continue
            stream_out.write(json.dumps(response, ensure_ascii=True, allow_nan=False) + "\n")
            stream_out.flush()
        return 0
    finally:
        for client in state["clients"]:
            client.close()
