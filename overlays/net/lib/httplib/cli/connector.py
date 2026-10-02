"""How each connection is made: its source address and port
(``--interface``, ``--local-port``), the headers its proxy CONNECT carries
(the proxy credential, and ``-p``'s CONNECT for http too), and the server key
it must present (``--pinnedpubkey``) -- one connection class per request,
built by :class:`Connector`, opened by :class:`ConnectHTTPHandler` /
:class:`ConnectHTTPSHandler` in place of urllib's own."""
import base64
import errno
import hashlib
import http.client
import ipaddress
import socket
import struct
import urllib.parse
import urllib.request

from .errors import EXIT_USAGE, EarlyExit, LocalError

EXIT_INTERFACE = 45
EXIT_PINNED = 90
EXIT_PROXY = 97

#: socket.create_connection's "no timeout given" marker (private to socket).
_DEFAULT_TIMEOUT = getattr(socket, "_GLOBAL_DEFAULT_TIMEOUT", None)

#: Proxy URL schemes that are SOCKS, by the protocol each speaks: curl's
#: ``socks://`` is SOCKS4; ``socks5`` resolves the name locally, ``socks5h``
#: and ``socks4a`` let the proxy resolve it.
SOCKS_SCHEMES = {'socks': 'socks4', 'socks4': 'socks4', 'socks4a': 'socks4a', 'socks5': 'socks5',
                 'socks5h': 'socks5h'}


class Socks(object):
    """One SOCKS proxy: ``kind`` (``socks4``/``socks4a``/``socks5``/``socks5h``),
    ``host``, ``port`` (1080 by default) and the ``user``/``password`` SOCKS5
    sends (SOCKS4 sends the user as its id)."""

    def __init__(self, url, user=None, password=None):
        parts = urllib.parse.urlsplit(url if '://' in url else 'socks4://' + url)
        self.kind = SOCKS_SCHEMES[parts.scheme.lower()]
        self.host = parts.hostname
        self.port = parts.port or 1080
        if user is None and parts.username is not None:
            user = urllib.parse.unquote(parts.username)
            password = urllib.parse.unquote(parts.password or '')
        self.user, self.password = user, password

    @staticmethod
    def is_socks(url):
        return bool(url) and url.split('://', 1)[0].lower() in SOCKS_SCHEMES and '://' in url


def _exact(sock, count):
    data = b''
    while len(data) < count:
        piece = sock.recv(count - len(data))
        if not piece:
            raise ConnectionResetError('Connection was reset')
        data += piece
    return data


def _local_address(socks, host, port, family=0):
    """``host`` resolved here, as socks4 / socks5 ask; not resolvable: curl's
    6, or 97 before 8.20 (``compat``)."""
    try:
        return socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)[0][4][0]
    except socket.gaierror:
        from .compat import socks_unresolved

        raise LocalError(*socks_unresolved(host, socks.host))


def socks_handshake(sock, socks, host, port):
    """Ask ``socks`` (connected as ``sock``) for ``host``:``port``. A refusal,
    an auth failure or a broken handshake is curl's 97."""
    try:
        if socks.kind in ('socks4', 'socks4a'):
            _socks4(sock, socks, host, port)
        else:
            _socks5(sock, socks, host, port)
    except OSError as exc:
        raise LocalError(EXIT_PROXY, 'Recv failure: %s' % (getattr(exc, 'strerror', None) or exc))


def _socks4(sock, socks, host, port):
    user = (socks.user or '').encode('utf-8') + b'\x00'
    try:
        address = socket.inet_aton(str(ipaddress.IPv4Address(host)))
        name = b''
    except ValueError:
        if socks.kind == 'socks4a':
            address, name = b'\x00\x00\x00\x01', host.encode('idna') + b'\x00'
        else:
            address, name = socket.inet_aton(_local_address(socks, host, port, socket.AF_INET)), b''
    sock.sendall(struct.pack('!BBH', 4, 1, port) + address + user + name)
    reply = _exact(sock, 8)
    if reply[1] != 0x5a:
        bound = '%s:%d' % (socket.inet_ntoa(reply[4:8]), struct.unpack('!H', reply[2:4])[0])
        raise LocalError(EXIT_PROXY, '[SOCKS] cannot complete SOCKS4 connection to %s. (%d), request rejected or '
                                     'failed.' % (bound, reply[1]))


