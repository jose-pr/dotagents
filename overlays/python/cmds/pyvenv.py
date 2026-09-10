"""`dotagents pyvenv` -- create a shared venv for a Python version, once.

Also ships `dotagents py`: run a python subprocess using this same shared
venv store, passing everything after ``--`` straight through (duho's
``_passthrough_`` convention -- ``dotagents py -- -c "print(1)"`` runs
``python -c "print(1)"`` against the resolved venv's interpreter). It
creates the venv first if missing (the same idempotent create-or-reuse
``Pyvenv`` logic, not a separate code path), so ``py`` is really "the venv
command AND where you run things with it" -- one command finds/creates the
interpreter, the other one uses it.

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
first and wins over the ``os.name`` value. ``<arch>`` is the architecture the
chosen interpreter was BUILT for -- the last segment of its
``sysconfig.get_platform()`` (``win-arm64`` -> ``arm64``, ``linux-x86_64`` ->
``x86_64``), probed from that interpreter. Not ``platform.machine()``: under
emulation (an x64 CPython on an ARM64 Windows box, Rosetta on macOS) that
reports the HOST, so an emulated venv would be filed as native and a native
one created later would collide with it.

**Native first**: when several installed interpreters satisfy a version spec
(or when picking the latest), the ones built for the host's native
architecture (:func:`_host_arch`) win before the highest version does -- an
ARM64 3.9.10 beats an emulated x64 3.9.13 on an ARM64 machine. An explicit
interpreter path is taken as given.

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


def _arch_from_platform_tag(tag: str) -> str:
    """``sysconfig.get_platform()`` -> arch bucket: the last dash-separated
    segment, lowercased (``win-amd64`` -> ``amd64``, ``macosx-14.0-arm64`` ->
    ``arm64``, ``linux-x86_64`` -> ``x86_64``)."""
    tag = (tag or "").strip().lower()
    return tag.rsplit("-", 1)[-1] or "unknown"


def _arch_bucket(interpreter: "Optional[Path]" = None) -> str:
    """The architecture ``interpreter`` was built for (the running one when
    omitted). Probed from the interpreter itself: ``platform.machine()`` is the
    HOST's answer and lies under emulation."""
    if interpreter is None:
        import sysconfig

        return _arch_from_platform_tag(sysconfig.get_platform())
    return _probe_arch(interpreter) or "unknown"


def _host_arch() -> str:
    """The machine's NATIVE architecture, in the same vocabulary as
    :func:`_arch_from_platform_tag` for this OS -- what a non-emulated
    interpreter here would report as its build arch. Windows asks the kernel
    (``IsWow64Process2``: the running process may itself be emulated, and
    ``PROCESSOR_ARCHITECTURE`` then lies the same way); macOS unmasks Rosetta;
    elsewhere ``platform.machine()`` is the machine."""
    import os
    import platform

    if sys.platform == "win32":
        try:
            import ctypes

            k32 = ctypes.windll.kernel32
            # Prototypes matter: the pseudo-handle is a 64-bit -1, which the
            # default int conversion truncates into an invalid handle.
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            k32.IsWow64Process2.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ushort), ctypes.POINTER(ctypes.c_ushort)]
            k32.IsWow64Process2.restype = ctypes.c_int
            proc, native = ctypes.c_ushort(0), ctypes.c_ushort(0)
            if k32.IsWow64Process2(k32.GetCurrentProcess(), ctypes.byref(proc), ctypes.byref(native)):
                known = {0xAA64: "arm64", 0x8664: "amd64", 0x014C: "win32", 0x01C4: "arm"}
                if native.value in known:
                    return known[native.value]
        except Exception:
            pass
        env = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or ""
        return {"arm64": "arm64", "amd64": "amd64", "x86": "win32"}.get(env.lower(), env.lower() or "unknown")
    machine = platform.machine().lower() or "unknown"
    if sys.platform == "darwin" and machine == "x86_64":
        try:
            proc = subprocess.run(["sysctl", "-n", "sysctl.proc_translated"], capture_output=True, text=True, timeout=5)
            if proc.returncode == 0 and proc.stdout.strip() == "1":
                return "arm64"
        except Exception:
            pass
    return machine


_VERSION_RE = re.compile(r"^Python (\d+)\.(\d+)\.(\d+)")
_PROBE_ARCH_SRC = "import sysconfig; print(sysconfig.get_platform())"


_PROBES: "dict[tuple[str, str], object]" = {}


def _cached(kind: str, python: Path, probe):
    """Memoize a probe of an EXISTING interpreter for this process: the answer
    cannot change within a run, and `_venv_target` would otherwise re-spawn the
    interpreter `_best_candidate` just probed (2 spawns per `dotagents py`).
    Non-files are never cached, so a test's fake path is probed afresh."""
    try:
        key = (kind, str(python.resolve())) if python.is_file() else None
    except OSError:
        key = None
    if key is not None and key in _PROBES:
        return _PROBES[key]
    result = probe(python)
    if key is not None:
        _PROBES[key] = result
    return result


