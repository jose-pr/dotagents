"""`dotagents env` -- chained env-file assembly + env.py execution (plan 07).

Self-contained block: all logic lives in `_env.py` (frozen contract B) and
`_agents.stamp_identity` (plan 08). The only umbrella touch is registering
`Env` on `Dotagents._subcommands_` (in `cli/__init__.py`). Never logs
DOTAGENTS_*/AGENTS_* VALUES -- output goes to stdout for the caller to consume;
the logger only ever names vars (Leakage rule).
"""

import re
from pathlib import Path, PureWindowsPath
from typing import Optional

from dotagents.cli._common import DotAgentsArgs, resolve_user_store

# POSIX shell variable names: a leading letter/underscore, then letters/digits/
# underscores only (IEEE Std 1003.1 "Name"). Windows env vars like
# `ProgramFiles(x86)` don't qualify -- `(`/`)` make `export NAME=...` a syntax
# error in bash, not just an unset/misinterpreted var.
_POSIX_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _dotenv_value(v: str) -> str:
    """Dotenv quoting: bare unless the value needs quoting.

    A value with whitespace, ``#``, ``"`` or a newline is wrapped in double
    quotes with ``"``, ``\\`` and newline backslash-escaped; otherwise emitted
    bare (the ``.env`` / ``docker --env-file`` convention).
    """
    if v == "":
        return ""
    if any(c in v for c in " \t#\"\n") or v.strip() != v:
        esc = v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return '"%s"' % esc
    return v


def _looks_like_path_list(key: str, value: str) -> bool:
    """True if `key`/`value` is a PATH-shaped var that needs POSIX conversion
    for a shell-sourceable format.

    Matches `PATH` exactly and any `*_PATH`/`*PATH` suffix an overlay's env.py
    might emit (e.g. `PYTHONPATH`, `AGENTS_LIB_PATH`) -- the same convention
    `get_bin_paths`/contract B use for path-list vars. Requires the value to
    actually look like an OS-native path list (contains a backslash, or the
    Windows `;` separator) so an ordinary single POSIX path or an unrelated
    string is never touched.
    """
    if not key.endswith("PATH"):
        return False
    return "\\" in value or ";" in value


_DRIVE_LETTER_RE = re.compile(r"^([A-Za-z]):/")


def _to_posix_path(segment: str) -> str:
    """One path segment, converted to a form MSYS2/Cygwin bash can actually
    resolve a PATH lookup through -- not just cosmetically POSIX-shaped.

    Uses ``PureWindowsPath`` explicitly, NOT the platform-dependent bare
    ``Path``. This distinction is load-bearing, not stylistic: `Path` resolves
    to `PosixPath` on a POSIX host, and `PosixPath("C:\\Users\\x").as_posix()`
    does NOT recognize `C:` as a drive or `\\` as a separator -- backslashes
    pass through as literal filename characters. This code runs in CI on
    Linux/macOS runners (the test suite exercises it there), so a bare `Path`
    silently only worked when the CI happened to run on a Windows runner --
    which for THIS job it does not. `PureWindowsPath` parses Windows syntax
    unconditionally, everywhere, matching what the value actually is (a
    Windows path string), independent of the host running the code.

    `PureWindowsPath(...).as_posix()` alone still leaves a drive letter as
    `C:/...`, which fails PATH lookup identically to the backslash form --
    verified directly on Windows (`command -v grep` empty for `C:/Program
    Files/Git/usr/bin`, populated for `/c/Program Files/Git/usr/bin`, same
    directory). So a genuine drive-letter prefix is additionally rewritten to
    its MSYS mount point (`C:/...` -> `/c/...`).

    A UNC segment (`\\\\server\\share\\...`) is handled correctly by
    `PureWindowsPath` natively -- it already preserves the double-slash UNC
    root through `.as_posix()` (`//server/share/...`), unlike a naive
    backslash-replace on a plain string, which collapses it to one slash.

    An already-POSIX or relative segment (`.agents/bin`, `/etc/agents/bin`) is
    parsed as a relative Windows path (backslashes are still separators there,
    but there are none to convert) and passes through unchanged.
    """
    posix = PureWindowsPath(segment).as_posix()
    m = _DRIVE_LETTER_RE.match(posix)
    if m:
        return "/%s%s" % (m.group(1).lower(), posix[2:])
    return posix