def _socks5(sock, socks, host, port):
    methods = b'\x00\x02' if socks.user is not None else b'\x00'
    sock.sendall(b'\x05' + bytes([len(methods)]) + methods)
    chosen = _exact(sock, 2)[1]
    if chosen == 0x02:
        user, password = (socks.user or '').encode('utf-8'), (socks.password or '').encode('utf-8')
        sock.sendall(b'\x01' + bytes([len(user)]) + user + bytes([len(password)]) + password)
        status = _exact(sock, 2)
        if status[1] != 0:
            raise LocalError(EXIT_PROXY, 'User was rejected by the SOCKS5 server (%d %d).' % (status[0], status[1]))
    elif chosen != 0x00:
        raise LocalError(EXIT_PROXY, 'No authentication method was acceptable.')
    try:
        literal = ipaddress.ip_address(host.strip('[]'))
    except ValueError:
        literal = None
    if literal is None and socks.kind == 'socks5':
        literal = ipaddress.ip_address(_local_address(socks, host, port))
    if literal is None:
        name = host.encode('idna')
        target = b'\x03' + bytes([len(name)]) + name
    elif literal.version == 4:
        target = b'\x01' + literal.packed
    else:
        target = b'\x04' + literal.packed
    sock.sendall(b'\x05\x01\x00' + target + struct.pack('!H', port))
    head = _exact(sock, 4)
    if head[1] != 0:
        raise LocalError(EXIT_PROXY, 'cannot complete SOCKS5 connection to %s. (%d)' % (host, head[1]))
    kind = head[3]
    _exact(sock, {1: 4, 4: 16}.get(kind, 0) or _exact(sock, 1)[0])
    _exact(sock, 2)

#: A bind that failed because of the address or port, not the peer: the next
#: port of a --local-port range is tried.
_BIND_ERRNOS = {errno.EADDRINUSE, errno.EADDRNOTAVAIL, errno.EACCES, 10013, 10048, 10049}


def parse_local_port(spec):
    """``N`` or ``N-M`` (curl's ``--local-port``) -> ``(low, high)``."""
    low, _, high = (spec or '').partition('-')
    try:
        low, high = int(low), int(high or low)
    except ValueError:
        low = high = -1
    if not 0 < low <= high <= 65535:
        raise EarlyExit(EXIT_USAGE, "option --local-port: is badly used here\n"
                                    "curl: try 'curl --help' or 'curl --manual' for more information")
    return low, high


def _interface_address(name):
    """The first address (IPv4 first) of the interface ``name``, via netimps
    when it is installed; ``None`` otherwise."""
    try:
        import netimps
    except ImportError:
        return None
    for iface in netimps.get_interfaces():
        if iface.name == name:
            addresses = [ipaddress.ip_address(str(getattr(ip, 'ip', ip))) for ip in iface.ips]
            addresses.sort(key=lambda a: a.version)
            if addresses:
                return str(addresses[0])
    return None


def resolve_interface(spec):
    """The source address ``--interface`` names: an IP address, an interface
    (``if!NAME``, or a bare name that is one), or a host name (``host!NAME``,
    or a bare name that is not an interface). Unusable: curl's 45."""
    kind, name = None, spec
    for prefix in ('if!', 'host!'):
        if spec.startswith(prefix):
            kind, name = prefix[:-1], spec[len(prefix):]
    try:
        return str(ipaddress.ip_address(name.strip('[]')))
    except ValueError:
        pass
    if kind in (None, 'if'):
        found = _interface_address(name)
        if found:
            return found
        if kind == 'if':
            raise LocalError(EXIT_INTERFACE, "Couldn't bind to interface '%s'" % name)
    try:
        return socket.getaddrinfo(name, None, 0, socket.SOCK_STREAM)[0][4][0]
    except socket.gaierror:
        raise LocalError(EXIT_INTERFACE, "Couldn't bind to '%s'" % name)


def _tlv(der, pos):
    """``(content start, content end)`` of the DER element at ``pos``."""
    length = der[pos + 1]
    start = pos + 2
    if length & 0x80:
        count = length & 0x7f
        length = int.from_bytes(der[start:start + count], 'big')
        start += count
    return start, start + length


def subject_public_key_info(cert_der):
    """The DER ``SubjectPublicKeyInfo`` of an X.509 certificate -- what curl's
    ``--pinnedpubkey`` hashes -- found by walking the TBSCertificate: an
    optional ``[0]`` version, then serial, signature, issuer, validity,
    subject, and the key."""
    start, _ = _tlv(cert_der, 0)              # Certificate
    pos, _ = _tlv(cert_der, start)            # TBSCertificate's content
    if cert_der[pos] == 0xa0:                 # [0] version
        pos = _tlv(cert_der, pos)[1]
    for _ in range(5):                        # serial .. subject
        pos = _tlv(cert_der, pos)[1]
    return cert_der[pos:_tlv(cert_der, pos)[1]]


def parse_pins(spec):
    """``--pinnedpubkey``: ``sha256//BASE64[;sha256//BASE64...]``, or a file
    holding the key (PEM ``PUBLIC KEY`` or DER) -> the set of SHA-256
    digests a server key may have."""
    if spec.startswith('sha256//'):
        pins = set()
        for part in spec.split(';'):
            part = part.strip()
            if not part.startswith('sha256//'):
                raise ValueError('--pinnedpubkey: expected sha256//BASE64[;sha256//BASE64], got %r' % spec)
            try:
                pins.add(base64.b64decode(part[len('sha256//'):], validate=True))
            except ValueError:
                raise ValueError('--pinnedpubkey: %r is not base64' % part)
        return pins
    try:
        with open(spec, 'rb') as handle:
            data = handle.read()
    except OSError as exc:
        raise LocalError(EXIT_PINNED, 'SSL: unable to read the pinned public key %s (%s)' % (spec, exc))
    if b'-----BEGIN PUBLIC KEY-----' in data:
        body = data.split(b'-----BEGIN PUBLIC KEY-----', 1)[1].split(b'-----END PUBLIC KEY-----', 1)[0]
        data = base64.b64decode(b''.join(body.split()))
    return {hashlib.sha256(data).digest()}


