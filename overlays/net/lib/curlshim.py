"""The overlay's ``curl``: the real curl with the agent proxy added, else
curlike's own (``pycurl``), called by ``bin/curl.py`` as :func:`main`.

  1. **A ``<P>_HOOKS_<KEY>_CURL`` wrapper** whose pattern matches the URL
     runs in curl's place, argv appended.
  2. **The real curl, when one is on PATH** (never this launcher), with the
     caller's argv and its exit code -- with ONE addition: when syshttp's
     ``<P>_PROXY`` is set, or no proxy variable is and the system's
     configuration names a proxy for the URL, and the caller does not steer
     the proxy (``-x``/``--proxy*``/``--noproxy``), ``--proxy`` and its
     credential are added, since real curl reads only the ``*_proxy``
     variables; and for a Schannel curl (Windows),
     ``--ssl-revoke-best-effort`` (``<P>_CURL_SSL_REVOKE``). A leading ``-q``
     stays first. ``<P>_CURL=0`` runs it exactly as typed.
  3. **Else curlike** (``curlike.main``): the common curl surface over the
     standard library. So is a request through a ``prefix`` gateway, which
     curl cannot express.

``<P>`` is syshttp's prefix (``AGENTS`` in this overlay). Reading argv the
way curl does needs curlike, which is imported only when a proxy or a URL
hook is in play.
"""

import os
import subprocess
import sys
from pathlib import Path

from syshttp.env import HTTPEnv

#: Where the real curl's revocation check is asked for, as ``<P>_CURL_SSL_REVOKE``:
#: ``best-effort`` (default), ``true`` or ``false``. Only a Schannel curl is affected.
REVOKE = "CURL_SSL_REVOKE"
_REVOKE_VALUES = ("best-effort", "true", "false")
_REVOKE_FLAGS = ("--ssl-no-revoke", "--ssl-revoke-best-effort")
#: ``<P>_CURL=0`` / ``n`` / ``no`` / ``false`` / ``off``: run the real curl
#: exactly as typed -- no configured proxy, no hooks, no fallback.
SHIM = "CURL"
_OFF = ("0", "n", "no", "false", "off")
#: The interpreter a ``.py`` hook wrapper runs under, as ``<P>_PYTHON``;
#: default this one.
PYTHON = "PYTHON"
# The variables real curl reads for its proxy: with one set, the system's
# configuration is not consulted (curl uses the variable).
_PROXY_VARIABLES = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")


def _var(setting):
    return HTTPEnv().name(setting) or setting


def shim_enabled():
    return (HTTPEnv().get(SHIM) or "").lower() not in _OFF


def find_real_curl(entry=None):
    """The system curl, or ``None`` -- never this launcher: ``entry``'s
    directory is skipped, and so is a curl with a ``curl.py`` beside it."""
    from curlike import find_real_curl as find

    return find(skip=Path(entry).resolve().parent if entry else None)


def revoke_argv(curl_path, argv):
    """The revocation flag to add for ``curl_path`` (see :data:`REVOKE`):
    only for a Schannel curl (Windows), only when the caller chose none, and
    best-effort only where curl has it (7.70). An unknown value: ValueError."""
    raw = HTTPEnv().get(REVOKE)
    mode = (raw or "best-effort").lower()
    if mode not in _REVOKE_VALUES:
        raise ValueError("%s=%s: expected one of %s" % (_var(REVOKE), raw, ", ".join(_REVOKE_VALUES)))
    if mode == "true" or os.name != "nt" or any(a in _REVOKE_FLAGS for a in argv):
        return []
    from curlike import describe_curl

    version, schannel = describe_curl(curl_path)
    if not schannel:
        return []
    if mode == "false":
        return ["--ssl-no-revoke"]
    return ["--ssl-revoke-best-effort"] if version and version >= (7, 70, 0) else []


def _first_reachable(url):
    """The system's entry for ``url``: the only one, or of a list the first
    that accepts a connection (``NoReachableProxyError`` when none does and
    the list holds no DIRECT)."""
    import proxylib
    from syshttp import proxy

    entries = proxylib.lookup(proxy.proxy_map(), url)
    if len(entries) > 1:
        try:
            entries = proxylib.lookup(proxylib.Probed(proxylib.SimpleProxyMap(list(entries))), url)
        except proxylib.NoReachableProxyError:
            names = ", ".join(e.url.split("://", 1)[1] for e in entries if e is not None)
            raise proxylib.NoReachableProxyError("no proxy of the list is reachable: %s" % names) from None
    return entries[0]


