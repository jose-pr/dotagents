"""Overlay support: the :class:`Overlay` type, plus the multi-overlay recompose.

An overlay is a directory (optionally carrying an `overlay.toml` manifest) whose
files install to the same relative path in the destination. Everything that
depends only on ONE overlay -- its name rules, its manifest, its setup script,
its files, what it contributes to the managed `AGENTS.md` block -- is a method or
property of :class:`Overlay`. Two manifest keys are read:

* `routing` — lines appended to the core's "Load on demand" list.
* `rules` — overlay-relative paths to markdown files whose `- **…` bullet blocks
  are appended to "Always-on rules".

:func:`recompose_overlay_block` is the one operation over a *set* of overlays and
stays a module function.

Dependency resolution (`requires`) is still deferred to a future
`dotagents overlays` subcommand.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Optional

# Default merge priority for an overlay that declares none (plan 02). Lower sorts
# earlier. 500 leaves generous headroom on both sides for overlays that want to
# sort before (0..499) or after (501..) the unprioritized default.
DEFAULT_PRIORITY = 500


# --------------------------------------------------------------------------- #
# Manifest text parsing (pure functions over TOML source; no overlay needed).
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# The overlay itself.
# --------------------------------------------------------------------------- #


class Overlay:
    """One overlay: a directory whose NAME identifies it and whose files install
    as a unit under ``<scope>/overlays/<name>/``.

    Construct it from the overlay's directory (a source dir or an installed
    dir -- both are overlays). Nothing is read at construction; every method
    reads the directory when called, so an ``Overlay`` made before its files
    land still sees them afterwards. The name-only rules (:meth:`is_valid_name`,
    :meth:`normalize_name`, :meth:`root_var_for`) are static so a caller holding
    just a name -- ``overlays add <name>`` -- uses the same rule as one holding a
    directory.
    """

    #: A directory under ``overlays/`` is an overlay IFF its name matches this: a
    #: leading ASCII letter, then ASCII letters/digits/``_``/``.``/``-``. A dot is
    #: allowed MID-name (``foo.bar``, ``v1.2``) but not as the first char, so
    #: ``.git``/``.hidden`` are excluded; a leading underscore (``__pycache__``) and
    #: a leading digit (``2fast``) are excluded too. One shared rule for
    #: :meth:`discover` (which backs ``_scope.discover_overlays``, the contract-A
    #: overlay walk in ``_resolve.py`` and the ``env`` overlay roots) and
    #: ``overlays add`` (D84). (Whether a path IS a directory is checked separately
    #: with ``is_dir()``, which follows symlinks -- a symlink-to-dir is a valid
    #: overlay.)
    NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")

    #: The optional manifest an overlay may carry at its root.
    MANIFEST_NAME = "overlay.toml"

    #: An overlay's optional idempotent setup script, tried in preference order.
    #: ``setup.py`` is the **recommended, OS-agnostic** form: it runs under the same
    #: interpreter that runs dotagents, so it works on every platform (the ``net``
    #: overlay uses it). The extensionless ``setup`` (a POSIX shell script) is a
    #: **legacy fallback**, kept for existing overlays but discouraged -- a shell
    #: script isn't portable to Windows without a shell. When an overlay ships both,
    #: ``setup.py`` wins (it is first in this tuple). Presence of one file = opt-in;
    #: absence = nothing to run.
    SETUP_SCRIPT_NAMES = ("setup.py", "setup")

    DEFAULT_PRIORITY = DEFAULT_PRIORITY

    __slots__ = ("path",)

    def __init__(self, path: "str | os.PathLike[str]"):
        self.path = Path(path)

    # -- identity: the plain object protocol ---------------------------------

    def __repr__(self) -> str:
        return "Overlay(%r)" % str(self.path)

    def __fspath__(self) -> str:
        return str(self.path)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Overlay) and self.path == other.path

    def __hash__(self) -> int:
        return hash(self.path)

    # -- name rules (static: usable with only a name in hand) -----------------

    @staticmethod
    def is_valid_name(name: str) -> bool:
        """A dir under ``overlays/`` is an overlay iff its name matches
        :data:`NAME_RE`: a leading ASCII letter, then ASCII letters/digits/``_``/
        ``.``/``-``. Dots are allowed mid-name (``foo.bar``, ``v1.2``) but NOT as
        the first char, so ``.git``/``.hidden`` and ``__pycache__`` (leading ``_``)
        and ``2fast`` (leading digit) are excluded. The contract-A level names
        (``user``, ``project``, ``system``, ``project-root``, ``default``,
        ``overlay`` -- :data:`dotagents._resolve.LEVEL_NAMES`) are reserved: an
        overlay's dir name is its level label in the walk, and one of these
        would collide with the per-level filename keys."""
        from dotagents._resolve import LEVEL_NAMES

        return bool(Overlay.NAME_RE.match(name)) and name.lower() not in LEVEL_NAMES

    @staticmethod
    def normalize_name(name: str) -> str:
        """THE canonical overlay name: lowercase, ``_`` -> ``-`` (precursor rule).

        Every place that refers to an overlay by name goes through this one
        function, so ``My_Overlay`` and ``my-overlay`` are one overlay everywhere:
        ``overlays add`` installs to ``<scope>/overlays/<normalize_name(name)>/``
        and resolves the source dir by the same name, and :meth:`root_var_for`
        derives the ``<NAME>_OVERLAY_ROOT`` env var from it. Dots are kept
        (``v1.2`` stays ``v1.2``): they are legal in an overlay dir name
        (:meth:`is_valid_name`) and are part of the install path."""
        return name.lower().replace("_", "-")

    @staticmethod
    def root_var_for(name: str) -> str:
        """The env-var name carrying an overlay's installed root: ``<NAME>_OVERLAY_ROOT``.

        ``NAME`` is :meth:`normalize_name` of the overlay upper-cased, with ``-``
        (and a mid-name ``.``, which no shell variable can carry) turned into
        ``_``: ``my-overlay`` / ``My_Overlay`` -> ``MY_OVERLAY_OVERLAY_ROOT``,
        ``v1.2`` -> ``V1_2_OVERLAY_ROOT``. Because it starts from the same
        normalized name the install dir uses, the variable for ``overlays/<n>/``
        is always ``root_var_for(n)`` (:attr:`root_var` on the instance). Both
        consumers go through here: ``dotagents env`` EMITS the variable and
        ``dotagents context`` expands the ``<NAME_OVERLAY_ROOT>`` placeholder to
        the same path."""
        return re.sub(r"[^A-Z0-9]", "_", Overlay.normalize_name(name).upper()) + "_OVERLAY_ROOT"

    # -- per-instance identity ------------------------------------------------

    @property
    def name(self) -> str:
        """The overlay's name: its directory name."""
        return self.path.name

    @property
    def normalized_name(self) -> str:
        """:meth:`normalize_name` of :attr:`name`."""
        return self.normalize_name(self.name)

    @property
    def root_var(self) -> str:
        """:meth:`root_var_for` of :attr:`name` -- the ``<NAME>_OVERLAY_ROOT`` var."""
        return self.root_var_for(self.name)

    @property
    def is_valid(self) -> bool:
        """:meth:`is_valid_name` of :attr:`name`."""
        return self.is_valid_name(self.name)

    @property
    def manifest_path(self) -> Path:
        return self.path / self.MANIFEST_NAME

    # -- discovery ------------------------------------------------------------

    @classmethod
    def discover(cls, root: "str | os.PathLike[str]") -> "list[Overlay]":
        """The overlays under an ``overlays/`` root, sorted by name.

        No registry, no manifest required: a directory under ``root`` *is* an
        overlay as long as its name passes :meth:`is_valid_name` (so ``.git``/
        ``__pycache__``/dotfiles are skipped). ``is_dir()`` follows symlinks, so a
        symlink-to-dir counts. Empty if ``root`` is absent. This is the ONE
        discovery rule -- ``_scope.discover_overlays``, the contract-A overlay
        walk (``_resolve.get_file_paths``) and ``env``'s overlay roots all call it,
        so they can never disagree about what is installed (D84)."""
        root = Path(root)
        if not root.is_dir():
            return []
        return [
            cls(p)
            for p in sorted(root.iterdir())
            if p.is_dir() and cls.is_valid_name(p.name)
        ]

    # -- manifest -------------------------------------------------------------

    def read_manifest(self) -> "dict[str, object]":
        """Parse `overlay.toml`, returning at least name/routing/rules/priority.

        A missing or unreadable manifest yields empty contributions -- an overlay
        is allowed to be just a directory of files."""
        empty = {
            "name": self.name, "routing": [], "rules": [], "priority": DEFAULT_PRIORITY,
        }
        path = self.manifest_path
        if not path.is_file():
            return empty
        try:
            raw = _strip_comments(path.read_text(encoding="utf-8"))
        except OSError:
            return empty
        name = re.search(r'(?m)^name\s*=\s*"([^"]*)"', raw)
        return {
            "name": name.group(1) if name else self.name,
            "routing": _parse_string_array(raw, "routing"),
            "rules": _parse_string_array(raw, "rules"),
            "priority": _parse_priority(raw),
        }

    @property
    def priority(self) -> int:
        """The manifest ``priority`` (plan 02), ``DEFAULT_PRIORITY`` when absent or
        unparseable. Lower sorts earlier."""
        manifest = self.read_manifest()
        try:
            return int(manifest.get("priority", DEFAULT_PRIORITY))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return DEFAULT_PRIORITY

    @property
    def sort_key(self) -> "tuple[int, str]":
        """The `(priority, name)` merge-order key (plan 02 / D68).

        Lower `priority` (default `DEFAULT_PRIORITY`, 500) sorts earlier; a
        numerically higher-priority overlay therefore lands *later* in the merged
        AGENTS.md block and wins on conflict -- the same "lower sorts earlier /
        higher wins" convention `_context.py` uses for its own priority ordering.
        `name` is the stable tiebreaker for equal-priority overlays, keyed off
        the manifest `name` (falling back to the directory name) so output is
        deterministic regardless of input order."""
        manifest = self.read_manifest()
        return (self.priority, str(manifest.get("name", self.name)))

    @classmethod
    def sort_by_priority(
        cls, overlays: "Iterable[Overlay | str | os.PathLike[str]]"
    ) -> "list[Overlay]":
        """`overlays` (instances or dirs) sorted by `(priority, name)` (plan 02 / D68).

        Deterministic regardless of the caller's order: `overlays add` passes
        overlays in add-invocation order and `overlays sync` in alphabetical
        discovery order, but the *merged* block must not depend on either -- a
        high-priority overlay's lines must always land last. See :attr:`sort_key`.
        """
        return sorted((cls(o) for o in overlays), key=lambda o: o.sort_key)

    # -- setup script ---------------------------------------------------------

    def find_setup_script(self) -> "Optional[Path]":
        """The overlay's setup script, or ``None`` if it ships none.

        Prefers the OS-agnostic ``setup.py`` over the legacy extensionless
        ``setup`` when an overlay ships both (``SETUP_SCRIPT_NAMES`` is in
        preference order).

        Call this on the *installed* overlay: setup runs against the copy under
        ``<scope>/overlays/<name>/`` with that as its cwd, so a script can
        reference its own sibling files by relative path."""
        for name in self.SETUP_SCRIPT_NAMES:
            candidate = self.path / name
            if candidate.is_file():
                return candidate
        return None

    def run_setup(self, *, agents_dir: Path, dry_run: bool, logger) -> "Optional[int]":
        """Run the overlay's idempotent ``setup`` script if it ships one.

        Returns the script's exit code, or ``None`` when the overlay has no setup
        script (nothing to run -- not an error). Presence of ``setup.py``
        (preferred, OS-agnostic) or the legacy ``setup`` at the overlay root is
        the opt-in; the runner never second-guesses a script the user chose to
        install (see the overlay-authoring contract).

        **Idempotency is the overlay author's contract**: the script must be safe
        to run on every ``add``/``sync`` (check-then-act). The runner only
        invokes it.

        Invocation (pure stdlib subprocess, the same style the private-sync
        overlay's sync hook uses):

        * **cwd** = the installed overlay dir, so the script sees its own files.
        * **env** carries ``AGENTS_HOME`` = the resolved store path (D58
          configurable store), so the script never hardcodes ``~/.agents``. It
          also gets ``AGENTS_OVERLAY_DIR`` = its own installed dir for
          convenience. The old ``DOTAGENTS_AGENTS_DIR`` / ``DOTAGENTS_OVERLAY_DIR``
          are also set for back-compat this release (removable next), so
          existing setup scripts keep working.
        * a ``.py`` script runs under the current interpreter; an extensionless
          ``setup`` runs directly, or via ``sh`` on Windows where it isn't
          executable.

        A non-zero exit is surfaced (returned) so the caller can raise a clear
        error -- never a silent skip. Never prints ``DOTAGENTS_*`` values
        (Leakage)."""
        script = self.find_setup_script()
        if script is None:
            return None

        logger.info("running setup for %s (%s)", self.name, script.name)
        if dry_run:
            logger.info("[dry-run] would run %s in %s", script.name, self.path)
            return 0

        env = dict(os.environ)
        env["AGENTS_HOME"] = str(agents_dir)
        env["AGENTS_OVERLAY_DIR"] = str(self.path)
        # back-compat: DOTAGENTS_* is deprecated, removable next release.
        env["DOTAGENTS_AGENTS_DIR"] = str(agents_dir)
        env["DOTAGENTS_OVERLAY_DIR"] = str(self.path)

        if script.suffix.lower() == ".py":
            import sys
            cmd = [sys.executable, str(script)]
        else:
            cmd = [str(script)]
            if os.name == "nt":
                # An extensionless script is not directly executable on Windows.
                cmd = ["sh"] + cmd

        try:
            res = subprocess.run(cmd, cwd=str(self.path), env=env)
        except OSError as exc:
            logger.error("setup for %s failed to start: %s", self.name, exc)
            return 1
        if res.returncode != 0:
            logger.error("setup for %s exited %d", self.name, res.returncode)
        return res.returncode

    # -- files + what they contribute -----------------------------------------

    def files(self) -> "list[Path]":
        """Files the overlay installs (everything except its manifest / caches)."""
        return [
            p
            for p in sorted(self.path.rglob("*"))
            if p.is_file() and p.name != self.MANIFEST_NAME and "__pycache__" not in p.parts
        ]

    def rule_blocks(self, rel_paths: "list[str]") -> "tuple[list[str], list[str]]":
        """Extract `- **…` bullet blocks from each referenced markdown file.

        Returns (blocks, warnings). Only the leading run of bullets is taken: a
        rules file may carry explanatory prose under a `## ` heading
        (engineering/rules.md documents *why* its rules are not in the base), and
        that must not be merged into the core. A path that does not exist is
        reported, not fatal -- a broken optional overlay must never block the
        base config landing."""
        blocks: "list[str]" = []
        warnings: "list[str]" = []
        for rel in rel_paths:
            src = self.path / rel
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

    def _copy_into(self, sources: "list[Path]", dest: Path, dry_run: bool, verb: str):
        """Copy `sources` (under this overlay) to the same relative path under
        `dest`, create-if-absent; never clobber. Returns (copied, skipped, lines)."""
        copied = skipped = 0
        lines = []
        for src in sources:
            rel = src.relative_to(self.path)
            target = dest / rel
            if target.exists():
                lines.append("skip (exists): %s" % rel.as_posix())
                skipped += 1
                continue
            lines.append("%s: %s" % (verb, rel.as_posix()))
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(target))
            copied += 1
        return copied, skipped, lines

    def apply_to(self, dest: Path, dry_run: bool):
        """Copy the overlay's files into `dest` (create-if-absent; never clobber
        an existing file). Returns (copied, skipped) counts and per-file log lines."""
        return self._copy_into(self.files(), dest, dry_run, "overlay")

    def install_to(self, dest_overlay_dir: Path, dry_run: bool):
        """Install the overlay as a *directory* under `<scope>/overlays/<name>/`
        (the "install model = overlay-dirs" decision): the overlay is the
        discoverable unit.

        Copies every file the overlay ships (minus its manifest / caches -- see
        :meth:`files`) into `dest_overlay_dir`, create-if-absent so re-adding an
        overlay never clobbers a file the user hand-edited inside the installed
        copy (additive/no-clobber, mirroring :meth:`apply_to`). The overlay's own
        `overlay.toml` is copied too so a later `sync`/`list` can re-read its
        manifest from the installed copy. Returns (copied, skipped) counts and
        log lines."""
        # Ship the manifest alongside the files so the installed dir is self-describing.
        sources = self.files()
        if self.manifest_path.is_file():
            sources.append(self.manifest_path)
        return self._copy_into(sources, dest_overlay_dir, dry_run, "install")

    def merge_rules_into(self, agents_md: Path, dry_run: bool, logger) -> bool:
        """Fold this overlay's D59 routing + rules into an already-installed
        AGENTS.md's managed block, in place (additive).

        `init` composes these into the base text *before* first write; an
        incremental `overlays add` instead re-composes the *existing* installed
        file's managed block. Extracts the current block (between the dotagents
        markers), runs the same `_compose_block` fold used at install time over
        just that block text, and writes the result back between the markers --
        content outside the block is never touched. A no-op (returns False) when
        the overlay contributes nothing, the file is absent, or it carries no
        managed block."""
        from dotagents._fs import write_text_lf
        from dotagents._merge import find_block
        from dotagents.cli import _compose_block

        manifest = self.read_manifest()
        if not manifest["routing"] and not manifest["rules"]:
            return False
        if not agents_md.is_file():
            logger.warning(
                "no installed AGENTS.md at %s; overlay rules/routing not merged", agents_md
            )
            return False
        existing = agents_md.read_text(encoding="utf-8")
        span = find_block(existing)
        if span is None:
            logger.warning(
                "AGENTS.md has no dotagents managed block; overlay rules/routing not "
                "merged (run `dotagents init` first)"
            )
            return False
        start, end = span
        block = existing[start:end]
        merged = _compose_block(block, [self], logger)
        if merged == block:
            return False
        new_text = existing[:start] + merged + existing[end:]
        if not dry_run:
            write_text_lf(agents_md, new_text)
        return True


