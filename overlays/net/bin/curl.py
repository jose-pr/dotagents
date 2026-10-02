"""A drop-in ``curl``, run by bin/curl (POSIX sh) and bin/curl.cmd (Windows).

  1. **The real curl, when one is on PATH** (never this shim). Run with the
     caller's argv and its exit code returned -- with ONE addition: when
     ``AGENTS_PROXY`` is set and the caller does not steer the proxy
     (``-x``/``--proxy*``/``--noproxy``/``-U``), ``--proxy`` and its
     credential are added, since real curl reads only the global proxy vars.
     A leading ``-q`` stays first. ``NET_CURL=0`` runs it exactly as typed.
  2. **Else the net overlay's fallback**, ``httplib.cli`` (``python -m
     httplib``): the common curl surface over the standard library and duho.
     So is a request through a ``prefix`` gateway, which curl cannot express.

A ``NET_HOOKS_<KEY>_CURL`` wrapper runs in place of both for the URLs it
matches. Python 3.9+; the real-curl path needs duho only when the agent proxy
or a URL hook is in play (to read argv the way curl does).
"""
import os
import subprocess
import sys
from pathlib import Path

# The overlay ships lib/ as a sibling of bin/: httplib (and certifi) live there.
LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from httplib import hooks as net_hooks  # noqa: E402  (pure stdlib; via LIB_DIR)
from httplib import proxy as agent_proxy  # noqa: E402  (pure stdlib; via LIB_DIR)
from httplib.cli import main as fallback_main  # noqa: E402  (imports duho only when run)
from httplib.cli import run_fallback  # noqa: E402,F401  (re-exported for callers and tests)

#: ``NET_CURL=0`` / ``n`` / ``no`` / ``false`` / ``off``: run the real curl
#: exactly as typed -- no agent proxy, no hooks, no fallback. Anything else
#: (unset, ``1``, ``y``) is the shim.
SHIM_ENV = 'NET_CURL'
_OFF = ('0', 'n', 'no', 'false', 'off')


def shim_enabled():
    return (os.environ.get(SHIM_ENV) or '').strip().lower() not in _OFF


def caller_steering(argv):
    """``(proxy, noproxy, proxy_user)`` as the caller wrote them (see
    ``httplib.cli.argv``)."""
    from httplib.cli.argv import caller_steering as steering

    return steering(argv)


def requested_url(argv):
    """The URL the caller asked for, curl's default scheme applied, or ``None``."""
    from httplib.cli.argv import requested_url as requested

    return requested(argv)


def find_real_curl():
    """The system curl, or ``None`` -- never a copy of this shim. With the
    overlay's ``bin/`` on PATH (what ``dotagents env`` does) a bare
    ``which("curl")`` finds ``bin/curl`` / ``bin/curl.cmd`` first, i.e.
    ourselves, and the shim would exec itself forever. PATH is walked
    directory by directory, skipping this file's own directory, any hit that
    is not actually inside the directory searched (Windows ``which`` on
    Python < 3.12 consults the cwd too) and any hit that has a ``curl.py``
    beside it (another installed copy of the shim -- a project-scope net
    overlay next to the user-scope one would otherwise exec each other)."""
    from httplib.cli.compat import find_real_curl as find

    return find(skip=Path(__file__).resolve().parent)


def agent_proxy_argv(argv):
    """Extra LEADING argv so real curl uses the agent proxy, or ``[]``.

    Real curl reads only the global proxy vars (``http_proxy`` & co.), never
    ``AGENTS_PROXY``, so a bare passthrough would send the agent's traffic
    the wrong way. Injected only when ``AGENTS_PROXY`` is set and the caller
    did not name a proxy (``-x``) or a bypass list (``--noproxy``); a
    caller's ``-U`` keeps the proxy but replaces the credential. A configured
    header value rides as ``--proxy-header`` (sent to the proxy only, the
    CONNECT included); URL userinfo as ``--proxy-user``. ``NO_PROXY`` needs
    nothing: curl honours it even with ``--proxy``. A prefix gateway is not
    expressible to curl -- see :func:`use_real_curl`."""
    if not os.environ.get('AGENTS_PROXY'):
        return []
    proxy, noproxy, proxy_user = caller_steering(argv)
    if proxy or noproxy is not None:
        return []
    plan = agent_proxy.plan(proxy_user=proxy_user)
    if plan is None or plan.endpoint is not None:
        return []
    extra = ['--proxy', plan.proxy]
    if proxy_user:
        pass  # curl already has -U on its own argv
    elif agent_proxy.configured_authorization():
        extra += ['--proxy-header', '%s: %s' % (plan.auth_header, plan.authorization)]
    else:
        creds = agent_proxy.userinfo(agent_proxy.proxy_url())
        if creds:
            extra += ['--proxy-user', '%s:%s' % creds]
    return extra


