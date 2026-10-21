"""The overlay's ``curl``: the real curl with the agent proxy added, else
curlike's own (``pycurl``), called by ``bin/curl.py`` as :func:`main`.

  1. **A ``SYSHTTP_HOOKS_<KEY>_CURL`` wrapper** whose pattern matches the URL
     runs in curl's place, argv appended. A ``.cmd`` / ``.bat`` wrapper is
     refused on Windows: cmd.exe would parse the URL's ``&`` again.
  2. **The real curl, when one is on PATH** (never this launcher), with the
     caller's argv and its exit code -- with ONE addition per ``--next``
     group: the proxy syshttp would use for the group's URL (``SYSHTTP_PROXY``,
     a ``*_PROXY`` variable under the prefix, else the system's
     configuration), unless the group steers the proxy itself or only curl's
     own unprefixed variables name one. ``--proxy`` is added and its
     credential goes in a private ``-K`` config file (never on the command
     line, which other accounts can read); and for a Schannel curl (Windows),
     ``--ssl-revoke-best-effort`` (``CURL_SSL_REVOKE``). A leading ``-q``
     stays first. ``CURL=0`` runs it exactly as typed.
  3. **Else curlike** (``curlike.main``): the common curl surface over the
     standard library. So is a request through a ``prefix`` gateway, which
     curl cannot express.

The real curl runs with ``CURL_SHIM=1`` in its environment: if "the real
curl" is in fact a wrapper that calls this launcher again, that call goes to
curlike instead of looking for curl once more.

Every setting is read as ``<PREFIX><NAME>`` for each prefix of
``SYSHTTP_PREFIX`` (``AGENTS_`` in this overlay: ``AGENTS_CURL``,
``AGENTS_SYSHTTP_PROXY``). Reading argv the way curl does needs curlike;
nothing here imports curlike or syshttp until a request needs them, so
:func:`find_real_curl` works without either.
"""

import os
import shutil
import subprocess
import sys
import tempfile

#: The real curl's revocation check, ``CURL_SSL_REVOKE``: ``best-effort``
#: (default), ``true`` or ``false``. Only a Schannel curl is affected.
REVOKE = "CURL_SSL_REVOKE"
_REVOKE_VALUES = ("best-effort", "true", "false")
_REVOKE_FLAGS = ("--ssl-no-revoke", "--ssl-revoke-best-effort")
#: ``CURL``: ``0``/``n``/``no``/``false``/``off`` run the real curl exactly
#: as typed -- no configured proxy, no hooks, no curlike; ``1``/``y``/``yes``/
#: ``true``/``on`` or unset is this launcher; anything else is exit 2.
SHIM = "CURL"
_OFF = ("0", "n", "no", "false", "off")
_ON = ("", "1", "y", "yes", "true", "on")
#: ``CURL_SHIM=1``: set in the real curl's environment; a launcher started
#: with it serves the request with curlike and never looks for curl.
MARKER = "CURL_SHIM"
#: ``PYTHON``: the interpreter a ``.py`` hook wrapper runs under; default
#: this one.
PYTHON = "PYTHON"
# The variables real curl reads for its proxy, as curl names them (no prefix):
# with one set and no agent setting, curl uses the variable.
_CURL_PROXY_VARIABLES = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")
# The variables syshttp's proxy map reads under the prefixes besides SYSHTTP_PROXY.
_MAP_PROXY_VARIABLES = tuple(
    n for s in ("HTTP", "HTTPS", "FTP", "WS", "WSS", "ALL") for n in (s.lower() + "_proxy", s + "_PROXY")
)
_BATCH = (".cmd", ".bat")
# Options that choose the proxy, besides the ones curlike.caller_steering reads:
# with any of them the caller steers, and nothing is injected.
_STEERING = ("--proxy1.0", "--preproxy", "--config")
_NEXT = ("--next", "-:")


def _env():
    from syshttp.env import HTTPEnv

    return HTTPEnv()


