"""httplib — a small session toolkit over the optional ``requests`` dependency.

Public surface (see kb/NET.md):
  proxy.proxy_url / proxies_from_env  — agent proxy from AGENTS_PROXY
  proxy.resolve                        — (clean proxy URL, Proxy-Authorization value):
                                         AGENTS_PROXY_AUTH / HTTP_PROXY_AUTH verbatim,
                                         else Basic from the URL's userinfo
  proxy.proxy_type / prefix_url        — AGENTS_PROXY_TYPE (connect | prefix[:/endpoint]);
                                         the <proxy><endpoint><url> a prefix gateway gets
  proxy.redact / should_bypass         — a printable URL, the NO_PROXY decision
  jar.FileCookieJar / FileTokenJar     — file-backed jars under the dotagents store
  hooks                                — NET_HOOKS_<KEY> URL hooks (a curl wrapper,
                                         an httplib callable) matched on the caller's URL
  session.new_session                  — configured requests.Session
  fetch.request_text / request_json / request_with_reauth / *_auto helpers

``proxy``, ``jar`` and ``hooks`` are dependency-free; ``session``/``fetch`` import
``requests`` lazily.
"""
from .proxy import (  # noqa: F401
    prefix_url, proxies_from_env, proxy_authorization, proxy_type, proxy_url, redact, resolve, should_bypass,
)
