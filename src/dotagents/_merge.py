"""Managed-block merge for `init`'s AGENTS.md/CLAUDE.md (D-init-merge).

`init` must never clobber a user-customized AGENTS.md. Content owned by dotagents
is delimited by literal marker lines and treated as a block *within* the file:
create if missing, prepend if present-without-markers, refresh-in-place if the
markers are already there. Detection is by marker presence only, never by prose
matching, so it survives user reformatting.

A marker counts only when it is a LINE of its own (surrounding whitespace
allowed). The base `AGENTS.md` itself *mentions* the marker names in prose
("everything between the `dotagents:begin`/`dotagents:end` markers is managed"),
and a substring match on such a mention replaced the sentence around it with
the block while leaving the real block in place -- two managed blocks and a
mangled sentence (review 2026-09-09).
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


def find_block(
    text: str, begin_marker: str = BEGIN_MARKER, end_marker: str = END_MARKER
) -> "tuple[int, int] | None":
    """``(start, end)`` character span of the managed block in ``text`` -- from
    the first BEGIN marker line through the first END marker line after it --
    or ``None`` when there is no block. A BEGIN with no END after it is not a
    block (the caller decides whether that is an error)."""
    begin = _marker_re(begin_marker).search(text)
    if begin is None:
        return None
    end = _marker_re(end_marker).search(text, begin.end())
    if end is None:
        return None
    return begin.start(), end.end()


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


def _backup(target: Path, backup_root: Path) -> None:
    backup_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup_root / target.name)


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
    "created" / "block-inserted" / "block-refreshed" / "replaced (--force, backed up)".

    Never overwrites content outside the markers unless `force` is True (then
    the whole file is replaced, after being backed up to `backup_root` if the
    target pre-exists).

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
        existed = target.exists()
        if existed and backup_root is not None:
            if not dry_run:
                _backup(target, backup_root)
        if not dry_run:
            write_text_lf(target, block_source_text)
        return "replaced (--force, backed up)" if existed else "created"

    if not target.exists():
        if not dry_run:
            write_text_lf(target, block_source_text)
        return "created"

    existing = target.read_text(encoding="utf-8")

    span = find_block(existing, begin_marker, end_marker)
    if span is not None:
        start, end = span
        new_text = existing[:start] + block + existing[end:]
        if new_text == existing:
            return "skipped (present)"
        if not dry_run:
            write_text_lf(target, new_text)
        return "block-refreshed"
    if _marker_re(begin_marker).search(existing):
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
        existing = target.read_text(encoding="utf-8")
        if any(ln.strip() == line for ln in existing.splitlines()):
            return "skipped (present)"
    block_text = "%s\n%s\n%s\n" % (BEGIN_MARKER, line, END_MARKER)
    return merge_block(
        target, block_text, force=force, dry_run=dry_run, backup_root=backup_root,
        append=True,
    )


def merge_claude_md(
    target: Path,
    block_source_text: str,
    *,
    force: bool = False,
    dry_run: bool = False,
    backup_root: "Path | None" = None,
) -> str:
    """Same managed-block rule, but for the CLAUDE.md one-liner: if the file
    already contains an `@AGENTS.md` reference (any form), leave it untouched
    (branch "skipped (present)") instead of requiring exact marker match --
    a bare `@AGENTS.md` file written by hand still counts as "present"."""
    if not force and target.exists():
        existing = target.read_text(encoding="utf-8")
        if "@AGENTS.md" in existing:
            return "skipped (present)"
    return merge_block(
        target, block_source_text, force=force, dry_run=dry_run, backup_root=backup_root
    )


def merge_context_block(target: Path, context_text: str, *, dry_run: bool = False) -> str:
    """Write an assembled context into ``target`` as a managed
    ``dotagents:context`` block: created if the file is absent, refreshed in
    place if the block is there, appended after the user's own content
    otherwise. Never a raw overwrite -- ``context --write-agent`` used to
    replace the whole file, which for Codex was the store's ``AGENTS.md`` (a
    context SOURCE), so every run re-inlined the previous run's output."""
    block_text = "%s\n%s\n%s\n" % (
        CONTEXT_BEGIN_MARKER, context_text.strip(), CONTEXT_END_MARKER,
    )
    return merge_block(
        target, block_text, dry_run=dry_run,
        begin_marker=CONTEXT_BEGIN_MARKER, end_marker=CONTEXT_END_MARKER, append=True,
    )


def timestamped_backup_root(dest: Path) -> Path:
    return dest / "install_backup" / time.strftime("%Y%m%d-%H%M%S")
