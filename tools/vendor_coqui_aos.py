"""Copy the ``coqui_aos`` package (the Agent Overlay Specification reader) from a coqui-aos checkout
into ``src/dotagents/_vendor/coqui_aos/``, at a given commit, and record what was copied.

    python tools/vendor_coqui_aos.py --source ../coqui-aos --commit <sha>

Only what runs is copied: the package's modules and its licence. The copy is refused when the
checkout is not at ``--commit`` or has uncommitted changes under ``python/``, and when files
already vendored differ from what ``VENDORED.json`` recorded (a local edit; ``--force`` replaces
it). ``--check`` only verifies the vendored files against ``VENDORED.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "src" / "dotagents" / "_vendor" / "coqui_aos"
RECORD = DEST / "VENDORED.json"
REPOSITORY = "https://github.com/Boriken-Dev/coqui-aos"


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def vendored_files() -> "dict[str, bytes]":
    """Every file in the vendored directory but the record and the note, by name."""
    return {
        path.name: path.read_bytes()
        for path in sorted(DEST.iterdir())
        if path.is_file() and path.name not in ("VENDORED.json", "VENDORED.md")
    }


def edited() -> "list[str]":
    """Files that differ from the record, were added or are missing."""
    if not RECORD.is_file():
        return []
    recorded = json.loads(RECORD.read_text(encoding="utf-8"))["files"]
    have = {name: digest(data) for name, data in vendored_files().items()}
    return sorted(name for name in set(recorded) | set(have) if recorded.get(name) != have.get(name))


def git(source: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(source), *args], capture_output=True, text=True, check=True)
    return done.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", type=Path, help="a coqui-aos checkout")
    parser.add_argument("--commit", help="the commit the checkout must be at (full or abbreviated)")
    parser.add_argument("--check", action="store_true", help="verify the vendored files against the record")
    parser.add_argument("--force", action="store_true", help="replace local edits in the vendored files")
    args = parser.parse_args()
    bad = edited()
    if args.check:
        for name in bad:
            print("edited, added or missing: %s" % name, file=sys.stderr)
        return 1 if bad else 0
    if args.source is None or args.commit is None:
        parser.error("--source and --commit are required")
    if bad and not args.force:
        print("refusing: local changes in the vendored files: %s (--force replaces them)" % ", ".join(bad),
              file=sys.stderr)
        return 1
    head = git(args.source, "rev-parse", "HEAD")
    if not head.startswith(args.commit):
        print("refusing: the checkout is at %s, not %s" % (head[:12], args.commit), file=sys.stderr)
        return 1
    if git(args.source, "status", "--porcelain", "--", "python"):
        print("refusing: uncommitted changes under python/ in the checkout", file=sys.stderr)
        return 1
    package = args.source / "python" / "src" / "coqui_aos"
    files = {path.name: path.read_bytes() for path in sorted(package.glob("*.py"))}
    files["LICENSE"] = (args.source / "python" / "LICENSE").read_bytes()
    DEST.mkdir(parents=True, exist_ok=True)
    for path in vendored_files():
        if path not in files:
            (DEST / path).unlink()
    for name, data in files.items():
        with open(str(DEST / name), "wb") as fh:
            fh.write(data)
    version = [line for line in (args.source / "python" / "pyproject.toml").read_text(encoding="utf-8").splitlines()
               if line.startswith("version")][0].split('"')[1]
    record = {"repository": REPOSITORY, "commit": head, "path": "python/src/coqui_aos", "version": version,
              "files": {name: digest(data) for name, data in files.items()}}
    with open(str(RECORD), "w", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(record, indent=2) + "\n")
    print("vendored %d files at %s" % (len(files), head[:12]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
