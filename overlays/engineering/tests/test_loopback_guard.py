"""references/loopback_guard: with its directory first on PYTHONPATH, a Python
run reaches loopback and nothing else, and the switch turns it off."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

GUARD = Path(__file__).resolve().parents[1] / "references" / "loopback_guard"

PROBE = r"""
import socket, sys
server = socket.socket(); server.bind(("127.0.0.1", 0)); server.listen(1)
client = socket.create_connection(server.getsockname(), timeout=5)
print("loopback ok")
for what, call in (
    ("connect", lambda: socket.socket().connect(("192.0.2.1", 9))),
    ("lookup", lambda: socket.getaddrinfo("example.invalid", 80)),
    ("udp", lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", ("192.0.2.1", 9))),
):
    try:
        call()
        print(what, "allowed")
    except PermissionError as exc:
        print(what, "refused", "loopback guard" in str(exc))
"""


def run(**env_extra):
    env = dict(os.environ, PYTHONPATH=str(GUARD), **env_extra)
    return subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True,
                          env=env, timeout=60)


def test_only_loopback_is_reached():
    out = run().stdout.splitlines()
    assert out == ["loopback ok", "connect refused True", "lookup refused True",
                   "udp refused True"], out


def test_the_switch_turns_it_off():
    env = dict(os.environ, PYTHONPATH=str(GUARD), AGENTS_LOOPBACK_GUARD="off")
    out = subprocess.run([sys.executable, "-c", "import socket; print(hasattr(socket, 'LOOPBACK_GUARD'))"],
                         capture_output=True, text=True, env=env, timeout=60).stdout.strip()
    assert out == "False"
