"""Write `dotagents` / `dotagents.cmd` wrappers pointing at a built pyz."""

import os
import stat
from pathlib import Path

POSIX_TEMPLATE = '#!/bin/sh\nexec python3 "{pyz}" "$@"\n'
CMD_TEMPLATE = '@echo off\r\npython "{pyz}" %*\r\n'


def write_wrappers(bin_dir: Path, pyz_path: Path) -> "list[Path]":
    """Write both wrapper scripts into `bin_dir`, returning the paths written."""
    bin_dir = Path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    pyz_path = Path(pyz_path).resolve()

    written = []

    # `Path.write_text(..., newline=...)` needs Python 3.10+; open() directly
    # (with newline="") so the exact \n / \r\n bytes above are preserved
    # unchanged on every Python 3.9+ platform.
    sh_path = bin_dir / "dotagents"
    with open(sh_path, "w", encoding="utf-8", newline="") as f:
        f.write(POSIX_TEMPLATE.format(pyz=pyz_path.as_posix()))
    if os.name != "nt":
        mode = sh_path.stat().st_mode
        sh_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    written.append(sh_path)

    cmd_path = bin_dir / "dotagents.cmd"
    with open(cmd_path, "w", encoding="utf-8", newline="") as f:
        f.write(CMD_TEMPLATE.format(pyz=str(pyz_path)))
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
