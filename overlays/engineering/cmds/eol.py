"""`dotagents eol` -- check and fix line endings, safely.

    dotagents eol check [path ...]
    dotagents eol fix   [path ...] [--dry-run]

Every text file is LF, unless the repository's own attributes say otherwise:

* ``eol=crlf`` on a path means that file is expected to be CRLF (a ``.bat``);
* ``-text`` or ``binary`` means the bytes are not ours to choose -- a recorded
  response, a third party's golden file -- and the file is left alone.

A file is counted in bytes, never by a text tool: LF when it has no CR, CRLF
when every newline is a CR LF pair, MIXED otherwise. ``fix`` reads the whole
file before it writes anything, writes a sibling temporary file and renames it
over the original, so a failure cannot leave a file empty or half-written.

In a git working tree the files are the tracked and the untracked ones, ignore
rules honoured. Elsewhere the directory is walked.

Exit codes: 0 nothing to fix, 1 files need fixing (``check``), 2 usage,
3 nothing could be checked.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from duho import Cli, Cmd, LoggingArgs

EXIT_OK = 0
EXIT_OFFENDERS = 1
EXIT_CANNOT_CHECK = 3

#: Used only where git cannot list the files.
PRUNE_DIRS = {
    ".git", ".agents", ".venv", ".pyvenv", "__pycache__", "node_modules", "target",
    "build", "dist", ".pytest_cache", ".mypy_cache", ".ruff_cache",
}
#: Control bytes that are ordinary in text: tab, newline, carriage return, form feed.
_ORDINARY = frozenset(b"\t\n\r\x0c")


def endings(data: bytes) -> "tuple[int, int, int]":
    """(CR LF pairs, bare LF, bare CR) in `data`."""
    crlf = data.count(b"\r\n")
    return crlf, data.count(b"\n") - crlf, data.count(b"\r") - crlf


def state_of(data: bytes) -> str:
    """`LF`, `CRLF`, `MIXED`, or `NONE` for a file with no line ending at all."""
    crlf, lf, cr = endings(data)
    if not (crlf or lf or cr):
        return "NONE"
    if not crlf and not cr:
        return "LF"
    if not lf and not cr:
        return "CRLF"
    return "MIXED"


def convert(data: bytes, expected: str) -> bytes:
    """`data` with every line ending made `expected` (`LF` or `CRLF`)."""
    unified = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return unified.replace(b"\n", b"\r\n") if expected == "CRLF" else unified


def is_binary(data: bytes) -> bool:
    """git's own test: a NUL in the first 8000 bytes."""
    return b"\0" in data[:8000]


def first_control(data: bytes) -> "tuple[int, int] | None":
    """(byte value, 1-based line) of the first control byte that is not ordinary text."""
    for offset, byte in enumerate(data):
        if byte < 0x20 and byte not in _ORDINARY:
            return byte, data.count(b"\n", 0, offset) + 1
    return None


def _git(args: "list[str]", *, stdin: "bytes | None" = None) -> "subprocess.CompletedProcess":
    return subprocess.run(["git", *args], input=stdin, capture_output=True,
                          stdin=None if stdin is not None else subprocess.DEVNULL,
                          timeout=120)