class Connector(object):
    """What every connection of one transfer shares: ``source`` (an address
    or ``None``), ``ports`` (``(low, high)`` or ``None``), ``pins`` (SHA-256
    digests of acceptable server keys, or ``None``), ``http10`` (``-0``:
    HTTP/1.0 requests)."""

    def __init__(self, source=None, ports=None, pins=None, http10=False):
        self.source = source
        self.ports = ports
        self.pins = pins
        self.http10 = http10

    @property
    def binds(self):
        return self.source is not None or self.ports is not None

    def create_connection(self, address, timeout=_DEFAULT_TIMEOUT, source_address=None):
        """``socket.create_connection`` from the chosen source: each port of
        the range in turn while the bind is what fails; none left: 45."""
        host = self.source or ''
        low, high = self.ports or (0, 0)
        last = None
        for port in range(low, high + 1):
            try:
                return socket.create_connection(address, timeout, (host, port))
            except OSError as exc:
                if getattr(exc, 'errno', None) not in _BIND_ERRNOS and getattr(exc, 'winerror', None) not in _BIND_ERRNOS:
                    raise
                last = exc
        what = '%s port %d-%d' % (host or '*', low, high) if self.ports else "'%s'" % host
        raise LocalError(EXIT_INTERFACE, "Couldn't bind to %s: %s" % (what, last))

    def check_pin(self, sock):
        if self.pins is None:
            return
        der = sock.getpeercert(binary_form=True)
        if not der or hashlib.sha256(subject_public_key_info(der)).digest() not in self.pins:
            sock.close()
            raise LocalError(EXIT_PINNED, 'SSL: public key does not match pinned public key')

    def socks_connection(self, socks, address, timeout=_DEFAULT_TIMEOUT, source_address=None):
        """A connection to ``address`` through ``socks``: TCP to the proxy
        (from this connector's source), then the handshake."""
        connect = self.create_connection if self.binds else socket.create_connection
        sock = connect((socks.host, socks.port), timeout, source_address)
        try:
            socks_handshake(sock, socks, address[0], address[1])
        except Exception:
            sock.close()
            raise
        return sock

    def connection_class(self, base, tunnel_headers=None, socks=None):
        """``base`` (``HTTPConnection`` / ``HTTPSConnection``) bound to this
        connector, its CONNECT carrying ``tunnel_headers`` too, its TCP
        connection made through ``socks`` (a :class:`Socks`) when given."""
        connector = self

        class Connection(base):
            if connector.http10:
                # http.client's own HTTP/1.0 mode: the request line, and no
                # chunked upload.
                _http_vsn = 10
                _http_vsn_str = 'HTTP/1.0'

            def __init__(self, *args, **kwargs):
                base.__init__(self, *args, **kwargs)
                if socks is not None:
                    self._create_connection = lambda address, timeout=_DEFAULT_TIMEOUT, source=None: (
                        connector.socks_connection(socks, address, timeout, source))
                elif connector.binds:
                    self._create_connection = connector.create_connection

            def set_tunnel(self, host, port=None, headers=None):
                merged = dict(headers or {})
                merged.update(tunnel_headers or {})
                base.set_tunnel(self, host, port, merged)

            def connect(self):
                base.connect(self)
                if isinstance(self, http.client.HTTPSConnection):
                    connector.check_pin(self.sock)

        return Connection


class ConnectHTTPHandler(urllib.request.HTTPHandler):
    """urllib's HTTP handler, connecting through a :class:`Connector`."""

    def __init__(self, connector):
        urllib.request.HTTPHandler.__init__(self)
        self.connector = connector

    def http_open(self, req):
        klass = self.connector.connection_class(http.client.HTTPConnection, getattr(req, 'tunnel_headers', None),
                                                getattr(req, 'socks_via', None))
        return self.do_open(klass, req)


class ConnectHTTPSHandler(urllib.request.HTTPSHandler):
    """urllib's HTTPS handler, connecting through a :class:`Connector`; the
    proxy credential set as ``req.tunnel_headers`` rides on the CONNECT and
    nowhere else (urllib itself moves only a header named
    ``Proxy-Authorization`` there)."""

    def __init__(self, connector, context):
        urllib.request.HTTPSHandler.__init__(self, context=context)
        self.connector = connector

    def https_open(self, req):
        klass = self.connector.connection_class(http.client.HTTPSConnection, getattr(req, 'tunnel_headers', None),
                                                getattr(req, 'socks_via', None))
        kwargs = {'context': self._context}
        if hasattr(self, '_check_hostname'):  # Python < 3.12
            kwargs['check_hostname'] = self._check_hostname
        return self.do_open(klass, req, **kwargs)
