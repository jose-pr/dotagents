#!/usr/bin/env python3
"""Scan a repo's *tracked* files for private agent-plan leakage.

Usage: py -3.12 ~/.agents/tools/leak_check.py <repo_path>

Patterns: references to .agents/, "Phase N" plan phrasing, and every plan basename
harvested from <repo>/.agents/plans/ (incl. completed/). The bare filename AGENTS.md
is NOT a pattern: post-D40 it is a committed, public file that tracked config/docs
legitimately reference; a genuinely private notes ref is .agents/AGENTS.md, already
caught by the .agents/ pattern. Findings are
human-judged: fix the file or consciously accept it. Exit 1 on any hit.
Runs on Python 3.9+, stdlib only. See flows/REPO.md (release discipline).
"""
import re
import subprocess
import sys
from pathlib import Path

SKIP_NAMES = {".gitignore"}  # legitimately names agent artifacts


def main(argv):
    if len(argv) != 1:
        sys.stderr.write(__doc__)
        return 2
    repo = Path(argv[0]).resolve()
    print("repo: %s" % repo)

    patterns = [".agents/"]  # NOT "AGENTS.md" — it's a public file post-D40 (see module docstring)
    plans_dir = repo / ".agents" / "plans"
    if plans_dir.is_dir():
        for p in plans_dir.rglob("*.md"):
            if p.stem.lower() not in ("readme", "index"):
                patterns.append(p.name)
    phase_re = re.compile(r"\bPhase [0-9]")

    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        capture_output=True, check=True,
    )
    tracked = [f for f in out.stdout.decode("utf-8").split("\0") if f]

    hits = 0
    for rel in tracked:
        path = repo / rel
        if path.name in SKIP_NAMES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for pat in patterns:
                if pat in line:
                    print("%s:%d: %r" % (rel, lineno, pat))
                    hits += 1
            if phase_re.search(line):
                print("%s:%d: 'Phase N'" % (rel, lineno))
                hits += 1
    print("FAIL (%d hits)" % hits if hits else "PASS")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