def _to_posix_path_list(value: str) -> str:
    """Convert an OS-native (Windows) `;`-joined path list to a POSIX
    `:`-joined one, for a shell-sourceable target format.

    Empty segments are dropped (a stray leading/trailing `;` must not become a
    bare `:` -- POSIX treats an empty PATH segment as `.`, the cwd, which is
    both wrong and a real security footgun).
    """
    segments = [_to_posix_path(s) for s in value.split(";") if s]
    return ":".join(segments)


_WSL_MOUNT_RE = re.compile(r"^/mnt/([A-Za-z])(/.*)?$")
_MSYS_MOUNT_RE = re.compile(r"^/([A-Za-z])(/.*)?$")


def _to_windows_path(segment: str) -> "str | None":
    """One path segment, converted to a form a native Windows process
    (``cmd.exe``/``powershell.exe``) can actually resolve a PATH lookup
    through -- the inverse of :func:`_to_posix_path`.

    Real machine bug this fixes: ``dotagents env`` was invoked from a genuine
    Windows PowerShell terminal, but the process's own inherited ``PATH``
    already contained POSIX/WSL-mount-style entries (``/mnt/c/Program
    Files/...``) -- not something dotagents itself emits (its own bin dirs
    resolve via native ``Path`` and are already Windows-native), but a
    pre-existing condition of the caller's live environment. Emitted
    unconverted into ``${env:PATH} = '...'``, the assignment is syntactically
    VALID PowerShell (a single-quoted string can contain anything), but the
    resulting ``PATH`` value is colon-joined POSIX paths with embedded spaces
    -- ``powershell.exe`` splits it on nothing (a single opaque string) and
    every subsequent bare-command PATH lookup in that session breaks, the
    Windows-side mirror of the POSIX PATH bug ``_to_posix_path`` already
    guards against.

    Handles the two mount conventions actually seen in practice:
    ``/mnt/c/...`` (WSL's own mount point) and ``/c/...`` (MSYS2/Git-Bash/
    Cygwin's mount point, i.e. exactly what `_to_posix_path` produces) --
    both rewrite to ``C:\\...``. A segment that already looks Windows-native
    (contains a backslash, or a drive-letter-colon prefix) passes through via
    ``PureWindowsPath`` unchanged (backslashes normalized, forward slashes
    converted) rather than being misparsed as a POSIX path.

    Returns ``None`` for a segment with NO drive to recover (e.g. WSL-only
    paths like ``/usr/bin`` or ``/home/x/.local/bin`` -- genuinely part of the
    Linux filesystem inside WSL, not reachable from Windows at all under any
    rewrite). Silently turning one of these into a relative ``\\usr\\bin``
    would be worse than useless: it wouldn't error, but it would resolve
    against Windows' cwd and could shadow an unrelated file there. Dropping
    it is correct -- that directory has no Windows-side meaning, so a lookup
    through it was never going to succeed either way.
    """
    if "\\" in segment or re.match(r"^[A-Za-z]:", segment):
        return str(PureWindowsPath(segment))
    m = _WSL_MOUNT_RE.match(segment) or _MSYS_MOUNT_RE.match(segment)
    if m:
        drive = m.group(1).upper()
        rest = (m.group(2) or "/").replace("/", "\\")
        return "%s:%s" % (drive, rest)
    if segment.startswith("/"):
        return None  # a real POSIX-only path with no Windows equivalent
    # A relative segment (e.g. ".agents/bin") -- just normalize separators.
    return str(PureWindowsPath(segment.replace("/", "\\")))


def _looks_like_posix_chunk(chunk: str) -> bool:
    """True if a single `;`-delimited chunk (see `_to_windows_path_list`) is
    itself a POSIX mount-point segment or a `:`-joined list of them -- i.e.
    something that needs further `:`-splitting, not a single already-native
    Windows path that merely happens to sit in the same PATH value.

    A chunk containing a backslash is unambiguous: nothing POSIX ever has
    one, so it is native and this returns False outright, before even
    checking for mount points -- guards a pathological case like a Windows
    path containing a literal `:` beyond its drive prefix (not realistic in
    practice, but the check order makes the function correct regardless).
    """
    if "\\" in chunk:
        return False
    return any(
        _WSL_MOUNT_RE.match(seg) or _MSYS_MOUNT_RE.match(seg)
        for seg in chunk.split(":")
        if seg
    )


