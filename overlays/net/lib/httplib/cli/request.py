"""Request options: the URL and its query, the method, and the headers --
the ones options imply, then ``-H`` on top."""
import re
import urllib.parse
from typing import List, Optional

from ._duho import NS, Arg
from .args import Group, read_source, url_encoded
from .errors import EXIT_PROTOCOL, EXIT_READ, EXIT_URL, EXIT_USAGE, EarlyExit, LocalError

USER_AGENT = "Python-curl/1.0"

#: The schemes the fallback speaks.
SCHEMES = ('http', 'https')
#: Schemes curl speaks and the fallback does not: refused out loud (exit 2),
#: as any flag it lacks. A scheme curl does not know either is curl's own
#: exit 1.
CURL_SCHEMES = frozenset([
    'dict', 'file', 'ftp', 'ftps', 'gopher', 'gophers', 'imap', 'imaps', 'ipfs', 'ipns', 'ldap', 'ldaps', 'mqtt',
    'pop3', 'pop3s', 'rtmp', 'rtsp', 'scp', 'sftp', 'smb', 'smbs', 'smtp', 'smtps', 'telnet', 'tftp', 'ws', 'wss',
])


#: An IPv6 literal host, which curl does not take for a glob range.
_IPV6_HOST = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.-]*://(?:[^/@]*@)?\[[0-9a-fA-F:.]+(?:%[^\]]*)?\]')


def check_glob(url):
    """curl expands ``{a,b}`` and ``[1-3]`` in a URL into one transfer each
    unless ``-g``; the fallback makes one transfer, so a pattern is refused
    out loud (exit 2) rather than sent as typed. A broken one is curl's own
    exit 3 at parse time (no ``-w``). An IPv6 literal host is not a range."""
    host = _IPV6_HOST.match(url)
    start = host.end() if host else 0
    depth = None
    for index in range(start, len(url)):
        char = url[index]
        if char in '{[':
            if depth is not None:
                raise EarlyExit(EXIT_URL, '(3) nested brace in position %d:' % (index + 1))
            depth = (char, index)
        elif char in '}]':
            if depth is None or '{['.index(depth[0]) != '}]'.index(char):
                raise EarlyExit(EXIT_URL, '(3) unmatched close brace/bracket in position %d:' % (index + 1))
            if char == ']' and not re.match(r'^(?:[a-zA-Z]-[a-zA-Z]|\d+-\d+)(?::\d+)?$', url[depth[1] + 1:index]):
                raise EarlyExit(EXIT_URL, '(3) bad range in position %d:' % (depth[1] + 2))
            raise NotImplementedError(
                'URL globbing (%s in the URL) makes several transfers, which the fallback does not do: '
                'pass -g to send the URL as typed' % url[depth[1]:index + 1])
    if depth is not None:
        if depth[0] == '{':
            raise EarlyExit(EXIT_URL, '(3) unmatched brace in position %d:' % (len(url) + 1))
        raise EarlyExit(EXIT_URL, '(3) bad range specification in position %d:' % (depth[1] + 2))


def check_url(url):
    """curl's verdict on a URL before anything is sent: 1 for a scheme
    curl does not speak, 3 for one it cannot parse (a broken IPv6 literal, a
    port out of range, no host). A scheme curl speaks and the fallback does
    not raises ``NotImplementedError``."""
    scheme = url.split('://', 1)[0].lower()
    if scheme not in SCHEMES:
        if scheme in CURL_SCHEMES:
            raise NotImplementedError('Unsupported protocol: %s' % scheme)
        raise LocalError(EXIT_PROTOCOL, 'Protocol "%s" not supported' % scheme)
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise LocalError(EXIT_URL, 'URL rejected: %s' % (
            'Bad IPv6 address' if 'IPv6' in str(exc) else 'Malformed input to a URL function'))
    try:
        parts.port
    except ValueError:
        raise LocalError(EXIT_URL, 'URL rejected: Port number was not a decimal number between 0 and 65535')
    if not parts.hostname:
        raise LocalError(EXIT_URL, 'URL rejected: No host part in the URL')


def _header_lines(values):
    """``-H`` values with each ``@file`` replaced by its lines (curl's
    header file: one header per line, blank lines skipped). A file that is
    not there is curl's exit 26, before anything is sent."""
    for value in values:
        if not value.startswith('@'):
            yield value
            continue
        try:
            text = read_source(value[1:]).decode('utf-8', errors='replace')
        except OSError:
            raise EarlyExit(EXIT_READ, 'Failed to open %s' % value[1:])
        for line in text.splitlines():
            if line.strip():
                yield line


def default_scheme(url):
    """``url`` with curl's default scheme, ``http://``, when it names none."""
    return url if '://' in url else 'http://' + url


