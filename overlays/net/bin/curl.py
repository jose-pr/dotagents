"""The ``curl`` the overlay puts on PATH: ``lib/curlshim``, which runs the
real curl with the agent proxy added when there is one, else curlike's
standard-library curl. Started by the launcher dotagents makes for this file
in the agents' venv, ``<venv>/overlays/curl`` (``curl.exe`` on Windows, a
native program, so no command interpreter parses the caller's arguments),
which ``dotagents env`` puts first on PATH.

curlike and syshttp come from the agents' venv (this overlay's
``requirements.txt``), which the launcher runs under. Settings are read under
the prefix ``AGENTS_`` (``AGENTS_SYSHTTP_PROXY``, ``AGENTS_CURL``):
``SYSHTTP_PREFIX=AGENTS_`` (this overlay's ``env.py``) is set here too when
nothing set a prefix, so the launcher means the same outside a ``dotagents
env`` session. Without curlike the real curl still runs, as typed.
"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE.parent / "lib"
PREFIX = "AGENTS_"


def entry():
    """This launcher's entry, what curlike skips when looking for the real
    curl and hands to hook wrappers: the ``curl`` launcher of the venv
    running this."""
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    import curlshim

    return curlshim.venv_curl()


def _libs_first():
    """The overlay's ``lib/``, then every overlay's (``$AGENTS_PYTHONPATH``),
    ahead of this process's imports and of ``PYTHONPATH`` for what it starts
    (a URL hook, the real curl), even outside a ``dotagents env`` session.
    Empty parts are left out: an empty PYTHONPATH entry means the current
    directory."""
    libs = [str(LIB), *(p for p in os.environ.get("AGENTS_PYTHONPATH", "").split(os.pathsep) if p)]
    for path in reversed(libs):
        if path not in sys.path:
            sys.path.insert(0, path)
    parts, seen = [], set()
    for path in [*libs, *os.environ.get("PYTHONPATH", "").split(os.pathsep)]:
        key = os.path.normcase(path)
        if path and key not in seen:
            seen.add(key)
            parts.append(path)
    os.environ["PYTHONPATH"] = os.pathsep.join(parts)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not (os.environ.get("SYSHTTP_PREFIX") or "").strip():
        os.environ["SYSHTTP_PREFIX"] = PREFIX
    _libs_first()
    # curlshim imports nothing outside the standard library until a request
    # needs it, so its finder serves the fallback below.
    import curlshim

    try:
        import curlike  # noqa: F401  (without it the real curl runs as typed)
    except ImportError as exc:
        real = curlshim.find_real_curl(entry())
        if real:
            print("curl: curlike is not installed (%s): running %s as typed" % (exc, real), file=sys.stderr)
            return subprocess.run([real, *argv]).returncode
        print("curl: (2) no curl on PATH and curlike is not installed: run `dotagents env` "
              "(it installs this overlay's requirements) or pip install curlike", file=sys.stderr)
        return 2
    return curlshim.main(argv, entry=entry())


if __name__ == "__main__":
    sys.exit(main())
