"""`dotagents env` -- chained env-file assembly + env.py execution.

All assembly logic lives in `_env.py` (contract B) and `_agents.stamp_identity`;
this module formats the result. Never logs env VALUES -- output goes to stdout
for the caller to consume; the logger only ever names vars (Leakage rule).
"""

import re
from pathlib import Path, PureWindowsPath
from typing import Iterable, Optional

from dotagents.cli._common import DotAgentsArgs, _write_stdout, resolve_user_store

# POSIX shell variable names: a leading letter/underscore, then letters/digits/
# underscores only (IEEE Std 1003.1 "Name"). Windows env vars like
# `ProgramFiles(x86)` don't qualify -- `(`/`)` make `export NAME=...` a syntax
# error in bash, not just an unset/misinterpreted var.
_POSIX_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _dotenv_value(v: str) -> str:
    """Dotenv quoting for ONE target reader, python-dotenv (``dotenv_values``
    / ``load_dotenv``): bare unless the value needs quoting.

    * A value with ``$``, a CR, or a quote character at either end is
      single-quoted, with ``\\`` and ``'`` backslash-escaped (python-dotenv
      decodes exactly those two in single quotes, and a bare or double-quoted
      value would lose the surrounding quotes or the CR).
    * Else a value with whitespace, ``#``, ``"`` or a newline is
      double-quoted, with ``"``, ``\\`` and newline backslash-escaped.
    * Else it is emitted bare.

    Limitation: python-dotenv's default ``interpolate=True`` expands
    ``${NAME}`` in EVERY quoting style, single quotes included, and has no
    escape for it; a value containing ``${`` reads back verbatim only with
    ``interpolate=False``. ``docker --env-file`` is a different format (no
    quote handling at all) and is not a target.
    """
    if v == "":
        return ""
    if "$" in v or "\r" in v or v[0] in "'\"" or v[-1] in "'\"":
        return "'%s'" % v.replace("\\", "\\\\").replace("'", "\\'")
    if any(c in v for c in " \t#\"\n") or v.strip() != v:
        esc = v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return '"%s"' % esc
    return v


def _ini_value(v: str) -> "Optional[str]":
    """The INI value for ONE target reader, Python's ``configparser`` (its
    default ``BasicInterpolation``), or ``None`` when that reader cannot hold
    the value verbatim.

    ``%`` is doubled (a bare ``%`` raised ``InterpolationSyntaxError``); a
    newline becomes a continuation line (the next line indented by a tab).
    configparser strips surrounding whitespace, drops a CR, strips each
    continuation line's leading whitespace and treats a continuation line
    that starts with ``#`` or ``;`` as a comment -- a value that runs into
    any of those has no INI spelling. (configparser also lowercases keys by
    default; a reader that needs them as emitted sets ``optionxform = str``.)
    """
    if "\r" in v or v.strip() != v:
        return None
    lines = v.replace("%", "%%").split("\n")
    for line in lines[1:]:
        if line[:1].isspace() or line.startswith(("#", ";")):
            return None
    return "\n\t".join(lines)


def _ini_key_ok(k: str) -> bool:
    """A key configparser reads back as itself (modulo its lowercasing): no
    ``=`` / ``:`` delimiter inside, no leading comment or section character,
    no surrounding whitespace."""
    return bool(k) and k.strip() == k and not any(c in k for c in "=:") and k[0] not in "#;["


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


def _host_is_windows() -> bool:
    """True on a native Windows host -- the only one whose PATH is in Windows
    form and so the only one where the formats convert PATH-shaped values."""
    import os

    return os.name == "nt"


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


