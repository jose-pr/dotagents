"""Running an overlay's setup and env scripts as AOS says: a named
interpreter found on the base ``PATH``, arguments as a list, a timeout, and a
process group (a job object on Windows) so that nothing the step started
outlives it."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Optional

#: Exit status reported for a step stopped at its timeout.
TIMED_OUT = 124
_CREATE_SUSPENDED = 0x4
_POWERSHELL_ARGS = ["-NoLogo", "-NoProfile", "-NonInteractive", "-File"]


class InterpreterError(Exception):
    """An interpreter dotagents does not support, or cannot find."""


def which_in(name: str, path: "Optional[str]") -> "Optional[str]":
    """``name`` as a program on the absolute directories of ``path`` (default: this process's), in
    order. Unlike :func:`shutil.which` it never looks in the current directory, which Windows
    searches first and an agent can write, and it skips relative entries of ``path``."""
    search = os.environ.get("PATH", "") if path is None else path
    suffixes = [""]
    if os.name == "nt" and not os.path.splitext(name)[1]:
        suffixes = [s for s in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if s]
    for directory in search.split(os.pathsep):
        if not directory or not os.path.isabs(directory):
            continue
        for suffix in suffixes:
            candidate = os.path.join(directory, name + suffix)
            if os.path.isfile(candidate) and (os.name == "nt" or os.access(candidate, os.X_OK)):
                return candidate
    return None


def _which(name: str, path: "Optional[str]") -> "Optional[str]":
    found = which_in(name, path)
    if found and os.name == "nt" and os.path.splitext(found)[1].lower() in (".cmd", ".bat"):
        # cmd.exe would parse the arguments again.
        return None
    return found


def interpreter_command(name: str, *, python: str, base_path: "Optional[str]",
                        trusted: bool = False) -> "list[str]":
    """The argv that comes before ``<script> <args>`` for interpreter ``name``.
    ``python`` is the Python runtime's interpreter; the others are found on
    ``base_path`` (the base environment's ``PATH``, never an overlay's
    additions). With ``trusted``, ``base_path`` has had every place an agent can write removed
    (:func:`dotagents._overlay_hooks.clean_path`), and a Windows bash is looked for only there and in
    the standard install directories. Raises :class:`InterpreterError`."""
    if name == "python":
        return [python]
    if name == "powershell":
        found = _which("pwsh", base_path)
        if found:
            return [found] + _POWERSHELL_ARGS
    elif name == "bash":
        if os.name == "nt":
            from dotagents._env import find_bash, find_bash_on

            found = find_bash_on(base_path) if trusted else find_bash()
        else:
            found = _which("bash", base_path)
        if found:
            return [found]
    elif name == "sh":
        if os.name == "nt":
            from dotagents._env import find_bash, find_bash_on

            bash = find_bash_on(base_path) if trusted else find_bash()
            found = str(Path(bash).with_name("sh.exe")) if bash else None
            found = found if found and os.path.isfile(found) else None
        else:
            found = _which("sh", base_path)
        if found:
            return [found]
    elif name == "node":
        found = _which("node", base_path)
        if found:
            return [found]
    else:
        raise InterpreterError("interpreter %r is not supported (python, bash, sh, powershell, node)" % name)
    raise InterpreterError("interpreter %r was not found on PATH" % name)


def can_run(name: str) -> bool:
    """Whether interpreter ``name`` can run here (``python`` always can)."""
    try:
        interpreter_command(name, python="python", base_path=os.environ.get("PATH"))
    except InterpreterError:
        return False
    return True


def version_of(name: str, command: "list[str]") -> "Optional[str]":
    """The ``X.Y.Z`` version of a ``python`` or ``node`` interpreter, or None."""
    probe = {
        "python": ["-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
        "node": ["--version"],
    }.get(name)
    if probe is None:
        return None
    try:
        done = subprocess.run(command + probe, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    text = done.stdout.strip().lstrip("v")
    return text if done.returncode == 0 and text else None


class _Job(object):
    """A Windows job object that ends every process in it when closed."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.handle = self._kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("CreateJobObject failed")

        class _Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class _Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", _Io),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        info = _Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close()
            raise OSError("SetInformationJobObject failed")

    def add(self, process: subprocess.Popen) -> bool:
        """Put a process started suspended into the job, then let it run: it
        starts nothing before it is in the job. False when it could not be
        assigned (it runs anyway)."""
        import ctypes

        handle = getattr(process, "_handle", None)
        assigned = handle is not None and bool(self._kernel.AssignProcessToJobObject(self.handle, int(handle)))
        if handle is not None:
            ctypes.WinDLL("ntdll").NtResumeProcess(int(handle))
        return assigned

    def close(self) -> None:
        """End every process in the job, and return once none is left
        (bounded to a few seconds): a process still exiting holds its files
        and its working directory."""
        import ctypes
        import time

        if not self.handle:
            return

        class _Accounting(ctypes.Structure):
            _fields_ = [(n, ctypes.c_int64) for n in ("u", "k", "tu", "tk")] + \
                       [(n, ctypes.c_uint32) for n in ("pages", "total", "active", "terminated")]

        self._kernel.TerminateJobObject(self.handle, 1)
        info = _Accounting()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not self._kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(info),
                                                          ctypes.sizeof(info), None):
                break
            if info.active == 0:
                break
            time.sleep(0.02)
        self._kernel.CloseHandle(self.handle)
        self.handle = None


