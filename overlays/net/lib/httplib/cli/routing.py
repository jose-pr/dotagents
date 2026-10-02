"""The urllib side of the proxy decision: the handler that applies an
``httplib.proxy.ProxyPlan`` -- the same plan the session's adapter applies --
to every hop, and the caller's view of a URL a prefix gateway rewrote."""
import re
import urllib.parse
import urllib.request

from httplib import proxy as agent_proxy

from .errors import EXIT_RESOLVE_PROXY, LocalError


def check_proxy_syntax(url):
    """curl's exit 5 for a proxy URL it cannot parse -- ``-x`` or the agent
    proxy alike, whether or not the request would use it: a broken IPv6
    literal, a port out of range, no host, or a host with spaces or control
    characters in it. The message names the URL with any password hidden."""
    if not url:
        return
    reason = None
    try:
        parts = urllib.parse.urlsplit(url if '://' in url else 'http://' + url)
    except ValueError:
        reason = 'Bad IPv6 address'
    else:
        try:
            parts.port
        except ValueError:
            reason = 'Port number was not a decimal number between 0 and 65535'
        if reason is None:
            authority = parts.netloc.rpartition('@')[2]
            if not parts.hostname or any(c.isspace() or ord(c) < 32 for c in authority):
                reason = 'Malformed input to a URL function'
    if reason is not None:
        shown = re.sub(r'(//[^:@/]*):[^@/]*@', r'\1:***@', url)
        raise LocalError(EXIT_RESOLVE_PROXY, "Unsupported proxy syntax in '%s': %s" % (shown, reason))


def caller_url(url, plan):
    """``url`` as the caller would name it: a prefix gateway's
    ``<proxy><endpoint>`` stripped, an empty path written ``/`` (curl's
    ``url_effective``)."""
    base = plan.gateway_base if plan is not None else None
    if base and url.startswith(base):
        url = url[len(base):]
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url  # one curl rejected (exit 3): reported as typed
    return url if parts.path else urllib.parse.urlunsplit(parts._replace(path='/'))


class AgentProxyHandler(urllib.request.BaseHandler):
    """Applies the plan to EVERY request the opener sends -- the first one
    and each redirect hop -- since urllib builds a fresh ``Request`` per hop
    that carries neither the proxy nor an unredirected header. Per hop: a
    bypassed host goes direct; a ``connect`` proxy is set on the request
    (what ``ProxyHandler`` does after its own env/registry checks, which are
    deliberately not consulted here); a prefix gateway rewrites the URL to
    ``<proxy><endpoint><url>`` unless the gateway already wrote it in its own
    namespace. The credential is an UNREDIRECTED header: urllib sends it to
    the proxy for http, moves it onto the https CONNECT, and never copies it
    into the next hop -- so it cannot reach an origin."""

    handler_order = 90  # before the (empty) ProxyHandler at 100

    def __init__(self, plan):
        self.plan = plan

    def _apply(self, req):
        plan = self.plan
        if plan.bypasses(req.full_url):
            return req
        if plan.endpoint is None:
            proxy_parts = urllib.parse.urlsplit(plan.proxy)
            req.set_proxy(proxy_parts.netloc, proxy_parts.scheme)
        elif not req.full_url.startswith(plan.gateway_base):
            req.full_url = agent_proxy.prefix_url(req.full_url, plan.proxy, plan.endpoint)
        if plan.authorization:
            req.add_unredirected_header(plan.auth_header, plan.authorization)
        return req

    http_request = _apply
    https_request = _apply