def _format_env(
    env: "dict[str, str]",
    output_format: str,
    removed: "Optional[Iterable[str]]" = None,
) -> str:
    """Render the assembled env in the requested (canonical or aliased) format.

    ``removed`` names vars to UNSET (default: ``env.removed`` when ``env`` is
    an :class:`dotagents._env.EnvChanges`). They are rendered after the
    assignments: ``unset K`` (export), ``set -e K`` (fish),
    ``${env:K} = $null`` (powershell), ``set "K="`` (cmd), ``null`` (json,
    yaml). ``dotenv`` and ``ini`` have no way to say "unset", so a removal is
    left out of them.

    Shell-sourceable / assignment forms, one var per line:

    * ``export`` (aliases ``posix``/``sh``/``bash``) -- ``export KEY='value'``,
      POSIX single-quoted (``'`` -> ``'\\''``, control chars literal) so nothing
      in a value is expanded or executed when sourced; the POSIX default a
      SessionStart hook sources.
    * ``dotenv`` (alias ``env``) -- bare ``KEY=value`` (no ``export``), value
      quoted only when it needs it, for python-dotenv (see
      :func:`_dotenv_value`). Distinct from ``export``: assigns, doesn't
      source+export.
    * ``powershell`` (aliases ``pwsh``/``ps``) -- ``$env:KEY = 'value'``,
      single-quoted, ``'`` escaped as ``''``.
    * ``cmd`` (aliases ``bat``/``batch``) -- ``set "KEY=value"``, ``%`` doubled.
      cmd has NO way to escape a literal ``"`` inside a value (emitted as ``""``
      best-effort) or to carry a newline (emitted as a space) -- documented
      limitations. Consume it as a batch file (``> f.cmd && call f.cmd``),
      the only way the ``%%`` collapses back; see :func:`_cmd_value`.
    * ``fish`` -- ``set -gx KEY value``, single-quoted, ``\\`` and ``'``
      backslash-escaped.

    Data forms:

    * ``json`` -- a sorted JSON object.
    * ``ini``  -- ``KEY=value`` lines under a ``[env]`` section, for Python's
      ``configparser`` (see :func:`_ini_value`); a var it cannot hold verbatim
      is named in a ``;`` comment instead.
    * ``yaml`` -- ``KEY: "value"`` lines, every value double-quoted.

    Aliases are normalized here via :data:`dotagents._env.FORMAT_ALIASES`. Values
    are emitted verbatim (this is the point of the command); callers must treat
    the output as sensitive.
    """
    import json as _json

    from dotagents._env import FORMAT_ALIASES

    fmt = FORMAT_ALIASES.get(output_format, output_format)
    if removed is None:
        removed = getattr(env, "removed", ())
    gone = sorted(k for k in set(removed) if k not in env)

    # `get_environment` assembles PATH in the HOST OS's convention (`;` and `\`
    # on Windows), which is what a native subprocess needs. On a WINDOWS host
    # the POSIX shell formats need `:` and `/`: a `;`-joined, backslash-laden
    # PATH sourced into Git Bash (the SessionStart hook writes this into
    # $CLAUDE_ENV_FILE) breaks every bare-name command lookup for that shell.
    # Convert PATH-shaped values only, for POSIX target formats only. On any
    # other host PATH is already POSIX and no conversion applies in either
    # direction: pwsh on Linux or WSL wants the Linux PATH as it is (converting
    # it dropped every Linux entry).
    windows_host = _host_is_windows()
    if fmt in ("export", "dotenv", "fish") and windows_host:
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
    elif fmt in ("export", "dotenv", "fish"):
        env = {k: v for k, v in env.items() if _POSIX_IDENTIFIER_RE.match(k)}
    elif fmt in ("powershell", "cmd") and windows_host:
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
        data: "dict[str, Optional[str]]" = {k: env[k] for k in keys}
        data.update((k, None) for k in gone)
        return _json.dumps(data, indent=2, sort_keys=True)
    if fmt == "ini":
        ini = ["[env]"]
        for k in keys:
            value = _ini_value(env[k])
            if value is None or not _ini_key_ok(k):
                # Said in a comment rather than silently dropped or mangled.
                ini.append("; %s omitted: not representable in INI" % (k if _ini_key_ok(k) else "a var"))
            else:
                ini.append("%s=%s" % (k, value))
        return "\n".join(ini)
    if fmt == "yaml":
        lines = [(k, _yaml_value(env[k])) for k in keys] + [(k, "null") for k in gone]
        return "\n".join("%s: %s" % (_yaml_key(k), v) for k, v in sorted(lines))
    if fmt == "dotenv":
        return "\n".join("%s=%s" % (k, _dotenv_value(env[k])) for k in keys)
    if fmt == "powershell":
        # `${env:NAME}` (curly-brace form), not the bare `$env:NAME` sigil:
        # Windows env vars with parens in their names (`ProgramFiles(x86)`)
        # make `$env:FOO(X86) = ...` a parse error, and the curly-brace form
        # accepts any name, so it is used unconditionally.
        # PowerShell treats U+2018..U+201B as single-quote delimiters too: an
        # undoubled `’` ended the string and the rest ran as code in the
        # `Invoke-Expression` loader. A name with `}` or a backtick cannot sit
        # inside `${env:...}` at all, so it is left out.
        ok = [k for k in keys if not any(c in k for c in "}`")]
        return "\n".join(
            ["${env:%s} = '%s'" % (k, _ps_quote_body(env[k])) for k in ok]
            + ["${env:%s} = $null" % k for k in gone if not any(c in k for c in "}`")]
        )
    if fmt == "cmd":
        return "\n".join(
            ['set "%s=%s"' % (k, _cmd_value(env[k])) for k in keys]
            + ['set "%s="' % k for k in gone]
        )
    if fmt == "fish":
        # fish single quotes: only `\` and `'` are escapes, and BOTH must be
        # escaped -- an unescaped `\\` in the value would collapse to `\`.
        return "\n".join(
            ["set -gx %s '%s'" % (k, env[k].replace("\\", "\\\\").replace("'", "\\'"))
             for k in keys]
            + ["set -e %s" % k for k in gone if _POSIX_IDENTIFIER_RE.match(k)]
        )
    # default / "export"
    return "\n".join(
        ["export %s=%s" % (k, _sh_quote(env[k])) for k in keys]
        + ["unset %s" % k for k in gone if _POSIX_IDENTIFIER_RE.match(k)]
    )


