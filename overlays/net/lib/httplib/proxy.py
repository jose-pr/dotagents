"""Agent proxy resolution.

The proxy source is **``AGENTS_PROXY``** — the *agent* proxy, deliberately
distinct from the machine's global proxy so non-agent tools (git, npm, corporate
CLIs) are not forced through it. If ``AGENTS_PROXY`` is unset, fall back to the OS
convention: ``HTTPS_PROXY`` -> ``HTTP_PROXY`` -> ``ALL_PROXY`` (either case).

(dotagents' ``env`` command seeds ``AGENTS_PROXY`` from the global proxy when
unset, so on a proxied box this still finds it — but ``httplib`` reads
``AGENTS_PROXY`` directly and never fans back out to the global vars.)
"""
from __future__ import annotations

import os
from typing import Optional

# Fallback order after AGENTS_PROXY, both cases (curl reads lowercase per httpoxy).
_FALLBACK_VARS = (
    "HTTPS_PROXY", "https_proxy",
    "HTTP_PROXY", "http_proxy",
    "ALL_PROXY", "all_proxy",
)


def proxy_url() -> Optional[str]:
    """Return the agent proxy URL, or ``None``.

    ``AGENTS_PROXY`` wins; otherwise the first set of
    ``HTTPS_PROXY``/``HTTP_PROXY``/``ALL_PROXY`` (either case)."""
    agent = os.environ.get("AGENTS_PROXY")
    if agent:
        return agent
    for var in _FALLBACK_VARS:
        val = os.environ.get(var)
        if val:
            return val
    return None


def proxies_from_env() -> Optional[dict]:
    """Return a ``requests``-style ``{"http": ..., "https": ...}`` dict, or ``None``."""
    p = proxy_url()
    if not p:
        return None
    return {"http": p, "https": p}
