"""`dotagents pyvenv` -- create a shared venv for a Python version, once.

Discovered command module shipped BY THIS OVERLAY (D84 per-overlay ``cmds/``
discovery, D85): plain dotagents ships no ``pyvenv`` command -- installing the
``python`` overlay is what makes it exist.

**Where the venv lives**: ``<scope-root>/.pyvenv/<version>-<os>-<arch>/``, a
SIBLING of the scope's own ``.agents`` dir, not inside it -- ``.pyvenv/`` is
never overlay content and must not be swept by anything that walks
``overlays/``/``skills/``/``dotagents/cmds/`` under the store. ``<scope-root>``
is ``Scope.agents_root`` from ``dotagents._scope`` (the ``~/.agents`` store for
``-g``, or ``<project>/.agents`` otherwise) -- so for the user scope this is
``~/.pyvenv/<version>-<os>-<arch>/``, a sibling of ``~/.agents``.

``<os>`` is ``os.name`` (``nt``/``posix``) EXCEPT macOS, which ``os.name``
reports as ``posix`` same as Linux -- explicitly called out by the user as a
case that needs its own bucket, so ``sys.platform == "darwin"`` is checked
first and wins over the ``os.name`` value. ``<arch>`` is
``platform.machine()`` lowercased (e.g. ``amd64``/``x86_64``/``arm64``) --
whatever the running interpreter reports for the machine it is actually on,
not a hardcoded guess.

**Idempotent by design, not by locking**: if the target directory already
looks like a real venv (a platform-appropriate python executable is present
under it), this command does nothing and exits 0. No lock file -- creating a
venv is not safely resumable mid-way, so a second concurrent invocation
racing the first is out of scope here, same posture as ``overlays add``.

**Version selection**: the positional ``version`` argument (``3.11``, ``3.12``,
``3``, or a full ``pythonX.Y``/``python.exe`` path) picks the interpreter that
becomes the venv. With no argument, EVERY discoverable Python on the system is
probed and the highest ``(major, minor, micro)`` wins -- discovery uses the
Windows ``py`` launcher's ``-0p``/``--list-paths`` output when present
(authoritative: it enumerates installs the launcher itself resolves, not a
PATH guess), else a PATH scan for ``python3.<N>``/``python<N>`` names on
POSIX and ``python.exe``/``pythonX.Y.exe`` on Windows, each interpreter's real
version confirmed by actually invoking it (``--version``) rather than trusted
from its filename alone.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from dotagents.cli import DotAgentsArgs

# `Pyvenv` inherits `DotAgentsArgs` (`dotagents.cli`) for the shared
# `-g/--global` + `--agents-dir` fields every scope-aware command uses (`init`,
# `overlays add/remove/sync`) -- same fields, same `resolve_scope` behind them,
# so `pyvenv`'s scope behaves identically to every other command's, not a
# reimplementation that could drift. This DOES mean `dotagents` must be
# importable to even define this class, unlike private-sync's `_link.py`
# (this overlay's own tests install the real `dotagents` package as a test
# dependency rather than avoiding the import -- see tests/conftest.py).
# Everything else in this file (os/arch bucketing, interpreter discovery,
# version probing) is pure stdlib and independently testable regardless.


def _venv_python(venv_dir: Path) -> Path:
    """Where the venv's own interpreter would live, platform-specific."""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _os_bucket() -> str:
    # macOS reports os.name == "posix" (same as Linux) -- called out explicitly
    # as needing its own bucket, so sys.platform is checked first for it.
    if sys.platform == "darwin":
        return "darwin"
    return "posix" if sys.platform != "win32" else "nt"


def _arch_bucket() -> str:
    import platform

    return platform.machine().lower() or "unknown"


_VERSION_RE = re.compile(r"^Python (\d+)\.(\d+)\.(\d+)")