_PS_SINGLE_QUOTES = "'\u2018\u2019\u201a\u201b"


def _ps_quote_body(v: str) -> str:
    """The inside of a PowerShell single-quoted string: every character
    PowerShell reads as a single quote, doubled."""
    return "".join(c + c if c in _PS_SINGLE_QUOTES else c for c in v)


def _sh_quote(v: str) -> str:
    """POSIX-shell quoting for a value that will be SOURCED by bash.

    Single quotes, with an embedded ``'`` written as ``'\\''``: nothing inside
    single quotes is ever expanded, so a value containing ``$(...)``, backticks
    or ``$HOME`` is set verbatim instead of being EXECUTED when the SessionStart
    hook's output is sourced from ``$CLAUDE_ENV_FILE``. Always this form, for
    every value: a newline or another control character is kept literally
    inside the quotes, which POSIX sh (dash included) reads back exactly --
    the ``$'...'`` form used before is a bash/ksh extension that dash parses
    as ``$`` followed by an ordinary quoted string, where ``\\'`` ENDS the
    quote. The one exception is CR: Git for Windows' bash drops a literal CR
    from the script text it reads, so a CR is spliced in as
    ``'"$(printf '\\r')"'`` -- a POSIX builtin running a constant, never
    anything from the value. Non-ASCII passes through as-is -- the file is
    written and sourced as UTF-8.
    """
    body = v.replace("'", "'\\''").replace("\r", "'\"$(printf '\\r')\"'")
    return "'%s'" % body


def _cmd_value(v: str) -> str:
    """cmd.exe ``set "K=v"`` value. ``%`` is doubled so a ``%4`` or ``%PATH%``
    inside a value survives batch-file expansion; a newline cannot be carried
    by ``set`` at all, so it becomes a space (documented limitation, like the
    ``"`` -> ``""`` best-effort below).

    The doubling is undone only when the output runs AS A BATCH FILE, so that
    is the one supported way to consume it::

        dotagents env --format cmd > "%TEMP%\\agents-env.cmd" && call "%TEMP%\\agents-env.cmd"

    Pasted at an interactive prompt or read through ``for /f``, a ``%%``
    stays doubled. And under ``setlocal EnableDelayedExpansion`` a ``!name!``
    inside a value is expanded when the line runs -- there is no escape for
    it that also works with delayed expansion off, so run the file with it
    off (the default)."""
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
    """A YAML double-quoted scalar, ALWAYS: a plain scalar reads back typed
    whenever it looks like anything (a date, ``0b101``, ``1_000``, a YAML 1.1
    sexagesimal ``1:30``), and no fixed list catches every form. Escapes are
    YAML's own: ``\\\\``, ``\\"``, ``\\n``/``\\r``/``\\t``, and ``\\x``/``\\u``
    for every other character YAML does not allow raw in a stream (C0/C1
    controls, DEL, U+2028/U+2029 line breaks, the BOM, surrogates,
    U+FFFE/U+FFFF). Everything else is emitted as itself, so the document
    stays readable UTF-8."""
    out = ['"']
    for c in v:
        o = ord(c)
        if c == "\\":
            out.append("\\\\")
        elif c == '"':
            out.append('\\"')
        elif c == "\n":
            out.append("\\n")
        elif c == "\r":
            out.append("\\r")
        elif c == "\t":
            out.append("\\t")
        elif o < 0x20 or 0x7F <= o <= 0x9F:
            out.append("\\x%02x" % o)
        elif o in (0x2028, 0x2029, 0xFEFF, 0xFFFE, 0xFFFF) or 0xD800 <= o <= 0xDFFF:
            out.append("\\u%04x" % o)
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


