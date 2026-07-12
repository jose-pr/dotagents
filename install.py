#!/usr/bin/env python3
"""Install (or update) this agent config into ~/.agents.

Usage: python install.py [--dest <path>] [--dry-run]

Copies the payload (AGENTS.md, CLAUDE.md, antigravity.md, flows/, kb/,
references/, tools/) from this checkout into the destination (default
~/.agents). Files that would be overwritten with different content are first
backed up to <dest>/install_backup/<timestamp>/, so a customized install is
never silently clobbered. Everything else in the destination (harness state,
plans, credentials) is left untouched. Runs on Python 3.9+, stdlib only.

After installing, point your agent runner at the config: for Claude Code,
~/.claude/CLAUDE.md containing "@AGENTS.md" does it (the payload's CLAUDE.md
is that one-liner, installed at <dest>/CLAUDE.md for runners that read it
there). Finishes by running tools/audit_config.py against the destination.
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent
PAYLOAD = ["AGENTS.md", "CLAUDE.md", "antigravity.md",
           "flows", "kb", "references", "tools"]


def iter_files(entry):
    path = SRC / entry
    if path.is_file():
        yield path
    else:
        for p in sorted(path.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                yield p


def main(argv):
    dest = Path.home() / ".agents"
    if "--dest" in argv:
        dest = Path(argv[argv.index("--dest") + 1]).expanduser().resolve()
    dry = "--dry-run" in argv
    backup_root = dest / "install_backup" / time.strftime("%Y%m%d-%H%M%S")

    copied = backed_up = unchanged = 0
    for entry in PAYLOAD:
        for src in iter_files(entry):
            rel = src.relative_to(SRC)
            target = dest / rel
            if target.is_file():
                if target.read_bytes() == src.read_bytes():
                    unchanged += 1
                    continue
                print("backup:  %s" % rel.as_posix())
                if not dry:
                    bak = backup_root / rel
                    bak.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, bak)
                backed_up += 1
            print("install: %s" % rel.as_posix())
            if not dry:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
            copied += 1
    print("%d installed, %d backed up (%s), %d unchanged%s"
          % (copied, backed_up,
             backup_root if backed_up and not dry else "none",
             unchanged, " [dry-run]" if dry else ""))
    if dry:
        return 0
    return subprocess.call([sys.executable,
                            str(dest / "tools" / "audit_config.py"),
                            "--root", str(dest)])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
