"""`dotagents env` -- chained env-file assembly + env.py execution.

All assembly logic lives in `_env.py` (contract B) and `_agents.stamp_identity`;
this module formats the result. Never logs env VALUES -- output goes to stdout
for the caller to consume; the logger only ever names vars (Leakage rule).
"""

import re
from pathlib import Path, PureWindowsPath
from typing import Optional

from dotagents.cli._common import DotAgentsArgs, _write_stdout, resolve_user_store

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
    """One path segment, converted to a form MSYS2/Cygwin bash can resolve a
    PATH lookup through.

    Uses ``PureWindowsPath`` explicitly, NOT the platform-dependent bare
    ``Path``: on a POSIX host `Path` is `PosixPath`, which treats `C:` and `\\`
    as literal filename characters. A drive-letter prefix is additionally
    rewritten to its MSYS mount point (`C:/...` -> `/c/...`), since bash does
    not resolve `C:/...` in PATH either. A UNC segment keeps its double-slash
    root through `.as_posix()` (`//server/share/...`). An already-POSIX or
    relative segment (`.agents/bin`, `/etc/agents/bin`) passes through
    unchanged.
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
    (``cmd.exe``/``powershell.exe``) can resolve a PATH lookup through -- the
    inverse of :func:`_to_posix_path`. The caller's inherited ``PATH`` may
    already hold POSIX-style entries (a WSL/MSYS shell), which PowerShell
    would otherwise take as one opaque string.

    Handles both mount conventions: ``/mnt/c/...`` (WSL) and ``/c/...``
    (MSYS2/Git-Bash/Cygwin, what `_to_posix_path` produces) rewrite to
    ``C:\\...``. A segment that already looks Windows-native (a backslash, or a
    drive-letter prefix) passes through ``PureWindowsPath`` with separators
    normalized. A relative segment just gets its separators normalized.

    Returns ``None`` for an absolute POSIX segment with no drive to recover
    (``/usr/bin``): it has no Windows-side meaning, and emitting a relative
    ``\\usr\\bin`` would resolve against the cwd and could shadow an unrelated
    file.
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

    A chunk containing a backslash or a drive-letter prefix is native and
    returns False before any mount-point check: `C:/a/tools` is a legal native
    path, and splitting it on `:` would misread `/a/tools` as an MSYS mount.
    """
    if "\\" in chunk or re.match(r"^[A-Za-z]:", chunk):
        return False
    return any(
        _WSL_MOUNT_RE.match(seg) or _MSYS_MOUNT_RE.match(seg)
        for seg in chunk.split(":")
        if seg
    )


def _to_windows_path_list(value: str) -> str:
    """Convert a PATH value back to an OS-native (Windows) ``;``-joined one,
    for a Windows-target shell-sourceable format (``powershell``, ``cmd``).

    Handles the MIXED case: `get_environment` prepends dotagents' own bin dirs
    (Windows-native, `;`-joined) onto whatever `PATH` the caller held, so a
    POSIX-sourced `PATH` arrives as ``<native;-joined prefix>;<colon-joined
    POSIX tail>`` with both separators live at once.

    Splits on `;` FIRST (a POSIX segment never contains a literal `;`), then
    splits each chunk that looks POSIX (`_looks_like_posix_chunk`) on `:` and
    converts each piece; a native chunk passes through as one segment. Empty
    segments are dropped (see :func:`_to_posix_path_list`), and so are POSIX
    segments with no Windows equivalent (`_to_windows_path` returning
    ``None``).
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
    :func:`_looks_like_path_list`. Checks every `;`-delimited chunk, so a value
    that MIXES native and POSIX chunks (see `_to_windows_path_list`) qualifies.
    """
    if not key.endswith("PATH"):
        return False
    return any(_looks_like_posix_chunk(chunk) for chunk in value.split(";") if chunk)


