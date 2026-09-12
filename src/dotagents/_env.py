"""Chained env-file assembly + ``env.py`` execution (contract B).

Contract B, the exact sequence :func:`get_environment` performs:

  1. **Bins onto PATH FIRST**, before any env eval. Each level's ``bin`` dir
     (contract-A precedence order, *except* project-root) is prepended to
     ``PATH`` so env scripts can call overlay helpers by name. **Libs onto
     PYTHONPATH the same way**: each level's ``lib`` dir that EXISTS is
     prepended to ``PYTHONPATH`` (:func:`get_lib_paths`), so an ``env.py`` --
     and every subprocess that inherits the env -- can ``import`` an overlay's
     library (an overlay ships ``lib/<module>.py`` beside its ``cmds/``).
  2. **Two tiers, in order**: ALL ``pre.env.py`` / ``pre.env`` / ``pre.local.env``
     first, THEN ALL ``env.py`` / ``env`` / ``local.env`` -- the concatenation of
     two contract-A resolutions (:func:`resolve_env_files`). The project-root
     level resolves ONLY ``pre.local.env`` / ``local.env``: a checkout's own
     top-level ``env.py`` / ``env`` is never executed or sourced.
  3. **Within each tier**, files are in the contract-A precedence order: each
     store in ``Scope.stores`` -- system, user, project -- with its overlays
     first and itself second, then project-root.
  4. **Chained, later-overrides-earlier**: each file is evaluated against the
     ACCUMULATED environment of every file before it; the result also
     accumulates. Later files win on conflicting keys.
  5. **``.py`` files are EXECUTED** (:func:`get_env_from_py` runs the script and
     reads back its env changes as JSON -- one object, or one object per line
     merged in order, so overlay-managed blocks appended to one ``env.py`` can
     each print their own); plain files are **sourced**
     (:func:`get_env_from_file`, via ``bash ... env -0``).
  6. :func:`get_diff` returns only the vars that differ from the caller's base
     environment; :func:`get_environment` returns the full change set.

The identity/proxy model is wired into the output around the file chain:

  * **Identity** (:func:`dotagents._agents.stamp_identity`) is seeded BEFORE the
    file chain, so env files can branch on ``AGENTS_HARNESS`` and override the
    stamped ``AGENTS_MODEL`` etc. (chained: a later file wins). Never clobbers a
    value already present in the base env -- unless ``explicit`` names the
    agent, in which case the identity is that agent's regardless (the static
    Codex env block is written FOR Codex, from whichever harness runs ``init``).
  * **Roots** are seeded right after identity, also before the chain and also
    only-if-unset: the two scope roots ``AGENTS_HOME`` / ``AGENTS_PROJECT_ROOT``,
    the interpreter ``AGENTS_PYTHON`` (``sys.executable`` -- the Python the CLI
    is running under, a default any shim or helper can trust), and one
    ``<NAME>_OVERLAY_ROOT`` per installed overlay
    (:func:`get_overlay_roots`, named by :attr:`_overlays.Overlay.root_var` --
    the same name ``dotagents context`` expands as a placeholder), so an env
    file can reference an overlay's install dir by name.
  * **Proxy** is normalized AFTER the file chain: ``AGENTS_PROXY`` is seeded if
    unset (from ``AGENTS_WEBFETCH_PROXY_URL`` else the global
    HTTPS/HTTP/ALL_PROXY, either case); any proxy var that *already exists* is
    mirrored into BOTH cases (never creating one that was not set;
    ``http_proxy`` stays lowercase-populated per httpoxy). ``AGENTS_PROXY`` is
    NOT fanned out into the global ``HTTP_PROXY``.

Security (Leakage rule): ``env.py`` runs arbitrary code, but only from files
resolved under the store/overlay/``<project>/.agents`` locations by contract A
-- never from the project root itself. Never log the
resulting ``DOTAGENTS_*``/``AGENTS_*`` secret VALUES -- callers that print the
diff must treat it as sensitive; this module logs var NAMES only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from dotagents._scope import Scope, project_root_default


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
        import subprocess as _sp

        out = _sp.run(
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
    """Lowercase basename of a command/path, ``.exe`` suffix stripped."""
    base = os.path.basename(name.strip()).lower()
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


def _changed_env(env_dump: bytes, base_env: "dict[str, str]") -> "dict[str, str]":
    """Parse a NUL-delimited ``env -0`` dump into the vars that changed vs base.

    Bytes that are not valid UTF-8 are kept via ``surrogateescape`` rather than
    aborting the whole assembly on one odd value (Python's own ``os.environ``
    uses the same trick on POSIX)."""
    sourced: "dict[str, str]" = {}
    for entry in env_dump.split(b"\0"):
        if not entry or b"=" not in entry:
            continue
        key, value = entry.split(b"=", 1)
        name = key.decode("utf-8", "surrogateescape")
        if _is_bash_own_var(name):
            continue
        sourced[name] = value.decode("utf-8", "surrogateescape")
    return {
        k: v for k, v in sourced.items() if k not in base_env or base_env[k] != v
    }


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
    project_root: Path,
    level: str,
    global_scope: bool,
    logger=None,
) -> "dict[str, str]":
    """Execute an ``env.py`` and read back its JSON object(s) of env changes.

    The script runs as a child with ``base_env`` (the accumulated environment)
    and prints its changes to stdout as JSON: ONE object, or one object PER
    LINE, merged in order (a later line wins on a key). The per-line form is
    what makes overlay-managed blocks composable: each overlay's ``setup.py``
    appends its own block to the store's ``env.py`` and each block prints its
    own object. A non-zero exit or unparseable output contributes nothing and
    is logged by NAME only -- never abort assembly, and never echo the child's
    stdout (it may carry secret values).
    """
    args = [interpreter(base_env), str(env_py), "--agent", level]
    if global_scope:
        args.append("--global")
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, check=False, env=_spawn_env(base_env)
        )
    except OSError as e:  # pragma: no cover - interpreter missing
        if logger:
            logger.warning("env.py could not run: %s (%s)", env_py, e)
        return {}
    if proc.returncode != 0:
        if logger:
            logger.warning("env.py failed (exit %s): %s", proc.returncode, env_py)
        return {}
    try:
        parsed = _parse_env_json(proc.stdout)
    except (json.JSONDecodeError, ValueError) as e:
        if logger:
            logger.warning("env.py output not JSON: %s (%s)", env_py, e)
        return {}
    return {k: str(v) for k, v in parsed.items() if isinstance(k, str)}


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


def get_env_from_file(
    env_file: Path, base_env: "dict[str, str]", logger=None
) -> "dict[str, str]":
    """Source a plain env file in bash and return the vars it changed.

    Runs ``set -a; source <file> || exit 1; env -0`` so exported assignments
    are captured. If ``bash`` is unavailable, or the source FAILS (a missing
    file, a directory, a syntax error -- ``|| exit 1`` is what makes that
    visible; a bare ``;`` list would run ``env -0`` regardless and report rc 0),
    the file contributes nothing -- logged by name, never fatal.
    """
    quoted = json.dumps(str(env_file))
    spawn = _spawn_env(base_env)

    # Resolve `bash` against the REAL environment's PATH, not `spawn`'s: that is
    # `base_env`, the chain's ACCUMULATED PATH (contract B step 1 prepends overlay
    # bin dirs onto it), which need not contain bash's install location
    # (`/usr/local/bin` on macOS, Git's `bin` on Windows).
    import shutil

    bash = shutil.which("bash", path=os.environ.get("PATH")) or "bash"
    try:
        proc = subprocess.run(
            [bash, "-c", "set -a; source %s >/dev/null 2>&1 || exit 1; env -0" % quoted],
            capture_output=True,
            text=False,
            check=False,
            env=spawn,
        )
    except OSError as e:
        if logger:
            logger.warning("cannot source env file (no bash?): %s (%s)", env_file, e)
        return {}
    if proc.returncode != 0:
        if logger:
            logger.warning("env file source failed: %s", env_file)
        return {}
    # Compare against the spawn env (bootstrap-backfilled) so the bootstrap vars
    # are not misreported as "changes"; only what the sourced file actually set
    # relative to what the child inherited counts.
    return _changed_env(proc.stdout, spawn)


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
    -- the ``PYTHONPATH`` counterpart of :func:`get_bin_paths`.

    Only dirs that EXIST are returned (unlike ``bin``): ``PYTHONPATH`` is read by every Python the
    session spawns, and a dozen absent entries on it are noise a reader has to
    rule out. project-root's ``lib`` is excluded for the same reason as its
    ``bin``: a project's own top-level ``lib`` is not an agent library.
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
    (``<project>/.agents``) and project-root levels ADDITIONALLY resolve
    ``pre.local.env`` / ``local.env``. Only existing regular FILES are returned
    (a directory named ``env`` -- a common virtualenv name -- is not an env file).

    project-root ``env.py`` / ``env`` are never resolved: the env chain runs at
    every session start, so a checkout's top-level ``env.py`` would be code
    execution from an untrusted repository the moment a session opened in it.
    """
    pre_tier = scope.paths(
        {"default": "pre.env.py", "project-root": ""},
        {"default": "pre.env", "project-root": ""},
        {"project": "pre.local.env", "project-root": "pre.local.env"},
    )
    main_tier = scope.paths(
        {"default": "env.py", "project-root": ""},
        {"default": "env", "project-root": ""},
        {"project": "local.env", "project-root": "local.env"},
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
      the missing case, never introduces a proxy that was not set. ``http_proxy``
      thus stays lowercase-populated when ``HTTP_PROXY`` is set (httpoxy).
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
) -> "dict[str, str]":
    """Assemble the env CHANGES (vars this adds/overrides vs ``base_env``) for a
    :class:`Scope`.

    Follows contract B: identity seeded, PATH bins first, the two tiers
    chained (later overrides earlier), then proxy normalization. Returns only
    what changed.
    """
    from dotagents._agents import stamp_identity

    agents_dir = scope.user_root
    # The project root a user-scope walk pins: the resolved default, so the
    # emitted AGENTS_PROJECT_ROOT still names the project the session is in.
    project_root = scope.project_root or project_root_default()
    global_scope = scope.global_scope

    osenv = dict(base_env if base_env is not None else os.environ)
    env: "dict[str, str]" = {}

    def _apply(changes: "dict[str, str]") -> None:
        env.update(changes)
        osenv.update(changes)

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

    # --- Libs onto PYTHONPATH, same shape, same moment --- so the env.py chain
    # below (and every subprocess inheriting the env) can import an overlay's
    # `lib/`. Existing dirs only (see `get_lib_paths`).
    lib_paths = [str(p) for p in get_lib_paths(scope)]
    if lib_paths:
        updated = _prepended_path_var(osenv, "PYTHONPATH", lib_paths)
        if updated is not None:
            _apply({"PYTHONPATH": updated})

    # --- Contract B steps 2-5: the two tiers, chained, later-overrides-earlier. ---
    for level, path, _root in resolve_env_files(scope):
        if path.suffix == ".py":
            changes = get_env_from_py(
                path, osenv, project_root, level, global_scope, logger=logger
            )
        else:
            changes = get_env_from_file(path, osenv, logger=logger)
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
) -> "dict[str, str]":
    """Only the assembled vars that differ from ``base_env`` (current env).

    ``get_environment`` already returns changes vs ``base_env``, so the diff is
    the subset whose value actually differs from the base.
    """
    base = dict(base_env if base_env is not None else os.environ)
    full = get_environment(
        scope,
        base_env=base,
        explicit=explicit,
        logger=logger,
    )
    return {k: v for k, v in full.items() if k not in base or base[k] != v}
