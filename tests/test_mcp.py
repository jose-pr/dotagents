"""`env`'s MCP opt-out (duho >= 0.6.0).

`env` prints resolved, possibly secret values (its own docstring's "Output is
sensitive" note) -- exactly what an MCP client should never be handed as a
callable tool. It is a compiled, module-level command CLASS (`Env`, one of
`dotagents.cli._BUILTIN_COMMANDS`, dispatched via `duho.app`'s `commands=`),
not a discovered `.py` module command, so the opt-out is the class-attribute
form: `_mcp_ = False` set directly on `Env` (`dotagents/cli/env.py`) -- NOT a
module-level `_mcp_ = False` in a `cmds/` file, which is the form for a
discovered module command instead.

`test_env_command_is_hidden_from_mcp_tools` drives a REAL
`DOTAGENTS_MCP=stdio` session (the env-var trigger duho.app checks before
parsing argv) in a subprocess, under the suite's autouse isolated
HOME/AGENTS_* (tests/conftest.py) -- never the real `~/.agents`. It asserts
`dotagents.env` is absent from `tools/list` while `dotagents.about` (an
ordinary built-in) is still present, then that `env` still works as a normal
CLI command -- the opt-out only removes it from the MCP surface.
"""

import json
import os
import subprocess
import sys

from dotagents.cli.env import Env


def test_env_command_is_hidden_from_mcp_tools():
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    env = dict(os.environ)
    env["DOTAGENTS_MCP"] = "stdio"

    proc = subprocess.run(
        [sys.executable, "-m", "dotagents"],
        input=request + "\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr

    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, proc.stdout
    response = json.loads(lines[0])
    names = {tool["name"] for tool in response["result"]["tools"]}

    assert "dotagents.about" in names, names
    assert "dotagents.env" not in names, names
    assert not any(name.startswith("dotagents.env.") for name in names), names


def test_env_still_works_on_the_cli(capsys):
    """The MCP opt-out is MCP-only: `env` dispatches normally on the CLI."""
    command = Env()
    command.format = "json"

    assert command() == 0
    assert isinstance(json.loads(capsys.readouterr().out), dict)
