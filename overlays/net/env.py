"""The net overlay's environment: syshttp and curlike read every setting as
``<PREFIX><NAME>`` for each prefix of ``SYSHTTP_PREFIX``, and the agents'
prefix is ``AGENTS_`` alone (``AGENTS_SYSHTTP_PROXY``, ``AGENTS_HTTPS_PROXY``,
``AGENTS_SYSHTTP_HOOKS_<KEY>``, ``AGENTS_CURL``, ...): the machine's
unprefixed variables are not the agents'. A prefix the caller already chose
is left alone.

On Windows, ``curl`` is the ``curl.exe`` setup.py made in the venv
``$AGENTS_PYTHON`` names: its directory (``<venv>/net-curl``, nothing else in
it) goes first on PATH, worked out here on every run so it follows the venv
when that is rebuilt elsewhere, and any other venv's is taken off PATH, since
its executable names that venv's interpreter."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import curlshim  # noqa: E402  (standard library only at import)


def _is_venv_curl_dir(path):
    return (os.path.basename(os.path.normpath(path)).lower() == curlshim.EXE_DIR
            and os.path.isfile(os.path.join(os.path.dirname(os.path.normpath(path)), "pyvenv.cfg")))


def curl_path(environ):
    """The new PATH, or None when it stays as it is."""
    current = environ.get("PATH", "")
    kept = [p for p in current.split(os.pathsep) if not (p and _is_venv_curl_dir(p))]
    lead = []
    python = environ.get("AGENTS_PYTHON", "")
    if python:
        venv = os.path.dirname(os.path.dirname(os.path.abspath(python)))
        exe = curlshim.venv_curl(venv)
        if os.path.isfile(os.path.join(venv, "pyvenv.cfg")) and os.path.isfile(exe):
            lead = [os.path.dirname(exe)]
    updated = os.pathsep.join(lead + kept) if current else os.pathsep.join(lead)
    return updated if updated != current else None


changes = {}
if not (os.environ.get("SYSHTTP_PREFIX") or "").strip():
    changes["SYSHTTP_PREFIX"] = "AGENTS_"
if os.name == "nt":
    path = curl_path(os.environ)
    if path is not None:
        changes["PATH"] = path
if changes:
    print(json.dumps(changes))
