"""Managed-block merge for `init`'s AGENTS.md/CLAUDE.md.

`init` must never clobber a user-customized AGENTS.md. Content owned by dotagents
is delimited by literal marker lines and treated as a block *within* the file:
create if missing, prepend if present-without-markers, refresh-in-place if the
markers are already there. Detection is by marker presence only, never by prose
matching, so it survives user reformatting.

A marker counts only when it is a LINE of its own (surrounding whitespace
allowed): the base `AGENTS.md` itself *mentions* the marker names in prose, and
a substring match would treat that mention as a block.
"""

import re
import shutil
import time
from pathlib import Path

from dotagents._fs import write_text_lf

BEGIN_MARKER = "<!-- dotagents:begin -->"
END_MARKER = "<!-- dotagents:end -->"

#: The marker pair for an assembled-context block written into a harness's own
#: file by `context --write-agent` (distinct from the base-config pair above, so
#: the two can coexist in one file).
CONTEXT_BEGIN_MARKER = "<!-- dotagents:context:begin -->"
CONTEXT_END_MARKER = "<!-- dotagents:context:end -->"


def _marker_re(marker: str) -> "re.Pattern[str]":
    return re.compile(r"(?m)^[ \t]*%s[ \t]*$" % re.escape(marker))


_FENCE_RE = re.compile(r"(?m)^[ \t]{0,3}(`{3,}|~{3,})[^\n]*$")


def _fenced_spans(text: str) -> "list[tuple[int, int]]":
    """Character spans of fenced code blocks (``` or ~~~, CommonMark-style: a
    fence closes on the same character at least as long). An unclosed fence
    runs to the end of the text."""
    spans: "list[tuple[int, int]]" = []
    opening = None
    start = 0
    for m in _FENCE_RE.finditer(text):
        fence = m.group(1)
        if opening is None:
            opening, start = fence, m.start()
        elif (fence[0] == opening[0] and len(fence) >= len(opening)
              and m.group(0).strip() == fence):  # a closing fence carries no info string
            spans.append((start, m.end()))
            opening = None
    if opening is not None:
        spans.append((start, len(text)))
    return spans


def _marker_lines(text: str, marker: str, pos: int = 0) -> "list[re.Match[str]]":
    """Every `marker` line at or after `pos` that is not inside a code fence --
    a fenced example of the markers is documentation, not a block."""
    fences = _fenced_spans(text)
    return [
        m for m in _marker_re(marker).finditer(text, pos)
        if not any(a <= m.start() < b for a, b in fences)
    ]


def find_block(
    text: str, begin_marker: str = BEGIN_MARKER, end_marker: str = END_MARKER
) -> "tuple[int, int] | None":
    """``(start, end)`` character span of the managed block in ``text`` -- from
    the first BEGIN marker line through the first END marker line after it,
    ignoring marker lines inside fenced code -- or ``None`` when there is no
    block. A BEGIN with no END after it is not a block (the caller decides
    whether that is an error)."""
    begins = _marker_lines(text, begin_marker)
    if not begins:
        return None
    ends = _marker_lines(text, end_marker, begins[0].end())
    if not ends:
        return None
    return begins[0].start(), ends[0].end()


def _extract_block(
    text: str, begin_marker: str = BEGIN_MARKER, end_marker: str = END_MARKER
) -> str:
    """Return the managed block from a base-overlay/template file that is itself
    fully marker-wrapped -- the inner text INCLUDING both marker lines.

    The markers are part of the returned string on purpose: callers write this
    straight into a target file, and the block has to stay detectable there (the
    merge finds an existing block by marker presence alone).

    A source without both markers (an ``--from`` base that dropped them) is a
    usage error reported as such, not a bare ``ValueError`` traceback."""
    span = find_block(text, begin_marker, end_marker)
    if span is None:
        raise SystemExit(
            "error: the base file has no managed block -- it must carry the "
            "%r / %r marker lines" % (begin_marker, end_marker)
        )
    return text[span[0] : span[1]]


def _backup_path(target: Path, backup_root: Path) -> Path:
    """Where `target` is backed up under `backup_root`: its path relative to the
    store (``backup_root``'s grandparent, see :func:`timestamped_backup_root`),
    or ``external/<absolute path>`` for a file outside it -- so two targets that
    share a basename (the store's ``AGENTS.md`` and pi's) never collide."""
    target = Path(target).resolve()
    store = Path(backup_root).resolve().parent.parent
    try:
        rel = target.relative_to(store)
    except ValueError:
        anchor = target.parts[0].replace(":", "").strip("\\/")  # "C" on Windows, "" on POSIX
        rel = Path("external", *([anchor] if anchor else []), *target.parts[1:])
    return Path(backup_root) / rel


def _backup(target: Path, backup_root: Path) -> bool:
    """Copy `target` to its mirrored path under `backup_root`, unless a backup
    of it already exists there -- the first backup of a run holds the original,
    and a later adapter re-writing the same file must not replace it with the
    text an earlier adapter just wrote. Returns whether a copy was made."""
    dest = _backup_path(target, backup_root)
    if dest.exists():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, dest)
    return True


