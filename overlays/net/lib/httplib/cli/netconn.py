"""Where connections go: ``-4`` / ``-6``, ``--resolve``, ``--connect-to``
and ``--unix-socket``.

The first three steer name resolution only -- ``socket.getaddrinfo`` is
wrapped for the length of the transfer -- so the request keeps the host the
caller named: its ``Host`` header, TLS server name and certificate check are
the URL's, as in curl. ``--unix-socket`` replaces the TCP connection with one
to a Unix domain socket (the Docker API's ``/var/run/docker.sock``)."""
import contextlib
import http.client
import socket
import urllib.request


def parse_resolve(entry):
    """``HOST:PORT:ADDR[,ADDR...]`` (curl's ``--resolve``) ->
    ``((host, port), [addr, ...])``; an IPv6 address may be bracketed."""
    parts = entry.split(':', 2)  # only the addresses may hold colons (IPv6)
    if len(parts) != 3 or not parts[0] or not parts[1].isdigit() or not parts[2]:
        raise ValueError('--resolve: expected HOST:PORT:ADDR[,ADDR], got %r' % entry)
    host, port, addrs = parts
    return (host.lower(), int(port)), [a.strip().strip('[]') for a in addrs.split(',') if a.strip()]


def parse_connect_to(entry):
    """``HOST1:PORT1:HOST2:PORT2`` (curl's ``--connect-to``; any part may be
    empty: empty HOST1/PORT1 match anything, empty HOST2/PORT2 keep the
    original) -> ``((host1, port1), (host2, port2))``."""
    parts = entry.split(':')
    if len(parts) != 4:
        raise ValueError('--connect-to: expected HOST1:PORT1:HOST2:PORT2, got %r' % entry)
    h1, p1, h2, p2 = parts
    return (h1.lower(), int(p1) if p1 else None), (h2, int(p2) if p2 else None)


@contextlib.contextmanager
def resolving(family=0, resolve=(), connect_to=()):
    """``socket.getaddrinfo`` as ``-4`` / ``-6`` (``family``), ``--resolve``
    and ``--connect-to`` ask, restored afterwards."""
    table = dict(parse_resolve(e) for e in resolve)
    remaps = [parse_connect_to(e) for e in connect_to]
    if not (family or table or remaps):
        yield
        return
    real = socket.getaddrinfo

    def getaddrinfo(host, port, fam=0, type=0, proto=0, flags=0):
        name = host.decode('idna') if isinstance(host, bytes) else (host or '')
        number = int(port) if isinstance(port, (int, str)) and str(port).isdigit() else port
        for (h1, p1), (h2, p2) in remaps:
            if h1 in ('', name.lower()) and p1 in (None, number):
                name, number = h2 or name, p2 or number
                break
        wanted = family or fam
        fixed = table.get((name.lower(), number))
        if fixed:
            out = []
            for addr in fixed:
                v6 = ':' in addr
                if wanted and wanted != (socket.AF_INET6 if v6 else socket.AF_INET):
                    continue
                sockaddr = (addr, number, 0, 0) if v6 else (addr, number)
                out.append((socket.AF_INET6 if v6 else socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', sockaddr))
            if out:
                return out
        return real(name, number, wanted, type, proto, flags)

    socket.getaddrinfo = getaddrinfo
    try:
        yield
    finally:
        socket.getaddrinfo = real


def unix_socket_handlers(path, context):
    """urllib handlers whose connections go to the Unix socket ``path``
    (TLS on top for https, with the URL's host as the server name)."""
    if not hasattr(socket, 'AF_UNIX'):
        raise NotImplementedError('--unix-socket: this Python has no AF_UNIX sockets')

    def _unix(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if self.timeout is not None and self.timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
            sock.settimeout(self.timeout)
        sock.connect(path)
        return sock

    class UnixHTTPConnection(http.client.HTTPConnection):
        def connect(self):
            self.sock = _unix(self)

    class UnixHTTPSConnection(http.client.HTTPSConnection):
        def connect(self):
            self.sock = self._context.wrap_socket(_unix(self), server_hostname=self.host)

    class UnixHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(UnixHTTPConnection, req)

    class UnixHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(UnixHTTPSConnection, req, context=context)

    return [UnixHTTPHandler(), UnixHTTPSHandler(context=context)]