def _to_windows_path_list(value: str) -> str:
    """Convert a PATH value back to an OS-native (Windows) ``;``-joined one,
    for a Windows-target shell-sourceable format (``powershell``, ``cmd``).

    Handles the MIXED case, not just a purely POSIX value: `get_environment`
    prepends dotagents' own bin dirs (already Windows-native, `;`-joined)
    onto whatever `PATH` the caller's environment already held -- so a
    genuinely POSIX-sourced `PATH` (WSL/MSYS pwsh inheriting a Linux-side
    `PATH`) arrives here as ``<native-bin-dirs-joined-with-;>;<original-
    colon-joined-POSIX-path>``, one string with BOTH separators live at once.
    Confirmed live: an earlier version of this function split on `;` only
    implicitly (via a value-wide backslash/`;` presence check bailing out
    entirely), so the native PREFIX converted correctly but the still-POSIX
    TAIL after it passed through completely untouched.

    Splits on `;` FIRST (safe: a POSIX path segment never legitimately
    contains a literal `;`), then for each chunk that looks POSIX
    (`_looks_like_posix_chunk`) splits it again on `:` and converts each
    piece; a chunk that is already native passes through as one segment
    unchanged. Empty segments are dropped, same rationale as
    :func:`_to_posix_path_list`; POSIX segments with no Windows equivalent
    (`_to_windows_path` returning ``None`` -- WSL-only paths like
    ``/usr/bin``) are dropped too, rather than emitted as a broken relative
    path.
    """
    segments: "list[str]" = []
    for chunk in value.split(";"):
        if not chunk:
            continue
        if _looks_like_posix_chunk(chunk):
            for piece in chunk.split(":"):
                if not piece:
                    continue
                converted = _to_windows_path(piece)
                if converted is not None:
                    segments.append(converted)
        else:
            segments.append(chunk)
    return ";".join(segments)


def _looks_like_posix_path_list(key: str, value: str) -> bool:
    """True if `key`/`value` is a PATH-shaped var containing at least one
    ALREADY-POSIX chunk that needs converting back to native Windows form for
    a Windows-target format (``powershell``/``cmd``) -- the inverse gate of
    :func:`_looks_like_path_list`. Handles a value that MIXES native and
    POSIX chunks (see `_to_windows_path_list`'s docstring for why that shape
    occurs), not just a purely POSIX one -- checks every `;`-delimited chunk
    rather than bailing out on the value as a whole the moment any `;` or
    `\\` appears anywhere in it.
    """
    if not key.endswith("PATH"):
        return False
    return any(_looks_like_posix_chunk(chunk) for chunk in value.split(";") if chunk)


