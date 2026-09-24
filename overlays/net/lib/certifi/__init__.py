"""OS-trust-store ``certifi`` shim (NOT the real cert bundle).

Drop-in for libraries that ``import certifi`` and call ``where()`` to find a CA
bundle. Instead of shipping a multi-hundred-KB PEM, this resolves a bundle from
the operating system's own trust store:

    $SSL_CERT_FILE  ->  ssl.get_default_verify_paths()  ->  well-known OS paths
    ->  (Windows) the system ROOT/CA stores, exported to a cached PEM
    ->  the real ``certifi`` package's bundle, when one is installed

``dotagents env`` puts ``overlays/net/lib`` on ``PYTHONPATH``, ahead of any real
``certifi``, so a bundled ``requests`` verifies TLS against the OS trust store
with zero shipped certificates. That makes this module every session's
``certifi``, so ``where()`` must always name a usable bundle: Windows has no CA
file for OpenSSL to find, hence the export. Pure stdlib; Python 3.9+.
"""
import importlib.machinery
import importlib.util
import os
import ssl
import sys
import tempfile
import time

# Well-known CA bundle locations across common Linux/BSD distributions, tried
# after $SSL_CERT_FILE and OpenSSL's compiled-in defaults.
COMMON_PATHS = [
    "/etc/ssl/certs/ca-certificates.crt",   # Debian/Ubuntu/Alpine
    "/etc/pki/tls/certs/ca-bundle.crt",     # Fedora/RHEL
    "/etc/ssl/ca-bundle.pem",               # OpenSUSE
    "/etc/ssl/cert.pem",                    # OpenBSD/Alpine/macOS
    "/usr/local/share/certs/ca-root-nss.crt",  # FreeBSD
    "/etc/pki/tls/cert.pem",                # RHEL variant
]

#: The Windows stores exported, and how long an export is reused (seconds).
WINDOWS_STORES = ("ROOT", "CA")
WINDOWS_BUNDLE_MAX_AGE = 24 * 60 * 60
_SERVER_AUTH = "1.3.6.1.5.5.7.3.1"


def _windows_bundle_path():
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return os.path.join(base, "dotagents", "net", "cacert.pem")


def _export_windows_stores(path):
    """Write the Windows ROOT and CA stores' TLS-server certificates to ``path``
    as PEM (atomically). Returns ``path``, or None when the stores are empty or
    unreadable."""
    pems = []
    seen = set()
    for store in WINDOWS_STORES:
        try:
            entries = ssl.enum_certificates(store)
        except (AttributeError, OSError):
            continue
        for der, encoding, trust in entries:
            if encoding != "x509_asn" or der in seen:
                continue
            if trust is not True and _SERVER_AUTH not in trust:
                continue
            seen.add(der)
            pems.append(ssl.DER_cert_to_PEM_cert(der))
    if not pems:
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="cacert.", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="\n") as f:
            f.write("".join(pems))
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None
    return path


def _windows_bundle():
    path = _windows_bundle_path()
    try:
        fresh = time.time() - os.path.getmtime(path) < WINDOWS_BUNDLE_MAX_AGE
    except OSError:
        fresh = False
    if fresh and os.path.getsize(path) > 0:
        return path
    return _export_windows_stores(path) or (path if os.path.isfile(path) else None)


def _real_certifi_bundle():
    """The bundle of an installed ``certifi`` this shim shadows, if any."""
    here = os.path.normcase(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    search = [p for p in sys.path if os.path.normcase(os.path.abspath(p or ".")) != here]
    spec = importlib.machinery.PathFinder.find_spec("certifi", search)
    if spec is None or spec.origin is None:
        return None
    core = os.path.join(os.path.dirname(spec.origin), "cacert.pem")
    return core if os.path.isfile(core) else None


def where():
    """Return a path to a CA bundle from the OS trust store, or ``None`` when
    no source at all has one.

    Resolution order: ``$SSL_CERT_FILE`` (if it exists) ->
    ``ssl.get_default_verify_paths().cafile`` -> ``.capath`` -> the first
    existing entry in ``COMMON_PATHS`` -> on Windows, the ROOT/CA stores
    exported to ``%LOCALAPPDATA%\\dotagents\\net\\cacert.pem`` (refreshed
    daily) -> the real ``certifi`` package's ``cacert.pem``."""
    cert_file = os.environ.get("SSL_CERT_FILE")
    if cert_file and os.path.exists(cert_file):
        return cert_file
    default_paths = ssl.get_default_verify_paths()
    if default_paths.cafile and os.path.exists(default_paths.cafile):
        return default_paths.cafile
    if default_paths.capath and os.path.exists(default_paths.capath):
        return default_paths.capath
    for path in COMMON_PATHS:
        if os.path.exists(path):
            return path
    if sys.platform == "win32":
        exported = _windows_bundle()
        if exported:
            return exported
    return _real_certifi_bundle()


def contents():
    """Return the bytes of the resolved CA bundle, or ``b""`` if none found."""
    cert_path = where()
    if cert_path and os.path.isfile(cert_path):
        with open(cert_path, "rb") as f:
            return f.read()
    return b""


__all__ = ["where", "contents"]
