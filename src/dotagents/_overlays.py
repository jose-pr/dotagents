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

import os
import re
import shutil
import subprocess
from pathlib import Path


#: The name of an overlay's optional idempotent setup script, tried in order.
#: An extensionless ``setup`` (a shell/POSIX script) or a ``setup.py`` at the
#: overlay root. Presence of one file = opt-in; absence = nothing to run.
SETUP_SCRIPT_NAMES = ("setup", "setup.py")


def find_setup_script(overlay_dir: Path) -> "Path | None":
    """Return the overlay's setup script (``setup`` or ``setup.py`` at its root),
    or ``None`` if it ships none.

    The *installed* overlay dir is what should be handed in: setup runs against
    the copy under ``<scope>/overlays/<name>/`` with that as its cwd, so a script
    can reference its own sibling files by relative path."""
    for name in SETUP_SCRIPT_NAMES:
        candidate = overlay_dir / name
        if candidate.is_file():
            return candidate
    return None


def run_overlay_setup(
    overlay_dir: Path,
    name: str,
    *,
    agents_dir: Path,
    dry_run: bool,
    logger,
) -> "int | None":
    """Run an overlay's idempotent ``setup`` script if it ships one.

    Returns the script's exit code, or ``None`` when the overlay has no setup
    script (nothing to run -- not an error). Presence of ``setup``/``setup.py`` at
    the overlay root is the opt-in; the runner never second-guesses a script the
    user chose to install (see the overlay-authoring contract).

    **Idempotency is the overlay author's contract**: the script must be safe to
    run on every ``add``/``sync`` (check-then-act). The runner only invokes it.

    Invocation, matching ``_link.py``'s hook style (pure stdlib subprocess):

    * **cwd** = the installed overlay dir, so the script sees its own files.
    * **env** carries ``DOTAGENTS_AGENTS_DIR`` = the resolved store path (D58
      configurable store), so the script never hardcodes ``~/.agents``. It also
      gets ``DOTAGENTS_OVERLAY_DIR`` = its own installed dir for convenience.
    * a ``.py`` script runs under the current interpreter; an extensionless
      ``setup`` runs directly, or via ``sh`` on Windows where it isn't executable.

    A non-zero exit is surfaced (returned) so the caller can raise a clear error
    -- never a silent skip. Never prints ``DOTAGENTS_*`` values (Leakage)."""
    script = find_setup_script(overlay_dir)
    if script is None:
        return None

    logger.info("running setup for %s (%s)", name, script.name)
    if dry_run:
        logger.info("[dry-run] would run %s in %s", script.name, overlay_dir)
        return 0

    env = dict(os.environ)
    env["DOTAGENTS_AGENTS_DIR"] = str(agents_dir)
    env["DOTAGENTS_OVERLAY_DIR"] = str(overlay_dir)

    if script.suffix.lower() == ".py":
        import sys
        cmd = [sys.executable, str(script)]
    else:
        cmd = [str(script)]
        if os.name == "nt":
            # An extensionless script is not directly executable on Windows.
            cmd = ["sh"] + cmd

    try:
        res = subprocess.run(cmd, cwd=str(overlay_dir), env=env)
    except OSError as exc:
        logger.error("setup for %s failed to start: %s", name, exc)
        return 1
    if res.returncode != 0:
        logger.error("setup for %s exited %d", name, res.returncode)
    return res.returncode


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


# Default merge priority for an overlay that declares none (plan 02). Lower sorts
# earlier. 500 leaves generous headroom on both sides for overlays that want to
# sort before (0..499) or after (501..) the unprioritized default.
DEFAULT_PRIORITY = 500


def _parse_priority(text: str) -> int:
    """Read a top-level `priority = <int>` from TOML source (plan 02).

    Same minimal-reader rationale as `_parse_string_array`: no `tomllib` on the
    3.9 floor. Missing/unparseable -> DEFAULT_PRIORITY."""
    m = re.search(r"(?m)^priority\s*=\s*(-?\d+)\s*$", text)
    if m is None:
        return DEFAULT_PRIORITY
    try:
        return int(m.group(1))
    except ValueError:
        return DEFAULT_PRIORITY


