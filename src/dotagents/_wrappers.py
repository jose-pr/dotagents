"""Write `dotagents` / `dotagents.cmd` wrappers: at a built pyz, or at
`python -m dotagents` for a plain install."""

import os
import stat
import sys
from pathlib import Path

# The sh wrapper. `{target}` is what the interpreter runs: the pyz (absolute,
# or `"$d/..."` relative to the wrapper's own directory, so moving the scope dir
# does not break it) or `-m dotagents` for a plain install. `readlink -f` so a
# wrapper run through a symlink still finds what is beside the real file.
#
# The interpreter recorded is the one of the system that ran `init`. A store
# shared with another system -- WSL reading a Windows store -- reaches a
# recorded path that does not run there; the wrapper then takes that system's
# own: the store's agents' venv for this OS and machine (`.pyvenv/*-<os>-<arch>`,
# the naming `dotagents._pyvenv` uses), else `python3`, and a recorded
# `C:/...` pyz path goes through `wslpath`.
POSIX_TEMPLATE = (
    '#!/bin/sh\n'
    'd=$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")\n'
    'py="{python}"\n'
    'set -- {target} "$@"\n'
    'if [ ! -x "$py" ]; then\n'
    '  case "$(uname -s)" in Darwin) os=darwin ;; *) os=posix ;; esac\n'
    '  py=\n'
    '  for v in "$d"/../.pyvenv/*-"$os"-"$(uname -m)"/bin/python; do\n'
    '    [ -x "$v" ] && py=$v\n'
    '  done\n'
    '  [ -n "$py" ] || py=$(command -v python3 || command -v python) || {{\n'
    '    echo "dotagents: no Python interpreter found" >&2; exit 127; }}\n'
    '  case "$1" in [A-Za-z]:/*) if command -v wslpath >/dev/null 2>&1; then\n'
    '    p=$(wslpath -u "$1"); shift; set -- "$p" "$@"; fi ;; esac\n'
    'fi\n'
    'exec "$py" "$@"\n'
)


def _posix_wrapper(python: str, target: str) -> str:
    return POSIX_TEMPLATE.format(python=Path(python).as_posix(), target=target)


def _cmd_safe(path: str) -> str:
    """``path`` in a form cmd.exe reads correctly from a batch file: cmd
    decodes .cmd files in the OEM code page, so a non-ASCII interpreter path
    ("C:\\Users\\Jos\u00e9\\...") written as UTF-8 was "cannot find the path". The
    8.3 short name is pure ASCII; when none exists the path is returned as is
    and :func:`_write_cmd` switches the code page instead."""
    try:
        path.encode("ascii")
        return path
    except UnicodeEncodeError:
        pass
    if os.name == "nt":
        import ctypes

        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf))
        if 0 < n < len(buf):
            try:
                buf.value.encode("ascii")
                return buf.value
            except UnicodeEncodeError:
                pass
    return path


def _write_cmd(path: Path, body: str) -> None:
    """Write a .cmd wrapper. A non-ASCII line runs under code page 65001 (and
    the file is UTF-8), with the caller's code page restored afterwards."""
    try:
        body.encode("ascii")
        text = "@echo off\r\n" + body + "\r\n"
    except UnicodeEncodeError:
        text = (
            "@echo off\r\n"
            'for /f "tokens=2 delims=:." %%a in (\'chcp\') do set "_dotagents_cp=%%a"\r\n'
            "chcp 65001 >nul\r\n"
            + body + "\r\n"
            'set "_dotagents_rc=%errorlevel%"\r\n'
            "chcp %_dotagents_cp% >nul\r\n"
            "exit /b %_dotagents_rc%\r\n"
        )
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def wrapper_points_at_pyz(bin_dir: Path) -> bool:
    """True if `bin_dir` holds a `dotagents` wrapper that runs a `.pyz`."""
    for name in ("dotagents", "dotagents.cmd"):
        try:
            if ".pyz" in (Path(bin_dir) / name).read_text(encoding="utf-8"):
                return True
        except OSError:
            pass
    return False