def run_step(argv: "list[str]", *, cwd: str, env: "dict[str, str]", timeout: int,
             capture: bool = False, max_output: int = 1024 * 1024) -> "tuple[int, bytes]":
    """Run one step to completion: ``(exit status, stdout)``, stdout only
    when ``capture`` (bounded by ``max_output``; more is a failure, exit 1).
    ``timeout`` seconds, ``0`` for none; a step stopped at it exits
    :data:`TIMED_OUT`. Every process it started ends with it."""
    options: "dict" = {"cwd": cwd, "env": env}
    if capture:
        options["stdout"] = subprocess.PIPE
    job = None
    if os.name == "nt":
        try:
            job = _Job()
        except OSError:
            job = None
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | (_CREATE_SUSPENDED if job else 0)
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(argv, **options)
    if job is not None and not job.add(process):
        job.close()
        job = None
    try:
        try:
            out, _err = process.communicate(timeout=timeout or None)
        except subprocess.TimeoutExpired:
            _end(process, job)
            process.communicate()
            return TIMED_OUT, b""
        status = process.returncode
        if capture and out is not None and len(out) > max_output:
            return 1, b""
        return status, out or b""
    finally:
        _end(process, job)


def run_hook(argv: "list[str]", *, cwd: str, env: "dict[str, str]", timeout: int, stdin: bytes,
             max_output: int, max_stderr: int) -> "dict[str, object]":
    """Run one hook to completion, bounded: its standard input is ``stdin`` (written without
    waiting on the hook, which may never read it), at most ``max_output`` bytes of its standard
    output are kept and a run that writes more is stopped, and at most ``max_stderr`` bytes of
    its standard error are kept, the rest read and dropped so it never blocks. It ends at
    ``timeout`` seconds, when its own process is stopped; what it started is its own business, as
    with a harness's own hooks.

    Returns ``exit``, ``stdout``, ``stderr``, ``timed_out``, ``too_large``, ``not_run`` (it could
    not start) and ``seconds``."""
    import threading
    import time

    result: "dict[str, object]" = {"exit": None, "stdout": b"", "stderr": b"", "timed_out": False,
                                   "too_large": False, "not_run": False, "seconds": 0.0}
    options: "dict" = {"cwd": cwd, "env": env, "stdin": subprocess.PIPE, "stdout": subprocess.PIPE,
                       "stderr": subprocess.PIPE}
    started = time.monotonic()
    try:
        process = subprocess.Popen(argv, **options)
    except (OSError, ValueError):
        result["not_run"] = True
        return result
    stopped = threading.Event()

    def write() -> None:
        try:
            process.stdin.write(stdin)
        except (OSError, ValueError):
            pass
        finally:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass

    def read(stream, key: str, bound: int, fatal: bool) -> None:
        kept = bytearray()
        total = 0
        try:
            while True:
                chunk = stream.read1(65536)
                if not chunk:
                    break
                total += len(chunk)
                if len(kept) < bound:
                    kept += chunk[:bound - len(kept)]
                result[key] = bytes(kept)
                if fatal and total > bound:
                    # More than the bound was written: the run stops and nothing more is read.
                    result["too_large"] = True
                    stopped.set()
                    break
        except (OSError, ValueError):
            pass
        result[key] = bytes(kept)

    threads = [threading.Thread(target=write, daemon=True),
               threading.Thread(target=read, args=(process.stdout, "stdout", max_output, True), daemon=True),
               threading.Thread(target=read, args=(process.stderr, "stderr", max_stderr, False), daemon=True)]
    for thread in threads:
        thread.start()
    deadline = started + timeout
    try:
        while process.poll() is None and not stopped.is_set():
            if time.monotonic() >= deadline:
                result["timed_out"] = True
                break
            stopped.wait(0.02)
    finally:
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    for thread in threads:
        thread.join(5)
    for stream in (process.stdout, process.stderr):
        try:
            stream.close()
        except (OSError, ValueError):
            pass
    result["exit"] = process.returncode
    result["seconds"] = time.monotonic() - started
    return result


def _end(process: subprocess.Popen, job: "Optional[_Job]") -> None:
    """End ``process`` and everything it started."""
    if job is not None:
        job.close()
    elif os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def pycache_prefix(user_store: "str | os.PathLike[str]") -> str:
    """Where Python started for an overlay keeps its bytecode: outside every
    overlay root, so no ``__pycache__`` is written into or read from one."""
    return str(Path(user_store) / ".cache" / "pycache")
