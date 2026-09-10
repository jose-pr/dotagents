"""Locate a bash that actually runs POSIX shell (tests only).

``shutil.which("bash")`` and a bare ``["bash", ...]`` argv are not enough on
Windows: System32 ships a ``bash.exe`` that is the WSL launcher, which exits 1
with no output when no distro is installed (GitHub's windows runners, and any
box whose PATH lists it before Git's bin). A bare argv is worse still:
CreateProcess searches System32 BEFORE PATH, so even a PATH that puts Git
first loses. Git for Windows ships the real bash beside git; prefer it, and
prove any candidate by running it.
"""
import functools
import os
import shutil
import subprocess
from pathlib import Path


@functools.lru_cache(maxsize=None)
def real_bash():
    """Absolute path of a working bash, or None."""
    candidates = []
    found = shutil.which("bash")
    if found:
        candidates.append(found)
    if os.name == "nt":
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
    seen = set()
    for cand in candidates:
        if cand in seen or not os.path.isfile(cand):
            continue
        seen.add(cand)
        try:
            proc = subprocess.run([cand, "-c", "echo ok"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0 and proc.stdout.strip() == "ok":
            return cand
    return None


BASH = real_bash()
