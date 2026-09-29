"""Connection options: proxy steering, timeouts, redirects, ``--retry``, and
the protocol no-ops (``-g``, ``--http1.1``, ``-q``)."""
import errno
import socket
from typing import Optional

from httplib.retry import TRANSIENT_STATUSES

from ._duho import NS, Arg
from .args import Group
from .errors import EXIT_CONNECT, EXIT_TIMEOUT

#: curl's longest backoff between retries, in seconds.
MAX_RETRY_SLEEP = 600


def _refused(reason):
    if isinstance(reason, ConnectionRefusedError):
        return True
    return getattr(reason, 'errno', None) in (errno.ECONNREFUSED, 10061) or 'refused' in str(reason)


class ConnectionArgs(Group):
    """Proxy, timeouts, redirects and retries."""

    proxy: Optional[str] = None
    "Use proxy"
    ("-x", "--proxy")

    proxy_user: Arg[Optional[str], NS(metavar='USER[:PASS]')] = None
    "Proxy user and password"
    ("-U", "--proxy-user")

    noproxy: Arg[Optional[str], NS(metavar='HOSTS')] = None
    "Hosts that bypass the proxy (* for all); replaces NO_PROXY"
    ("--noproxy",)

    location: bool = False
    "Follow redirects"
    ("-L", "--location")

    max_redirs: Optional[int] = None
    "Maximum number of redirects to follow with -L"
    ("--max-redirs",)

    location_trusted: bool = False
    "With -L, keep sending credentials (Authorization, Cookie) to other hosts"
    ("--location-trusted",)

    post301: bool = False
    "Keep POST on a 301 redirect (default: GET)"
    ("--post301",)

    post302: bool = False
    "Keep POST on a 302 redirect (default: GET)"
    ("--post302",)

    post303: bool = False
    "Keep POST on a 303 redirect (default: GET)"
    ("--post303",)

    timeout: float = 30.0
    "Request timeout in seconds"
    ("--timeout",)

    max_time: Optional[float] = None
    "Maximum time in seconds for the whole request"
    ("-m", "--max-time")

    connect_timeout: Optional[float] = None
    "Connect timeout in seconds"
    ("--connect-timeout",)

    retry: Arg[Optional[int], NS(metavar='NUM')] = None
    "Retry a transient failure (timeout, HTTP 408/429/500/502/503/504) NUM times"
    ("--retry",)

    retry_delay: Arg[Optional[float], NS(metavar='SECONDS')] = None
    "Wait this long between retries (default: 1s, doubling)"
    ("--retry-delay",)

    retry_max_time: Arg[Optional[float], NS(metavar='SECONDS')] = None
    "Retry only within this many seconds"
    ("--retry-max-time",)

    retry_connrefused: bool = False
    "Also retry a refused connection"
    ("--retry-connrefused",)

    retry_all_errors: bool = False
    "Retry on any error (with -f, an HTTP error status too)"
    ("--retry-all-errors",)

    # No-ops: the fallback reads no config file, never globs, and speaks HTTP/1.1.
    disable: bool = False
    "Skip the curl config file (a no-op: the fallback reads none)"
    ("-q", "--disable")

    globoff: bool = False
    "No URL globbing (a no-op: none is done)"
    ("-g", "--globoff")

    http1_1: bool = False
    "Use HTTP/1.1 (a no-op: it always does)"
    ("--http1.1",)

    def request_timeout(self):
        """One timeout, as urllib has one: -m wins, then --connect-timeout,
        then the shim's own --timeout."""
        return self.max_time or self.connect_timeout or self.timeout

    def retry_reason_for_error(self, code, reason):
        """curl's wording when a transport failure is worth another attempt,
        else ``None``."""
        if code == EXIT_TIMEOUT or isinstance(reason, socket.timeout):
            return ': timeout'
        if code == EXIT_CONNECT and self.retry_connrefused and _refused(reason):
            return ': connection refused'
        if self.retry_all_errors:
            return ' (retrying all errors)'
        return None

    def retry_reason_for_status(self, status):
        """... and when an HTTP response is (a transient status, or -- with
        --retry-all-errors -- one that -f makes an error)."""
        if status in TRANSIENT_STATUSES:
            return ': HTTP error'
        if self.retry_all_errors and self.fails_on(status):
            return ' (retrying all errors)'
        return None

    def retry_wait(self, backoff, after):
        """Seconds before the next attempt: the server's Retry-After, else
        --retry-delay, else the doubling backoff."""
        if after is not None:
            return after
        return self.retry_delay if self.retry_delay else backoff
