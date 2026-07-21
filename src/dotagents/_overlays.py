"""Overlay support: copy an overlay's files in, and collect what it contributes
to the managed `AGENTS.md` block.

An overlay is a directory (optionally carrying an `overlay.toml` manifest) whose
files install to the same relative path in the destination. Two manifest keys are
read here:

* `routing` — lines appended to the core's "Load on demand" list.
* `rules` — overlay-relative paths to markdown files whose `- **…` bullet blocks
  are appended to "Always-on rules".

Dependency resolution (`requires`) is still deferred to a future
`dotagents overlays` subcommand.
"""

import re
import shutil
from pathlib import Path


def _strip_comments(text: str) -> str:
    """Drop whole-line `#` comments, leaving string contents untouched.

    Only line comments are removed, and only when `#` is the first non-space
    character -- enough for these manifests, and it cannot corrupt a `#` inside
    a quoted value the way a general strip-to-end-of-line would."""
    return "\n".join(
        ln for ln in text.splitlines() if not ln.lstrip().startswith("#")
    )


def _parse_string_array(text: str, key: str) -> "list[str]":
    """Read a top-level `key = [...]` array of strings from TOML source.

    A deliberately small reader rather than a TOML dependency: the floor is
    Python 3.9, where `tomllib` does not exist, and pulling in `tomli` to read
    two arrays would be a real dependency for a trivial need (D13). Handles the
    forms these manifests actually use -- `"..."` and `\"\"\"...\"\"\"`
    (multi-line), one or many per array."""
    # Single-line form first (incl. the empty `key = []`). It must be tried
    # before the multi-line form and must not cross a newline: `routing = []`
    # followed later by `rules = [...]` would otherwise let a greedy multi-line
    # match swallow the *next* array and report it as this key's value.
    m = re.search(r"(?m)^%s\s*=\s*\[([^\[\]\n]*)\]\s*$" % re.escape(key), text)
    if m is None:
        m = re.search(r"(?ms)^%s\s*=\s*\[(.*?)^\]" % re.escape(key), text)
        if m is None:
            return []
    body = m.group(1)
    # Triple-quoted first so its content is not re-matched as single-quoted.
    items = re.findall(r'"""(.*?)"""', body, re.DOTALL)
    remainder = re.sub(r'""".*?"""', "", body, flags=re.DOTALL)
    items += re.findall(r'"([^"\n]*)"', remainder)
    return [s.strip("\n") for s in items if s.strip()]


def read_manifest(overlay_dir: Path) -> "dict[str, object]":
    """Parse `overlay.toml`, returning at least name/routing/rules.

    A missing or unreadable manifest yields empty contributions -- an overlay is
    allowed to be just a directory of files."""
    path = overlay_dir / "overlay.toml"
    if not path.is_file():
        return {"name": overlay_dir.name, "routing": [], "rules": []}
    try:
        raw = _strip_comments(path.read_text(encoding="utf-8"))
    except OSError:
        return {"name": overlay_dir.name, "routing": [], "rules": []}
    name = re.search(r'(?m)^name\s*=\s*"([^"]*)"', raw)
    return {
        "name": name.group(1) if name else overlay_dir.name,
        "routing": _parse_string_array(raw, "routing"),
        "rules": _parse_string_array(raw, "rules"),
    }


def rule_blocks(overlay_dir: Path, rel_paths: "list[str]") -> "tuple[list[str], list[str]]":
    """Extract `- **…` bullet blocks from each referenced markdown file.

    Returns (blocks, warnings). Only the leading run of bullets is taken: a rules
    file may carry explanatory prose under a `## ` heading (engineering/rules.md
    documents *why* its rules are not in the base), and that must not be merged
    into the core. A path that does not exist is reported, not fatal -- a broken
    optional overlay must never block the base config landing."""
    blocks: "list[str]" = []
    warnings: "list[str]" = []
    for rel in rel_paths:
        src = overlay_dir / rel
        if not src.is_file():
            warnings.append("rules file not found: %s" % rel)
            continue
        text = src.read_text(encoding="utf-8")
        # Everything from the first bullet up to the first `## ` heading after it.
        start = re.search(r"(?m)^- \*\*", text)
        if start is None:
            warnings.append("no rules found in: %s" % rel)
            continue
        body = text[start.start():]
        end = re.search(r"(?m)^## ", body)
        if end is not None:
            body = body[: end.start()]
        blocks.append(body.rstrip())
    return blocks, warnings


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
