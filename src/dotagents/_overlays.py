"""Minimal overlay support: copy an overlay's files into a destination.

An overlay is a directory (optionally carrying an `overlay.toml` manifest) whose
files install to the same relative path in the destination. This module keeps
things deliberately small — no dependency resolution, no AGENTS.md routing merge.
Those belong to a future `dotagents overlays` subcommand; the manifests in the
repo's example overlays already carry `requires`/`routing` for it to use.

For now `install --overlays <dir>` just copies that overlay's files in.
"""

import shutil
from pathlib import Path


def overlay_files(overlay_dir: Path) -> "list[Path]":
    """Files an overlay installs (everything except its manifest / caches)."""
    files = []
    for p in sorted(overlay_dir.rglob("*")):
        if p.is_file() and p.name != "overlay.toml" and "__pycache__" not in p.parts:
            files.append(p)
    return files


def apply_overlay(overlay_dir: Path, dest: Path, dry_run: bool):
    """Copy `overlay_dir`'s files into `dest` (create-if-absent; never clobber
    an existing file). Returns (copied, skipped) counts and per-file log lines."""
    copied = skipped = 0
    lines = []
    for src in overlay_files(overlay_dir):
        rel = src.relative_to(overlay_dir)
        target = dest / rel
        if target.exists():
            lines.append("skip (exists): %s" % rel.as_posix())
            skipped += 1
            continue
        lines.append("overlay: %s" % rel.as_posix())
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(target))
        copied += 1
    return copied, skipped, lines