def _format_env(env: "dict[str, str]", output_format: str) -> str:
    """Render the assembled env in the requested (canonical or aliased) format.

    Shell-sourceable / assignment forms, one var per line:

    * ``export`` (aliases ``posix``/``sh``/``bash``) -- ``export KEY="value"``,
      value JSON-quoted; the POSIX default a SessionStart hook sources.
    * ``dotenv`` (alias ``env``) -- bare ``KEY=value`` (no ``export``), value
      quoted only when it contains whitespace/``#``/``"``/newline (``.env`` /
      ``docker --env-file`` rules). Distinct from ``export``: assigns, doesn't
      source+export.
    * ``powershell`` (aliases ``pwsh``/``ps``) -- ``$env:KEY = 'value'``,
      single-quoted, ``'`` escaped as ``''``.
    * ``cmd`` (aliases ``bat``/``batch``) -- ``set "KEY=value"``. cmd has NO way
      to escape a literal ``"`` inside a value; any ``"`` is emitted as ``""``
      best-effort (documented limitation).
    * ``fish`` -- ``set -gx KEY value``, single-quoted, ``'`` escaped as ``\\'``.

    Data forms:

    * ``json`` -- a sorted JSON object.
    * ``ini``  -- ``KEY=value`` lines under a ``[env]`` section.
    * ``yaml`` -- ``KEY: value`` lines (values quoted when ambiguous).

    Aliases are normalized here via :data:`dotagents._env.FORMAT_ALIASES`. Values
    are emitted verbatim (this is the point of the command); callers must treat
    the output as sensitive.
    """
    import json as _json

    from dotagents._env import FORMAT_ALIASES

    fmt = FORMAT_ALIASES.get(output_format, output_format)

    # `get_environment` assembles PATH using the HOST OS's own convention
    # (os.pathsep + native Path separators -- `;` and `\` on Windows), because
    # that is what a Windows subprocess (cmd/PowerShell, or `dotagents` itself
    # spawning a child) needs. Shell-sourceable POSIX formats need the opposite:
    # bash/fish always use `:` and `/`, regardless of host OS. Left unconverted
    # on Windows, `export PATH="C:\...;C:\..."` sourced into a POSIX shell (this
    # is exactly what the SessionStart hook writes into $CLAUDE_ENV_FILE) hands
    # bash a PATH it cannot parse -- every `;`-joined, backslash-laden segment
    # becomes one broken entry, and EVERY bare-name command lookup breaks for
    # the rest of that session. This is not cosmetic: it can take down `git`,
    # `grep`, `python` -- anything resolved via PATH -- for the shell that
    # sources it. Convert PATH-shaped values only, for POSIX target formats
    # only; every other var (and every other format) is untouched.
    if fmt in ("export", "dotenv", "fish"):
        # PATHEXT is a Windows-only concept (extensionless exec resolution) with
        # no POSIX meaning; dropped rather than emitted as noise.
        #
        # A handful of Windows-native var names (`ProgramFiles(x86)`,
        # `CommonProgramFiles(Arm)`, inherited from os.environ into base_env)
        # contain parentheses -- not a legal POSIX shell identifier. `export
        # FOO(X86)=...` is a hard bash SYNTAX ERROR, not a bad value: sourcing
        # it aborts the rest of the file, so every var after the first offender
        # in iteration order never gets set either. Worse than the PATH bug,
        # same root cause (unfiltered OS-native env reaching a POSIX target).
        env = {
            k: (_to_posix_path_list(v) if _looks_like_path_list(k, v) else v)
            for k, v in env.items()
            if k != "PATHEXT" and _POSIX_IDENTIFIER_RE.match(k)
        }
    elif fmt in ("powershell", "cmd"):
        # The MIRROR bug, going the other way: confirmed live on a real
        # machine running `dotagents env` from a genuine Windows PowerShell
        # terminal, where the process's own inherited `PATH` already
        # contained WSL/MSYS-mount-style entries (`/mnt/c/Program Files/...`)
        # -- not something dotagents' own bin-path logic emits, a pre-existing
        # condition of the caller's live environment (see `_to_windows_path`'s
        # docstring). Left unconverted, `${env:PATH} = '/usr/bin:/mnt/c/...'`
        # is syntactically valid PowerShell (a quoted string can hold
        # anything) but semantically useless: powershell.exe's own PATH
        # lookup expects `;`-joined, backslash-native segments, so the whole
        # value becomes one opaque unusable string and every bare-command
        # lookup breaks for that session -- the Windows-target mirror of the
        # POSIX-target PATH bug already guarded above. Convert only
        # PATH-shaped values that are ALREADY POSIX-style
        # (`_looks_like_posix_path_list`); an ordinary already-native value is
        # never touched.
        env = {
            k: (_to_windows_path_list(v) if _looks_like_posix_path_list(k, v) else v)
            for k, v in env.items()
        }

    keys = sorted(env)

    if fmt == "json":
        return _json.dumps({k: env[k] for k in keys}, indent=2, sort_keys=True)
    if fmt == "ini":
        return "\n".join(["[env]"] + ["%s=%s" % (k, env[k]) for k in keys])
    if fmt == "yaml":
        lines = []
        for k in keys:
            v = env[k]
            if v == "" or any(c in v for c in ":#'\"\n") or v.strip() != v:
                v = _json.dumps(v)
            lines.append("%s: %s" % (k, v))
        return "\n".join(lines)
    if fmt == "dotenv":
        return "\n".join("%s=%s" % (k, _dotenv_value(env[k])) for k in keys)
    if fmt == "powershell":
        # `${env:NAME}` (curly-brace form), not the bare `$env:NAME` sigil form.
        # A handful of real Windows env vars have parens in their names
        # (`ProgramFiles(x86)`, `CommonProgramFiles(Arm)`, inherited via
        # os.environ) -- `$env:FOO(X86) = ...` is a PowerShell parse error
        # ("Unexpected token '('"), the same class of bug D90 fixed for the
        # export/dotenv/fish formats. The curly-brace form accepts ANY
        # character in the name and is valid for every var, not just the
        # special-cased ones, so it is used unconditionally rather than only
        # for names that need it.
        return "\n".join(
            "${env:%s} = '%s'" % (k, env[k].replace("'", "''")) for k in keys
        )
    if fmt == "cmd":
        return "\n".join(
            'set "%s=%s"' % (k, env[k].replace('"', '""')) for k in keys
        )
    if fmt == "fish":
        return "\n".join(
            "set -gx %s '%s'" % (k, env[k].replace("'", "\\'")) for k in keys
        )
    # default / "export"
    return "\n".join("export %s=%s" % (k, _json.dumps(env[k])) for k in keys)


