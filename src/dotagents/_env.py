"""Chained env-file assembly + ``env.py`` execution (contract B).

Contract B, the exact sequence :func:`get_environment` performs:

  1. **Bins onto PATH FIRST**, before any env eval. Each level's ``bin`` dir
     (contract-A precedence order, *except* project-root) is prepended to
     ``PATH`` so env scripts can call overlay helpers by name. **Libs are
     published, not injected**: each level's ``lib`` dir that EXISTS
     (:func:`get_lib_paths`) is listed, ``os.pathsep``-joined, in
     ``AGENTS_PYTHONPATH``, and only an ``env.py`` child gets them prepended to
     its own ``PYTHONPATH`` -- so an ``env.py`` can ``import`` an overlay's
     library (an overlay ships ``lib/<module>.py`` beside its ``cmds/``). The
     session's ``PYTHONPATH`` is left alone: there an overlay module would
     come before site-packages and the stdlib in every Python the agent runs
     and shadow any same-named one. A caller that wants the libs opts in
     (``PYTHONPATH="$AGENTS_PYTHONPATH"``).
  2. **Two tiers, in order**: ALL ``pre.env.py`` / ``pre.env`` / ``pre.local.env``
     first, THEN ALL ``env.py`` / ``env`` / ``local.env`` -- the concatenation of
     two contract-A resolutions (:func:`resolve_env_files`). The project-root
     level resolves NOTHING: a checkout's own top-level ``env.py`` / ``env`` /
     ``local.env`` is never executed or sourced (D93).
  3. **Within each tier**, files are in the contract-A precedence order: each
     store in ``Scope.stores`` -- system, user, project -- with its overlays
     first and itself second.
  4. **Chained, later-overrides-earlier**: each file is evaluated against the
     ACCUMULATED environment of every file before it; the result also
     accumulates. Later files win on conflicting keys.
  5. **``.py`` files are EXECUTED** (:func:`get_env_from_py` runs the script as
     ``env.py --level <level> [--global]`` and reads back its env changes as
     JSON -- one object, or one object per line merged in order, so
     overlay-managed blocks appended to one ``env.py`` can each print their
     own); plain files are **sourced** (:func:`get_env_from_file`, via bash:
     an ``env -0`` snapshot before and after the ``source``, in one process).
  6. :func:`get_diff` returns only the vars that differ from the caller's base
     environment; :func:`get_environment` returns the full change set. Both
     are an :class:`EnvChanges`: the vars set, plus ``removed`` -- the base
     vars a layer unset (an ``env.py`` printing ``null`` for a key, a plain
     file running ``unset``) and no later layer set again.

An ``env.py`` value must be a JSON string, or ``null`` to unset the variable;
any other JSON type is skipped with a warning naming the key (it used to be
``str()``-coerced: ``false`` became ``False``, an array its Python repr).

The identity/proxy model is wired into the output around the file chain:

  * **Identity** (:func:`dotagents._agents.stamp_identity`) is seeded BEFORE the
    file chain, so env files can branch on ``AGENTS_HARNESS`` and override the
    stamped ``AGENTS_MODEL`` etc. (chained: a later file wins). Never clobbers a
    value already present in the base env -- unless ``explicit`` names the
    agent, in which case the identity is that agent's regardless (``launch``
    starts a harness from inside another one).
  * **Roots** are seeded right after identity, also before the chain and also
    only-if-unset: the two scope roots ``AGENTS_HOME`` / ``AGENTS_PROJECT_ROOT``,
    the interpreter ``AGENTS_PYTHON`` (``sys.executable`` -- the Python the CLI
    is running under, a default any shim or helper can trust), and one
    ``<NAME>_OVERLAY_ROOT`` per installed overlay (:attr:`Scope.overlays`,
    the same set :func:`get_overlay_roots` lists, named by
    :attr:`_overlays.Overlay.root_var` --
    the same name ``dotagents context`` expands as a placeholder), so an env
    file can reference an overlay's install dir by name.
  * **Proxy** is normalized AFTER the file chain: ``AGENTS_PROXY`` is seeded if
    unset (from ``AGENTS_WEBFETCH_PROXY_URL`` else the global
    HTTPS/HTTP/ALL_PROXY, either case); any proxy var that *already exists* is
    mirrored into BOTH cases (never creating one that was not set).
    ``AGENTS_PROXY`` is NOT fanned out into the global ``HTTP_PROXY``.
    Mirroring ``HTTP_PROXY`` into ``http_proxy`` is the opposite of the
    httpoxy mitigation, which is for a CGI handler to IGNORE the upper-case
    name (a ``Proxy:`` request header arrives as ``HTTP_PROXY``). An agent
    session is not a CGI handler, so there the upper-case var is the user's
    own setting; the chain is not meant to run inside one.

Trust model (D93): ``env.py`` runs arbitrary code, from every store the walk
visits -- the system store (only when administrators alone can write it), the
user store, and the project's ``<project>/.agents`` with their overlays. A
project's ``.agents/`` is trusted like a harness's own ``.claude/settings.json``:
opening a session in a repo trusts it. The project ROOT outside ``.agents/``
never contributes code. Security (Leakage rule): never log the
resulting ``DOTAGENTS_*``/``AGENTS_*`` secret VALUES -- callers that print the
diff must treat it as sensitive; this module logs var NAMES only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional

from dotagents._scope import Scope, project_root_default


class EnvChanges(dict):
    """A change set: the vars set (``dict[str, str]``, as before) plus
    :attr:`removed`, the vars a layer unset.

    A plain ``dict`` subclass, so a caller that treats the result as the vars
    to set keeps working; one that also honours removals reads ``removed``
    (empty unless an ``env.py`` printed ``null`` for a key or a plain env file
    ran ``unset``). A key is never in both."""

    def __init__(self, *args, removed: "Iterable[str]" = (), **kwargs):
        super().__init__(*args, **kwargs)
        #: Names to unset.
        self.removed: "set[str]" = set(removed)

    def __repr__(self) -> str:
        return "EnvChanges(%s, removed=%r)" % (dict.__repr__(self), sorted(self.removed))


# OS-bootstrap vars a spawned interpreter needs to even start (Windows
# CreateProcess wants SystemRoot; POSIX loaders want the temp dir). These are
# backfilled from the real environment ONLY when the accumulated env lacks them,
# so a child env.py always launches -- without leaking user config the chain did
# not itself set.
_SPAWN_BOOTSTRAP_VARS = (
    "SYSTEMROOT",
    "SystemRoot",
    "COMSPEC",
    "PATHEXT",
    "WINDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LD_LIBRARY_PATH",
)


def _spawn_env(child_env: "dict[str, str]") -> "dict[str, str]":
    """Backfill OS-bootstrap vars so a spawned process can start (see above)."""
    out = dict(child_env)
    for var in _SPAWN_BOOTSTRAP_VARS:
        if var not in out and var in os.environ:
            out[var] = os.environ[var]
    return out


# --------------------------------------------------------------------------- #
# Output-format aliases + calling-shell detection (D83).
# --------------------------------------------------------------------------- #

#: Alias -> canonical output format. `_format_env` normalizes through this so the
#: renderer only ever sees a canonical name. `auto` is resolved earlier (by the
#: caller, via :func:`detect_shell_format`) and is NOT a renderer format.
FORMAT_ALIASES = {
    "export": "export",
    "posix": "export",
    "sh": "export",
    "bash": "export",
    "dotenv": "dotenv",
    "env": "dotenv",
    "powershell": "powershell",
    "pwsh": "powershell",
    "ps": "powershell",
    "cmd": "cmd",
    "bat": "cmd",
    "batch": "cmd",
    "fish": "fish",
    "json": "json",
    "ini": "ini",
    "yaml": "yaml",
}

#: Every value `--format` accepts on the CLI (aliases + `auto`).
KNOWN_FORMATS = tuple(sorted(set(FORMAT_ALIASES) | {"auto"}))

#: Shell-exe basename (lowercased, no extension) -> canonical output format.
_SHELL_EXE_FORMAT = {
    "powershell": "powershell",
    "pwsh": "powershell",
    "cmd": "cmd",
    "bash": "export",
    "sh": "export",
    "zsh": "export",
    "dash": "export",
    "ksh": "export",
    "fish": "fish",
}


def _win_ppid_exe_map():  # pragma: no cover - exercised only on win32
    """pid -> (parent_pid, exe_basename_lower_noext) via a Toolhelp snapshot.

    Pure stdlib ctypes against kernel32. Any failure returns ``{}`` so the caller
    degrades to the OS default rather than raising. Reads only process names.
    """
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002
    MAX_PATH = 260

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * MAX_PATH),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    INVALID = wintypes.HANDLE(-1).value
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID:
        return {}
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        result = {}
        ok = kernel32.Process32First(snap, ctypes.byref(entry))
        while ok:
            name = entry.szExeFile.decode("ascii", "replace").lower()
            if name.endswith(".exe"):
                name = name[:-4]
            result[int(entry.th32ProcessID)] = (
                int(entry.th32ParentProcessID),
                name,
            )
            ok = kernel32.Process32Next(snap, ctypes.byref(entry))
        return result
    finally:
        kernel32.CloseHandle(snap)


def _detect_shell_format_win():  # pragma: no cover - exercised only on win32
    """Walk the parent-process chain on Windows; first shell exe wins.

    Returns a canonical format, defaulting to ``"powershell"`` when no shell is
    found in the chain (the common Windows case). Never raises.

    A ``cmd.exe`` whose OWN parent is another shell is skipped: that is the
    `dotagents.cmd` wrapper `_wrappers.py` writes (a batch file cannot `exec`,
    so the chain from PowerShell is `python <- cmd.exe <- pwsh`, and stopping
    there would hand a PowerShell user `set "K=v"` lines). A `cmd` whose
    parent is not a shell (Windows Terminal, explorer, a scheduler) is a real
    interactive cmd and still wins.
    """
    try:
        pmap = _win_ppid_exe_map()
        if not pmap:
            return "powershell"
        pid = os.getpid()
        for _ in range(10):
            entry = pmap.get(pid)
            if entry is None:
                break
            parent_pid, name = entry
            fmt = _SHELL_EXE_FORMAT.get(name)
            if fmt is not None:
                if fmt == "cmd":
                    grand = pmap.get(parent_pid)
                    if grand is not None and grand[1] in _SHELL_EXE_FORMAT:
                        pid = parent_pid
                        continue  # the batch wrapper; keep walking
                return fmt
            if parent_pid == pid or parent_pid == 0:
                break
            pid = parent_pid
    except Exception:  # noqa: BLE001 - detection must never crash
        return "powershell"
    return "powershell"


def _posix_parent_comm(ppid: int) -> str:
    """Parent-process command name on POSIX, via layered fallbacks.

    ``/proc`` is Linux-only (absent on macOS/OpenBSD), so this tries in order and
    each layer is guarded so a failure falls through to the next, never raising:

      1. ``/proc/<ppid>/comm`` -- Linux/WSL, fast.
      2. ``ps -o comm= -p <ppid>`` -- POSIX-standard; works on macOS, OpenBSD,
         and Linux. Basename of the output is taken.
      3. ``$SHELL`` basename -- last resort.

    Returns a lowercased basename with any ``.exe`` suffix stripped, or ``""``.
    """
    # 1. Linux /proc
    try:
        with open("/proc/%d/comm" % ppid, "r", encoding="ascii", errors="replace") as fh:
            comm = fh.read().strip()
        if comm:
            return _norm_comm(comm)
    except Exception:  # noqa: BLE001 - fall through to the next layer
        pass
    # 2. POSIX `ps -o comm=` (macOS / OpenBSD / any POSIX)
    try:
        out = subprocess.run(
            ["ps", "-o", "comm=", "-p", str(ppid)],
            capture_output=True, text=True, check=False,
        ).stdout.strip()
        if out:
            return _norm_comm(out)
    except Exception:  # noqa: BLE001 - fall through to $SHELL
        pass
    # 3. $SHELL basename
    try:
        shell = os.environ.get("SHELL", "")
        if shell:
            return _norm_comm(shell)
    except Exception:  # noqa: BLE001
        pass
    return ""


def _norm_comm(name: str) -> str:
    """Lowercase basename of a command/path, ``.exe`` suffix stripped, and the
    leading ``-`` that marks a login shell dropped (macOS ``ps`` reports a
    login fish as ``-fish``)."""
    base = os.path.basename(name.strip()).lower().lstrip("-")
    if base.endswith(".exe"):
        base = base[:-4]
    return base


def _detect_shell_format_posix():
    """Detect the calling shell on POSIX; default ``"export"``. Never raises.

    Uses :func:`_posix_parent_comm` (layered ``/proc`` -> ``ps -o comm=`` ->
    ``$SHELL``) so it works on Linux/WSL, macOS, and OpenBSD alike.
    """
    try:
        comm = _posix_parent_comm(os.getppid())
        return _SHELL_EXE_FORMAT.get(comm, "export")
    except Exception:  # noqa: BLE001 - detection must never crash
        return "export"


def detect_shell_format() -> str:
    """Best-effort detection of the calling shell's output format.

    Walks the parent-process chain (Windows: a stdlib-ctypes Toolhelp snapshot;
    POSIX: ``/proc/<ppid>/comm`` else ``$SHELL``) and returns the canonical
    format for the first shell found. Any failure degrades to the OS default
    (``"powershell"`` on win32, ``"export"`` elsewhere) and NEVER raises. Reads
    process names only -- no environment values.
    """
    if sys.platform == "win32":
        return _detect_shell_format_win()
    return _detect_shell_format_posix()


# --------------------------------------------------------------------------- #
# File evaluators.
# --------------------------------------------------------------------------- #


#: Vars bash itself sets in the child that sources a plain env file. They are
#: bash's, not the file's, and must not be reported as the file's changes:
#: `_`, `PWD` (in POSIX `/c/...` form -- emitted into a PowerShell session it
#: is simply wrong), `OLDPWD`, `SHLVL`, and MSYS2's `MSYSTEM*` family.
_BASH_OWN_VARS = frozenset({"_", "PWD", "OLDPWD", "SHLVL"})
_BASH_OWN_PREFIXES = ("MSYSTEM", "MINGW_", "MSYS2_")


def _is_bash_own_var(key: str) -> bool:
    return key in _BASH_OWN_VARS or key.startswith(_BASH_OWN_PREFIXES)


def _parse_env_entries(entries: "Iterable[bytes]") -> "dict[str, str]":
    """``KEY=value`` entries (``env -0`` records) into a dict; bash's own vars
    and entries without ``=`` are skipped. Bytes that are not valid UTF-8 are
    kept via ``surrogateescape`` rather than aborting the whole assembly on
    one odd value (Python's own ``os.environ`` uses the same trick on POSIX)."""
    out: "dict[str, str]" = {}
    for entry in entries:
        if not entry or b"=" not in entry:
            continue
        key, value = entry.split(b"=", 1)
        name = key.decode("utf-8", "surrogateescape")
        if _is_bash_own_var(name):
            continue
        out[name] = value.decode("utf-8", "surrogateescape")
    return out


def _changed_env(env_dump: bytes, base_env: "dict[str, str]") -> "dict[str, str]":
    """Parse a NUL-delimited ``env -0`` dump into the vars that changed vs base."""
    sourced = _parse_env_entries(env_dump.split(b"\0"))
    return {
        k: v for k, v in sourced.items() if k not in base_env or base_env[k] != v
    }


#: Section markers in the sourcing script's output. Each is printed as its own
#: NUL-terminated record and has no ``=``, so no ``env -0`` record can equal it.
_SNAP_AFTER = b"@@dotagents:after@@"
_SNAP_NATIVE = b"@@dotagents:native@@"

#: The script :func:`get_env_from_file` runs as ``bash -c <script> bash <file>``.
#:
#: Both snapshots come from ONE bash process, so whatever bash itself does to
#: the environment at startup cancels out: MSYS2 / Cygwin rewrite PATH, HOME,
#: TEMP/TMP, SHELL, ORIGINAL_PATH into POSIX form, Git's ``bin/bash.exe``
#: prepends its own dirs to PATH, and an x64 bash on ARM64 reports another
#: PROCESSOR_ARCHITECTURE. Only a key whose value the FILE changed differs.
#:
#: Under MSYS2 / Cygwin the ``env`` and ``cygpath`` there are addressed as
#: ``/usr/bin/...`` (``usr/bin/bash.exe`` started from a Windows PATH does not
#: have them on it), and every ``*PATH`` variable the file changed is converted
#: back to Windows form by ``cygpath -w -p`` -- after (``A:``) and, when it
#: existed, before (``B:``) -- so the caller can splice the file's additions
#: onto the Windows value it passed in. A value already in Windows form
#: (``;``, ``\``, or a drive prefix) is left for the caller as it is.
_SOURCE_SCRIPT = r"""
__da_env=env
__da_msys=
case "$OSTYPE" in msys*|cygwin*) __da_msys=1; __da_env=/usr/bin/env ;; esac
if [ -n "$__da_msys" ]; then
  for __da_n in $(compgen -e); do
    case "$__da_n" in *PATH) printf -v "__da_b_$__da_n" '%s' "${!__da_n}" ;; esac
  done
fi
"$__da_env" -0 || exit 1
set -a
source "$1" >/dev/null 2>&1 || exit 1
set +a +e +u
printf '%s\0' '@@dotagents:after@@'
"$__da_env" -0 || exit 1
IFS=$' \t\n'
if [ -n "$__da_msys" ]; then
  printf '%s\0' '@@dotagents:native@@'
  for __da_n in $(compgen -e); do
    case "$__da_n" in *PATH) ;; *) continue ;; esac
    __da_b=__da_b_$__da_n
    if [ -n "${!__da_b+x}" ] && [ "${!__da_b}" = "${!__da_n}" ]; then continue; fi
    case "${!__da_n}" in *\;*|*\\*|[A-Za-z]:*) continue ;; esac
    printf 'A:%s=%s\0' "$__da_n" "$(/usr/bin/cygpath -w -p -- "${!__da_n}")"
    if [ -n "${!__da_b+x}" ]; then
      case "${!__da_b}" in
        *\;*|*\\*|[A-Za-z]:*) ;;
        *) printf 'B:%s=%s\0' "$__da_n" "$(/usr/bin/cygpath -w -p -- "${!__da_b}")" ;;
      esac
    fi
  done
fi
exit 0
"""


def _env_lookup(env: "dict[str, str]", key: str) -> "Optional[str]":
    """``env[key]``, case-insensitively on Windows (MSYS2 reports ``Path`` as
    ``PATH``)."""
    if key in env:
        return env[key]
    if os.name == "nt":
        folded = key.upper()
        for k, v in env.items():
            if k.upper() == folded:
                return v
    return None


def _splice_native(
    native_after: str, native_before: "Optional[str]", original: "Optional[str]"
) -> str:
    """The file's new ``*PATH`` value in Windows form, keeping the caller's
    original value verbatim where the file kept it.

    ``native_before`` / ``native_after`` are bash's own view converted by
    ``cygpath``; the before view may carry dirs bash added at startup (Git's
    launcher prepends ``/mingw64/bin:/usr/bin``). When the file only added
    around the old value (``PATH=/x:$PATH``), the old block is found in the
    new one and replaced by ``original`` -- so neither the launcher's dirs
    nor a round-trip through ``cygpath`` leak into the result. Anything else
    (the file rewrote the value) is returned as converted."""
    after = [s for s in native_after.split(";") if s]
    if native_before is not None and original is not None:
        before = [s for s in native_before.split(";") if s]
        n = len(before)
        if n:
            for i in range(len(after) - n + 1):
                if after[i:i + n] == before:
                    parts = after[:i] + ([original] if original else []) + after[i + n:]
                    return ";".join(parts)
    return ";".join(after)


def interpreter(osenv: "dict[str, str]") -> str:
    """The Python to run env-layer scripts with: a pinned ``AGENTS_PYTHON`` that
    names an existing file, else the interpreter running this code. The pin is
    what ``env`` exports for shims, so dotagents honours it for its own
    subprocesses too -- otherwise a user who pins 3.12 because the CLI's venv
    is 3.9 gets their helpers on 3.12 and their ``env.py`` on 3.9."""
    pinned = osenv.get("AGENTS_PYTHON")
    if pinned and Path(pinned).is_file():
        return pinned
    return sys.executable


def get_env_from_py(
    env_py: Path,
    base_env: "dict[str, str]",
    *,
    level: str = "",
    global_scope: bool = False,
    logger=None,
) -> EnvChanges:
    """Execute an ``env.py`` and read back its JSON object(s) of env changes.

    The script runs as ``<python> env.py --level <level> [--global]``:
    ``<level>`` is the contract-A level it was found at (``system``, ``user``,
    ``project``, or an overlay's name) and ``--global`` says the project tiers
    are skipped. ``--agent <level>`` is passed as well -- the old name of
    ``--level`` (it never named an agent), kept for scripts that read it.

    The script runs as a child with ``base_env`` (the accumulated environment)
    and prints its changes to stdout as JSON: ONE object, or one object PER
    LINE, merged in order (a later line wins on a key). The per-line form is
    what makes overlay-managed blocks composable: each overlay's ``setup.py``
    appends its own block to the store's ``env.py`` and each block prints its
    own object. A non-zero exit or unparseable output contributes nothing and
    is logged by NAME only -- never abort assembly, and never echo the child's
    stdout (it may carry secret values).

    The child runs with ``PYTHONIOENCODING=utf-8`` and its stdout is decoded as
    UTF-8 with ``surrogateescape``, so no byte it prints can fail the read. A
    key that cannot be an environment variable (empty, or containing ``=`` or
    NUL) and a value containing NUL are dropped with a warning naming the key:
    applied, they would crash the next spawn instead.

    A value must be a JSON string; ``null`` unsets the variable (it lands in
    the result's :attr:`EnvChanges.removed`); any other type is skipped with
    a warning naming the key, never the value.
    """
    args = [interpreter(base_env), str(env_py), "--level", level, "--agent", level]
    if global_scope:
        args.append("--global")
    spawn = _spawn_env(base_env)
    spawn["PYTHONIOENCODING"] = "utf-8"
    # The overlay libs, for this child only (never the session's PYTHONPATH).
    libs = [p for p in base_env.get("AGENTS_PYTHONPATH", "").split(os.pathsep) if p]
    if libs:
        updated = _prepended_path_var(spawn, "PYTHONPATH", list(reversed(libs)))
        if updated is not None:
            spawn["PYTHONPATH"] = updated
    try:
        proc = subprocess.run(args, capture_output=True, check=False, env=spawn)
    except (OSError, ValueError) as e:  # interpreter missing / unusable env
        if logger:
            logger.warning("env.py could not run: %s (%s)", env_py, type(e).__name__)
        return EnvChanges()
    if proc.returncode != 0:
        if logger:
            logger.warning("env.py failed (exit %s): %s", proc.returncode, env_py)
        return EnvChanges()
    try:
        parsed = _parse_env_json(proc.stdout.decode("utf-8", "surrogateescape"))
    except (json.JSONDecodeError, ValueError) as e:
        if logger:
            logger.warning("env.py output not JSON: %s (%s)", env_py, e)
        return EnvChanges()
    out = EnvChanges()
    for key, value in parsed.items():
        if not _is_valid_env_key(key):
            if logger:
                logger.warning("env.py %s: skipped a key that is not a valid variable name", env_py)
            continue
        if value is None:
            out.pop(key, None)
            out.removed.add(key)
            continue
        if not isinstance(value, str):
            if logger:
                logger.warning(
                    "env.py %s: skipped %s (value is a JSON %s, not a string)",
                    env_py, key, type(value).__name__,
                )
            continue
        if "\0" in value:
            if logger:
                logger.warning("env.py %s: skipped %s (value contains NUL)", env_py, key)
            continue
        out.removed.discard(key)
        out[key] = value
    return out


def _is_valid_env_key(key: object) -> bool:
    """A name the OS accepts as an environment variable: a non-empty str with
    no ``=`` and no NUL."""
    return isinstance(key, str) and bool(key) and "=" not in key and "\0" not in key


def _parse_env_json(stdout: str) -> "dict[str, object]":
    """One JSON object, or one JSON object per non-blank line merged in order.

    Raises ``json.JSONDecodeError`` / ``ValueError`` when any line is not an
    object, so a partly-broken script still contributes nothing (the caller
    warns by name) rather than a half-applied change set."""
    text = stdout.strip()
    if not text:
        return {}
    try:
        whole = json.loads(text)
    except json.JSONDecodeError:
        whole = None
    if whole is not None:
        if not isinstance(whole, dict):
            raise ValueError("env.py must output a JSON object")
        return whole
    merged: "dict[str, object]" = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if not isinstance(obj, dict):
            raise ValueError("env.py must output a JSON object per line")
        merged.update(obj)
    return merged


#: The resolved bash (``None`` = none found), once per process; see find_bash.
_BASH_RESOLVED: "list[Optional[str]]" = []
_BASH_WARNED: "list[bool]" = []


def _windows_bash_candidates() -> "list[str]":
    """Where a Windows bash that runs POSIX shell lives, most specific first:
    beside the ``git`` on PATH (``<root>/cmd/git.exe``,
    ``<root>/<mingw>/bin/git.exe``: ``<root>/bin/bash.exe`` then
    ``<root>/usr/bin/bash.exe``), then the standard install dirs, then
    whatever ``bash`` PATH names -- LAST, because on a stock Windows PATH that
    is ``WindowsApps\\bash.exe`` (or System32's), the WSL launcher."""
    candidates: "list[str]" = []
    found = shutil.which("git", path=os.environ.get("PATH"))
    if found:
        here = Path(found).resolve().parent
        for root in (here.parent, here.parent.parent, here.parent.parent.parent):
            candidates += [str(root / "bin" / "bash.exe"), str(root / "usr" / "bin" / "bash.exe")]
    dirs = [os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432")]
    if os.environ.get("LOCALAPPDATA"):
        dirs.append(os.path.join(os.environ["LOCALAPPDATA"], "Programs"))
    for base in dirs:
        if base:
            candidates += [
                os.path.join(base, "Git", "bin", "bash.exe"),
                os.path.join(base, "Git", "usr", "bin", "bash.exe"),
            ]
    found = shutil.which("bash", path=os.environ.get("PATH"))
    if found:
        candidates.append(found)
    return candidates


def _is_windows_posix_bash(candidate: str) -> bool:
    """True if ``candidate`` is an MSYS2 / Cygwin bash -- one that runs on
    THIS filesystem. The WSL launcher also runs ``bash -c`` (and passes an
    ``echo ok`` proof when a distro is installed), but inside Linux, where the
    Windows path of an env file does not exist; its ``$OSTYPE`` is
    ``linux-gnu``."""
    try:
        proc = subprocess.run(
            [candidate, "-c", 'printf %s "$OSTYPE"'],
            capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    ostype = proc.stdout.decode("ascii", "replace").strip().lower()
    return proc.returncode == 0 and ostype.startswith(("msys", "cygwin"))


def find_bash(logger=None) -> "Optional[str]":
    """The absolute path of a bash that can source a plain env file, or
    ``None``. Resolved once per process against the REAL environment (not the
    chain's accumulated PATH, which need not contain bash's install dir).

    On POSIX it is ``bash`` on ``PATH``. On Windows a bare ``which`` is wrong
    whenever ``WindowsApps`` precedes Git on PATH -- the usual case in a
    PowerShell -- because that ``bash.exe`` is the WSL launcher; so Git's bash
    beside ``git.exe`` and under the standard install dirs is tried first,
    and every candidate must prove it is MSYS2 / Cygwin
    (:func:`_is_windows_posix_bash`). Warns once when none is found."""
    if not _BASH_RESOLVED:
        if os.name == "nt":
            seen: "set[str]" = set()
            resolved = None
            for cand in _windows_bash_candidates():
                key = os.path.normcase(cand)
                if key in seen or not os.path.isfile(cand):
                    continue
                seen.add(key)
                if _is_windows_posix_bash(cand):
                    resolved = cand
                    break
        else:
            resolved = shutil.which("bash", path=os.environ.get("PATH"))
        _BASH_RESOLVED.append(resolved)
    bash = _BASH_RESOLVED[0]
    if bash is None and logger and not _BASH_WARNED:
        _BASH_WARNED.append(True)
        logger.warning("no usable bash found: plain env files are skipped")
    return bash


def get_env_from_file(
    env_file: Path, base_env: "dict[str, str]", logger=None
) -> EnvChanges:
    """Source a plain env file in bash and return the vars it changed.

    Runs :data:`_SOURCE_SCRIPT`: an ``env -0`` snapshot, ``set -a; source
    <file> || exit 1``, a second snapshot -- in one bash process, so only what
    the file changed differs (an MSYS2 bash's own PATH/HOME/TEMP rewriting
    cancels out), with a changed ``*PATH`` value converted back to Windows form
    under MSYS2 / Cygwin. If no bash is found (:func:`find_bash`), or the
    source FAILS (a missing file, a directory, a syntax error, an ``exit`` in
    the file), the file contributes nothing -- logged by name, never fatal.
    A variable the file ``unset`` is in the result's ``removed``.
    """
    spawn = _spawn_env(base_env)

    bash = find_bash(logger)
    if bash is None:
        return EnvChanges()
    try:
        proc = subprocess.run(
            # The path is bash's `$1`, never spliced into the script: a
            # JSON-quoted path lost non-ASCII characters (`\u00e9`) and
            # expanded `$` / backticks inside the double quotes.
            [bash, "-c", _SOURCE_SCRIPT, "bash", str(env_file)],
            capture_output=True,
            text=False,
            check=False,
            env=spawn,
        )
    except OSError as e:
        if logger:
            logger.warning("cannot source env file (no bash?): %s (%s)", env_file, e)
        return EnvChanges()
    records = proc.stdout.split(b"\0")
    # A file that calls `exit` itself ends the shell before the second
    # snapshot: that is a failure too, not an empty change set.
    if proc.returncode != 0 or _SNAP_AFTER not in records:
        if logger:
            logger.warning("env file source failed: %s", env_file)
        return EnvChanges()
    cut = records.index(_SNAP_AFTER)
    tail = records[cut + 1:]
    native_at = tail.index(_SNAP_NATIVE) if _SNAP_NATIVE in tail else len(tail)
    before = _parse_env_entries(records[:cut])
    after = _parse_env_entries(tail[:native_at])
    native = _parse_env_entries(tail[native_at + 1:])

    changes = EnvChanges(removed=(k for k in before if k not in after))
    for key, value in after.items():
        if key in before and before[key] == value:
            continue
        converted = native.get("A:" + key)
        if converted is not None:
            value = _splice_native(
                converted, native.get("B:" + key), _env_lookup(spawn, key)
            )
        changes[key] = value
    return changes


# --------------------------------------------------------------------------- #
# PATH bins (contract B step 1).
# --------------------------------------------------------------------------- #


def get_bin_paths(scope: Scope) -> "list[Path]":
    """Each level's ``bin`` dir in contract-A precedence order, EXCEPT project-root.

    Uses ``include_missing=True``: a bin dir is offered for every level even
    if absent, and
    :func:`get_environment` prepends ALL of them to ``PATH`` -- including the
    ones that do not exist (so a later-created ``<store>/bin`` is found without
    re-running ``env``). project-root's ``bin`` is explicitly excluded
    (``{"project-root": ""}``): a project's own top-level ``bin`` is not an
    agent bin.
    """
    resolved = scope.paths({"default": "bin", "project-root": ""}, include_missing=True)
    return [path for _level, path, _root in resolved]


def get_overlay_roots(scope: Scope) -> "list[Path]":
    """The installed overlay dirs of the scope (:attr:`Scope.overlays` -- the
    user store's, then the project's, a same-named project overlay shadowing
    the store's copy): the set of overlays that get a ``<NAME>_OVERLAY_ROOT``
    var is exactly the set whose ``bin``/``env`` files the contract-A walk
    resolves."""
    return [overlay.path for overlay in scope.overlays]


def get_lib_paths(scope: Scope) -> "list[Path]":
    """Each level's ``lib`` dir in contract-A precedence order, EXCEPT project-root
    -- what :func:`get_environment` publishes as ``AGENTS_PYTHONPATH`` (in
    reverse, highest precedence first) and puts on an ``env.py`` child's
    ``PYTHONPATH``; never on the session's.

    Only dirs that EXIST are returned (unlike ``bin``): a dozen absent entries
    on a search path are noise a reader has to rule out. project-root's
    ``lib`` is excluded for the same reason as its ``bin``: a project's own
    top-level ``lib`` is not an agent library.
    """
    resolved = scope.paths({"default": "lib", "project-root": ""}, include_missing=False)
    return [path for _level, path, _root in resolved if path.is_dir()]


def _prepend_missing(path_entries: "list[str]", new_entries: "list[str]") -> "list[str]":
    """Prepend each new entry not already present.

    Each new entry is inserted at position 0 in iteration order, so a later
    new entry ends up EARLIER.
    """
    for entry in new_entries:
        if entry not in path_entries:
            path_entries.insert(0, entry)
    return path_entries


def _prepended_path_var(osenv: "dict[str, str]", var: str, entries: "list[str]") -> "Optional[str]":
    """The new value of an ``os.pathsep``-joined path var with ``entries``
    prepended (:func:`_prepend_missing` order), or ``None`` if nothing changes."""
    current = osenv.get(var, "").split(os.pathsep) if osenv.get(var) else []
    updated = os.pathsep.join(_prepend_missing(current, entries))
    return updated if updated != osenv.get(var, "") else None


# --------------------------------------------------------------------------- #
# Env-file resolution (contract B steps 2 + 3).
# --------------------------------------------------------------------------- #


def resolve_env_files(scope: Scope) -> "list[tuple[str, Path, Optional[Path]]]":
    """The ordered, existing env files: ALL pre-tier then ALL main-tier, for a
    :class:`Scope`.

    Each tier is one contract-A resolution (:meth:`Scope.paths`). Per-level
    filename resolution (contract A point 2): ``pre.env.py``/``pre.env`` and
    ``env.py``/``env`` resolve at every level EXCEPT project-root; the project
    level (``<project>/.agents``) ADDITIONALLY resolves ``pre.local.env`` /
    ``local.env``. Only existing regular FILES are returned (a directory named
    ``env`` -- a common virtualenv name -- is not an env file).

    The project ROOT resolves nothing: the env chain runs at every session
    start, so a checkout's top-level ``env.py`` / ``env`` / ``local.env`` would
    be code a cloned repository runs the moment a session opened in it. A
    user's own local overrides live in ``<project>/.agents/local.env`` (D93).
    """
    pre_tier = scope.paths(
        {"default": "pre.env.py", "project-root": ""},
        {"default": "pre.env", "project-root": ""},
        {"project": "pre.local.env"},
    )
    main_tier = scope.paths(
        {"default": "env.py", "project-root": ""},
        {"default": "env", "project-root": ""},
        {"project": "local.env"},
    )
    return [item for item in pre_tier + main_tier if item[1].is_file()]


# --------------------------------------------------------------------------- #
# Proxy normalization.
# --------------------------------------------------------------------------- #

_PROXY_BASES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
#: Seed order for AGENTS_PROXY when it is unset (webfetch var wins, then global).
_PROXY_SEED_ORDER = (
    "AGENTS_WEBFETCH_PROXY_URL",
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
)


def apply_proxy_model(osenv: "dict[str, str]") -> "dict[str, str]":
    """Return the proxy changes for ``osenv`` per the proxy model.

    * Seed ``AGENTS_PROXY`` if unset, from the first populated
      :data:`_PROXY_SEED_ORDER` var (webfetch var wins, then the global proxy in
      either case). An existing ``AGENTS_PROXY`` is respected.
    * Mirror every proxy var that ALREADY EXISTS into both cases -- fills only
      the missing case, never introduces a proxy that was not set (so
      ``http_proxy`` is populated from ``HTTP_PROXY``; see the module note on
      httpoxy).
    * ``AGENTS_PROXY`` is NOT fanned into the global ``HTTP_PROXY``.
    """
    changes: "dict[str, str]" = {}

    if not osenv.get("AGENTS_PROXY"):
        for src in _PROXY_SEED_ORDER:
            val = osenv.get(src)
            if val:
                changes["AGENTS_PROXY"] = val
                break

    for base in _PROXY_BASES:
        upper, lower = base, base.lower()
        up_val = osenv.get(upper)
        lo_val = osenv.get(lower)
        if up_val and not lo_val:
            changes[lower] = up_val
        elif lo_val and not up_val:
            changes[upper] = lo_val

    return changes


# --------------------------------------------------------------------------- #
# Assembly (contract B).
# --------------------------------------------------------------------------- #


def get_environment(
    scope: Scope,
    *,
    base_env: "Optional[dict[str, str]]" = None,
    explicit: "Optional[str]" = None,
    logger=None,
) -> EnvChanges:
    """Assemble the env CHANGES (vars this adds/overrides vs ``base_env``) for a
    :class:`Scope`.

    Follows contract B: identity seeded, PATH bins first, the two tiers
    chained (later overrides earlier), then proxy normalization. Returns only
    what changed: the vars set, and in ``removed`` the ``base_env`` vars a
    layer unset (and no later layer set again).
    """
    from dotagents._agents import stamp_identity

    agents_dir = scope.user_root
    # The project root a user-scope walk pins: the resolved default, so the
    # emitted AGENTS_PROJECT_ROOT still names the project the session is in.
    project_root = scope.project_root or project_root_default()
    global_scope = scope.global_scope

    osenv = dict(base_env if base_env is not None else os.environ)
    base_keys = frozenset(osenv)
    env = EnvChanges()

    def _apply(changes: "dict[str, str]") -> None:
        for key, value in changes.items():
            env[key] = osenv[key] = value
            env.removed.discard(key)
        for key in getattr(changes, "removed", ()):
            env.pop(key, None)
            osenv.pop(key, None)
            if key in base_keys:
                env.removed.add(key)

    # --- Identity seed --- before the file chain so files can override.
    _apply(stamp_identity(osenv, explicit=explicit, root=project_root))

    def _seed(key: str, value: "Optional[str]") -> None:
        """Only-if-unset: a value already set upstream (user, harness, an
        earlier layer) holds; an empty value is never written."""
        if value and not osenv.get(key):
            _apply({key: value})

    # --- Scope roots --- pin the two scope roots so every command/subprocess agrees:
    # AGENTS_HOME = the user store (agents_dir, ~/.agents by default); AGENTS_PROJECT_ROOT
    # = this project's root (resolve_scope reads it).
    _seed("AGENTS_HOME", str(agents_dir))
    _seed("AGENTS_PROJECT_ROOT", str(project_root))
    # --- Interpreter --- AGENTS_PYTHON = the Python dotagents itself is running
    # under, so shims and helper scripts have a known-good default: a bare
    # `python`/`python3` on PATH may be a Store alias stub, an emulated build,
    # a venv's, or absent, while this one demonstrably runs the CLI.
    # `sys.executable` is '' or None under an embedding host: then nothing is
    # exported rather than an empty command name.
    _seed("AGENTS_PYTHON", sys.executable)

    # --- Overlay roots --- one `<NAME>_OVERLAY_ROOT` per installed overlay, the
    # same name `dotagents context` expands as a `<NAME_OVERLAY_ROOT>` placeholder
    # (`Overlay.root_var` is the single naming rule). Seeded before the chain so
    # env files can reference an overlay's install dir; only-if-unset, like the
    # scope roots, so an upstream pin holds.
    for overlay in scope.overlays:
        pinned = osenv.get(overlay.root_var)
        store_copy = str(Path(agents_dir) / "overlays" / overlay.name)
        if not pinned:
            _apply({overlay.root_var: str(overlay.path)})
        elif pinned == store_copy and str(overlay.path) != store_copy:
            # The session pinned the STORE's copy (as the SessionStart env does),
            # and a same-named PROJECT overlay now shadows it: re-point to the
            # copy that is actually in play. Any other pin (a harness's own
            # value) is respected.
            _apply({overlay.root_var: str(overlay.path)})

    # --- Contract B step 1: bins onto PATH FIRST. ---
    bin_paths = [str(p) for p in get_bin_paths(scope)]
    updated = _prepended_path_var(osenv, "PATH", bin_paths)
    if updated is not None:
        _apply({"PATH": updated})

    # --- Libs: published as AGENTS_PYTHONPATH, same moment --- the env.py
    # chain below gets them on its own PYTHONPATH (`get_env_from_py`); the
    # session's PYTHONPATH is never touched, since an overlay module there
    # shadows a same-named site-packages or stdlib module in every Python the
    # agent runs. Highest precedence first (the project's lib before the
    # user store's), existing dirs only (see `get_lib_paths`). The value is
    # this walk's, so an inherited one from another scope is replaced.
    lib_paths: "list[str]" = []
    for p in reversed(get_lib_paths(scope)):
        if str(p) not in lib_paths:
            lib_paths.append(str(p))
    if lib_paths:
        if osenv.get("AGENTS_PYTHONPATH") != os.pathsep.join(lib_paths):
            _apply({"AGENTS_PYTHONPATH": os.pathsep.join(lib_paths)})
    elif "AGENTS_PYTHONPATH" in osenv:
        _apply(EnvChanges(removed=["AGENTS_PYTHONPATH"]))

    # --- Contract B steps 2-5: the two tiers, chained, later-overrides-earlier. ---
    # One broken layer contributes nothing; it never takes the rest of the
    # env with it (the hooks send stderr to /dev/null, so a crash here would
    # silently empty every session). Logged by file name only.
    for level, path, _root in resolve_env_files(scope):
        try:
            if path.suffix == ".py":
                changes = get_env_from_py(
                    path, osenv, level=level, global_scope=global_scope, logger=logger
                )
            else:
                changes = get_env_from_file(path, osenv, logger=logger)
        except Exception as e:  # noqa: BLE001 - see above
            if logger:
                logger.warning("env layer skipped: %s (%s)", path, type(e).__name__)
            continue
        _apply(changes)

    # --- Proxy normalization --- after the chain so file-set proxies
    #     are normalized too.
    _apply(apply_proxy_model(osenv))

    return env


def get_diff(
    scope: Scope,
    *,
    base_env: "Optional[dict[str, str]]" = None,
    explicit: "Optional[str]" = None,
    logger=None,
) -> EnvChanges:
    """Only the assembled vars that differ from ``base_env`` (current env).

    ``get_environment`` already returns changes vs ``base_env``, so the diff is
    the subset whose value actually differs from the base, plus the base vars
    a layer unset (``removed``).
    """
    base = dict(base_env if base_env is not None else os.environ)
    full = get_environment(
        scope,
        base_env=base,
        explicit=explicit,
        logger=logger,
    )
    return EnvChanges(
        {k: v for k, v in full.items() if k not in base or base[k] != v},
        removed=(k for k in full.removed if k in base),
    )
