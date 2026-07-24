"""`dotagents env` -- chained env-file assembly + env.py execution (plan 07).

Self-contained block: all logic lives in `_env.py` (frozen contract B) and
`_agents.stamp_identity` (plan 08). The only umbrella touch is registering
`Env` on `Dotagents._subcommands_` (in `cli/__init__.py`). Never logs
DOTAGENTS_*/AGENTS_* VALUES -- output goes to stdout for the caller to consume;
the logger only ever names vars (Leakage rule).
"""

import re
from pathlib import Path

from duho import Cmd, LoggingArgs

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


_DRIVE_LETTER_RE = re.compile(r"^([A-Za-z]):[/\\]")


def _to_posix_path(segment: str) -> str:
    """One path segment: backslashes to slashes, then a leading drive letter
    (``C:/...``) to its MSYS mount point (``/c/...``).

    Slash direction alone is NOT sufficient: MSYS2/Cygwin bash resolves PATH
    lookups through its own mount table, and a `C:/...`-form segment (right
    separator, wrong root) fails command lookup exactly like a backslash one
    does -- verified directly (`command -v grep` empty for `C:/Program
    Files/Git/usr/bin`, populated for `/c/Program Files/Git/usr/bin`, same
    directory). Only a genuine `X:/` or `X:\\` prefix is rewritten, so an
    already-POSIX or relative segment (`.agents/bin`, `/etc/agents/bin`) is
    left untouched.
    """
    segment = segment.replace("\\", "/")
    m = _DRIVE_LETTER_RE.match(segment)
    if m:
        segment = "/%s%s" % (m.group(1).lower(), segment[2:])
    return segment


def _to_posix_path_list(value: str) -> str:
    """Convert an OS-native (Windows) `;`-joined path list to a POSIX
    `:`-joined one, for a shell-sourceable target format.

    Empty segments are dropped (a stray leading/trailing `;` must not become a
    bare `:` -- POSIX treats an empty PATH segment as `.`, the cwd, which is
    both wrong and a real security footgun).
    """
    segments = [_to_posix_path(s) for s in value.split(";") if s]
    return ":".join(segments)


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
        return "\n".join(
            "$env:%s = '%s'" % (k, env[k].replace("'", "''")) for k in keys
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


class Env(LoggingArgs, Cmd):
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
    forms ``json``/``ini``/``yaml``. An explicit ``--format`` always wins."""

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

    global_scope: bool = False
    "Use global scope (skip project-level env files)."
    ("--global", "-g")

    def __call__(self) -> int:
        import os

        from dotagents import _env

        project_root = Path.cwd()
        agents_dir = Path.home() / ".agents"
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
