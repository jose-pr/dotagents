"""curl's exit codes, as far as the fallback can tell failures apart, and the
two exceptions a transfer raises on its way to one."""

EXIT_USAGE = 2
EXIT_RESOLVE = 6
EXIT_CONNECT = 7
EXIT_HTTP = 22
EXIT_WRITE = 23
EXIT_READ = 26
EXIT_TIMEOUT = 28
EXIT_REDIRECTS = 47
EXIT_CLIENT_CERT = 58
EXIT_SSL_CONNECT = 35
EXIT_SSL = 60
EXIT_CACERT = 77


class LocalError(Exception):
    """A local file or name the transfer cannot use -- a CA bundle (77), a
    client certificate (58), an upload (26), an output (23). Reported like a
    transfer failure: one ``curl: (N)`` line, ``-w`` still written."""

    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code


class Retry(Exception):
    """A transient failure another ``--retry`` attempt may fix. ``what`` is
    curl's wording for the warning (``: timeout``, ``: HTTP error``, ...);
    ``after`` the server's ``Retry-After`` in seconds, or ``None``."""

    def __init__(self, what, after=None):
        Exception.__init__(self, what)
        self.what = what
        self.after = after