class Eol(LoggingArgs, Cli):
    """Check and fix line endings: LF everywhere, except where the repository says otherwise."""

    _parsername_ = "eol"

    class _Scan(Cmd):
        """What both subcommands share: which files, and what each should be."""

        paths: "list[Path]" = []
        "Files or directories to examine (default: the current directory)."
        ("paths",)

        def targets(self) -> "list[tuple[Path, str]]":
            """(file, expected ending or `EXEMPT`) for every file to examine."""
            found: "list[tuple[Path, str]]" = []
            for given in (list(self.paths) or [Path(".")]):
                path = Path(given)
                if path.is_file():
                    found += self._with_attributes(path.parent, [path.name])
                elif path.is_dir():
                    found += self._with_attributes(path, self._list(path))
                else:
                    raise SystemExit("error: no such file or directory: %s" % path)
            return found

        def _list(self, root: Path) -> "list[str]":
            """Files under `root`, relative to it: git's view where there is one."""
            try:
                inside = _git(["-C", str(root), "rev-parse", "--is-inside-work-tree"])
                if inside.returncode == 0 and inside.stdout.strip() == b"true":
                    out = _git(["-C", str(root), "ls-files", "--cached", "--others",
                                "--exclude-standard", "-z"])
                    if out.returncode == 0:
                        names = out.stdout.decode("utf-8", "surrogateescape").split("\0")
                        return sorted({name for name in names if name})
            except (OSError, subprocess.TimeoutExpired):
                pass
            names = []
            for current, dirs, files in os.walk(root):
                dirs[:] = sorted(d for d in dirs if d not in PRUNE_DIRS)
                for name in sorted(files):
                    names.append(os.path.relpath(os.path.join(current, name), root))
            return names

        def _with_attributes(self, root: Path, names: "list[str]") -> "list[tuple[Path, str]]":
            """Pair each file with what its attributes expect of it."""
            expected = dict.fromkeys(names, "LF")
            try:
                out = _git(["-C", str(root), "check-attr", "-z", "--stdin", "text", "eol"],
                           stdin="\0".join(names).encode("utf-8", "surrogateescape") + b"\0")
                fields = out.stdout.decode("utf-8", "surrogateescape").split("\0")
            except (OSError, subprocess.TimeoutExpired):
                fields = []
            if names and fields:
                for name, attr, value in zip(fields[0::3], fields[1::3], fields[2::3]):
                    if name not in expected:
                        continue
                    if attr == "text" and value == "unset":
                        expected[name] = "EXEMPT"
                    elif attr == "eol" and value == "crlf" and expected[name] != "EXEMPT":
                        expected[name] = "CRLF"
            return [(root / name, expected[name]) for name in names]

        def offenders(self) -> "tuple[list[tuple[Path, str, bytes]], dict[str, int]]":
            """(file, expected, its bytes) for every file that is not as expected,
            and how many files fell into each class."""
            wrong: "list[tuple[Path, str, bytes]]" = []
            seen = {"text": 0, "binary": 0, "exempt": 0, "unreadable": 0}
            for path, expected in self.targets():
                if path.is_symlink() or not path.is_file():
                    continue
                if expected == "EXEMPT":
                    seen["exempt"] += 1
                    continue
                try:
                    data = path.read_bytes()
                except OSError:
                    seen["unreadable"] += 1
                    continue
                if is_binary(data):
                    seen["binary"] += 1
                    continue
                seen["text"] += 1
                if state_of(data) not in ("NONE", expected):
                    wrong.append((path, expected, data))
            return wrong, seen

        @staticmethod
        def describe(data: bytes) -> str:
            crlf, lf, cr = endings(data)
            state = state_of(data)
            if state == "MIXED":
                parts = ["%d CRLF" % crlf, "%d LF" % lf] + (["%d bare CR" % cr] if cr else [])
                return "MIXED  (%s)" % ", ".join(parts)
            return "%-5s  (%d lines)" % (state, crlf + lf)

        @staticmethod
        def summary(seen: "dict[str, int]") -> str:
            extra = ", ".join("%d %s" % (count, kind) for kind, count in seen.items()
                              if kind != "text" and count)
            return "%d text files%s" % (seen["text"], " (%s skipped)" % extra if extra else "")

    class Check(_Scan):
        """Report every file whose line endings are not what they should be. Exit 1 if any."""

        _parsername_ = "check"

        control: bool = False
        "Also report control bytes that do not belong in text, such as a stray backspace."
        ("--control",)

        def __call__(self) -> int:
            wrong, seen = self.offenders()
            for path, expected, data in wrong:
                print("%s  %s  expected %s" % (self.describe(data), path, expected))
            strays = self._control_bytes() if self.control else []
            for path, byte, line in strays:
                print("CTRL   (byte 0x%02x, line %d)  %s" % (byte, line, path))
            if not seen["text"]:
                print("CANNOT CHECK: no text file found (%s)" % self.summary(seen))
                return EXIT_CANNOT_CHECK
            found = len(wrong) + len(strays)
            print("%s: %s" % (self.summary(seen),
                              "%d to fix (`dotagents eol fix`)" % len(wrong) if wrong else "all as expected")
                  + (", %d with control bytes" % len(strays) if strays else ""))
            return EXIT_OFFENDERS if found else EXIT_OK

        def _control_bytes(self) -> "list[tuple[Path, int, int]]":
            found = []
            for path, expected in self.targets():
                if expected == "EXEMPT" or path.is_symlink() or not path.is_file():
                    continue
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                hit = None if is_binary(data) else first_control(data)
                if hit:
                    found.append((path, hit[0], hit[1]))
            return found

    class Fix(_Scan):
        """Convert every file that needs it. Reads all of a file before writing any of it."""

        _parsername_ = "fix"

        dry_run: bool = False
        "Show what would change without writing."
        ("--dry-run",)

        def __call__(self) -> int:
            wrong, seen = self.offenders()
            for path, expected, data in wrong:
                before = state_of(data)
                if not self.dry_run:
                    self._replace(path, convert(data, expected))
                print("%s %s  (%s -> %s)" % ("would fix" if self.dry_run else "fixed",
                                             path, before, expected))
            print("%s: %d %s" % (self.summary(seen), len(wrong),
                                 "would change" if self.dry_run else "fixed"))
            return EXIT_OK

        @staticmethod
        def _replace(path: Path, data: bytes) -> None:
            """Write `data` beside `path`, then rename it over `path`."""
            handle, name = tempfile.mkstemp(dir=str(path.parent), prefix=".eol-")
            try:
                with os.fdopen(handle, "wb") as out:
                    out.write(data)
                shutil.copymode(str(path), name)
                os.replace(name, str(path))
            except BaseException:
                try:
                    os.unlink(name)
                except OSError:
                    pass
                raise

    _subcommands_ = [Check, Fix]
