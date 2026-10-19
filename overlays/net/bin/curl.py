"""The ``curl`` the overlay puts on PATH, run by bin/curl (POSIX sh) and
bin/curl.cmd (Windows): ``lib/curlshim``, which runs the real curl with the
agent proxy added when there is one, else curlike's standard-library curl.

curlike and syshttp come from the agents' venv (this overlay's
``requirements.txt``), which the launchers run under ``$AGENTS_PYTHON``.
Settings are ``AGENTS_*``: ``SYSHTTP_PREFIX=AGENTS`` (this overlay's
``env.py``) is set here too when nothing set a prefix, so the launcher means
the same outside a ``dotagents env`` session. Without curlike the real curl
still runs, as typed.
"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE.parent / "lib"


def entry():
    """This launcher's platform entry, what curlike skips when looking for the
    real curl and hands to hook wrappers."""
    return str(HERE / ("curl.cmd" if os.name == "nt" else "curl"))


def _real_curl():
    """The first curl on PATH outside this directory, for running without curlike."""
    import shutil

    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        try:
            if Path(directory).resolve() == HERE:
                continue
        except OSError:
            continue
        found = shutil.which("curl", path=directory)
        if found and not (Path(found).resolve().parent / "curl.py").is_file():
            return found
    return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not (os.environ.get("SYSHTTP_PREFIX") or "").strip():
        os.environ["SYSHTTP_PREFIX"] = "AGENTS"
    try:
        if str(LIB) not in sys.path:
            sys.path.insert(0, str(LIB))
        import curlike  # noqa: F401  (without it the real curl runs as typed)
        from curlshim import main as shim_main
    except ImportError as exc:
        real = _real_curl()
        if real:
            print("curl: curlike is not installed (%s): running %s as typed" % (exc, real), file=sys.stderr)
            return subprocess.run([real, *argv]).returncode
        print("curl: (2) no curl on PATH and curlike is not installed: run `dotagents env` "
              "(it installs this overlay's requirements) or pip install curlike", file=sys.stderr)
        return 2
    return shim_main(argv, entry=entry())


if __name__ == "__main__":
    sys.exit(main())
