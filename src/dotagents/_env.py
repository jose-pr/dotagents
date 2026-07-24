"""Chained env-file assembly + ``env.py`` execution (plan 07).

Ported from the precursor ``agentic`` under **frozen contract B** -- the
observable behavior (what files are found, in what order, and how they are
evaluated) is preserved verbatim; only the code shape is duho/dotagents-native.

Contract B, the exact sequence :func:`get_environment` performs:

  1. **Bins onto PATH FIRST**, before any env eval. Each level's ``bin`` dir
     (contract-A precedence order, *except* project-root) is prepended to
     ``PATH`` so env scripts can call overlay helpers by name.
  2. **Two tiers, in order**: ALL ``pre.env.py`` / ``pre.env`` / ``pre.local.env``
     first, THEN ALL ``env.py`` / ``env`` / ``local.env`` -- the concatenation of
     two contract-A resolutions (:func:`resolve_env_files`).
  3. **Within each tier**, files are in the contract-A precedence order
     (overlays -> system -> user -> project -> project-root).
  4. **Chained, later-overrides-earlier**: each file is evaluated against the
     ACCUMULATED environment of every file before it; the result also
     accumulates. Later files win on conflicting keys.
  5. **``.py`` files are EXECUTED** (:func:`get_env_from_py` runs the script and
     reads back a JSON object of env changes); plain files are **sourced**
     (:func:`get_env_from_file`, via ``bash ... env -0``).
  6. :func:`get_diff` returns only the vars that differ from the caller's base
     environment; :func:`get_environment` returns the full change set.

Plan-08 identity/proxy model is wired into the output around the file chain:

  * **Identity** (:func:`dotagents._agents.stamp_identity`) is seeded BEFORE the
    file chain, so env files can branch on ``AGENTS_HARNESS`` and override the
    stamped ``AGENTS_MODEL`` etc. (chained: a later file wins). Never clobbers a
    value already present in the base env.
  * **Proxy** is normalized AFTER the file chain: ``AGENTS_PROXY`` is seeded if
    unset (from ``AGENTS_WEBFETCH_PROXY_URL`` else the global
    HTTPS/HTTP/ALL_PROXY, either case); any proxy var that *already exists* is
    mirrored into BOTH cases (never creating one that was not set;
    ``http_proxy`` stays lowercase-populated per httpoxy). ``AGENTS_PROXY`` is
    NOT fanned out into the global ``HTTP_PROXY``.

Security (Leakage rule): ``env.py`` runs arbitrary code, but only from files
resolved under the store/overlay/project locations by contract A. Never log the
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

from dotagents._resolve import get_file_paths


# OS-bootstrap vars a spawned interpreter needs to even start (Windows
# CreateProcess wants SystemRoot; POSIX loaders want the temp dir). These are
# backfilled from the real environment ONLY when the accumulated env lacks them,
# so a child env.py always launches -- without leaking user config the chain did
# not itself set. Not part of contract B (which is about what the chain
# resolves/evaluates); purely making subprocess spawn portable.
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
# Output-format aliases + calling-shell detection (plan 07, D83).
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
# File evaluators (ported from the precursor helpers.py -- keep the protocols).
# --------------------------------------------------------------------------- #


def _changed_env(env_dump: bytes, base_env: "dict[str, str]") -> "dict[str, str]":
    """Parse a NUL-delimited ``env -0`` dump into the vars that changed vs base."""
    sourced: "dict[str, str]" = {}
    for entry in env_dump.split(b"\0"):
        if not entry or b"=" not in entry:
            continue
        key, value = entry.split(b"=", 1)
        if key == b"_":
            continue
        sourced[key.decode()] = value.decode()
    return {
        k: v for k, v in sourced.items() if k not in base_env or base_env[k] != v
    }


def get_env_from_py(
    env_py: Path,
    base_env: "dict[str, str]",
    project_root: Path,
    level: str,
    global_scope: bool,
    logger=None,
) -> "dict[str, str]":
    """Execute an ``env.py`` and read back its JSON object of env changes.

    The script runs as a child with ``base_env`` (the accumulated environment)
    and must print a JSON dict to stdout. A non-zero exit or unparseable output
    contributes nothing and is logged by NAME only -- never abort assembly, and
    never echo the child's stdout (it may carry secret values).
    """
    args = [sys.executable, str(env_py), "--agent", level]
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
        parsed = json.loads(proc.stdout)
        if not isinstance(parsed, dict):
            raise ValueError("env.py must output a JSON object")
    except (json.JSONDecodeError, ValueError) as e:
        if logger:
            logger.warning("env.py output not JSON: %s (%s)", env_py, e)
        return {}
    return {k: str(v) for k, v in parsed.items() if isinstance(k, str)}


def get_env_from_file(
    env_file: Path, base_env: "dict[str, str]", logger=None
) -> "dict[str, str]":
    """Source a plain env file in bash and return the vars it changed.

    Runs ``set -a; source <file>; env -0`` so exported assignments are captured.
    If ``bash`` is unavailable (or the source errors) the file contributes
    nothing -- logged by name, never fatal.
    """
    quoted = json.dumps(str(env_file))
    spawn = _spawn_env(base_env)
    try:
        proc = subprocess.run(
            ["bash", "-c", "set -a; source %s >/dev/null 2>&1; env -0" % quoted],
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


def get_bin_paths(
    *, agents_dir: Path, project_root: Path, global_scope: bool = False
) -> "list[Path]":
    """Each level's ``bin`` dir in contract-A precedence order, EXCEPT project-root.

    Uses ``include_missing=True`` (precursor semantics) so a bin dir is offered
    for every level even if absent -- the caller prepends only real dirs where it
    matters. project-root's ``bin`` is explicitly excluded (``{"project-root":
    None}``): a project's own top-level ``bin`` is not an agent bin.
    """
    resolved = get_file_paths(
        {"default": "bin", "project-root": ""},
        agents_dir=agents_dir,
        project_root=project_root,
        global_scope=global_scope,
        include_missing=True,
    )
    return [path for _level, path, _root in resolved]


def _prepend_missing(path_entries: "list[str]", new_entries: "list[str]") -> "list[str]":
    """Prepend each new entry not already present, preserving precursor order.

    The precursor inserts each new entry at position 0 in iteration order, so a
    later new entry ends up EARLIER. Reproduced exactly.
    """
    for entry in new_entries:
        if entry not in path_entries:
            path_entries.insert(0, entry)
    return path_entries


# --------------------------------------------------------------------------- #
# Env-file resolution (contract B steps 2 + 3).
# --------------------------------------------------------------------------- #


def resolve_env_files(
    *, agents_dir: Path, project_root: Path, global_scope: bool = False
) -> "list[tuple[str, Path, Optional[Path]]]":
    """The ordered, existing env files: ALL pre-tier then ALL main-tier.

    Each tier is one contract-A resolution (:func:`get_file_paths`). Per-level
    filename resolution (contract A point 2): ``pre.env.py``/``pre.env`` and
    ``env.py``/``env`` resolve everywhere; the project + project-root levels use
    ``pre.local.env`` / ``local.env`` instead. Only existing files are returned.
    """
    common = dict(
        agents_dir=agents_dir, project_root=project_root, global_scope=global_scope
    )
    pre_tier = get_file_paths(
        "pre.env.py",
        "pre.env",
        {"project": "pre.local.env", "project-root": "pre.local.env"},
        **common,
    )
    main_tier = get_file_paths(
        "env.py",
        "env",
        {"project": "local.env", "project-root": "local.env"},
        **common,
    )
    return pre_tier + main_tier


# --------------------------------------------------------------------------- #
# Proxy normalization (plan 08).
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
    """Return the proxy changes for ``osenv`` per the plan-08 proxy model.

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
    *,
    agents_dir: Path,
    project_root: Path,
    base_env: "Optional[dict[str, str]]" = None,
    global_scope: bool = False,
    explicit: "Optional[str]" = None,
    logger=None,
) -> "dict[str, str]":
    """Assemble the env CHANGES (vars this adds/overrides vs ``base_env``).

    Follows frozen contract B: identity seeded, PATH bins first, the two tiers
    chained (later overrides earlier), then proxy normalization. Returns only
    what changed -- mirrors the precursor's ``env={}`` accumulator.
    """
    from dotagents._agents import stamp_identity

    osenv = dict(base_env if base_env is not None else os.environ)
    env: "dict[str, str]" = {}

    def _apply(changes: "dict[str, str]") -> None:
        env.update(changes)
        osenv.update(changes)

    # --- Identity seed (plan 08) --- before the file chain so files can override.
    _apply(stamp_identity(osenv, explicit=explicit, root=project_root))

    # --- Scope roots --- pin the two scope roots so every command/subprocess agrees:
    # AGENTS_HOME = the user store (agents_dir, ~/.agents by default); AGENTS_PROJECT_ROOT
    # = this project's root (resolve_scope reads it). Respect any already set upstream.
    if not osenv.get("AGENTS_HOME"):
        _apply({"AGENTS_HOME": str(agents_dir)})
    if not osenv.get("AGENTS_PROJECT_ROOT"):
        _apply({"AGENTS_PROJECT_ROOT": str(project_root)})

    # --- Contract B step 1: bins onto PATH FIRST. ---
    bin_paths = [str(p) for p in get_bin_paths(
        agents_dir=agents_dir, project_root=project_root, global_scope=global_scope
    )]
    current = osenv.get("PATH", "").split(os.pathsep) if osenv.get("PATH") else []
    updated = os.pathsep.join(_prepend_missing(current, bin_paths))
    if updated != osenv.get("PATH", ""):
        _apply({"PATH": updated})

    # --- Contract B steps 2-5: the two tiers, chained, later-overrides-earlier. ---
    for level, path, _root in resolve_env_files(
        agents_dir=agents_dir, project_root=project_root, global_scope=global_scope
    ):
        if path.suffix == ".py":
            changes = get_env_from_py(
                path, osenv, project_root, level, global_scope, logger=logger
            )
        else:
            changes = get_env_from_file(path, osenv, logger=logger)
        _apply(changes)

    # --- Proxy normalization (plan 08) --- after the chain so file-set proxies
    #     are normalized too.
    _apply(apply_proxy_model(osenv))

    return env


def get_diff(
    *,
    agents_dir: Path,
    project_root: Path,
    base_env: "Optional[dict[str, str]]" = None,
    global_scope: bool = False,
    explicit: "Optional[str]" = None,
    logger=None,
) -> "dict[str, str]":
    """Only the assembled vars that differ from ``base_env`` (current env).

    ``get_environment`` already returns changes vs ``base_env``, so the diff is
    the subset whose value actually differs from the base -- identical to the
    precursor's ``get_diff`` over ``os.environ``.
    """
    base = dict(base_env if base_env is not None else os.environ)
    full = get_environment(
        agents_dir=agents_dir,
        project_root=project_root,
        base_env=base,
        global_scope=global_scope,
        explicit=explicit,
        logger=logger,
    )
    return {k: v for k, v in full.items() if k not in base or base[k] != v}
