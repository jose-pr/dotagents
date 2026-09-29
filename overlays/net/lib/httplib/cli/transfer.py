"""The pure-stdlib (urllib) fallback: assemble the request from the parsed
options, run it -- again, under ``--retry`` -- and answer as curl does:
the same output, the same one-line errors, the same exit codes."""
import gzip
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request

from httplib import proxy as agent_proxy
from httplib.retry import retry_after

from .body import FORM_CONTENT_TYPE
from .download import EXIT_FILESIZE
from .connection import MAX_RETRY_SLEEP
from .errors import (EXIT_CONNECT, EXIT_HTTP, EXIT_REDIRECTS, EXIT_RESOLVE, EXIT_SSL, EXIT_SSL_CONNECT, EXIT_TIMEOUT,
                     EXIT_WRITE, LocalError, Retry)
from .request import has_header
from .redirects import CurlRedirectHandler
from .routing import AgentProxyHandler

#: The shim's own version line (``-V``); real curl answers when it is present.
SHIM_VERSION = 'curl 0.0.0-dotagents-shim (python %d.%d, urllib) -- the net overlay fallback' % sys.version_info[:2]

#: ``time.sleep`` between retries (a seam for tests).
sleep = time.sleep


class Transfer(object):
    """What one attempt did, recorded as it goes so ``-w`` can report it
    however the attempt ends."""

    def __init__(self, typed_url, target, method, plan):
        self.typed_url = typed_url or ''  # as the caller typed it: %{url}
        self.target = target              # the URL requested first
        self.method = method
        self.plan = plan
        self.started = time.monotonic()
        self.first_byte = None            # when the final response's headers arrived
        self.version = None               # http.client's: 10, 11
        self.status = 0
        self.header_items = []
        self.size_download = 0
        self.errormsg = ''
        self.sent = []                    # (url, method, body size): every request, redirects included

    def responded(self, response):
        self.first_byte = time.monotonic()
        self.version = getattr(response, 'version', None)


