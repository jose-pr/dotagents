"""Locate a bash that actually runs POSIX shell (tests only).

``shutil.which("bash")`` and a bare ``["bash", ...]`` argv are not enough on
Windows. ``WindowsApps\\bash.exe`` and ``System32\\bash.exe`` are the WSL
launcher: with no distro it exits 1 with no output, and with one it boots the
WSL VM and runs a Linux bash that cannot see Windows paths the way the tests
pass them. Both answer ``bash -c 'echo ok'`` correctly, so running a probe is
not enough to reject them either. A bare argv is worse still: CreateProcess
searches System32 BEFORE PATH, so even a PATH that puts Git first loses.

So on Windows: Git for Windows' bash is tried first, the WSL launcher paths
are never run at all, and a candidate counts only if ``uname -s`` names an
MSYS/MinGW/Cygwin runtime. Elsewhere the PATH bash is used, proven by running
it. Resolution is lazy (first access of ``BASH`` or first ``real_bash()``
call), never at import, and cached for the process.
"""

import functools
import os
import shutil
import subprocess
from pathlib import Path

_WINDOWS_RUNTIMES = ("MINGW", "MSYS", "CYGWIN")


def _is_wsl_launcher(path: str) -> bool:
    parts = {p.lower() for p in Path(path).parts}
    return bool(parts & {"windowsapps", "system32", "syswow64", "sysnative"})


def _candidates() -> "list[str]":
    if os.name != "nt":
        found = shutil.which("bash")
        return [found] if found else []
    candidates = []
    git = shutil.which("git")
    if git:
        root = Path(git).resolve().parent.parent
        candidates += [str(root / "bin" / "bash.exe"), str(root / "usr" / "bin" / "bash.exe")]
    for pf in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"), "C:\\Program Files"):
        if pf:
            candidates += [
                str(Path(pf) / "Git" / "bin" / "bash.exe"),
                str(Path(pf) / "Git" / "usr" / "bin" / "bash.exe"),
            ]
    found = shutil.which("bash")
    if found:
        candidates.append(found)
    return [c for c in candidates if not _is_wsl_launcher(c)]


def _works(cand: str) -> bool:
    probe = "uname -s" if os.name == "nt" else "echo ok"
    try:
        proc = subprocess.run([cand, "-c", probe], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    out = proc.stdout.strip()
    if proc.returncode != 0:
        return False
    if os.name == "nt":
        return out.upper().startswith(_WINDOWS_RUNTIMES)
    return out == "ok"


@functools.lru_cache(maxsize=None)
def real_bash() -> "str | None":
    """Absolute path of a working bash, or None."""
    seen = set()
    for cand in _candidates():
        key = os.path.normcase(cand)
        if key in seen or not os.path.isfile(cand):
            continue
        seen.add(key)
        if _works(cand):
            return cand
    return None


def __getattr__(name):
    # `from _shell import BASH` resolves here, on first use (PEP 562).
    if name == "BASH":
        return real_bash()
    raise AttributeError(name)