def read_manifest(overlay_dir: Path) -> "dict[str, object]":
    """Parse `overlay.toml`, returning at least name/routing/rules/priority.

    A missing or unreadable manifest yields empty contributions -- an overlay is
    allowed to be just a directory of files."""
    path = overlay_dir / "overlay.toml"
    if not path.is_file():
        return {"name": overlay_dir.name, "routing": [], "rules": [], "priority": DEFAULT_PRIORITY}
    try:
        raw = _strip_comments(path.read_text(encoding="utf-8"))
    except OSError:
        return {"name": overlay_dir.name, "routing": [], "rules": [], "priority": DEFAULT_PRIORITY}
    name = re.search(r'(?m)^name\s*=\s*"([^"]*)"', raw)
    return {
        "name": name.group(1) if name else overlay_dir.name,
        "routing": _parse_string_array(raw, "routing"),
        "rules": _parse_string_array(raw, "rules"),
        "priority": _parse_priority(raw),
    }


def overlay_sort_key(overlay_dir: Path) -> "tuple[int, str]":
    """The `(priority, name)` merge-order key for one overlay (plan 02 / D68).

    Lower `priority` (default `DEFAULT_PRIORITY`, 500) sorts earlier; a numerically
    higher-priority overlay therefore lands *later* in the merged AGENTS.md block and
    wins on conflict -- the same "lower sorts earlier / higher wins" convention
    `_context.py` uses for its own priority ordering. `name` is the stable tiebreaker
    for equal-priority overlays, keyed off the manifest `name` (falling back to the
    directory name) so output is deterministic regardless of input order."""
    manifest = read_manifest(overlay_dir)
    try:
        priority = int(manifest.get("priority", DEFAULT_PRIORITY))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        priority = DEFAULT_PRIORITY
    name = str(manifest.get("name", overlay_dir.name))
    return (priority, name)


def sort_overlays_by_priority(overlay_dirs: "list[Path]") -> "list[Path]":
    """Return `overlay_dirs` sorted by `(priority, name)` (plan 02 / D68).

    Deterministic regardless of the caller's order: `install`/`overlays add` pass
    overlays in add-invocation order and `overlays sync` in alphabetical discovery
    order, but the *merged* block must not depend on either -- a high-priority
    overlay's lines must always land last. See `overlay_sort_key` for the convention.
    """
    return sorted(overlay_dirs, key=overlay_sort_key)


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


def install_overlay_dir(overlay_dir: Path, dest_overlay_dir: Path, dry_run: bool):
    """Install an overlay as a *directory* under `<scope>/overlays/<name>/` (the
    "install model = overlay-dirs" decision): the overlay is the discoverable unit.

    Copies every file the overlay ships (minus its manifest / caches -- see
    `overlay_files`) into `dest_overlay_dir`, create-if-absent so re-adding an
    overlay never clobbers a file the user hand-edited inside the installed copy
    (additive/no-clobber, mirroring `apply_overlay`). The overlay's own
    `overlay.toml` is copied too so a later `sync`/`list` can re-read its manifest
    from the installed copy. Returns (copied, skipped) counts and log lines.
    """
    copied = skipped = 0
    lines = []
    # Ship the manifest alongside the files so the installed dir is self-describing.
    sources = list(overlay_files(overlay_dir))
    manifest = overlay_dir / "overlay.toml"
    if manifest.is_file():
        sources.append(manifest)
    for src in sources:
        rel = src.relative_to(overlay_dir)
        target = dest_overlay_dir / rel
        if target.exists():
            lines.append("skip (exists): %s" % rel.as_posix())
            skipped += 1
            continue
        lines.append("install: %s" % rel.as_posix())
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(target))
        copied += 1
    return copied, skipped, lines


