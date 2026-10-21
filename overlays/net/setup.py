"""The overlay's setup step: dotagents runs it on ``overlays add`` and
``overlays sync``, after this overlay's requirements, under ``$AGENTS_PYTHON``
(the agents' venv) with the installed overlay as the current directory.

On Windows it gives the venv a ``curl.exe``, the overlay's ``curl`` there:
pip installs ``launcher/`` into the venv, generating the console script
``Scripts/curl.exe`` as for any pip-installed tool, and that file is linked
into ``<venv>/net-curl/``, which holds nothing else; ``env.py`` puts that
directory on PATH for the venv ``$AGENTS_PYTHON`` names, so python.exe and
pip.exe stay off it. A ``.cmd`` cannot be the entry: cmd.exe parses its
command line before the first line runs, so a URL's ``&``, ``|``, ``>`` or
``%VAR%`` would act. The executable names the venv's interpreter by absolute
path, so it lives in the venv and goes with it; nothing is written here.
Elsewhere ``bin/curl`` (POSIX sh) is the entry and this does nothing.

Idempotent: pip runs only when the venv lacks the launcher's version or its
``curl.exe``; the link is remade only when it differs.
"""

import filecmp
import os
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE / "launcher"
DIST = "net-curl-launcher"


def _declared_version():
    text = (PACKAGE / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'(?m)^version\s*=\s*"([^"]+)"', text).group(1)


def _installed_version():
    from importlib import metadata

    try:
        return metadata.version(DIST)
    except metadata.PackageNotFoundError:
        return None


def _place(made, target):
    """``target`` as the same file as ``made``: a hard link, else a copy."""
    if target.is_file() and filecmp.cmp(str(made), str(target), shallow=False):
        return
    target.parent.mkdir(exist_ok=True)
    partial = target.with_name(".curl-new.exe")
    if partial.exists():
        partial.unlink()
    try:
        os.link(str(made), str(partial))
    except OSError:
        shutil.copyfile(str(made), str(partial))
    try:
        os.replace(str(partial), str(target))
    except OSError:
        partial.unlink()
        raise


def main():
    if os.name != "nt":
        return 0
    if sys.prefix == sys.base_prefix:
        # Never install into an interpreter outside a venv (AGENTS_NO_PYVENV).
        print("net setup: %s is not a venv: no curl.exe made" % sys.executable, file=sys.stderr)
        return 0
    sys.path.insert(0, str(HERE / "lib"))
    import curlshim

    made = Path(sysconfig.get_path("scripts")) / "curl.exe"
    if _installed_version() != _declared_version() or not made.is_file():
        command = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                   "--no-deps", "--force-reinstall", str(PACKAGE)]
        rc = subprocess.run(command).returncode
        if rc:
            print("net setup: pip could not install %s (exit %d)" % (PACKAGE, rc), file=sys.stderr)
            return rc
        if not made.is_file():
            print("net setup: pip installed %s but made no %s" % (PACKAGE, made), file=sys.stderr)
            return 1
    target = Path(curlshim.venv_curl())
    try:
        _place(made, target)
    except OSError as exc:
        print("net setup: cannot replace %s (%s): is a curl running?" % (target, exc.strerror or exc),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
