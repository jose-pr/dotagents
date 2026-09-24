"""OS-trust-store ``certifi`` shim (NOT the real cert bundle).

Drop-in for libraries that ``import certifi`` and call ``where()`` to find a CA
bundle. It never ships or reuses a packaged bundle; it names the operating
system's own trust store:

    an explicit override ($SSL_CERT_FILE, $REQUESTS_CA_BUNDLE, $CURL_CA_BUNDLE)
    ->  the system's bundle file (OpenSSL's default, then well-known OS paths)
    ->  a bundle BUILT from the system's certificates when it has no such file:
        the Windows ROOT/CA stores, a hashed certs directory ($SSL_CERT_DIR,
        OpenSSL's default capath, /etc/ssl/certs), and whatever OpenSSL loads
        by default -- written to a per-user temp file, a cache rebuilt when it is
        older than a minute

Every OS ships a trust store; one without any (a bare container) is configured
by pointing an override variable at a bundle. With nothing at all,
``where()`` returns ``None`` and says once, on stderr, which variables to set.

``dotagents env`` puts ``overlays/net/lib`` on ``PYTHONPATH``, ahead of any real
``certifi``, so a bundled ``requests`` verifies TLS against the OS trust store.
Pure stdlib; Python 3.9+.
"""
import glob
import os
import re
import ssl
import sys
import tempfile
import time

#: Variables naming a CA bundle FILE, in the order they are honoured.
OVERRIDE_VARS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")

# Well-known CA bundle files across common Linux/BSD distributions and macOS,
# tried after OpenSSL's compiled-in default file.
COMMON_PATHS = [
    "/etc/ssl/certs/ca-certificates.crt",   # Debian/Ubuntu/Alpine
    "/etc/pki/tls/certs/ca-bundle.crt",     # Fedora/RHEL
    "/etc/ssl/ca-bundle.pem",               # OpenSUSE
    "/etc/ssl/cert.pem",                    # OpenBSD/Alpine/macOS
    "/usr/local/share/certs/ca-root-nss.crt",  # FreeBSD
    "/etc/pki/tls/cert.pem",                # RHEL variant
]

#: Hashed-certificate directories read when building a bundle.
COMMON_DIRS = ["/etc/ssl/certs", "/etc/pki/tls/certs", "/usr/local/share/certs"]

#: The Windows stores read, and how long a built bundle is reused (seconds):
#: a short-lived cache, so a certificate added to or removed from the system
#: store is picked up within a minute.
WINDOWS_STORES = ("ROOT", "CA")
BUILT_BUNDLE_MAX_AGE = 60
_SERVER_AUTH = "1.3.6.1.5.5.7.3.1"
_PEM_RE = re.compile(
    r"-----BEGIN CERTIFICATE-----\s.*?-----END CERTIFICATE-----", re.DOTALL
)


def built_bundle_path():
    """Where the built bundle goes: a per-user location, never a shared
    ``/tmp`` (a trust list another user could replace)."""
    if sys.platform == "win32":
        base = tempfile.gettempdir()  # %TEMP%: per user
    else:
        base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(
            os.path.expanduser("~"), ".cache"
        )
    return os.path.join(base, "dotagents-net", "cacert.pem")


def _windows_certs():
    ders = []
    for store in WINDOWS_STORES:
        try:
            entries = ssl.enum_certificates(store)
        except (AttributeError, OSError):
            continue
        for der, encoding, trust in entries:
            if encoding == "x509_asn" and (trust is True or _SERVER_AUTH in trust):
                ders.append(der)
    return ders


def _dir_certs(directories):
    ders = []
    for directory in directories:
        if not directory or not os.path.isdir(directory):
            continue
        for path in sorted(glob.glob(os.path.join(directory, "*"))):
            try:
                with open(path, encoding="ascii", errors="ignore") as f:
                    text = f.read()
            except OSError:  # a directory, a dangling link, unreadable
                continue
            for pem in _PEM_RE.findall(text):
                try:
                    ders.append(ssl.PEM_cert_to_DER_cert(pem))
                except ValueError:
                    continue
    return ders


def _default_context_certs():
    try:
        ctx = ssl.create_default_context()
        return ctx.get_ca_certs(binary_form=True)
    except (ssl.SSLError, OSError):
        return []


def system_certs():
    """Every CA certificate the OS trusts that the stdlib can reach, as DER,
    deduplicated in first-seen order."""
    paths = ssl.get_default_verify_paths()
    ders = []
    if sys.platform == "win32":
        ders += _windows_certs()
    ders += _dir_certs([os.environ.get("SSL_CERT_DIR"), paths.capath,
                        paths.openssl_capath, *COMMON_DIRS])
    ders += _default_context_certs()
    seen, unique = set(), []
    for der in ders:
        if der not in seen:
            seen.add(der)
            unique.append(der)
    return unique


def build_bundle(path=None):
    """Write :func:`system_certs` to ``path`` (default :func:`built_bundle_path`)
    as a PEM bundle, atomically, in a directory only this user can write.
    Returns the path, or ``None`` when the system has no certificates."""
    ders = system_certs()
    if not ders:
        return None
    path = path or built_bundle_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="cacert.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="\n") as f:
            f.write("".join(ssl.DER_cert_to_PEM_cert(der) for der in ders))
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None
    return path


def _built_bundle():
    path = built_bundle_path()
    try:
        fresh = (time.time() - os.path.getmtime(path) < BUILT_BUNDLE_MAX_AGE
                 and os.path.getsize(path) > 0)
    except OSError:
        fresh = False
    if fresh:
        return path
    return build_bundle(path) or (path if os.path.isfile(path) else None)


_warned = False


def _warn_no_source():
    global _warned
    if not _warned:
        _warned = True
        sys.stderr.write(
            "certifi (dotagents net overlay): the system has no CA certificates; "
            "set one of %s to a bundle\n" % ", ".join("$" + v for v in OVERRIDE_VARS)
        )


def where():
    """Return the path of a CA bundle FILE from the OS trust store, or ``None``
    (with a one-time note on stderr) when the system has no certificates and
    no override names a bundle.

    Resolution order: the first of :data:`OVERRIDE_VARS` naming an existing
    file -> ``ssl.get_default_verify_paths().cafile`` -> the first existing
    file in ``COMMON_PATHS`` -> a bundle built from the system's certificates
    (:func:`build_bundle`), cached for a minute. Never raises: ``requests`` calls
    this at import time."""
    for var in OVERRIDE_VARS:
        value = os.environ.get(var)
        if value and os.path.isfile(value):
            return value
    cafile = ssl.get_default_verify_paths().cafile
    if cafile and os.path.isfile(cafile):
        return cafile
    for path in COMMON_PATHS:
        if os.path.isfile(path):
            return path
    built = _built_bundle()
    if built:
        return built
    _warn_no_source()
    return None


def contents():
    """Return the bytes of the resolved CA bundle, or ``b""`` if none found."""
    cert_path = where()
    if cert_path and os.path.isfile(cert_path):
        with open(cert_path, "rb") as f:
            return f.read()
    return b""


__all__ = ["where", "contents"]