def merge_overlay_rules(agents_md: Path, overlay_dir: Path, dry_run: bool, logger):
    """Fold one overlay's D59 routing + rules into an already-installed AGENTS.md's
    managed block, in place (additive).

    `install` composes these into the base text *before* first write; an incremental
    `overlays add` instead re-composes the *existing* installed file's managed block.
    Extracts the current block (between the dotagents markers), runs the same
    `_compose_block` fold used at install time over just that block text, and writes
    the result back between the markers -- content outside the block is never
    touched. A no-op (returns False) when the overlay contributes nothing, the file
    is absent, or it carries no managed block.
    """
    from dotagents._merge import BEGIN_MARKER, END_MARKER
    from dotagents.cli import _compose_block

    manifest = read_manifest(overlay_dir)
    if not manifest["routing"] and not manifest["rules"]:
        return False
    if not agents_md.is_file():
        logger.warning(
            "no installed AGENTS.md at %s; overlay rules/routing not merged", agents_md
        )
        return False
    existing = agents_md.read_text(encoding="utf-8")
    if BEGIN_MARKER not in existing or END_MARKER not in existing:
        logger.warning(
            "AGENTS.md has no dotagents managed block; overlay rules/routing not "
            "merged (run `dotagents init` first)"
        )
        return False
    start = existing.index(BEGIN_MARKER)
    end = existing.index(END_MARKER) + len(END_MARKER)
    block = existing[start:end]
    merged = _compose_block(block, [overlay_dir], logger)
    if merged == block:
        return False
    new_text = existing[:start] + merged + existing[end:]
    if not dry_run:
        agents_md.write_text(new_text, encoding="utf-8")
    return True


def recompose_overlay_block(
    agents_md: Path,
    base_block: str,
    overlay_dirs: "list[Path]",
    dry_run: bool,
    logger,
) -> bool:
    """Rebuild AGENTS.md's managed block from the *pristine* base over ALL installed
    overlays in `(priority, name)` order (plan 02 / D68), in place.

    This is what makes single-`overlays add` positioning correct: a lone `add` merges
    one overlay, but its rules must land in the right slot *relative to overlays
    already present in the block*. Incremental append (`merge_overlay_rules`) can only
    tack the newcomer on at the end, so a high-priority overlay added *after* a
    low-priority one would wrongly sort last. Recomposing from the base each time
    sidesteps that: `base_block` is the freshly-read base overlay's managed block (no
    overlay content), `overlay_dirs` is every installed overlay in the scope, and
    `_compose_block` folds them in sorted order -- so the block is a pure function of
    *which* overlays are installed, never *when* each was added.

    Only the managed block (between the dotagents markers) is rewritten; content
    outside the markers is untouched. Returns True if the file changed, False on a
    no-op (nothing to merge, file/markers absent, or block already correct).
    """
    from dotagents._merge import BEGIN_MARKER, END_MARKER
    from dotagents.cli import _compose_block

    if not agents_md.is_file():
        logger.warning(
            "no installed AGENTS.md at %s; overlay rules/routing not merged", agents_md
        )
        return False
    existing = agents_md.read_text(encoding="utf-8")
    if BEGIN_MARKER not in existing or END_MARKER not in existing:
        logger.warning(
            "AGENTS.md has no dotagents managed block; overlay rules/routing not "
            "merged (run `dotagents init` first)"
        )
        return False

    # Compose over the pristine base block, not the current (already-merged) one, so
    # every install of the same overlay set yields identical output.
    b_start = base_block.index(BEGIN_MARKER)
    b_end = base_block.index(END_MARKER) + len(END_MARKER)
    pristine = base_block[b_start:b_end]
    merged = _compose_block(pristine, overlay_dirs, logger)

    start = existing.index(BEGIN_MARKER)
    end = existing.index(END_MARKER) + len(END_MARKER)
    if existing[start:end] == merged:
        return False
    new_text = existing[:start] + merged + existing[end:]
    if not dry_run:
        agents_md.write_text(new_text, encoding="utf-8")
    return True