def use_real_curl(argv):
    """Real curl handles everything except a prefix gateway: it has no notion
    of ``<proxy><endpoint><url>``, and rewriting the URL argument by hand
    cannot cover multi-URL invocations, ``-L`` (curl would follow the
    gateway's Location straight to the origin, credential attached) or
    config files. So with the agent proxy in play and ``prefix`` configured,
    the request is served by the fallback, which speaks the gateway per hop;
    flags the fallback lacks fail loud there, as the shim's contract says."""
    if not os.environ.get('AGENTS_PROXY'):
        return True
    proxy, noproxy, _ = caller_steering(argv)
    if proxy or noproxy is not None:
        return True
    return agent_proxy.proxy_type()[0] != 'prefix'


def maybe_run_system_curl(argv):
    """Run the real curl with the agent proxy applied; ``None`` when the
    fallback must serve the request (no real curl, or a prefix gateway).
    A malformed proxy configuration -- or no duho to read argv with --
    never blocks real curl: it runs with argv as typed, after one warning."""
    curl_path = find_real_curl()
    if not curl_path:
        return None
    try:
        if not use_real_curl(argv):
            return None
        extra = agent_proxy_argv(argv)
    except (ValueError, ImportError) as exc:
        print('curl: ignoring the agent proxy: %s' % exc, file=sys.stderr)
        extra = []
    # curl skips its config file only when -q / --disable is its FIRST
    # argument (any "-q..." cluster, e.g. -qs); anywhere else it is ignored,
    # so the injected proxy options go after it.
    lead = argv[:1] if argv and (argv[0].startswith('-q') or argv[0] == '--disable') else []
    result = subprocess.run([curl_path, *lead, *extra, *argv[len(lead):]])
    return result.returncode


def run_real_curl_as_typed(argv):
    """``NET_CURL=0``: the real curl with argv untouched, or exit 2 when there
    is none -- the caller asked for the real thing, a fallback would be a
    surprise."""
    curl_path = find_real_curl()
    if not curl_path:
        print('curl: (2) %s=%s asks for the real curl, which is not on PATH' % (
            SHIM_ENV, os.environ.get(SHIM_ENV)), file=sys.stderr)
        return 2
    return subprocess.run([curl_path, *argv]).returncode


def shim_entry():
    """The platform entry of this shim (``curl`` / ``curl.cmd`` beside this
    file), what a wrapper is told in ``NET_HOOK_CURL``."""
    here = Path(__file__).resolve().parent
    return str(here / ('curl.cmd' if os.name == 'nt' else 'curl'))


def maybe_run_hook(argv):
    """``NET_HOOKS_<KEY>`` + ``_CURL``: when the caller's URL matches, run the
    wrapper in this shim's place with argv appended and, in its environment,
    ``NET_HOOK_URL`` (the requested URL), ``NET_HOOK_KEY`` (the hook),
    ``NET_HOOK_CURL`` (this shim, to call curl back with) and ``NET_HOOK_SKIP``
    carrying the KEY so that call runs the shim, not the wrapper again.
    Returns the wrapper's exit code, or ``None`` when no hook applies."""
    if not net_hooks.hooks_from_env():
        return None  # no hook configured: argv need not be read
    url = requested_url(argv)
    if not url:
        return None
    hooks = net_hooks.matching(url, kind='curl')
    if not hooks:
        return None
    hook = hooks[0]
    command = net_hooks.curl_command(hook, python=os.environ.get('AGENTS_PYTHON') or sys.executable)
    env = dict(os.environ)
    env[net_hooks.CURL_ENV] = shim_entry()
    env[net_hooks.URL_ENV] = url
    env[net_hooks.KEY_ENV] = hook.key
    env[net_hooks.SKIP_ENV] = ','.join(sorted(net_hooks.skipped(env) | {hook.key}))
    return subprocess.run([*command, *argv], env=env).returncode


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not shim_enabled():
        return run_real_curl_as_typed(argv)
    try:
        hook_rc = maybe_run_hook(argv)
        if hook_rc is not None:
            return hook_rc
        system_curl_rc = maybe_run_system_curl(argv)
        if system_curl_rc is not None:
            return system_curl_rc
    except (ValueError, ImportError) as exc:
        # A configuration error (a bad AGENTS_PROXY / AGENTS_PROXY_TYPE that
        # would be used), or a URL hook with no duho to read argv with, is
        # curl's exit 2, one line on stderr -- not a traceback.
        print('curl: (2) %s' % exc, file=sys.stderr)
        return 2
    return fallback_main(argv)


if __name__ == '__main__':
    sys.exit(main())