def write_module_wrappers(bin_dir: Path, python: "str | None" = None) -> "list[Path]":
    """Write both wrapper forms into `bin_dir` running `"<python>" -m dotagents`
    (``python`` defaults to ``sys.executable``, absolute -- see
    :func:`write_wrappers`). What a plain install gets, so the hooks that call
    `<store>/bin/dotagents[.cmd]` find a command there too."""
    bin_dir = Path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    python = python or sys.executable
    sh_path, cmd_path = bin_dir / "dotagents", bin_dir / "dotagents.cmd"
    with open(sh_path, "w", encoding="utf-8", newline="") as f:
        f.write(_posix_wrapper(python, "-m dotagents"))
    if os.name != "nt":
        sh_path.chmod(sh_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    _write_cmd(cmd_path, '"%s" -m dotagents %%*' % _cmd_safe(str(python)))
    return [sh_path, cmd_path]


def write_wrappers(
    bin_dir: Path,
    pyz_path: Path,
    python: "str | None" = None,
    *,
    relative: bool = False,
) -> "list[Path]":
    """Write both wrapper scripts into `bin_dir`, returning the paths written.

    BOTH files are always written, on every platform: `dotagents` (sh) and
    `dotagents.cmd`. A Windows box with Git Bash / WSL runs the sh one and cmd
    runs the `.cmd`, and the two never collide -- cmd.exe resolves `.cmd` via
    PATHEXT while sh picks the extensionless file. Writing only the platform's
    "native" form would break the other shell on the same box.

    `python` defaults to the interpreter running this code (`sys.executable`),
    embedded as an absolute path. A bare `python`/`python3` is NOT usable: on
    Windows it resolves to the Microsoft Store alias stub ("Python was not
    found...") for anyone who has not installed the Store package, so a wrapper
    calling it exits 0 having done nothing -- which then silently breaks any hook
    that shells out to `dotagents`.

    `relative=True` points the wrapper at `pyz_path` relative to `bin_dir`
    (the real file's directory / `%~dp0`), so a scope directory stays relocatable. Falls
    back to an absolute path when the two live on different drives.
    """
    bin_dir = Path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    pyz_path = Path(pyz_path).resolve()
    python = python or sys.executable

    rel_pyz = None
    if relative:
        try:
            candidate = Path(os.path.relpath(pyz_path, bin_dir))
        except ValueError:
            # Different drives on Windows -- no relative path exists.
            candidate = None
        # Relative is for a pyz living in or beside the scope (keeps the store
        # relocatable): `dotagents.pyz` or `../dotagents.pyz`. A pyz elsewhere on
        # disk yields a long `../../../..` chain that is unreadable and MORE
        # fragile than absolute -- it breaks when either side moves, not just the
        # scope. One `..` is the limit.
        if candidate is not None and candidate.parts.count("..") <= 1:
            rel_pyz = candidate

    written = []

    # `Path.write_text(..., newline=...)` needs Python 3.10+; open() directly
    # (with newline="") so the exact \n / \r\n bytes above are preserved
    # unchanged on every Python 3.9+ platform.
    sh_path = bin_dir / "dotagents"
    with open(sh_path, "w", encoding="utf-8", newline="") as f:
        if rel_pyz is not None:
            f.write(_posix_wrapper(python, '"$d/%s"' % rel_pyz.as_posix()))
        else:
            f.write(_posix_wrapper(python, '"%s"' % pyz_path.as_posix()))
    if os.name != "nt":
        mode = sh_path.stat().st_mode
        sh_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    written.append(sh_path)

    cmd_path = bin_dir / "dotagents.cmd"
    exe = _cmd_safe(str(python))
    if rel_pyz is not None:
        _write_cmd(cmd_path, '"%s" "%%~dp0%s" %%*' % (exe, rel_pyz))
    else:
        _write_cmd(cmd_path, '"%s" "%s" %%*' % (exe, _cmd_safe(str(pyz_path))))
    written.append(cmd_path)

    return written


def check_path_warning(bin_dir: Path) -> "str | None":
    """Return a warning string (with the literal export hint) if `bin_dir` is
    not on PATH, else None."""
    bin_dir = str(Path(bin_dir).resolve())
    path_entries = [str(Path(p).resolve()) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if bin_dir in path_entries:
        return None
    if os.name == "nt":
        hint = '$env:PATH += ";%s"' % bin_dir
    else:
        hint = 'export PATH="%s:$PATH"' % bin_dir
    return "warning: %s is not on PATH. Add it with:\n  %s" % (bin_dir, hint)
