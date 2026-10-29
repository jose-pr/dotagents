"""Read MCP server and skill declarations from overlay manifests."""

from __future__ import annotations

import math
import re
import urllib.parse
from pathlib import Path

from dotagents._overlays import _toml_module

_MAX_LINE = 16 * 1024 * 1024
_PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
_HTTP_PROTOCOLS = _PROTOCOLS[:-1]
_TOOL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


class GatewayConfigError(ValueError):
    """An overlay's MCP section is invalid."""


class _ServerSpec:
    __slots__ = (
        "overlay", "name", "transport", "command", "args", "url", "env",
        "env_passthrough", "headers_env", "timeout", "skills",
    )

    def __init__(self, overlay, data):
        self.overlay = overlay
        self.name = data["name"]
        self.transport = data["transport"]
        self.command = data.get("command")
        self.args = data.get("args", [])
        self.url = data.get("url")
        self.env = data.get("env", {})
        self.env_passthrough = data.get("env_passthrough", [])
        self.headers_env = data.get("headers_env", {})
        self.timeout = float(data.get("timeout", 60))
        self.skills = []

    def prefix(self, name=None, template=None):
        namespace = name or self.name
        return (template.format(name=namespace) if template is not None else namespace) + "."


def _valid_name(value):
    return isinstance(value, str) and _TOOL_NAME.fullmatch(value) is not None


def _read_specs(overlay_name, raw, origin):
    if not isinstance(raw, dict):
        raise GatewayConfigError("mcp in %s must be a table" % origin)
    unknown = set(raw) - {"servers", "skills"}
    if unknown:
        raise GatewayConfigError("unknown mcp setting %r in %s" % (sorted(unknown)[0], origin))
    raw_servers = raw.get("servers", [])
    raw_skills = raw.get("skills", [])
    if not isinstance(raw_servers, list) or not isinstance(raw_skills, list):
        raise GatewayConfigError("mcp.servers and mcp.skills in %s must be arrays of tables" % origin)
    servers = {}
    for item in raw_servers:
        if not isinstance(item, dict):
            raise GatewayConfigError("each mcp.servers entry in %s must be a table" % origin)
        name = item.get("name")
        if not _valid_name(name) or name in servers:
            raise GatewayConfigError("mcp server names in %s must be unique tool-name segments" % origin)
        transport = item.get("transport")
        if transport not in ("stdio", "streamable-http"):
            raise GatewayConfigError("mcp server %s transport must be stdio or streamable-http" % name)
        common = {"name", "transport", "timeout"}
        if transport == "stdio":
            allowed = common | {"command", "args", "env", "env_passthrough"}
            command = item.get("command")
            args = item.get("args", [])
            env = item.get("env", {})
            env_passthrough = item.get("env_passthrough", [])
            if not isinstance(command, str) or not command.strip():
                raise GatewayConfigError("stdio MCP server %s needs a command" % name)
            if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
                raise GatewayConfigError("stdio MCP server %s args must be strings" % name)
            if not isinstance(env, dict) or any(
                not isinstance(var, str) or not _ENV_NAME.fullmatch(var)
                or not isinstance(value, str) for var, value in env.items()
            ):
                raise GatewayConfigError("stdio MCP server %s env must map names to string values" % name)
            if not isinstance(env_passthrough, list) or any(
                not isinstance(var, str) or not _ENV_NAME.fullmatch(var) for var in env_passthrough
            ) or len(set(env_passthrough)) != len(env_passthrough):
                raise GatewayConfigError("stdio MCP server %s env_passthrough must list unique variable names" % name)
        else:
            allowed = common | {"url", "headers_env"}
            url = item.get("url")
            try:
                parsed = urllib.parse.urlsplit(url) if isinstance(url, str) else None
            except ValueError:
                parsed = None
            if parsed is None or parsed.scheme not in ("https", "http") or not parsed.netloc:
                raise GatewayConfigError("HTTP MCP server %s needs an absolute HTTP(S) url" % name)
            if parsed.username or parsed.password or parsed.fragment:
                raise GatewayConfigError("HTTP MCP server %s url cannot include credentials or a fragment" % name)
            headers_env = item.get("headers_env", {})
            if not isinstance(headers_env, dict) or any(
                not isinstance(key, str) or not _HEADER_NAME.fullmatch(key)
                or not isinstance(var, str) or not _ENV_NAME.fullmatch(var)
                for key, var in headers_env.items()
            ):
                raise GatewayConfigError("HTTP MCP server %s headers_env maps header names to env names" % name)
        unknown = set(item) - allowed
        if unknown:
            raise GatewayConfigError("unknown setting %r for MCP server %s" % (sorted(unknown)[0], name))
        timeout = item.get("timeout", 60)
        try:
            valid_timeout = (
                not isinstance(timeout, bool)
                and isinstance(timeout, (int, float))
                and math.isfinite(float(timeout))
                and timeout > 0
            )
        except (OverflowError, ValueError):
            valid_timeout = False
        if not valid_timeout:
            raise GatewayConfigError("MCP server %s timeout must be a positive number of seconds" % name)
        servers[name] = _ServerSpec(overlay_name, item)

    used_skills = set()
    for item in raw_skills:
        if not isinstance(item, dict) or set(item) != {"name", "server"}:
            raise GatewayConfigError("each mcp.skills entry in %s needs name and server" % origin)
        name, server_name = item["name"], item["server"]
        if not _valid_name(name) or name in used_skills:
            raise GatewayConfigError("mcp skill names in %s must be unique tool-name segments" % origin)
        if server_name not in servers:
            raise GatewayConfigError("MCP skill %s refers to an undeclared server" % name)
        used_skills.add(name)
        servers[server_name].skills.append(name)
    return list(servers.values())


def discover(scope):
    """Resolve MCP servers and skill links for the active overlays."""
    specs = []
    for overlay_name, path, root in scope.paths({"overlay": "overlay.toml"}):
        if root is None or not path.is_file():
            continue
        toml = _toml_module()
        if toml is None:
            raise GatewayConfigError("MCP declarations require tomli below Python 3.11")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise GatewayConfigError(
                "cannot read MCP declaration %s (%s)" % (path, type(exc).__name__)
            ) from None
        try:
            manifest = toml.loads(text)
        except ValueError:
            raise GatewayConfigError("invalid TOML in MCP declaration %s" % path) from None
        raw = manifest.get("mcp", {})
        for spec in _read_specs(overlay_name, raw, path):
            for skill in spec.skills:
                if not (root / "skills" / skill / "SKILL.md").is_file():
                    raise GatewayConfigError(
                        "MCP skill %s has no SKILL.md in overlay %s" % (skill, overlay_name)
                    )
            specs.append(spec)
    return specs
