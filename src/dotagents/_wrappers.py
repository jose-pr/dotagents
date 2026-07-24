"""Write `dotagents` / `dotagents.cmd` wrappers pointing at a built pyz."""

import os
import stat
import sys
from pathlib import Path

POSIX_TEMPLATE = '#!/bin/sh\nexec "{python}" "{pyz}" "$@"\n'
CMD_TEMPLATE = '@echo off\r\n"{python}" "{pyz}" %*\r\n'

# Relative forms: resolve the pyz from the wrapper's own directory, so moving or
# copying the scope dir does not break the command.
POSIX_TEMPLATE_REL = (
    '#!/bin/sh\nexec "{python}" "$(dirname "$0")/{pyz}" "$@"\n'
)
CMD_TEMPLATE_REL = '@echo off\r\n"{python}" "%~dp0{pyz}" %*\r\n'


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
    "native" form (what the precursor did) breaks the other shell on the same box,
    which on this project's own dev machine is the common case.

    `python` defaults to the interpreter running this code (`sys.executable`),
    embedded as an absolute path. A bare `python`/`python3` is NOT usable: on
    Windows it resolves to the Microsoft Store alias stub ("Python was not
    found...") for anyone who has not installed the Store package, so a wrapper
    calling it exits 0 having done nothing -- which then silently breaks any hook
    that shells out to `dotagents`.

    `relative=True` points the wrapper at `pyz_path` relative to `bin_dir`
    (`$(dirname $0)` / `%~dp0`), so a scope directory stays relocatable. Falls
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
            f.write(
                POSIX_TEMPLATE_REL.format(
                    python=Path(python).as_posix(), pyz=rel_pyz.as_posix()
                )
            )
        else:
            f.write(
                POSIX_TEMPLATE.format(
                    python=Path(python).as_posix(), pyz=pyz_path.as_posix()
                )
            )
    if os.name != "nt":
        mode = sh_path.stat().st_mode
        sh_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    written.append(sh_path)

    cmd_path = bin_dir / "dotagents.cmd"
    with open(cmd_path, "w", encoding="utf-8", newline="") as f:
        if rel_pyz is not None:
            f.write(CMD_TEMPLATE_REL.format(python=str(python), pyz=str(rel_pyz)))
        else:
            f.write(CMD_TEMPLATE.format(python=str(python), pyz=str(pyz_path)))
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