def _probe_version(python: Path) -> "Optional[tuple[int, int, int]]":
    """Actually invoke ``python --version`` and parse it. ``None`` on any failure
    -- a candidate that cannot run, or whose output does not parse, is discarded
    rather than trusted from its filename."""
    return _cached("version", python, _spawn_version)


def _spawn_version(python: Path) -> "Optional[tuple[int, int, int]]":
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


def _probe_arch(python: Path) -> "Optional[str]":
    """Ask ``python`` what it was built for (``sysconfig.get_platform()``).
    ``None`` when it cannot run -- an unknown arch never counts as native."""
    return _cached("arch", python, _spawn_arch)


def _spawn_arch(python: Path) -> "Optional[str]":
    try:
        proc = subprocess.run(
            [str(python), "-c", _PROBE_ARCH_SRC],
            capture_output=True, timeout=10, text=True,
        )
    except Exception:
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return _arch_from_platform_tag(proc.stdout.strip().splitlines()[-1])


def _candidates_windows() -> "list[Path]":
    """The ``py`` launcher's own list of installs, if present -- authoritative:
    it enumerates what the launcher resolves, not a PATH guess -- plus whatever
    Python Manager has under ``%LOCALAPPDATA%/Python/pythoncore-*``. A venv's
    interpreter is never a candidate (the launcher lists the ACTIVE venv first,
    marked ``*``): a venv made from a venv inherits its base and hides which
    install that was. Falls back to a PATH scan for ``python*.exe`` names."""
    import os

    try:
        proc = subprocess.run(
            ["py", "--list-paths"], capture_output=True, timeout=10, text=True,
        )
    except Exception:
        proc = None
    paths: "list[Path]" = []
    if proc is not None and proc.returncode == 0:
        for line in proc.stdout.splitlines():
            # Lines look like " -V:3.12-64      C:\...\python.exe"; the active
            # venv, if any, is " *              C:\...\.venv\Scripts\python.exe".
            if line.strip().startswith("*"):
                continue
            parts = line.strip().rsplit(None, 1)
            if len(parts) == 2 and parts[1].lower().endswith(".exe"):
                candidate = Path(parts[1])
                if candidate.is_file():
                    paths.append(candidate)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        for exe in sorted(Path(local).glob("Python/pythoncore-*/python.exe")):
            if exe.is_file():
                paths.append(exe)
    paths = _unvenv(paths)
    if paths:
        return paths
    return _unvenv(_scan_path(("python.exe",) + tuple(
        "python3.%d.exe" % n for n in range(6, 30)
    )))


def _venv_cfg(python: Path) -> "Optional[Path]":
    """The ``pyvenv.cfg`` of the venv ``python`` belongs to (beside it or one
    level up: ``<venv>/Scripts/python.exe`` on Windows, ``<venv>/bin/python``
    elsewhere), or ``None`` for a real install."""
    for parent in (python.parent, python.parent.parent):
        cfg = parent / "pyvenv.cfg"
        if cfg.is_file():
            return cfg
    return None


def _is_venv_python(python: Path) -> bool:
    return _venv_cfg(python) is not None


def _venv_base(python: Path) -> "Optional[Path]":
    """The install a venv interpreter was created from, from ``pyvenv.cfg``'s
    ``home =`` line, or ``None``. A venv is never itself a candidate (a venv
    made from a venv inherits its base and hides which install that was), but
    its base is -- so an activated venv on an otherwise bare PATH (a uv/pyenv
    venv in a minimal container) still yields the interpreter behind it."""
    cfg = _venv_cfg(python)
    if cfg is None:
        return None
    try:
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "home" and value.strip():
                home = Path(value.strip())
                for name in ("python.exe", "python3", "python"):
                    candidate = home / name
                    if candidate.is_file():
                        return candidate
    except OSError:
        pass
    return None


def _unvenv(paths: "list[Path]") -> "list[Path]":
    """Replace venv interpreters by their bases, dropping the ones whose base
    cannot be found; order kept, duplicates removed."""
    out = []
    for path in paths:
        if _is_venv_python(path):
            base = _venv_base(path)
            if base is not None:
                out.append(base)
        else:
            out.append(path)
    return _dedupe(out)