# --------------------------------------------------------------------------- #
# Over a SET of overlays.
# --------------------------------------------------------------------------- #


def recompose_overlay_block(
    agents_md: Path,
    base_block: str,
    overlays: "Iterable[Overlay | str | os.PathLike[str]]",
    dry_run: bool,
    logger,
) -> bool:
    """Rebuild AGENTS.md's managed block from the *pristine* base over ALL installed
    overlays in `(priority, name)` order (plan 02 / D68), in place.

    This is what makes single-`overlays add` positioning correct: a lone `add` merges
    one overlay, but its rules must land in the right slot *relative to overlays
    already present in the block*. Incremental append (`Overlay.merge_rules_into`)
    can only tack the newcomer on at the end, so a high-priority overlay added
    *after* a low-priority one would wrongly sort last. Recomposing from the base
    each time sidesteps that: `base_block` is the freshly-read base overlay's managed
    block (no overlay content), `overlays` is every installed overlay in the scope
    (instances or dirs), and `_compose_block` folds them in sorted order -- so the
    block is a pure function of *which* overlays are installed, never *when* each
    was added.

    Only the managed block (between the dotagents markers) is rewritten; content
    outside the markers is untouched. Returns True if the file changed, False on a
    no-op (nothing to merge, file/markers absent, or block already correct).
    """
    from dotagents._fs import write_text_lf
    from dotagents._merge import _extract_block, find_block
    from dotagents.cli import _compose_block

    if not agents_md.is_file():
        logger.warning(
            "no installed AGENTS.md at %s; overlay rules/routing not merged", agents_md
        )
        return False
    existing = agents_md.read_text(encoding="utf-8")
    span = find_block(existing)
    if span is None:
        logger.warning(
            "AGENTS.md has no dotagents managed block; overlay rules/routing not "
            "merged (run `dotagents init` first)"
        )
        return False

    # Compose over the pristine base block, not the current (already-merged) one, so
    # every install of the same overlay set yields identical output.
    pristine = _extract_block(base_block)
    merged = _compose_block(pristine, list(overlays), logger)

    start, end = span
    if existing[start:end] == merged:
        return False
    new_text = existing[:start] + merged + existing[end:]
    if not dry_run:
        write_text_lf(agents_md, new_text)
    return True