def agent_proxy_argv(argv):
    """Extra LEADING argv so real curl uses the proxy syshttp would, or ``[]``.

    Injected when ``<P>_PROXY`` is set -- or, with no proxy variable at all,
    when the system's configuration names a proxy for the URL asked for (the
    command's first URL) -- and the caller did not name a proxy (``-x``) or a
    bypass list (``--noproxy``); a caller's ``-U`` keeps the proxy and
    replaces the credential. A header credential rides as ``--proxy-header``
    (sent to the proxy only, the CONNECT included), userinfo as
    ``--proxy-user``. ``NO_PROXY`` needs nothing: curl honours it even with
    ``--proxy``. A prefix gateway is not expressible to curl -- see
    :func:`use_real_curl`."""
    from syshttp import proxy

    env = HTTPEnv()
    configured = bool(env.get(proxy.PROXY))
    if not configured and env.conventional(*_PROXY_VARIABLES):
        return []  # curl reads the variable itself
    from curlike import caller_steering, requested_url

    given, noproxy, proxy_user = caller_steering(argv)
    if given or noproxy is not None:
        return []
    if configured:
        entry = proxy.configured_proxy()
    else:
        url = requested_url(argv)
        if not url:
            return []
        entry = _first_reachable(url)
    if entry is None or entry.is_gateway:
        return []
    extra = ["--proxy", entry.url]
    if proxy_user:
        pass  # curl already has -U on its own argv
    elif entry.headers:
        for name, value in entry.headers.items():
            extra += ["--proxy-header", "%s: %s" % (name, value)]
    elif entry.username is not None:
        extra += ["--proxy-user", "%s:%s" % (entry.username, entry.password or "")]
    return extra


def use_real_curl(argv):
    """Real curl handles everything except a prefix gateway: it has no notion
    of ``<proxy><endpoint><url>``, and rewriting the URL argument by hand
    cannot cover multi-URL invocations, ``-L`` (curl would follow the
    gateway's Location straight to the origin, credential attached) or
    config files. So with the configured proxy in play and ``prefix``
    configured, curlike serves the request, speaking the gateway per hop."""
    from syshttp import proxy

    if not HTTPEnv().get(proxy.PROXY):
        return True
    from curlike import caller_steering

    given, noproxy, _ = caller_steering(argv)
    if given or noproxy is not None:
        return True
    return proxy.proxy_type()[0] != "prefix"


def maybe_run_system_curl(argv, entry=None):
    """Run the real curl with the configured proxy applied; ``None`` when
    curlike must serve the request (no real curl, or a prefix gateway). A
    malformed proxy configuration never blocks real curl: it runs with argv
    as typed, after one warning."""
    curl_path = find_real_curl(entry)
    if not curl_path:
        return None
    revoke = revoke_argv(curl_path, argv)  # a bad <P>_CURL_SSL_REVOKE: exit 2
    try:
        import proxylib

        try:
            if not use_real_curl(argv):
                return None
            extra = agent_proxy_argv(argv)
        except proxylib.NoReachableProxyError as exc:
            # Nothing to hand real curl: no proxy the system names answers,
            # and its list holds no DIRECT. curl's own exit for that.
            print("curl: (7) %s" % exc, file=sys.stderr)
            return 7
    except (ValueError, ImportError) as exc:
        print("curl: ignoring the configured proxy: %s" % exc, file=sys.stderr)
        extra = []
    # curl skips its config file only when -q / --disable is its FIRST
    # argument (any "-q..." cluster, e.g. -qs); anywhere else it is ignored,
    # so the injected proxy options go after it.
    lead = argv[:1] if argv and (argv[0].startswith("-q") or argv[0] == "--disable") else []
    return subprocess.run([curl_path, *lead, *revoke, *extra, *argv[len(lead) :]]).returncode


def run_real_curl_as_typed(argv, entry=None):
    """``<P>_CURL=0``: the real curl with argv untouched, or exit 2 when
    there is none -- the caller asked for the real thing."""
    curl_path = find_real_curl(entry)
    if not curl_path:
        print(
            "curl: (2) %s=%s asks for the real curl, which is not on PATH" % (_var(SHIM), HTTPEnv().get(SHIM)),
            file=sys.stderr,
        )
        return 2
    return subprocess.run([curl_path, *argv]).returncode


def maybe_run_hook(argv, entry=None):
    """``<P>_HOOKS_<KEY>`` + ``_CURL``: when the caller's URL matches, run the
    wrapper in curl's place with argv appended and, in its environment, the
    ``<P>_HOOK_*`` variables of ``syshttp.hooks.wrapper_environment``
    (``<P>_HOOK_CURL`` is ``entry``: the executable to call curl back with).
    Returns the wrapper's exit code, or ``None`` when no hook applies."""
    from syshttp import hooks

    if not hooks.hooks_from_env():
        return None  # no hook configured: argv need not be read
    from curlike import requested_url

    url = requested_url(argv)
    if not url:
        return None
    matched = hooks.matching(url, kind="curl")
    if not matched:
        return None
    hook = matched[0]
    command = hooks.curl_command(hook, python=HTTPEnv().get(PYTHON) or sys.executable)
    env = dict(os.environ)
    env.update(hooks.wrapper_environment(hook, url, entry or ""))
    return subprocess.run([*command, *argv], env=env).returncode


def main(argv=None, *, entry=None):
    """curl's exit code. ``entry`` -- the launcher's own path, skipped when
    looking for the real curl and handed to hook wrappers."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not shim_enabled():
        return run_real_curl_as_typed(argv, entry)
    try:
        hook_rc = maybe_run_hook(argv, entry)
        if hook_rc is not None:
            return hook_rc
        system_curl_rc = maybe_run_system_curl(argv, entry)
        if system_curl_rc is not None:
            return system_curl_rc
    except (ValueError, ImportError) as exc:
        # A configuration error (a bad <P>_PROXY / <P>_PROXY_TYPE that would
        # be used), or a URL hook with no curlike to read argv with, is
        # curl's exit 2, one line on stderr -- not a traceback.
        print("curl: (2) %s" % exc, file=sys.stderr)
        return 2
    from curlike import main as curlike_main

    return curlike_main(argv)
