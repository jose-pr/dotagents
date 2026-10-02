"""Which curl the fallback answers as, where curl versions disagree.

``NET_CURL_COMPAT`` names a curl version (``8.19``, ``8.19.0``) or ``auto``
(the default): the version of the real curl on PATH -- the shim serves a
prefix-gateway request from the fallback even when curl is installed -- else
:data:`NEWEST`. Read lazily: only a version-dependent answer asks for it.

The differences, measured against curl's own builds (8.17-8.22):

- a proxy refusing the CONNECT: 56 before 8.20, 7 from 8.20 on;
- a ``--cacert`` / ``--netrc-file`` naming no file: refused before anything
  else (exit 2) from 8.18 on; before, the CA file fails at the TLS handshake
  (77) and the netrc file when it is read (26).
"""
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Tuple

VAR = 'NET_CURL_COMPAT'

#: The newest curl measured: what the fallback answers as with no curl to ask.
NEWEST = (8, 22, 0)

_cached: Dict[str, Tuple[int, int, int]] = {}


def find_real_curl(skip=None):
    """The system curl, or ``None`` -- never a copy of the shim. PATH is
    walked directory by directory, skipping ``skip``, any hit that is not
    actually inside the directory searched (Windows ``which`` on Python <
    3.12 consults the cwd too) and any hit with a ``curl.py`` beside it (a
    copy of the shim: the overlay's ``bin/`` is on PATH in a session)."""
    for directory in os.environ.get('PATH', '').split(os.pathsep):
        if not directory:
            continue
        try:
            searched = Path(directory).resolve()
            if skip is not None and searched == skip:
                continue
            found = shutil.which('curl', path=directory)
            if not found:
                continue
            found_path = Path(found).resolve()
            if found_path.parent != searched or (found_path.parent / 'curl.py').is_file():
                continue
        except OSError:
            continue
        return found
    return None


def parse_version(text):
    """``(major, minor, patch)`` from ``8.19`` / ``8.19.0`` / ``curl 8.19.0
    (...)``, or ``None``."""
    m = re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', text or '')
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def installed_version():
    """The real curl's version (``curl -V``), or ``None`` without one."""
    path = find_real_curl()
    if not path:
        return None
    try:
        out = subprocess.run([path, '-V'], capture_output=True, timeout=10, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    first = out.decode('ascii', 'replace').splitlines()[:1]
    return parse_version(first[0]) if first and first[0].startswith('curl ') else None


def version():
    """The curl version the fallback answers as. An unreadable
    ``NET_CURL_COMPAT`` raises ``ValueError`` (the command's exit 2)."""
    raw = (os.environ.get(VAR) or '').strip()
    if raw not in _cached:
        if raw.lower() in ('', 'auto'):
            _cached[raw] = installed_version() or NEWEST
        else:
            parsed = parse_version(raw) if re.match(r'^\d+\.\d+(\.\d+)?$', raw) else None
            if parsed is None:
                raise ValueError('%s=%s: expected a curl version (8.19, 8.19.0) or auto' % (VAR, raw))
            _cached[raw] = parsed
    return _cached[raw]


def check():
    """Validate an explicit ``NET_CURL_COMPAT`` before anything is sent (exit
    2); ``auto`` stays lazy, as it may run ``curl -V``."""
    if (os.environ.get(VAR) or '').strip().lower() not in ('', 'auto'):
        version()


def connect_refused_code():
    """curl's exit when the proxy answers the CONNECT with an error status."""
    return 7 if version() >= (8, 20, 0) else 56


def checks_files_first():
    """Whether a ``--cacert`` / ``--netrc-file`` naming no file is refused
    before anything else (exit 2)."""
    return version() >= (8, 18, 0)
