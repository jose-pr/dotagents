"""Forward the tools of overlay-declared MCP servers through duho's MCP server."""

from __future__ import annotations

import logging

from dotagents._mcp_gateway_clients import (
    _HTTPClient,
    _StdioClient,
    _prefix_tools,
    _server_error,
)
from dotagents._mcp_gateway_config import GatewayConfigError, discover  # noqa: F401  (re-exported)

_LOG = logging.getLogger("dotagents.mcp")
_REFRESH_TOOL = "dotagents.mcp.refresh"
_REFRESH_SPEC = {
    "name": _REFRESH_TOOL,
    "description": "Reload active overlay MCP declarations and reconnect their servers.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}


def _text_result(text, is_error=False):
    result = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


class Gateway:
    """A `duho.mcp.ToolProvider` over the servers `load_specs()` declares.

    Servers start on the first `list_tools`; one that fails to start, or a tool
    whose name is already taken, is left out with a warning.
    """

    def __init__(self, notify, load_specs, name_prefix=None):
        self._notify = notify
        self._load_specs = load_specs
        self._name_prefix = name_prefix
        self._specs = load_specs()
        self._clients = []
        self._tool_map = {}
        self._tools = []
        self._connected = False

    def _connect(self):
        self._connected = True
        for spec in self._specs:
            client = None
            try:
                client = _StdioClient(spec) if spec.transport == "stdio" else _HTTPClient(spec)
                client.start()
                candidates = [
                    pair
                    for expose_as in spec.skills or [spec.name]
                    for pair in _prefix_tools(
                        spec, client, spec.prefix(expose_as, self._name_prefix)
                    )
                ]
            except Exception as exc:
                if client is not None:
                    client.close()
                reason = _server_error(exc, spec)[:500]
                _LOG.warning("MCP server %s.%s unavailable (%s)", spec.overlay, spec.name, reason)
                continue
            for tool, remote_name in candidates:
                name = tool["name"]
                if name == _REFRESH_TOOL or name in self._tool_map:
                    _LOG.warning("MCP server %s.%s has a colliding tool name", spec.overlay, spec.name)
                    continue
                self._tool_map[name] = (client, remote_name)
                self._tools.append(tool)
            self._clients.append(client)

    def list_tools(self):
        if not self._connected:
            self._connect()
        return [_REFRESH_SPEC] + self._tools

    def call_tool(self, name, arguments):
        if name == _REFRESH_TOOL:
            return self._refresh()
        target = self._tool_map.get(name)
        if target is None:
            return None
        client, remote_name = target
        try:
            value = client.call(remote_name, arguments)
        except Exception as exc:
            return _text_result(_server_error(exc, client.spec), is_error=True)
        if not isinstance(value, dict) or "content" not in value:
            return _text_result("tool returned an invalid result", is_error=True)
        return value

    def _refresh(self):
        try:
            specs = self._load_specs()
        except Exception as exc:
            return _text_result("Could not reload MCP configuration: %s" % exc, is_error=True)
        self.close()
        self._specs = specs
        self._notify("notifications/tools/list_changed")
        return _text_result(
            "Reloaded %d declared MCP server(s); request tools/list to reconnect them."
            % len(specs)
        )

    def close(self):
        for client in self._clients:
            client.close()
        self._clients = []
        self._tool_map = {}
        self._tools = []
        self._connected = False
