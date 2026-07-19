#!/usr/bin/env python3
"""Scan a repo's *tracked* files and commit messages for private agent leakage.

Usage: py -3.12 ~/.agents/tools/leak_check.py <repo_path>

Tracked files -- patterns: references to .agents/, AGENTS.md, "Phase N" plan
phrasing, and every plan basename harvested from <repo>/.agents/plans/ (incl.
completed/).

Commit messages -- agent-session trailers/URLs (a `Claude-Session:` trailer or a
`claude.ai/code/session` link) that must never reach a public repo: the link
exposes a session id, and the trailer is auto-added by the agent harness, so it
slips in unless a gate catches it. Scanned across the current branch's history.

Findings are human-judged: fix the file/message or consciously accept it. Exit 1
on any hit. Runs on Python 3.9+, stdlib only. See flows/REPO.md (release discipline).
"""
import re
import subprocess
import sys
from pathlib import Path

SKIP_NAMES = {".gitignore"}  # legitimately names agent artifacts

# Commit-message leaks to catch, WITHOUT false-positiving on a message that merely
# documents them (this tool's own commits, filter-branch remediation snippets, etc.):
#   * the real git trailer -- a line that STARTS with `Claude-Session:` (a backticked
#     `Claude-Session:` mid-sentence starts with a backtick, so it won't match), and
#   * an actual session URL -- `claude.ai/code/session_<id>` (the bare phrase
#     "claude.ai/code/session" with no id is a mention, not a leak).
COMMIT_MSG_CHECKS = [
    ("Claude-Session: trailer", re.compile(r"^\s*Claude-Session:", re.IGNORECASE)),
    ("session URL", re.compile(r"claude\.ai/code/session_\w", re.IGNORECASE)),
]


def scan_commit_messages(repo: Path) -> int:
    """Flag any commit whose message carries an agent-session trailer or a real
    session URL. Scans the current branch's history (HEAD); returns the hit count."""
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%H%x1f%B%x1e", "HEAD"],
        capture_output=True, check=True,
    )
    hits = 0
    for rec in out.stdout.decode("utf-8", "replace").split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, _, body = rec.partition("\x1f")
        for lineno, line in enumerate(body.splitlines(), 1):
            for label, rx in COMMIT_MSG_CHECKS:
                if rx.search(line):
                    print("commit %s:%d: %s" % (sha[:12], lineno, label))
                    hits += 1
    return hits


def main(argv):
    if len(argv) != 1:
        sys.stderr.write(__doc__)
        return 2
    repo = Path(argv[0]).resolve()
    print("repo: %s" % repo)

    patterns = [".agents/", "AGENTS.md"]
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

    hits += scan_commit_messages(repo)

    print("FAIL (%d hits)" % hits if hits else "PASS")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
