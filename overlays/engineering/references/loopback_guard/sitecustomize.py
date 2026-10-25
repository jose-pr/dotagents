"""Refuse network I/O to anything but loopback, for an agent's probe and test runs.

Put this directory first on ``PYTHONPATH``; Python imports ``sitecustomize`` at
start-up, so every interpreter the run starts is guarded, subprocesses included:

    PYTHONPATH=$ENGINEERING_OVERLAY_ROOT/references/loopback_guard python probe.py

A connect, connect_ex or sendto to an address that is not loopback, and a name
lookup for a host that is not ``localhost``, raise ``PermissionError`` before any
packet leaves. Unix sockets are untouched. ``AGENTS_LOOPBACK_GUARD=off`` disables
it. This file shadows any other ``sitecustomize`` while it is first on the path.
"""

import ipaddress
import os
import socket

_OFF = ("off", "0", "false", "no")


def _is_loopback(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    if host.lower() in ("localhost", "localhost.", ""):
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _check(address) -> None:
    if isinstance(address, tuple) and address and not _is_loopback(address[0]):
        raise PermissionError("loopback guard: refused network I/O to %r "
                              "(AGENTS_LOOPBACK_GUARD=off to allow)" % (address[0],))


def _install() -> None:
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_sendto = socket.socket.sendto
    real_getaddrinfo = socket.getaddrinfo

    def connect(self, address):
        _check(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        _check(address)
        return real_connect_ex(self, address)

    def sendto(self, data, *args):
        _check(args[-1] if args else None)
        return real_sendto(self, data, *args)

    def getaddrinfo(host, *args, **kwargs):
        if host is not None and not _is_loopback(host):
            raise PermissionError("loopback guard: refused a name lookup for %r "
                                  "(AGENTS_LOOPBACK_GUARD=off to allow)" % (host,))
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.sendto = sendto
    socket.getaddrinfo = getaddrinfo
    socket.LOOPBACK_GUARD = True


if os.environ.get("AGENTS_LOOPBACK_GUARD", "").strip().lower() not in _OFF:
    _install()