class RequestLog(urllib.request.BaseHandler):
    """Records every request the opener sends -- the first and each redirect
    hop -- into a :class:`Transfer`, before a prefix gateway rewrites it."""

    handler_order = 80  # before AgentProxyHandler (90)

    def __init__(self, transfer):
        self.transfer = transfer

    def _record(self, req):
        data = req.data
        size = len(data) if isinstance(data, (bytes, bytearray)) else 0
        self.transfer.sent.append((req.full_url, req.get_method(), size))
        return req

    http_request = _record
    https_request = _record


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """When -L is absent, curl does not follow redirects; surface the 3xx as-is."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def format_response_headers(status, reason, header_items):
    status_line = ('HTTP/1.1 %s %s' % (status, reason or '')).rstrip()
    header_text = ''.join('%s: %s\r\n' % (k, v) for k, v in header_items)
    return ('%s\r\n%s\r\n' % (status_line, header_text)).encode('utf-8')


def decode_body(content, header_items):
    """``--compressed``: the body decoded per ``Content-Encoding`` (gzip,
    deflate -- zlib-wrapped or raw); anything else is returned as it came."""
    import zlib

    encoding = ''
    for name, value in header_items:
        if name.lower() == 'content-encoding':
            encoding = value.strip().lower()
    try:
        if encoding in ('gzip', 'x-gzip'):
            return gzip.decompress(content)
        if encoding == 'deflate':
            try:
                return zlib.decompress(content)
            except zlib.error:
                return zlib.decompress(content, -zlib.MAX_WBITS)
    except (OSError, EOFError, zlib.error):
        pass
    return content


def build_opener(args, plan, context, transfer, removed):
    """The opener for one attempt. It always carries an EMPTY ProxyHandler:
    urllib's own would re-read the global vars (never AGENTS_PROXY), re-check
    NO_PROXY and, on Windows, the registry's bypass list -- and silently go
    direct where we decided to proxy. ``AgentProxyHandler`` applies the plan
    per hop instead."""
    handlers = args.connection_handlers(context) or [urllib.request.HTTPSHandler(context=context)]
    handlers.append(urllib.request.ProxyHandler({}))
    if plan is not None:
        handlers.append(AgentProxyHandler(plan))
    handlers.append(RequestLog(transfer))
    creds = args.credentials() if args.digest else None
    if creds is not None:
        # --digest: urllib answers the server's challenge; nothing is sent up front.
        passwords = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        passwords.add_password(None, transfer.target, creds[0], creds[1])
        handlers.append(urllib.request.HTTPDigestAuthHandler(passwords))
    if args.location:
        redirects = CurlRedirectHandler(args, plan)  # curl's rules for what each hop carries
        if args.max_redirs is not None and args.max_redirs >= 0:
            # urllib has two limits: distinct URLs (max_redirections) and
            # repeats of one URL (max_repeats, 4); curl's --max-redirs is both.
            redirects.max_redirections = args.max_redirs
            redirects.max_repeats = args.max_redirs
        handlers.append(redirects)
    else:
        handlers.append(NoRedirect())
    opener = urllib.request.build_opener(*handlers)
    for name in removed:
        # urllib adds these itself; a `-H 'Name:'` removal must reach the wire.
        opener.addheaders = [(k, v) for k, v in opener.addheaders if k.lower() != name]
    return opener


def fail(args, transfer, code, message):
    """curl's one line, ``curl: (N) message`` (unless -s without -S); the
    message is ``%{errormsg}``. Returns ``code``."""
    transfer.errormsg = message
    if args.should_print_error():
        print('curl: (%d) %s' % (code, message), file=sys.stderr)
    return code


def transport_error(args, reason, transfer, may_retry):
    """A failure before any response: curl's exit code -- 6 could not
    resolve, 28 timed out, 35 a failed TLS handshake, 60 a certificate that
    does not verify, else 7 could not connect -- or
    :class:`Retry` when ``--retry`` covers it."""
    text = str(reason)
    if isinstance(reason, socket.gaierror):
        code, what = EXIT_RESOLVE, 'Could not resolve host'
    elif isinstance(reason, (socket.timeout, TimeoutError)) or 'timed out' in text:
        code, what = EXIT_TIMEOUT, 'Operation timed out'
    elif isinstance(reason, ssl.SSLCertVerificationError):
        code, what = EXIT_SSL, 'SSL certificate problem'
    elif isinstance(reason, ssl.SSLError):
        code, what = EXIT_SSL_CONNECT, 'SSL connect error'
    else:
        code, what = EXIT_CONNECT, 'Failed to connect'
    why = args.retry_reason_for_error(code, reason)
    if may_retry and why:
        raise Retry(why)
    return fail(args, transfer, code, '%s: %s' % (what, text))


def exchange(args, opener, req, timeout, url, transfer, may_retry=False):
    """Send ``req``, write what curl writes and return curl's exit code;
    ``transfer`` records the outcome for ``-w``. Raises :class:`Retry`
    instead of answering when ``may_retry`` and the failure is transient."""
    transfer.started = time.monotonic()
    try:
        resp = opener.open(req, timeout=timeout)
        transfer.responded(resp)
        status = resp.getcode()
        reason = getattr(resp, 'reason', '') or ''
        header_items = list(resp.headers.items())
        if not args.head and args.too_big(header_items):
            resp.close()
            transfer.status, transfer.header_items = status, header_items
            return fail(args, transfer, EXIT_FILESIZE, 'Maximum file size exceeded')
        content = b'' if args.head else resp.read()
    except urllib.error.HTTPError as exc:
        # An HTTP status is a response, not a transport error: curl prints it
        # and exits 0 -- unless -f, which is exit 22.
        transfer.responded(getattr(exc, 'fp', None))
        status = exc.code
        reason = getattr(exc, 'reason', '') or ''
        header_items = list(exc.headers.items()) if exc.headers else []
        transfer.status, transfer.header_items = status, header_items
        if args.location and 'infinite loop' in str(reason):
            # urllib's redirect handler gave up (--max-redirs, or a loop):
            # curl's "(47) Maximum (N) redirects followed".
            if may_retry and args.retry_all_errors:
                raise Retry(' (retrying all errors)')
            limit = args.max_redirs if args.max_redirs is not None else urllib.request.HTTPRedirectHandler.max_redirections
            return fail(args, transfer, EXIT_REDIRECTS, 'Maximum (%d) redirects followed' % limit)
        content = b'' if args.head else (exc.read() or b'')
    except NotImplementedError:
        raise
    except urllib.error.URLError as exc:
        return transport_error(args, exc.reason, transfer, may_retry)
    except (socket.timeout, TimeoutError, OSError, ssl.SSLError) as exc:
        return transport_error(args, exc, transfer, may_retry)
    transfer.status, transfer.header_items = status, header_items
    transfer.size_download = len(content)  # as it came over the wire, before --compressed
    if args.too_big(header_items, len(content)):
        return fail(args, transfer, EXIT_FILESIZE, 'Maximum file size exceeded')
    why = args.retry_reason_for_status(status)
    if may_retry and why:
        raise Retry(why, after=retry_after(header_items))

    if args.verbose and not args.silent:
        print('Response status: %s' % status, file=sys.stderr)
    args.save_cookies(url, header_items)
    if args.compressed and not args.head:
        content = decode_body(content, header_items)
    header_bytes = format_response_headers(status, reason, header_items)
    try:
        outcome = args.download_outcome(status, header_items)
    except LocalError as exc:
        return fail(args, transfer, exc.code, str(exc))
    try:
        args.write_header_dump(header_bytes)
        if outcome == 'skip':
            # A 304, an unmet -z, a finished resume: no body, no file.
            transfer.size_download = 0
            return 0
        if args.fails_on(status):
            # -f: no body; --fail-with-body: the body, then the same exit 22.
            if args.fail_with_body:
                args.emit_output(header_bytes, content)
            else:
                transfer.size_download = 0
            return fail(args, transfer, EXIT_HTTP, 'The requested URL returned error: %s' % status)
        args.emit_output(header_bytes, content)
        args.finish_download(header_items)
    except OSError as exc:
        # -o / -D not writable, or stdout closed under us: curl's (23).
        return fail(args, transfer, EXIT_WRITE, 'Failure writing output to destination: %s' % exc)
    return 0


def _count(number, one, many):
    return '%d %s' % (number, one if number == 1 else many)


def run(args):
    """One curl invocation from the parsed options (``options.CurlCmd``):
    urllib underneath, curl's output, errors and exit codes on top."""
    if args.show_version:
        print(SHIM_VERSION)
        return 0
    args.check()
    tokens = args.write_out_tokens()  # an unsupported -w variable fails before anything is sent
    url = args.target_url()
    typed = args.url or args.url_positional
    body = args.build_body()
    form = args.build_form()

    transfer = Transfer(typed, url, 'GET', None)
    try:
        headers, removed = args.request_headers()  # may read a netrc: exit 26
        url, upload = args.build_upload(url)
        if args.get:
            url, body = args.query_url(url, body), None
        if form is not None:
            body, form_type = form
            if not has_header(headers, 'content-type'):
                headers['Content-Type'] = form_type
        elif body is not None and 'content-type' not in removed and not has_header(headers, 'content-type'):
            headers['Content-Type'] = FORM_CONTENT_TYPE
        data = upload if upload is not None else body
        method = args.request_method(data is not None, upload is not None)
        if method == 'HEAD':
            data = None
        cookie_header = args.cookie_header(url)
        if cookie_header and not has_header(headers, 'cookie'):
            headers['Cookie'] = cookie_header
        plan = agent_proxy.plan(proxy=args.proxy, noproxy=args.noproxy, proxy_user=args.proxy_user)
        transfer = Transfer(typed, url, method, plan)
        args.output_target(url)
        if args.prepare_download(headers):
            return 0  # --skip-existing: the file is there
        context = args.ssl_context()
    except LocalError as exc:
        rc = fail(args, transfer, exc.code, str(exc))
        args.report(tokens, transfer, rc)
        return rc

    if args.verbose and not args.silent:
        print('Request: %s %s' % (method, url), file=sys.stderr)
        if data:
            print('Data:', data.decode('utf-8', errors='replace'), file=sys.stderr)
        print('Headers:', headers, file=sys.stderr)
        if plan is not None and not plan.bypasses(url):
            # The URL only, never the credential: this line ends up in logs.
            print('Proxy%s: %s%s' % (' (prefix %s)' % plan.endpoint if plan.endpoint else '',
                                     agent_proxy.redact(plan.proxy),
                                     ' (with %s)' % plan.auth_header if plan.authorization else ''),
                  file=sys.stderr)

    timeout = args.request_timeout()
    with args.resolution():
        rc, transfer = _attempts(args, typed, url, method, plan, context, removed, data, headers, timeout)
    args.cleanup_after(rc)
    args.report(tokens, transfer, rc)
    return rc


