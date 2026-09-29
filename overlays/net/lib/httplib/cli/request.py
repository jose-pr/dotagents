"""Request options: the URL and its query, the method, and the headers --
the ones options imply, then ``-H`` on top."""
from typing import List, Optional

from ._duho import NS, Arg
from .args import Group, url_encoded

USER_AGENT = "Python-curl/1.0"

#: The schemes the fallback speaks.
SCHEMES = ('http', 'https')


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
        http/https is refused."""
        url = self.url or self.url_positional
        if not url:
            self.usage_error('URL is required')
        url = self.remember_auth_url(default_scheme(url))  # user:pw@ -> credentials
        scheme = url.split('://', 1)[0].lower()
        if scheme not in SCHEMES:
            raise NotImplementedError('Unsupported protocol: %s' % scheme)
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
        the header (a default included), ``Name;`` sends it empty."""
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
        for header in self.header or []:
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