def _var(setting, env=None):
    """The variable to name in a message about ``setting``: the one set, else
    the name under the first prefix."""
    env = _env() if env is None else env
    found = env.find(setting)
    return found[0] if found else env.name(setting)


def shim_enabled():
    """``CURL`` as a boolean; an unrecognised value is a ``ValueError``
    naming the variable."""
    env = _env()
    raw = (env.get(SHIM) or "").lower()
    if raw in _OFF:
        return False
    if raw in _ON:
        return True
    raise ValueError(
        "%s=%s: expected one of %s to turn the launcher off, or %s"
        % (_var(SHIM, env), env.get(SHIM), "/".join(_OFF), "/".join(v for v in _ON if v))
    )


def find_real_curl(entry=None):
    """The system curl, or ``None`` -- never this launcher. PATH is walked
    directory by directory without resolving links: ``entry``'s directory is
    skipped, and so is any directory with a ``curl.py`` (a launcher like this
    one). On Windows only ``PATHEXT`` executables count, never ``.cmd`` /
    ``.bat``; elsewhere the file must be executable. The current directory is
    never searched, nor is any relative PATH entry."""
    skip = os.path.normcase(os.path.dirname(os.path.abspath(entry))) if entry else None
    if os.name == "nt":
        exts = [e for e in os.environ.get("PATHEXT", ".COM;.EXE").lower().split(";") if e and e not in _BATCH]
    else:
        exts = [""]
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory or not os.path.isabs(directory):
            continue
        if skip is not None and os.path.normcase(os.path.abspath(directory)) == skip:
            continue
        if os.path.isfile(os.path.join(directory, "curl.py")):
            continue
        for ext in exts:
            candidate = os.path.join(directory, "curl" + ext)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


def revoke_argv(curl_path, argv):
    """The revocation flag to add for ``curl_path`` (see :data:`REVOKE`):
    only for a Schannel curl (Windows), only when the caller chose none, and
    best-effort only where curl has it (7.70). An unknown value: ValueError."""
    env = _env()
    raw = env.get(REVOKE)
    mode = (raw or "best-effort").lower()
    if mode not in _REVOKE_VALUES:
        raise ValueError("%s=%s: expected one of %s" % (_var(REVOKE, env), raw, ", ".join(_REVOKE_VALUES)))
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
    """syshttp's entry for ``url``: the only one, or of a list the first
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


def _steers(group):
    """Whether the group chooses its proxy itself: curlike's reading of
    ``-x``/``--proxy``/``--socks*``/``--noproxy``, or a ``--proxy1.0``,
    ``--preproxy`` or config file (``-K``), whose ``proxy`` would win over an
    injected one and receive the configured credential. Reading too much as
    steering only means nothing is injected."""
    from curlike import caller_steering

    given, noproxy, _ = caller_steering(group)
    if given or noproxy is not None:
        return True
    for arg in group:
        if arg.startswith(_STEERING):
            return True
        if arg.startswith("-") and not arg.startswith("--") and "K" in arg[1:]:
            return True
    return False


def _agent_routing(env):
    """Whether an agent setting names the proxy: ``SYSHTTP_PROXY`` or a
    ``*_PROXY`` variable, read under the prefixes."""
    from syshttp import proxy

    return env.get(proxy.PROXY, *_MAP_PROXY_VARIABLES) is not None


def _quote(value):
    """A value for a curl config file: double-quoted, ``\\`` and ``"`` escaped."""
    return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')


