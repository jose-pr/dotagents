"""``-w`` / ``--write-out``: curl's format language and the variables the
fallback can report, formatted as curl formats them (checked against curl
8.21). Written however the transfer ends -- ``000`` and the exit code after a
transport failure, the status after ``-f``'s 22."""
import json
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Optional

from ._duho import NS, Arg
from .args import Group, first_header
from .errors import EXIT_READ, EarlyExit
from .routing import caller_url

#: The ``--write-out`` variables the fallback reports, with curl's meaning and
#: format. Any other ``%{name}`` (``time_connect``, ``remote_ip``, ``json``...)
#: or an ``%output{file}`` is refused before the request, like an unsupported flag.
WRITE_OUT_VARIABLES = frozenset([
    'content_type', 'errormsg', 'exitcode', 'filename_effective', 'header_json',
    'http_code', 'http_version', 'method', 'num_headers', 'num_redirects',
    'redirect_url', 'response_code', 'scheme', 'size_download', 'size_upload',
    'time_starttransfer', 'time_total', 'url', 'url_effective', 'urlnum',
])
#: ``%{stdout}`` / ``%{stderr}`` switch the stream; after ``%{onerror}`` the
#: rest is written only when the transfer failed.
_CONTROL = ('stdout', 'stderr', 'onerror')
_ESCAPES = {'n': '\n', 'r': '\r', 't': '\t'}
#: http.client's response version -> curl's ``%{http_version}``.
_HTTP_VERSIONS = {10: '1', 11: '1.1'}


def parse_write_out(fmt):
    """``-w FORMAT`` as ``(kind, value)`` tokens -- ``text``, ``var``,
    ``header`` (``%header{name}``) and ``control`` -- read the way curl reads
    it: ``%%`` is ``%``; ``\\n``, ``\\r``, ``\\t`` are escapes; any other
    ``%x`` or ``\\x``, and an unclosed ``%{``, is written as it stands.
    ``@file`` / ``@-`` read the format from a file / stdin. A variable the
    fallback cannot report raises ``NotImplementedError``, before anything
    is sent."""
    if fmt.startswith('@'):
        source = fmt[1:]
        try:
            fmt = sys.stdin.read() if source == '-' else Path(source).read_text(encoding='utf-8')
        except OSError as exc:
            raise EarlyExit(EXIT_READ, 'Failed to open %s' % source)
    tokens, text, i = [], [], 0

    def take(kind, value):
        if text:
            tokens.append(('text', ''.join(text)))
            del text[:]
        tokens.append((kind, value))

    while i < len(fmt):
        char, nxt = fmt[i], fmt[i + 1:i + 2]
        if char == '\\' and nxt:
            text.append(_ESCAPES.get(nxt, char + nxt))
            i += 2
        elif char != '%' or not nxt:
            text.append(char)
            i += 1
        elif nxt == '%':
            text.append('%')
            i += 2
        else:
            opener = next((o for o in ('{', 'header{', 'output{') if fmt.startswith(o, i + 1)), None)
            start = i + 1 + len(opener or '')
            end = fmt.find('}', start) if opener else -1
            if opener is None:
                text.append(char + nxt)  # not a variable: written as it stands
                i += 2
            elif end < 0:
                text.append(fmt[i:start])  # an unclosed %{ is written, the rest read on
                i = start
            else:
                name, i = fmt[start:end], end + 1
                if opener == 'header{':
                    take('header', name)
                elif opener == '{' and name in _CONTROL:
                    take('control', name)
                elif opener == '{' and name in WRITE_OUT_VARIABLES:
                    take('var', name)
                else:
                    raise NotImplementedError('Unsupported --write-out variable: %%%s%s}' % (opener, name))
    if text:
        tokens.append(('text', ''.join(text)))
    return tokens


def _header_json(header_items):
    """curl's ``%{header_json}``: lower-cased names in first-seen order, each
    with all its values, one name per line."""
    grouped = {}
    for name, value in header_items:
        grouped.setdefault(name.lower(), []).append(value)
    return '{%s\n}' % ',\n'.join(
        '%s:%s' % (json.dumps(name, ensure_ascii=False), json.dumps(values, ensure_ascii=False, separators=(',', ':')))
        for name, values in grouped.items())


def write_out_values(args, transfer, exitcode):
    """Every ``WRITE_OUT_VARIABLES`` value for a finished transfer, formatted
    as curl formats it: a 3-digit code (``000`` when nothing answered),
    6-decimal seconds, the LAST request's URL, method and body size."""
    total = time.monotonic() - transfer.started
    sent_url, method, upload = transfer.sent[-1] if transfer.sent else (transfer.target, transfer.method, 0)
    effective = caller_url(sent_url, transfer.plan)
    headers = transfer.header_items
    location = first_header(headers, 'location')
    redirect = urllib.parse.urljoin(effective, location) if location and 300 <= transfer.status < 400 else ''
    # curl reports the whole elapsed time as start-transfer when nothing answered.
    first_byte = total if transfer.first_byte is None else transfer.first_byte - transfer.started
    return {
        'content_type': first_header(headers, 'content-type'),
        'errormsg': transfer.errormsg,
        'exitcode': str(exitcode),
        'filename_effective': args.body_target or '',
        'header_json': _header_json(headers),
        'http_code': '%03d' % transfer.status,
        'http_version': _HTTP_VERSIONS.get(transfer.version, '1.1' if transfer.status else '0'),
        'method': method,
        'num_headers': str(len(headers)),
        'num_redirects': str(max(len(transfer.sent) - 1, 0)),
        'redirect_url': redirect,
        'response_code': '%03d' % transfer.status,
        'scheme': effective.split('://', 1)[0].lower() if '://' in effective else '',
        'size_download': str(transfer.size_download),
        'size_upload': str(upload),
        'time_starttransfer': '%.6f' % first_byte,
        'time_total': '%.6f' % total,
        'url': transfer.typed_url,
        'url_effective': effective,
        'urlnum': '0',
    }


def _write_stream(name, text):
    """``text`` to stdout or stderr as UTF-8, after whatever is already buffered."""
    stream = sys.stderr if name == 'stderr' else sys.stdout
    stream.flush()
    raw = getattr(stream, 'buffer', None)
    if raw is None:
        stream.write(text)
    else:
        raw.write(text.encode('utf-8'))
        raw.flush()


def emit_write_out(tokens, values, header_items, exitcode):
    """Write the parsed ``-w`` format: to stdout until ``%{stderr}`` switches
    it, and nothing after ``%{onerror}`` when the transfer succeeded."""
    stream = 'stdout'
    for kind, value in tokens:
        if kind == 'control':
            if value != 'onerror':
                stream = value
            elif exitcode == 0:
                return
            continue
        if kind == 'header':
            value = first_header(header_items, value)
        elif kind == 'var':
            value = values[value]
        _write_stream(stream, value)


class WriteOutArgs(Group):
    """``-w``."""

    write_out: Arg[Optional[str], NS(metavar='FORMAT')] = None
    "Write FORMAT after the transfer (@file / @- read it); see httplib.cli.writeout.WRITE_OUT_VARIABLES"
    ("-w", "--write-out")

    def write_out_tokens(self):
        """The parsed format, or ``None`` without -w. Called before anything
        is sent, so an unsupported variable is refused first."""
        return parse_write_out(self.write_out) if self.write_out else None

    def report(self, tokens, transfer, exitcode):
        """Write the -w output for a finished transfer."""
        if tokens is not None:
            emit_write_out(tokens, write_out_values(self, transfer, exitcode), transfer.header_items, exitcode)
