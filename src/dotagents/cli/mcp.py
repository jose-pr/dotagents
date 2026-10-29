"""`dotagents mcp` -- start the MCP stdio gateway."""

from __future__ import annotations

import re
import sys
from typing import Optional

from duho import Cmd, LoggingArgs

class Mcp(LoggingArgs, Cmd):
    """Serve dotagents command tools and active overlay MCP tools over stdio."""

    _parsername_ = "mcp"
    # Starting another stdio MCP server is a CLI operation, not a tool call.
    _mcp_ = False

    prefix: "Optional[str]" = None
    "Name prefix template for forwarded tools; `{name}` is the skill or server name."
    ("--prefix",)

    def __call__(self) -> int:
        if self.prefix is not None:
            try:
                sample = self.prefix.format(name="server")
            except (AttributeError, IndexError, KeyError, ValueError):
                sys.stderr.write("dotagents mcp: --prefix may use only the {name} placeholder\n")
                return 2
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", sample):
                sys.stderr.write("dotagents mcp: --prefix must format to a valid MCP name\n")
                return 2
        from dotagents.cli import _run_mcp_gateway

        return _run_mcp_gateway(self.prefix)