def _probe_version(python: Path) -> "Optional[tuple[int, int, int]]":
    """Actually invoke ``python --version`` and parse it. ``None`` on any failure
    -- a candidate that cannot run, or whose output does not parse, is discarded
    rather than trusted from its filename."""
    try:
        proc = subprocess.run(
            [str(python), "--version"],
            capture_output=True, timeout=10, text=True,
        )
    except Exception:
        return None
    match = _VERSION_RE.match((proc.stdout or proc.stderr or "").strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _candidates_windows() -> "list[Path]":
    """The ``py`` launcher's own list of installs, if present -- authoritative:
    it enumerates what the launcher resolves, not a PATH guess. Falls back to a
    PATH scan for ``python*.exe`` names if ``py`` is absent."""
    try:
        proc = subprocess.run(
            ["py", "--list-paths"], capture_output=True, timeout=10, text=True,
        )
    except Exception:
        proc = None
    paths: "list[Path]" = []
    if proc is not None and proc.returncode == 0:
        for line in proc.stdout.splitlines():
            # Lines look like " -3.12-64        C:\...\python.exe"
            parts = line.strip().rsplit(None, 1)
            if len(parts) == 2 and parts[1].lower().endswith(".exe"):
                candidate = Path(parts[1])
                if candidate.is_file():
                    paths.append(candidate)
    if paths:
        return paths
    return _scan_path(("python.exe",) + tuple(
        "python3.%d.exe" % n for n in range(6, 30)
    ))


def _scan_path(names: "tuple[str, ...]") -> "list[Path]":
    import os
    import shutil

    found: "list[Path]" = []
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            found.append(Path(resolved))
    # Also walk PATH directly for python3.N / pythonN names shutil.which's
    # single-name lookup would miss without knowing N in advance.
    seen = {str(p) for p in found}
    path_env = os.environ.get("PATH", "")
    for directory in path_env.split(os.pathsep):
        if not directory:
            continue
        try:
            entries = list(Path(directory).iterdir())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_file():
                continue
            if re.match(r"^python3?(\.\d+)?(\.exe)?$", entry.name):
                if str(entry) not in seen:
                    seen.add(str(entry))
                    found.append(entry)
    return found


def _discover_all() -> "list[Path]":
    if sys.platform == "win32":
        return _candidates_windows()
    return _scan_path(tuple("python3.%d" % n for n in range(6, 30)) + ("python3", "python"))


def _resolve_interpreter(version: "Optional[str]", logger) -> Path:
    """Resolve ``version`` (a bare X.Y, a bare X, a full interpreter path, or
    ``None`` for "latest available") to a real, runnable interpreter path."""
    if version:
        as_path = Path(version)
        if as_path.is_file():
            if _probe_version(as_path) is None:
                raise SystemExit("error: %s does not run (--version failed)" % version)
            return as_path
        # A bare "3.11" / "3" spec: probe every discovered candidate and keep
        # the ones whose real (probed) version matches the requested prefix.
        wanted = tuple(int(p) for p in version.split(".") if p.isdigit())
        if not wanted:
            raise SystemExit("error: not a usable version spec or interpreter path: %s" % version)
        matches = []
        for candidate in _discover_all():
            probed = _probe_version(candidate)
            if probed is not None and probed[: len(wanted)] == wanted:
                matches.append((probed, candidate))
        if not matches:
            raise SystemExit("error: no installed Python matches %r" % version)
        matches.sort(key=lambda pair: pair[0], reverse=True)
        return matches[0][1]

    # No version given: the highest real version among every discovered
    # candidate, system-wide.
    best: "Optional[tuple[tuple[int, int, int], Path]]" = None
    for candidate in _discover_all():
        probed = _probe_version(candidate)
        if probed is None:
            continue
        if best is None or probed > best[0]:
            best = (probed, candidate)
    if best is None:
        raise SystemExit("error: no Python interpreter found on this system")
    return best[1]


class Pyvenv(DotAgentsArgs):
    """Create a shared venv for a Python version under the scope's ``.pyvenv/``
    store, once. If the target already looks like a real venv, do nothing --
    this command is a create-if-missing operation, not an updater.

    Scope (``-g/--global``, ``--agents-dir``) comes from ``DotAgentsArgs`` --
    same fields, same defaults, same ``resolve_scope`` behind them, as every
    other scope-aware command (``init``, ``overlays add/remove/sync``): project
    scope by default, the user store only with an explicit ``-g``."""

    _parsername_ = "pyvenv"

    dry_run: bool = False
    "Show what would happen without creating anything."
    ("--dry-run",)

    py_version: Optional[str] = None
    ("Python version to use: a full interpreter path, a bare version spec "
     "(\"3.11\", \"3\"), or omitted for the highest version found on the system.")
    ("py_version",)

    def __call__(self) -> int:
        scope = self.resolve_scope()
        pyvenv_root = scope.agents_root.parent / ".pyvenv"
        interpreter = _resolve_interpreter(self.py_version, self._logger_)
        probed = _probe_version(interpreter)
        version_str = "%d.%d.%d" % probed if probed else "unknown"

        target = pyvenv_root / ("%s-%s-%s" % (version_str, _os_bucket(), _arch_bucket()))
        venv_python = _venv_python(target)

        if venv_python.is_file():
            self._logger_.info("pyvenv: exists, nothing to do: %s" % target)
            return 0

        self._logger_.info("pyvenv: creating %s (from %s)" % (target, interpreter))
        if self.dry_run:
            self._logger_.info("dry-run: no venv created")
            return 0

        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [str(interpreter), "-m", "venv", str(target)], check=True,
        )
        if not venv_python.is_file():
            raise SystemExit("error: venv creation reported success but %s is missing" % venv_python)
        self._logger_.info("pyvenv: created %s" % target)
        return 0