_YAML_PLAIN_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _yaml_key(k: str) -> str:
    """A key bare when it is a plain identifier YAML reads as a string, else
    quoted like a value (``Y`` / ``NO`` / ``ON`` are booleans to a YAML 1.1
    reader, and a Windows name like ``ProgramFiles(x86)`` is safer quoted)."""
    if _YAML_PLAIN_KEY_RE.match(k) and not _YAML_TYPED_RE.match(k):
        return k
    return _yaml_value(k)


class Env(DotAgentsArgs):
    """Assemble the chained env (env files + env.py execution) under contract B.

    Prepends every level's ``bin`` dir to ``PATH`` first and every existing
    ``lib`` dir to ``PYTHONPATH`` (listing them in ``AGENTS_PYTHONPATH``), then evaluates the ``pre.*``
    tier and the main tier in precedence order (store by store -- system,
    user, project -- each store's overlays before the store itself), chaining
    each file over the accumulated env so later files win. ``.py`` files are
    EXECUTED and emit JSON env changes; plain files are sourced by bash. A
    project root's own files outside ``.agents/`` never run. Standardized
    ``AGENTS_*``/``AGENT`` identity vars and the ``AGENTS_PROXY`` model are
    wired in.

    ``--diff`` emits only the vars that differ from the current environment (what
    a SessionStart hook injects); the default emits the full assembled env merged
    over the current one. Output is sensitive -- it may carry secret values.
    A var an env layer unsets (``null`` from an ``env.py``, ``unset`` in a
    plain file) is emitted as an unset where the format has one.

    ``--cache`` reuses the output of an earlier run whose inputs were the same
    -- the whole environment, the cwd, the stores and their overlays, every
    env file's and ``lib`` file's mtime and size -- for up to five minutes
    (an ``env.py`` may read something no key can see), stored owner-only under
    ``<user store>/.cache/env/``. The env-loader hooks, which run before every
    shell command, pass it; an edited env file is picked up on the next call.

    ``--format`` selects the emitted syntax and defaults to ``auto``, which
    detects the CALLING shell (parent-process chain) and picks a matching format
    so the output is sourceable where it runs: ``export`` (aliases
    ``posix``/``sh``/``bash``), ``dotenv`` (``env``), ``powershell``
    (``pwsh``/``ps``), ``cmd`` (``bat``/``batch``), ``fish``, plus the data
    forms ``json``/``ini``/``yaml``. An explicit ``--format`` always wins.
    ``cmd`` output is meant to run as a batch file: redirect it to a ``.cmd``
    file and ``call`` that file, with delayed expansion off. Only then do its
    doubled percent signs collapse back, and a ``!name!`` in a value is not
    expanded.

    Roots (both configurable, never hardcoded): the user store is
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

    cache: bool = False
    (
        "Reuse the output of an earlier identical run (same environment, cwd, "
        "store, overlays and env/lib files, at most 5 minutes old) instead of "
        "running the env files again; the per-command env loaders use it."
    )
    ("--cache",)

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

        key = None
        if self.cache:
            # A hook runs this before EVERY shell call; the assembly (one
            # child process per env file) is skipped while its inputs hold.
            key = _env.env_cache_key(scope, base, output_format, "diff" if self.diff else "full")
            cached = _env.read_env_cache(scope, key)
            if cached is not None:
                _write_stdout(cached)
                return 0

        if self.diff:
            env = _env.get_diff(scope, base_env=base, logger=self._logger_)
            removed = env.removed
        else:
            changes = _env.get_environment(scope, base_env=base, logger=self._logger_)
            removed = changes.removed
            # Inherited DOTAGENTS_* values are tool-internal secrets that are
            # never printed; only a change the env layers make shows up.
            env = {
                k: v for k, v in base.items()
                if not k.startswith("DOTAGENTS_") and k not in removed
            }
            env.update(changes)

        # UTF-8 straight to the buffer: a bare print() encodes with the console
        # codepage and dies on the first non-Latin-1 character in any value.
        text = _format_env(env, output_format, removed) + "\n"
        if key is not None:
            _env.write_env_cache(scope, key, text)
        _write_stdout(text)
        return 0