class Env(DotAgentsArgs):
    """Assemble the chained env (env files + env.py execution) under contract B.

    Prepends overlay/level ``bin`` dirs to ``PATH`` first, then evaluates the
    ``pre.*`` tier and the main tier in precedence order (overlays -> user ->
    project -> project-root), chaining each file over the accumulated env so
    later files win. ``.py`` files are EXECUTED and emit JSON env changes; plain
    files are sourced. Standardized ``AGENTS_*``/``AGENT`` identity vars and the
    ``AGENTS_PROXY`` model are wired in.

    ``--diff`` emits only the vars that differ from the current environment (what
    a SessionStart hook injects); the default emits the full assembled env merged
    over the current one. Output is sensitive -- it may carry secret values.

    ``--format`` selects the emitted syntax and defaults to ``auto``, which
    detects the CALLING shell (parent-process chain) and picks a matching format
    so the output is sourceable where it runs: ``export`` (aliases
    ``posix``/``sh``/``bash``), ``dotenv`` (``env``), ``powershell``
    (``pwsh``/``ps``), ``cmd`` (``bat``/``batch``), ``fish``, plus the data
    forms ``json``/``ini``/``yaml``. An explicit ``--format`` always wins.

    Roots (both configurable, never hardcoded -- D58/D79/D80): the user store is
    ``--agents-dir`` -> ``$AGENTS_HOME`` -> legacy ``$DOTAGENTS_AGENTS_DIR`` ->
    ``~/.agents`` (:func:`~dotagents.cli._common.resolve_user_store`), and the
    project root is ``$AGENTS_PROJECT_ROOT`` -> ``$CLAUDE_PROJECT_DIR`` -> the cwd
    (:func:`~dotagents._scope.project_root_default`). A harness that pins those
    vars gets ONE root per session regardless of the cwd a subprocess runs in --
    which is the point of ``env`` emitting them in the first place.

    ``-g/--global`` here means **skip the project-level env files**, NOT "resolve a
    different store": the walk always starts from the USER store and ``-g`` only
    drops the project tiers. (`DotAgentsArgs.resolve_scope` -- which would return
    ``<project>/.agents`` as the root -- is deliberately NOT used; this command
    inherits the class only for the shared ``-g``/``--agents-dir`` flag pair.)"""

    _parsername_ = "env"

    format: str = "auto"
    (
        "Output format. Default 'auto' detects the calling shell. Shell forms: "
        "export (aliases posix/sh/bash), dotenv (env), powershell (pwsh/ps), "
        "cmd (bat/batch), fish. Data forms: json, ini, yaml."
    )
    ("--format",)

    diff: bool = False
    "Emit only vars that differ from the current environment."
    ("--diff",)

    # Both flags come from `DotAgentsArgs`; only their HELP is restated here,
    # because this command's `-g` means something narrower than the base's (skip
    # the project tiers, same store) and its store is always the user store. The
    # flags, defaults and types are identical to the base's -- duho takes the
    # subclass's declaration, so the strings below are what `--help` prints.
    global_scope: bool = False
    "Skip project-level env files (the store root is unaffected)."
    ("--global", "-g")

    agents_dir: "Optional[Path]" = None
    "User store root override (default: $AGENTS_HOME, else ~/.agents)."
    ("--agents-dir",)

    def __call__(self) -> int:
        import os

        from dotagents import _env, _scope

        project_root = _scope.project_root_default()
        agents_dir = resolve_user_store(self.agents_dir)
        base = dict(os.environ)

        if self.format not in _env.KNOWN_FORMATS:
            raise SystemExit(
                "error: --format must be one of %s (got %r)"
                % (", ".join(_env.KNOWN_FORMATS), self.format)
            )
        # `auto` (the default) resolves to the calling shell's format; an explicit
        # --format always wins. Detection reads process names only (never values).
        output_format = self.format
        if output_format == "auto":
            output_format = _env.detect_shell_format()

        if self.diff:
            env = _env.get_diff(
                agents_dir=agents_dir, project_root=project_root,
                base_env=base, global_scope=self.global_scope, logger=self._logger_,
            )
        else:
            changes = _env.get_environment(
                agents_dir=agents_dir, project_root=project_root,
                base_env=base, global_scope=self.global_scope, logger=self._logger_,
            )
            env = dict(base)
            env.update(changes)

        print(_format_env(env, output_format))
        return 0