def agent_proxy_argv(group):
    """``(extra leading argv, config lines)`` so real curl uses, for this
    ``--next`` group, the proxy syshttp would; ``([], [])`` when nothing is
    injected.

    The proxy is syshttp's answer for the group's URL (its ``NO_PROXY``
    under the prefix applied; ``SYSHTTP_PROXY`` alone when the group names
    no URL). Nothing is injected when the group steers the proxy
    (:func:`_steers`), or when no agent setting names one and curl's own
    unprefixed variables do (curl reads them itself). A caller's ``-U``
    keeps the proxy and replaces the credential. The credential is a config
    line (``proxy-header`` for a header credential, ``proxy-user`` for
    userinfo), never an argument. A prefix gateway is not expressible to
    curl -- see :func:`use_real_curl`."""
    from syshttp import proxy

    env = _env()
    if not group:
        return [], []
    if not _agent_routing(env) and any(os.environ.get(n, "").strip() for n in _CURL_PROXY_VARIABLES):
        return [], []
    if _steers(group):
        return [], []
    from curlike import caller_steering, requested_url

    proxy_user = caller_steering(group)[2]
    url = requested_url(group)
    if url:
        entry = _first_reachable(url)
    elif env.get(proxy.PROXY):
        entry = proxy.configured_proxy()
    else:
        return [], []
    if entry is None or entry.is_gateway:
        return [], []
    config = []
    if proxy_user:
        pass  # curl already has -U on its own argv
    elif entry.headers:
        config = ["proxy-header = %s" % _quote("%s: %s" % item) for item in entry.headers.items()]
    elif entry.username is not None:
        config = ["proxy-user = %s" % _quote("%s:%s" % (entry.username, entry.password or ""))]
    return ["--proxy", entry.url], config


def use_real_curl(argv):
    """Real curl handles everything except a prefix gateway: it has no notion
    of ``<proxy><endpoint><url>``, and rewriting the URL argument by hand
    cannot cover multi-URL invocations, ``-L`` (curl would follow the
    gateway's Location straight to the origin, credential attached) or
    config files. So with the configured proxy in play and ``prefix``
    configured, curlike serves the request, speaking the gateway per hop."""
    from syshttp import proxy

    if not _env().get(proxy.PROXY):
        return True
    if _steers(argv):
        return True
    return proxy.proxy_type()[0] != "prefix"


def _groups(argv):
    """argv split at ``--next`` / ``-:`` (each separator starts its group)."""
    groups = [[]]
    for arg in argv:
        if arg in _NEXT:
            groups.append([])
        groups[-1].append(arg)
    return groups


def _lead(group):
    """``(lead, rest)``: what must stay first in a group -- a leading ``-q``
    (curl skips ``.curlrc`` only for a first ``-q``, and a ``-q...`` cluster
    is split so that what follows ``q`` keeps its value), or a ``--next``."""
    if not group:
        return [], group
    first = group[0]
    if first in _NEXT:
        return [first], group[1:]
    if first == "--disable" or first == "-q":
        return [first], group[1:]
    if first.startswith("-q") and not first.startswith("--"):
        return ["-q"], ["-" + first[2:], *group[1:]]
    return [], group


def _run(command, label, env=None):
    """``command``'s exit code, curl's way: a signal death N is 128 + N. One
    that cannot be started is a ``ValueError`` naming ``label`` (exit 2)."""
    try:
        code = subprocess.run(command, env=env).returncode
    except OSError as exc:
        raise ValueError("%s: cannot run %s: %s" % (label, command[0], exc.strerror or exc)) from None
    return 128 - code if code < 0 else code


def _child_env():
    """This environment with the recursion marker set."""
    env = dict(os.environ)
    env[_env().name(MARKER)] = "1"
    return env