def _dedupe(paths: "list[Path]") -> "list[Path]":
    seen, out = set(), []
    for path in paths:
        key = str(path).lower() if sys.platform == "win32" else str(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


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
    return _unvenv(_scan_path(tuple("python3.%d" % n for n in range(6, 30)) + ("python3", "python")))


def _resolve_interpreter(version: "Optional[str]", logger) -> Path:
    """Resolve ``version`` (a bare X.Y, a bare X, a full interpreter path, or
    ``None`` for "latest available") to a real, runnable interpreter path.
    Among the candidates that satisfy the spec, one built for the host's
    native architecture beats a higher version that is not (an emulated
    interpreter is never the default when a native one exists)."""
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
        chosen = _best_candidate(lambda probed: probed[: len(wanted)] == wanted, logger)
        if chosen is None:
            raise SystemExit("error: no installed Python matches %r" % version)
        return chosen

    # No version given: native arch first, then the highest real version
    # among every discovered candidate, system-wide.
    chosen = _best_candidate(lambda probed: True, logger)
    if chosen is None:
        raise SystemExit("error: no Python interpreter found on this system")
    return chosen


def _best_candidate(accept, logger) -> "Optional[Path]":
    """Rank every discovered interpreter whose probed version ``accept``s:
    native-arch ones first, then the highest ``(major, minor, micro)``."""
    host = _host_arch()
    ranked = []
    for candidate in _discover_all():
        probed = _probe_version(candidate)
        if probed is None or not accept(probed):
            continue
        arch = _probe_arch(candidate)
        ranked.append((arch == host or arch == "universal2", probed, candidate, arch))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    native, probed, candidate, arch = ranked[0]
    if logger is not None and not native:
        logger.warning(
            "pyvenv: no %s-native interpreter satisfies the spec; using %s (%s, built for %s)"
            % (host, candidate, "%d.%d.%d" % probed, arch or "unknown arch")
        )
    return candidate


def _venv_target(scope, py_version: "Optional[str]", logger=None) -> "tuple[Path, Path]":
    """The venv dir a given scope + version spec resolves to, whether or not it
    exists yet -- the single naming rule shared by ``pyvenv`` and ``py``."""
    pyvenv_root = scope.agents_root.parent / ".pyvenv"
    interpreter = _resolve_interpreter(py_version, logger=logger)
    probed = _probe_version(interpreter)
    version_str = "%d.%d.%d" % probed if probed else "unknown"
    return pyvenv_root / ("%s-%s-%s" % (version_str, _os_bucket(), _arch_bucket(interpreter))), interpreter


def _ensure_venv(scope, py_version: "Optional[str]", *, dry_run: bool, logger) -> "Optional[Path]":
    """Resolve the venv for ``py_version`` under ``scope``, creating it first if
    missing -- the one code path both ``pyvenv`` and ``py`` share, so ``py``
    never drifts from what ``pyvenv`` would have produced for the same inputs.
    Returns the venv's own python executable, or ``None`` on ``--dry-run`` when
    nothing exists yet (nothing to run against)."""
    target, interpreter = _venv_target(scope, py_version, logger)
    venv_python = _venv_python(target)

    if venv_python.is_file():
        logger.info("pyvenv: exists, nothing to do: %s" % target)
        return venv_python

    logger.info("pyvenv: creating %s (from %s)" % (target, interpreter))
    if dry_run:
        logger.info("dry-run: no venv created")
        return None

    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(interpreter), "-m", "venv", str(target)], check=True)
    if not venv_python.is_file():
        raise SystemExit("error: venv creation reported success but %s is missing" % venv_python)
    logger.info("pyvenv: created %s" % target)
    return venv_python


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
        _ensure_venv(scope, self.py_version, dry_run=self.dry_run, logger=self._logger_)
        return 0


class Py(DotAgentsArgs):
    """Run Python against the shared ``.pyvenv/`` store, creating the venv first
    if it doesn't exist yet (same logic as ``pyvenv`` -- this IS "the venv
    command and where you run things with it", not a separate lookup).

    Everything after the first literal ``--`` is passed straight through to the
    subprocess (duho's ``_passthrough_`` convention) -- stdin/stdout/stderr are
    the real terminal streams, not captured, so interactive input and streamed
    output both work exactly like invoking python directly.

        dotagents py -- -c "print(1)"      # -> python<selected> -c "print(1)"
        dotagents py 3.9 -- -m pytest -q   # pick a version explicitly, then run it

    ``--version``/``py_version`` before ``--`` picks which interpreter (same
    spec as ``pyvenv``'s positional: a bare "3.11"/"3", a full interpreter path,
    or omitted for latest); everything from the FIRST ``--`` on belongs to the
    subprocess, including any ``-v``/``-q`` python would otherwise try to
    consume as its own flags -- they are never parsed by duho once past ``--``.
    """

    _parsername_ = "py"

    py_version: Optional[str] = None
    ("Python version to use: a full interpreter path, a bare version spec "
     "(\"3.11\", \"3\"), or omitted for the highest version found on the system.")
    ("py_version",)

    def __call__(self) -> int:
        scope = self.resolve_scope()
        venv_python = _ensure_venv(scope, self.py_version, dry_run=False, logger=self._logger_)
        # `_ensure_venv` only returns None on dry_run, which this command never
        # sets -- but guard anyway rather than pass None to subprocess.
        if venv_python is None:
            raise SystemExit("error: venv creation failed; nothing to run")
        proc = subprocess.run([str(venv_python), *self._passthrough_])
        return proc.returncode
