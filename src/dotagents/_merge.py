"""Managed-block merge for `init`'s AGENTS.md/CLAUDE.md (D-init-merge).

`init` must never clobber a user-customized AGENTS.md. Content owned by dotagents
is delimited by literal marker lines and treated as a block *within* the file:
create if missing, prepend if present-without-markers, refresh-in-place if the
markers are already there. Detection is by marker presence only, never by prose
matching, so it survives user reformatting.
"""

import shutil
import time
from pathlib import Path

BEGIN_MARKER = "<!-- dotagents:begin -->"
END_MARKER = "<!-- dotagents:end -->"


def _extract_block(text: str) -> str:
    """Return the managed block's inner text (without the marker lines) from
    a skeleton/template file that is itself fully marker-wrapped."""
    start = text.index(BEGIN_MARKER)
    end = text.index(END_MARKER)
    return text[start : end + len(END_MARKER)]


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
) -> str:
    """Merge `block_source_text` (a fully marker-wrapped skeleton file's
    contents) into `target`, returning the branch taken:
    "created" / "block-inserted" / "block-refreshed" / "replaced (--force, backed up)".

    Never overwrites content outside the markers unless `force` is True (then
    the whole file is replaced, after being backed up to `backup_root` if the
    target pre-exists).
    """
    block = _extract_block(block_source_text)

    if force:
        existed = target.exists()
        if existed and backup_root is not None:
            if not dry_run:
                _backup(target, backup_root)
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(block_source_text, encoding="utf-8")
        return "replaced (--force, backed up)" if existed else "created"

    if not target.exists():
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(block_source_text, encoding="utf-8")
        return "created"

    existing = target.read_text(encoding="utf-8")

    if BEGIN_MARKER in existing and END_MARKER in existing:
        start = existing.index(BEGIN_MARKER)
        end = existing.index(END_MARKER) + len(END_MARKER)
        new_text = existing[:start] + block + existing[end:]
        if new_text == existing:
            return "skipped (present)"
        if not dry_run:
            target.write_text(new_text, encoding="utf-8")
        return "block-refreshed"

    new_text = block + "\n\n" + existing
    if not dry_run:
        target.write_text(new_text, encoding="utf-8")
    return "block-inserted"


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


def timestamped_backup_root(dest: Path) -> Path:
    return dest / "install_backup" / time.strftime("%Y%m%d-%H%M%S")