def _format_env(env: "dict[str, str]", output_format: str) -> str:
    """Render the assembled env in the requested (canonical or aliased) format.

    Shell-sourceable / assignment forms, one var per line:

    * ``export`` (aliases ``posix``/``sh``/``bash``) -- ``export KEY='value'``,
      single-quoted (``'`` -> ``'\\''``, control chars via ``$'...'``) so nothing
      in a value is expanded or executed when sourced; the POSIX default a
      SessionStart hook sources.
    * ``dotenv`` (alias ``env``) -- bare ``KEY=value`` (no ``export``), value
      quoted only when it contains whitespace/``#``/``"``/newline (``.env`` /
      ``docker --env-file`` rules). Distinct from ``export``: assigns, doesn't
      source+export.
    * ``powershell`` (aliases ``pwsh``/``ps``) -- ``$env:KEY = 'value'``,
      single-quoted, ``'`` escaped as ``''``.
    * ``cmd`` (aliases ``bat``/``batch``) -- ``set "KEY=value"``, ``%`` doubled.
      cmd has NO way to escape a literal ``"`` inside a value (emitted as ``""``
      best-effort) or to carry a newline (emitted as a space) -- documented
      limitations.
    * ``fish`` -- ``set -gx KEY value``, single-quoted, ``\\`` and ``'``
      backslash-escaped.

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

    # `get_environment` assembles PATH in the HOST OS's convention (`;` and `\`
    # on Windows), which is what a native subprocess needs. POSIX shell formats
    # need `:` and `/` regardless of host: a `;`-joined, backslash-laden PATH
    # sourced into bash (the SessionStart hook writes this into
    # $CLAUDE_ENV_FILE) breaks every bare-name command lookup for that shell.
    # Convert PATH-shaped values only, for POSIX target formats only.
    if fmt in ("export", "dotenv", "fish"):
        # PATHEXT is Windows-only with no POSIX meaning; dropped.
        #
        # Windows-native var names with parentheses (`ProgramFiles(x86)`,
        # inherited from os.environ) are not legal POSIX identifiers: `export
        # FOO(X86)=...` is a bash SYNTAX ERROR that aborts the rest of the
        # sourced file, so they are dropped too.
        env = {
            k: (_to_posix_path_list(v) if _looks_like_path_list(k, v) else v)
            for k, v in env.items()
            if k != "PATHEXT" and _POSIX_IDENTIFIER_RE.match(k)
        }
    elif fmt in ("powershell", "cmd"):
        # The mirror case: the caller's inherited `PATH` may hold WSL/MSYS-mount
        # entries (`/mnt/c/...`), which PowerShell would take as one opaque
        # string (see `_to_windows_path`). Convert only PATH-shaped values that
        # are already POSIX-style; a native value is never touched.
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
        return "\n".join("%s: %s" % (k, _yaml_value(env[k])) for k in keys)
    if fmt == "dotenv":
        return "\n".join("%s=%s" % (k, _dotenv_value(env[k])) for k in keys)
    if fmt == "powershell":
        # `${env:NAME}` (curly-brace form), not the bare `$env:NAME` sigil:
        # Windows env vars with parens in their names (`ProgramFiles(x86)`)
        # make `$env:FOO(X86) = ...` a parse error, and the curly-brace form
        # accepts any name, so it is used unconditionally.
        return "\n".join(
            "${env:%s} = '%s'" % (k, env[k].replace("'", "''")) for k in keys
        )
    if fmt == "cmd":
        return "\n".join('set "%s=%s"' % (k, _cmd_value(env[k])) for k in keys)
    if fmt == "fish":
        # fish single quotes: only `\` and `'` are escapes, and BOTH must be
        # escaped -- an unescaped `\\` in the value would collapse to `\`.
        return "\n".join(
            "set -gx %s '%s'"
            % (k, env[k].replace("\\", "\\\\").replace("'", "\\'"))
            for k in keys
        )
    # default / "export"
    return "\n".join("export %s=%s" % (k, _sh_quote(env[k])) for k in keys)


def _sh_quote(v: str) -> str:
    """POSIX-shell quoting for a value that will be SOURCED by bash.

    Single quotes, with an embedded ``'`` written as ``'\\''``: nothing inside
    single quotes is ever expanded, so a value containing ``$(...)``, backticks
    or ``$HOME`` is set verbatim instead of being EXECUTED when the SessionStart
    hook's output is sourced from ``$CLAUDE_ENV_FILE``. A value with a newline
    or another control character uses bash's ``$'...'`` form instead, since a
    single-quoted string cannot carry an escape for them. Non-ASCII passes
    through as-is -- the file is written and sourced as UTF-8.
    """
    if any(ord(c) < 0x20 or c == "\x7f" for c in v):
        out = []
        for c in v:
            if c == "\\":
                out.append("\\\\")
            elif c == "'":
                out.append("\\'")
            elif ord(c) < 0x20 or c == "\x7f":
                out.append("\\x%02x" % ord(c))
            else:
                out.append(c)
        return "$'%s'" % "".join(out)
    return "'%s'" % v.replace("'", "'\\''")


def _cmd_value(v: str) -> str:
    """cmd.exe ``set "K=v"`` value. ``%`` is doubled so a ``%4`` or ``%PATH%``
    inside a value survives batch-file expansion; a newline cannot be carried
    by ``set`` at all, so it becomes a space (documented limitation, like the
    ``"`` -> ``""`` best-effort below)."""
    return (
        v.replace("%", "%%")
        .replace('"', '""')
        .replace("\r\n", " ")
        .replace("\n", " ")
        .replace("\r", " ")
    )


#: A YAML plain scalar that a reader would type as something other than a
#: string: booleans (YAML 1.1 spellings included), null, numbers, and the
#: ambiguous forms handled by quoting below.
_YAML_TYPED_RE = re.compile(
    r"^(?:true|false|yes|no|on|off|y|n|null|~|"
    r"[-+]?(?:\d[\d_]*(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|"
    r"0x[0-9a-fA-F_]+|0o[0-7_]+|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$",
    re.IGNORECASE,
)


def _yaml_value(v: str) -> str:
    """Quote a value unless a YAML reader would read it back as exactly this
    string: empty, typed-looking (``true``, ``123``, ``null``), containing a
    YAML indicator, or with surrounding whitespace all get JSON (double-quote)
    quoting, which YAML accepts verbatim."""
    import json as _json

    if (
        v == ""
        or _YAML_TYPED_RE.match(v)
        or any(c in v for c in ":#'\"\n\t")
        or v.strip() != v
        or v[0] in "-?[]{}&*!|>%@`,"
    ):
        return _json.dumps(v)
    return v


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
    ``--agents-dir`` -> ``$AGENTS_HOME`` ->
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

    # Both flags come from `DotAgentsArgs`; only their HELP is restated here
    # (same flags, defaults and types), because this command's `-g` is narrower
    # than the base's and its store is always the user store.
    global_scope: bool = False
    "Skip project-level env files (the store root is unaffected)."
    ("--global", "-g")

    agents_dir: "Optional[Path]" = None
    "User store root override (default: $AGENTS_HOME, else ~/.agents)."
    ("--agents-dir",)

    def __call__(self) -> int:
        import os

        from dotagents import _env, _scope

        # The walk's scope: the user store always, plus the project's tier
        # unless -g (which here means "skip the project tiers", not "another
        # store" -- see the class docstring).
        scope = _scope.Scope.of(
            agents_dir=resolve_user_store(self.agents_dir),
            project_root=_scope.project_root_default(),
            global_scope=self.global_scope,
        )
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
            env = _env.get_diff(scope, base_env=base, logger=self._logger_)
        else:
            changes = _env.get_environment(scope, base_env=base, logger=self._logger_)
            env = dict(base)
            env.update(changes)

        # UTF-8 straight to the buffer: a bare print() encodes with the console
        # codepage and dies on the first non-Latin-1 character in any value.
        _write_stdout(_format_env(env, output_format) + "\n")
        return 0