def _attempts(args, typed, url, method, plan, context, removed, data, headers, timeout):
    """The transfer, again after each transient failure ``--retry`` covers:
    ``(exit code, the last attempt's Transfer)``."""
    retries = max(args.retry or 0, 0)
    first = time.monotonic()
    backoff = 1.0
    while True:
        in_time = not args.retry_max_time or time.monotonic() - first < args.retry_max_time
        transfer = Transfer(typed, url, method, plan)
        opener = build_opener(args, plan, context, transfer, removed)
        req = urllib.request.Request(url, data=data, headers=dict(headers), method=method)
        try:
            rc = exchange(args, opener, req, timeout, url, transfer, may_retry=retries > 0 and in_time)
            break
        except Retry as retry:
            wait = args.retry_wait(backoff, retry.after)
            if args.retry_max_time:
                wait = max(0.0, min(wait, args.retry_max_time - (time.monotonic() - first)))
            if not args.silent:
                print('Warning: Problem %s. Will retry in %s. %s left.' % (
                    retry.what, _count(int(round(wait)), 'second', 'seconds'), _count(retries, 'retry', 'retries')),
                    file=sys.stderr)
            sleep(wait)
            retries -= 1
            backoff = min(backoff * 2, MAX_RETRY_SLEEP)
    return rc, transfer
