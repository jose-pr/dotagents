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
import urllib.request

from .errors import EXIT_USAGE, EarlyExit, LocalError

EXIT_INTERFACE = 45
EXIT_PINNED = 90

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

    def create_connection(self, address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
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

    def connection_class(self, base, tunnel_headers=None):
        """``base`` (``HTTPConnection`` / ``HTTPSConnection``) bound to this
        connector, its CONNECT carrying ``tunnel_headers`` too."""
        connector = self

        class Connection(base):
            if connector.http10:
                # http.client's own HTTP/1.0 mode: the request line, and no
                # chunked upload.
                _http_vsn = 10
                _http_vsn_str = 'HTTP/1.0'

            def __init__(self, *args, **kwargs):
                base.__init__(self, *args, **kwargs)
                if connector.binds:
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
        klass = self.connector.connection_class(http.client.HTTPConnection, getattr(req, 'tunnel_headers', None))
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
        klass = self.connector.connection_class(http.client.HTTPSConnection, getattr(req, 'tunnel_headers', None))
        kwargs = {'context': self._context}
        if hasattr(self, '_check_hostname'):  # Python < 3.12
            kwargs['check_hostname'] = self._check_hostname
        return self.do_open(klass, req, **kwargs)
