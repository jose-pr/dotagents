"""The urllib side of the proxy decision: the handler that applies an
``httplib.proxy.ProxyPlan`` -- the same plan the session's adapter applies --
to every hop, and the caller's view of a URL a prefix gateway rewrote."""
import urllib.parse
import urllib.request

from httplib import proxy as agent_proxy


def caller_url(url, plan):
    """``url`` as the caller would name it: a prefix gateway's
    ``<proxy><endpoint>`` stripped, an empty path written ``/`` (curl's
    ``url_effective``)."""
    base = plan.gateway_base if plan is not None else None
    if base and url.startswith(base):
        url = url[len(base):]
    parts = urllib.parse.urlsplit(url)
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