def add_query(url, pieces):
    """``url`` with ``pieces`` appended to its query (``?`` or ``&``)."""
    pieces = [piece for piece in pieces if piece]
    if not pieces:
        return url
    base, hashmark, fragment = url.partition('#')
    return base + ('&' if '?' in base else '?') + '&'.join(pieces) + hashmark + fragment


def has_header(headers, name):
    """Is ``name`` among ``headers`` (any case)?"""
    return any(k.lower() == name.lower() for k in headers)


def _set_header(headers, key, value):
    """Set ``key`` case-insensitively (one entry per name, the caller's case)."""
    for existing in list(headers):
        if existing.lower() == key.lower():
            del headers[existing]
    headers[key] = value


class RequestArgs(Group):
    """URL, method and headers."""

    url_positional: Arg[Optional[str], NS(metavar='URL', nargs='?')] = None
    "URL to fetch"
    ("url_positional",)

    url: Optional[str] = None
    "URL to fetch (option)"
    ("--url",)

    request: Arg[Optional[str], NS(metavar='METHOD')] = None
    "Request method (default GET; POST with a body, PUT with -T, HEAD with -I)"
    ("-X", "--request")

    get: bool = False
    "Send the -d / --data-urlencode data as the query of a GET"
    ("-G", "--get")

    url_query: Arg[Optional[List[str]], NS(metavar='DATA')] = None
    "Add to the URL query (the --data-urlencode forms; +DATA as it stands)"
    ("--url-query",)

    header: Optional[List[str]] = None
    "Custom header (Name: value sets, Name: removes, Name; sends it empty)"
    ("-H", "--header")

    user_agent: Optional[str] = None
    "Set User-Agent"
    ("-A", "--user-agent")

    referer: Arg[Optional[str], NS(metavar='URL')] = None
    "Referer header"
    ("-e", "--referer")

    range: Arg[Optional[str], NS(metavar='RANGE')] = None
    "Ask for a byte range (Range: bytes=RANGE)"
    ("-r", "--range")

    compressed: bool = False
    "Ask for a compressed response and decode it"
    ("--compressed",)

    def target_url(self):
        """The URL to request: ``--url`` or the positional, ``http://`` when
        it names no scheme (as curl), ``--url-query`` appended. Any scheme but
        http/https is refused (:func:`check_url`); no URL at all is curl's
        usage error."""
        url = self.url or self.url_positional
        if not url:
            raise EarlyExit(EXIT_USAGE, "(2) no URL specified\ncurl: try 'curl --help' or 'curl --manual' for more information")
        url = default_scheme(url)
        if not self.globoff:
            check_glob(url)
        check_url(url)
        url = self.remember_auth_url(url)  # user:pw@ -> credentials
        return add_query(url, [url_encoded(q, self.read_data, raw_plus=True) for q in self.url_query or []])

    def request_method(self, has_body, uploading):
        """-I is HEAD; else -X; else GET with -G, PUT with -T, POST with a
        body, GET."""
        if self.head:
            return 'HEAD'
        if self.request:
            return self.request
        if self.get:
            return 'GET'
        if uploading:
            return 'PUT'
        return 'POST' if has_body else 'GET'

    def request_headers(self):
        """``(headers, removed)``. The implied headers first -- User-Agent,
        the ``Authorization`` the auth options imply (``AuthArgs``), -e's
        Referer, --compressed's Accept-Encoding, -r's Range, the body's
        (``--json``) -- then ``-H`` on top: ``Name: v`` sets, ``Name:`` removes
        the header (a default included), ``Name;`` sends it empty; ``@file``
        (``@-`` stdin) is one header per line of the file."""
        headers = {'User-Agent': self.user_agent or USER_AGENT}
        authorization = self.authorization()
        if authorization:
            headers['Authorization'] = authorization
        if self.referer:
            headers['Referer'] = self.referer
        if self.compressed:
            headers['Accept-Encoding'] = 'gzip, deflate'
        if self.range:
            headers['Range'] = 'bytes=' + self.range
        headers.update(self.body_headers())
        removed = set()
        for header in _header_lines(self.header or []):
            if ':' in header:
                key, value = header.split(':', 1)
                key, value = key.strip(), value.strip()
                if not value:
                    _set_header(headers, key, None)
                    del headers[key]
                    removed.add(key.lower())
                    continue
            elif header.endswith(';') and header[:-1].strip():
                key, value = header[:-1].strip(), ''
            else:
                self.usage_error('Invalid header: %s' % header)
            removed.discard(key.lower())
            _set_header(headers, key, value)
        return headers, removed

    def query_url(self, url, body):
        """-G: ``url`` with the body moved into its query."""
        return add_query(url, [body.decode('utf-8')]) if body else url