def merge_block(
    target: Path,
    block_source_text: str,
    *,
    force: bool = False,
    dry_run: bool = False,
    backup_root: "Path | None" = None,
    begin_marker: str = BEGIN_MARKER,
    end_marker: str = END_MARKER,
    append: bool = False,
) -> str:
    """Merge `block_source_text` (a fully marker-wrapped skeleton file's
    contents) into `target`, returning the branch taken:
    "created" / "block-inserted" / "block-refreshed" / "unchanged" /
    "replaced (--force, backed up)" / "replaced (--force)".

    Never overwrites content outside the markers unless `force` is True (then
    the whole file is replaced, after being backed up under `backup_root` if the
    target pre-exists -- at a path mirroring the target's, and never over an
    earlier backup of the same file; "backed up" is reported only when a copy
    was made).

    `begin_marker`/`end_marker` default to the Markdown/HTML comment pair; pass a
    different pair for other comment syntaxes (e.g. `#`-prefixed markers for TOML).

    `append` puts the block at the END of an existing file rather than the start.
    Required for TOML: a `[table]` header captures every key line that follows it,
    so prepending a table would silently swallow the user's existing top-level keys
    into our table.

    Files are written LF-only (:func:`dotagents._fs.write_text_lf`). A target
    carrying a BEGIN marker line with no END after it is malformed and refused
    (``SystemExit``) rather than gaining a second block.
    """
    block = _extract_block(block_source_text, begin_marker, end_marker)

    if force:
        if not target.exists():
            if not dry_run:
                write_text_lf(target, block_source_text)
            return "created"
        if target.read_text(encoding="utf-8-sig") == block_source_text:
            return "unchanged"
        backed_up = False
        if backup_root is not None:
            backed_up = dry_run or _backup(target, backup_root)
        if not dry_run:
            write_text_lf(target, block_source_text)
        return "replaced (--force, backed up)" if backed_up else "replaced (--force)"

    if not target.exists():
        if not dry_run:
            write_text_lf(target, block_source_text)
        return "created"

    # utf-8-sig: a leading BOM (PowerShell 5's `-Encoding UTF8`) would otherwise
    # hide a block on line 1 from the `^` anchor; it is not written back.
    existing = target.read_text(encoding="utf-8-sig")

    span = find_block(existing, begin_marker, end_marker)
    if span is not None:
        start, end = span
        new_text = existing[:start] + block + existing[end:]
        if new_text == existing:
            return "skipped (present)"
        if not dry_run:
            write_text_lf(target, new_text)
        return "block-refreshed"
    if _marker_lines(existing, begin_marker):
        raise SystemExit(
            "error: %s has a %r line with no %r after it -- fix the file, then re-run"
            % (target, begin_marker, end_marker)
        )

    if append:
        sep = "" if existing.endswith("\n") else "\n"
        new_text = existing + sep + "\n" + block + "\n"
    else:
        new_text = block + "\n\n" + existing
    if not dry_run:
        write_text_lf(target, new_text)
    return "block-inserted"


def remove_block(
    target: Path,
    *,
    dry_run: bool = False,
    begin_marker: str = BEGIN_MARKER,
    end_marker: str = END_MARKER,
) -> str:
    """Delete the managed block (markers included) from `target`, with the one
    blank line `merge_block(append=True)` put before it. Everything else stays.
    Returns "removed" or "absent" (no file, or no block in it)."""
    if not target.exists():
        return "absent"
    existing = target.read_text(encoding="utf-8-sig")
    span = find_block(existing, begin_marker, end_marker)
    if span is None:
        return "absent"
    start, end = span
    head, tail = existing[:start], existing[end:]
    if tail.startswith("\n"):
        tail = tail[1:]
    if head.endswith("\n\n"):
        head = head[:-1]
    if not dry_run:
        write_text_lf(target, head + tail)
    return "removed"

def merge_include_line(
    target: Path,
    include_line: str,
    *,
    force: bool = False,
    dry_run: bool = False,
    backup_root: "Path | None" = None,
) -> str:
    """Managed-block merge of a one-line ``@<path>`` include into a harness's
    entry file (``~/.claude/CLAUDE.md``, ``<project>/.claude/CLAUDE.md``,
    ``~/.gemini/GEMINI.md``), so the harness actually loads the store's
    ``AGENTS.md``.

    If the file already carries ``include_line`` ANYWHERE (a hand-written include,
    inside or outside a managed block), it is left untouched (branch
    "skipped (present)") -- the include is the point, not the markers. Otherwise
    the marker-wrapped line is merged like any block, APPENDED so an existing
    file's own content stays first."""
    line = include_line.strip()
    if not force and target.exists():
        existing = target.read_text(encoding="utf-8-sig")
        if any(ln.strip() == line for ln in existing.splitlines()):
            return "skipped (present)"
    block_text = "%s\n%s\n%s\n" % (BEGIN_MARKER, line, END_MARKER)
    return merge_block(
        target, block_text, force=force, dry_run=dry_run, backup_root=backup_root,
        append=True,
    )


def merge_context_block(target: Path, context_text: str, *, dry_run: bool = False) -> str:
    """Write an assembled context into ``target`` as a managed
    ``dotagents:context`` block: created if the file is absent, refreshed in
    place if the block is there, appended after the user's own content
    otherwise. Never a raw overwrite: the target may be a context SOURCE (a
    harness's own ``AGENTS.md``), and overwriting it would re-inline the
    previous run's output."""
    block_text = "%s\n%s\n%s\n" % (
        CONTEXT_BEGIN_MARKER, context_text.strip(), CONTEXT_END_MARKER,
    )
    return merge_block(
        target, block_text, dry_run=dry_run,
        begin_marker=CONTEXT_BEGIN_MARKER, end_marker=CONTEXT_END_MARKER, append=True,
    )


_backup_roots: "dict[Path, Path]" = {}


def timestamped_backup_root(dest: Path) -> Path:
    """``<dest>/install_backup/<timestamp>``, ONE per store per process: every
    adapter of an ``init --force`` run backs up into the same root, so
    :func:`_backup`'s never-overwrite rule sees the earlier copies."""
    key = Path(dest).resolve()
    if key not in _backup_roots:
        _backup_roots[key] = Path(dest) / "install_backup" / time.strftime("%Y%m%d-%H%M%S")
    return _backup_roots[key]
