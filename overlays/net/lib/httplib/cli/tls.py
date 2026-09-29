"""TLS options: how the server is verified (the OS trust store, ``--cacert``
/ ``--capath``, or not at all with ``-k``) and the client certificate
(``--cert`` / ``--key`` / ``--pass``, PEM)."""
import os
import ssl
from typing import Optional

from httplib.tls import new_os_context

from ._duho import NS, Arg
from .args import Group
from .errors import EXIT_CACERT, EXIT_CLIENT_CERT, LocalError


def split_cert(value):
    """curl's ``--cert <file[:password]>``: the first ``:`` starts the
    password; ``\\:`` is a literal colon in the name and, on Windows, a
    drive (``C:\\`` / ``C:/``) is part of it, as curl reads it."""
    name, i = [], 0
    while i < len(value):
        char = value[i]
        if char == '\\' and value[i + 1:i + 2] == ':':
            name.append(':')
            i += 2
            continue
        drive = os.name == 'nt' and i == 1 and value[0].isalpha() and value[2:3] in ('\\', '/')
        if char == ':' and not drive:
            return ''.join(name), value[i + 1:]
        name.append(char)
        i += 1
    return ''.join(name), None


def _pem_only(option, value):
    if value and value.upper() != 'PEM':
        raise NotImplementedError('Unsupported %s: %s (the fallback reads PEM only)' % (option, value))


#: curl's own override of the CA bundle, honoured like ``--cacert``.
CA_BUNDLE_VAR = 'CURL_CA_BUNDLE'


def build_ssl_context(insecure, cacert=None, capath=None, cert=None, key=None, passphrase=None):
    """An SSL context. The server is verified against ``cacert`` / ``capath``
    when given, else ``$CURL_CA_BUNDLE`` (both replace the trust store, as in
    curl), else the OS trust store -- ``httplib.tls``, the store the requests
    session verifies against too; ``insecure`` (-k) verifies nothing.
    ``cert`` (``file[:password]``, may hold the key) / ``key`` /
    ``passphrase`` present a client certificate. A file that cannot be used
    raises :class:`LocalError`: 77 for the CA side, 58 for the client
    certificate."""
    if not (cacert or capath):
        cacert = os.environ.get(CA_BUNDLE_VAR) or None
    if insecure:
        ctx = new_os_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    elif cacert or capath:
        if capath and not os.path.isdir(capath):
            raise LocalError(EXIT_CACERT, 'error setting certificate verify locations: CApath: %s (not a directory)' % capath)
        try:
            ctx = new_os_context(cafile=cacert, capath=capath)
        except (OSError, ssl.SSLError) as exc:
            raise LocalError(EXIT_CACERT, 'error setting certificate verify locations: CAfile: %s CApath: %s (%s)' % (
                cacert or 'none', capath or 'none', exc))
    else:
        ctx = new_os_context()
    if cert:
        certfile, password = split_cert(cert)
        if passphrase is not None:
            password = passphrase
        try:
            ctx.load_cert_chain(certfile, keyfile=key or None, password=password)
        except (OSError, ssl.SSLError) as exc:
            raise LocalError(EXIT_CLIENT_CERT, 'could not load PEM client certificate %s%s (%s)' % (
                certfile, ', key %s' % key if key else '', exc))
    return ctx


class TLSArgs(Group):
    """Server verification and the client certificate."""

    insecure: bool = False
    "Allow insecure server connections"
    ("-k", "--insecure")

    cacert: Arg[Optional[str], NS(metavar='FILE')] = None
    "Verify the server against this CA bundle (PEM) instead of the OS trust store"
    ("--cacert",)

    capath: Arg[Optional[str], NS(metavar='DIR')] = None
    "Verify the server against this hashed CA directory instead of the OS trust store"
    ("--capath",)

    cert: Arg[Optional[str], NS(metavar='FILE[:PASSWORD]')] = None
    "Client certificate (PEM; may hold the key too)"
    ("-E", "--cert")

    cert_type: Arg[Optional[str], NS(metavar='TYPE')] = None
    "Client certificate type: PEM only"
    ("--cert-type",)

    key: Arg[Optional[str], NS(metavar='FILE')] = None
    "Private key for --cert (PEM)"
    ("--key",)

    key_type: Arg[Optional[str], NS(metavar='TYPE')] = None
    "Private key type: PEM only"
    ("--key-type",)

    pass_: Arg[Optional[str], NS(metavar='PHRASE')] = None
    "Passphrase for the private key"
    ("--pass",)

    def _check(self):
        _pem_only('--cert-type', self.cert_type)
        _pem_only('--key-type', self.key_type)

    def ssl_context(self):
        return build_ssl_context(self.insecure, cacert=self.cacert, capath=self.capath,
                                 cert=self.cert, key=self.key, passphrase=self.pass_)
