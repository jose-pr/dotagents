"""httplib — a small session toolkit over the optional ``requests`` dependency.

Public surface (see kb/NET.md):
  proxy.proxy_url / proxies_from_env  — agent proxy from AGENTS_PROXY
  jar.FileCookieJar / FileTokenJar     — file-backed jars under the dotagents store
  session.new_session                  — configured requests.Session
  fetch.request_text / request_json / request_with_reauth / *_auto helpers

``proxy`` and ``jar`` are dependency-free; ``session``/``fetch`` import ``requests``
lazily.
"""
from .proxy import proxies_from_env, proxy_url  # noqa: F401
