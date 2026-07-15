"""PathSyncer wrapper reproducing the legacy install.py backup/copy report.

Uses pathlib_next's PathSyncer (checksum-driven one-way tree sync) instead of
a hand-rolled read_bytes()==read_bytes() loop. On a would-overwrite of changed
content, the hook backs the old target file up to
<dest>/install_backup/<timestamp>/ before the sync writes, then counts
installed/backed-up/unchanged exactly as the legacy installer's final line.
"""

import shutil
from pathlib import Path

from pathlib_next import LocalPath
from pathlib_next.utils.sync import PathSyncer, SyncEvent


class _Counts:
    def __init__(self):
        self.installed = 0
        self.backed_up = 0
        self.unchanged = 0


def sync_payload(
    src: Path,
    dest: Path,
    entries: "list[str]",
    *,
    dry_run: bool = False,
) -> "tuple[_Counts, list[str]]":
    """Sync a fixed list of top-level payload entries (files or dirs) from
    `src` into `dest`, matching the legacy installer's PAYLOAD-list behavior.
    """
    from dotagents._merge import timestamped_backup_root

    dest = Path(dest)
    backup_root = timestamped_backup_root(dest)
    counts = _Counts()
    lines: "list[str]" = []
    dest_local = LocalPath(dest)
    copied_ids = set()

    def hook(source, target, event: SyncEvent, is_dry_run: bool):
        if event is SyncEvent.Copy:
            rel = target.path.relative_to(dest_local)
            existed = target.exists()
            if existed:
                lines.append("backup:  %s" % rel.as_posix())
                if not is_dry_run:
                    bak = backup_root / str(rel)
                    bak.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(target.path), str(bak))
                counts.backed_up += 1
            lines.append("install: %s" % rel.as_posix())
            counts.installed += 1
            copied_ids.add(id(target))
        elif event is SyncEvent.Synced and source.is_file():
            if target.exists() and id(target) not in copied_ids:
                counts.unchanged += 1

    syncer = PathSyncer(hook=hook)

    for entry in entries:
        source_path = Path(src) / entry
        target_path = dest / entry
        if not source_path.exists():
            continue
        if not dry_run:
            target_path.parent.mkdir(parents=True, exist_ok=True)
        syncer.sync(LocalPath(source_path), LocalPath(target_path), dry_run=dry_run)

    return counts, lines, backup_root
