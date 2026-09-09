"""Filesystem write helpers shared by every module that writes a text file.

One rule, one place: **files are LF-only, on every platform.** `Path.write_text`
without `newline=` translates `\\n` to the platform default (CRLF on Windows),
which is how the live `~/.agents/AGENTS.md` on a Windows dev box ended up CRLF on
every line while the config's own always-on rule says LF. `Path.write_text(...,
newline=)` is Python 3.10+, and this package's floor is 3.9, so the helpers
open the file themselves.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_text_lf(path: "str | os.PathLike[str]", text: str, *, atomic: bool = False) -> Path:
    """Write ``text`` to ``path`` as UTF-8 with LF line endings, creating parent
    directories. With ``atomic=True`` the content goes to a sibling temp file
    first and is moved into place with ``os.replace``, so a reader (or a crash)
    never sees a half-written file -- use it for a config file an agent may be
    reading concurrently (``settings.json``)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not atomic:
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return target
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp_name, str(target))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target