def maybe_run_system_curl(argv, entry=None):
    """Run the real curl with the configured proxy applied; ``None`` when
    curlike must serve the request (no real curl, or a prefix gateway). A
    malformed proxy configuration never blocks real curl: it runs with argv
    as typed, after one warning."""
    curl_path = find_real_curl(entry)
    if not curl_path:
        return None
    revoke = revoke_argv(curl_path, argv)  # a bad CURL_SSL_REVOKE: exit 2
    groups = _groups(argv)
    plans = []
    try:
        import proxylib

        try:
            if not use_real_curl(argv):
                return None
            plans = [agent_proxy_argv(group) for group in groups]
        except proxylib.NoReachableProxyError as exc:
            # Nothing to hand real curl: no proxy the system names answers,
            # and its list holds no DIRECT. curl's own exit for that.
            print("curl: (7) %s" % exc, file=sys.stderr)
            return 7
    except (ValueError, ImportError) as exc:
        print("curl: ignoring the configured proxy: %s" % exc, file=sys.stderr)
        plans = []
    workdir = None
    try:
        command = [curl_path]
        for index, group in enumerate(groups):
            extra, config = plans[index] if plans else ([], [])
            if config:
                if workdir is None:
                    workdir = tempfile.mkdtemp(prefix="curlshim-")
                path = os.path.join(workdir, "group%d.curlrc" % index)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write("\n".join(config) + "\n")
                extra = [*extra, "-K", path]
            lead, rest = _lead(group)
            command += [*lead, *revoke, *extra, *rest]
        return _run(command, "the real curl", env=_child_env())
    finally:
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)


def run_real_curl_as_typed(argv, entry=None):
    """``CURL=0``: the real curl with argv untouched, or exit 2 when there is
    none -- the caller asked for the real thing."""
    curl_path = find_real_curl(entry)
    if not curl_path:
        env = _env()
        print(
            "curl: (2) %s=%s asks for the real curl, which is not on PATH" % (_var(SHIM, env), env.get(SHIM)),
            file=sys.stderr,
        )
        return 2
    return _run([curl_path, *argv], "the real curl")


def maybe_run_hook(argv, entry=None):
    """``SYSHTTP_HOOKS_<KEY>`` + ``_CURL``: when the caller's URL matches, run
    the wrapper in curl's place with argv appended and, in its environment,
    the ``SYSHTTP_HOOK_*`` variables of ``syshttp.hooks.wrapper_environment``
    (``SYSHTTP_HOOK_CURL`` is ``entry``: the executable to call curl back
    with). Returns the wrapper's exit code, or ``None`` when no hook applies."""
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
    label = hook.variable + hooks.CURL_SUFFIX
    command = hooks.curl_command(hook, python=_env().get(PYTHON) or sys.executable)
    if os.name == "nt" and os.path.splitext(command[0])[1].lower() in _BATCH:
        raise ValueError("%s: a .cmd/.bat wrapper cannot take a URL safely; use a .py or .exe" % label)
    env = dict(os.environ)
    env.update(hooks.wrapper_environment(hook, url, entry or ""))
    return _run([*command, *argv], label, env=env)


def main(argv=None, *, entry=None):
    """curl's exit code. ``entry`` -- the launcher's own path, skipped when
    looking for the real curl and handed to hook wrappers. Ctrl-C is 130,
    quietly, as curl's own."""
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        return _main(argv, entry)
    except KeyboardInterrupt:
        return 130


def _main(argv, entry):
    try:
        if os.environ.get(_env().name(MARKER)):
            # Started by a curl this launcher ran: never look for curl again.
            from curlike import main as curlike_main

            return curlike_main(argv)
        if not shim_enabled():
            return run_real_curl_as_typed(argv, entry)
        hook_rc = maybe_run_hook(argv, entry)
        if hook_rc is not None:
            return hook_rc
        system_curl_rc = maybe_run_system_curl(argv, entry)
        if system_curl_rc is not None:
            return system_curl_rc
    except (ValueError, ImportError) as exc:
        # A configuration error (a bad SYSHTTP_PROXY / SYSHTTP_PROXY_TYPE that
        # would be used, an unknown CURL, a malformed SYSHTTP_PREFIX), a
        # wrapper that cannot start, or no curlike to read argv with: curl's
        # exit 2, one line on stderr.
        print("curl: (2) %s" % exc, file=sys.stderr)
        return 2
    from curlike import main as curlike_main

    return curlike_main(argv)
