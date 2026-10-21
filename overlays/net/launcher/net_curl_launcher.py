"""The entry point pip turns into ``curl.exe`` (``[project.scripts]``): runs
the net overlay's ``bin/curl.py``, found through ``$NET_OVERLAY_ROOT``.

That variable comes from the same ``dotagents env`` session that puts the
executable on PATH (the overlay's ``env.py``). pip's launcher hands Python
the caller's arguments as passed.
"""

import os
import runpy
import sys

ROOT = "NET_OVERLAY_ROOT"


def main():
    root = os.environ.get(ROOT, "")
    script = os.path.join(root, "bin", "curl.py")
    if not root or not os.path.isfile(script):
        print(
            "curl: (2) %s=%s names no net overlay (no bin/curl.py there): run curl in a `dotagents env` session"
            % (ROOT, root),
            file=sys.stderr,
        )
        return 2
    return runpy.run_path(script)["main"]()
