"""Filesystem write helpers shared by every module that writes a text file.

One rule, one place: **files are LF-only, on every platform.** `Path.write_text`
without `newline=` translates `\\n` to the platform default (CRLF on Windows).
`Path.write_text(..., newline=)` is Python 3.10+, and this package's floor is
3.9, so the helpers open the file themselves.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def _default_file_mode() -> int:
    """The mode ``open(path, "w")`` gives a new file: 0o666 less the umask.
    ``os.umask`` can only be read by setting it, so it is set and put back."""
    mask = os.umask(0)
    os.umask(mask)
    return 0o666 & ~mask


def write_text_lf(path: "str | os.PathLike[str]", text: str, *, atomic: bool = False) -> Path:
    """Write ``text`` to ``path`` as UTF-8 with LF line endings, creating parent
    directories. With ``atomic=True`` the content goes to a sibling temp file
    first and is moved into place with ``os.replace``, so a reader (or a crash)
    never sees a half-written file -- use it for a config file an agent may be
    reading concurrently (``settings.json``).

    An atomic write goes THROUGH a symlink: the temp file is made beside the
    link's final target and replaces that, so a settings file kept in a
    dotfiles repo (stow, chezmoi) stays a link and the repo copy is what
    changes. The replaced file keeps its permission bits; a new one gets the
    mode a plain ``open()`` would give it, not ``mkstemp``'s 0600. Returns
    ``path`` as given (the link, not its target)."""
    given = Path(path)
    if not atomic:
        given.parent.mkdir(parents=True, exist_ok=True)
        with open(given, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return given
    # realpath follows every link in the chain, including a dangling one: the
    # write then creates the file the link names, as a plain open() would.
    target = Path(os.path.realpath(str(given)))
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        if target.exists():
            shutil.copymode(str(target), tmp_name)
        elif os.name != "nt":
            os.chmod(tmp_name, _default_file_mode())
        os.replace(tmp_name, str(target))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return given
