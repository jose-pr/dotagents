"""OS-trust-store ``certifi`` shim (NOT the real cert bundle).

Drop-in for libraries that ``import certifi`` and call ``where()`` to find a CA
bundle. Instead of shipping a multi-hundred-KB PEM, this resolves a bundle from
the operating system's own trust store:

    $SSL_CERT_FILE  ->  ssl.get_default_verify_paths()  ->  well-known OS paths

Put ``overlays/net/lib`` on ``PYTHONPATH`` ahead of any real ``certifi`` (the net
overlay's ``setup`` does this) so a bundled ``requests`` verifies TLS against the
OS trust store with zero shipped certificates. Pure stdlib; Python 3.9+.
"""
import os
import ssl

# Well-known CA bundle locations across common Linux/BSD distributions, tried
# after $SSL_CERT_FILE and OpenSSL's compiled-in defaults. (macOS/Windows verify
# through the platform APIs; ssl.get_default_verify_paths() covers those.)
COMMON_PATHS = [
    "/etc/ssl/certs/ca-certificates.crt",   # Debian/Ubuntu/Alpine
    "/etc/pki/tls/certs/ca-bundle.crt",     # Fedora/RHEL
    "/etc/ssl/ca-bundle.pem",               # OpenSUSE
    "/etc/ssl/cert.pem",                    # OpenBSD/Alpine/macOS
    "/usr/local/share/certs/ca-root-nss.crt",  # FreeBSD
    "/etc/pki/tls/cert.pem",                # RHEL variant
]


def where():
    """Return a path to a CA bundle from the OS trust store, or ``None``.

    Resolution order: ``$SSL_CERT_FILE`` (if it exists) ->
    ``ssl.get_default_verify_paths().cafile`` -> ``.capath`` -> the first
    existing entry in ``COMMON_PATHS``."""
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
    return None


def contents():
    """Return the bytes of the resolved CA bundle, or ``b""`` if none found."""
    cert_path = where()
    if cert_path and os.path.isfile(cert_path):
        with open(cert_path, "rb") as f:
            return f.read()
    return b""


__all__ = ["where", "contents"]
