"""dotagents CLI: init / install / build-pyz / audit subcommands."""

import importlib.resources
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import duho
from duho import LoggingArgs

from dotagents import __version__
from dotagents._merge import merge_block, merge_claude_md, timestamped_backup_root


_extracted_dirs_cache: "dict[str, Path]" = {}


def _package_data_dir(name: str) -> "Path | None":
    """Resolve a directory under the installed `dotagents` package (e.g.
    `_overlay` or `_payload`) to a real filesystem Path, working whether the
    package is a plain directory (pip install / editable) or inside a zipapp.

    Inside a zipapp, `importlib.resources.files()` returns a `zipfile.Path`
    (a `Traversable`, not a real filesystem `Path`): `.is_dir()` correctly
    reports membership in the archive, but `str(traversable)` produces a
    path string that does not exist on disk (`Path(str(...)).exists()` is
    always False -- there is no real file there to stat). So a zip-backed
    hit is extracted once to a process-lifetime temp directory and that
    real path is cached and returned; a plain-directory hit is returned
    as-is. Returns None if the directory isn't present in the package at all.
    """
    if name in _extracted_dirs_cache:
        return _extracted_dirs_cache[name]

    traversable = importlib.resources.files("dotagents") / name
    if not traversable.is_dir():
        return None

    as_path = Path(str(traversable))
    if as_path.exists():
        _extracted_dirs_cache[name] = as_path
        return as_path

    # Zip-backed (or otherwise non-filesystem) Traversable: extract to a
    # temp dir that lives for the process lifetime.
    extract_root = Path(tempfile.mkdtemp(prefix="dotagents-%s-" % name))

    def _extract(node, dest: Path):
        dest.mkdir(parents=True, exist_ok=True)
        for child in node.iterdir():
            child_dest = dest / child.name
            if child.is_dir():
                _extract(child, child_dest)
            else:
                child_dest.write_bytes(child.read_bytes())

    _extract(traversable, extract_root)
    _extracted_dirs_cache[name] = extract_root
    return extract_root


OVERLAY_ROOT = _package_data_dir("_overlay") or (Path(__file__).resolve().parent / "_overlay")
# Bundled full payload lives at <pyz-or-package>/dotagents/_payload (Phase 4
# build-pyz stages it there); absent on a plain pip install (Design Q1).
BUNDLED_PAYLOAD = _package_data_dir("_payload")

PAYLOAD_ENTRIES = ["AGENTS.md", "CLAUDE.md", "MODELS.md", "dotagents", "flows", "kb", "references", "tools"]
EXAMPLES_ENTRY = "examples"

# Skeleton files that are create-if-absent only (never overwrite), i.e.
# everything except the managed-block files (AGENTS.md/CLAUDE.md).
OVERLAY_PLAIN_FILES = [
    "README.md",
    "dotagents/DECISIONS.md",
    "dotagents/decisions/.gitkeep",
    "dotagents/findings/.gitkeep",
    "dotagents/findings/processed/.gitkeep",
]


def _resolve_from(from_arg: "str | None", default: Path) -> Path:
    """Resolve --from to a local directory Path. A bare local path/dir is used
    directly; a URI string is constructed via pathlib_next's UriPath (lazy
    import so the `uri` extra is only required when actually used)."""
    if from_arg is None:
        return default
    candidate = Path(from_arg)
    if candidate.exists():
        return candidate
    if "://" in from_arg or from_arg.startswith(("http:", "https:", "sftp:", "s3:", "zip:")):
        try:
            from pathlib_next import UriPath
        except ImportError as e:
            raise SystemExit(
                'error: --from %r needs URI support. Install it with: pip install "dotagents[uri]"'
                % from_arg
            ) from e
        return UriPath(from_arg)
    raise SystemExit("error: --from path does not exist: %s" % from_arg)


class Init(LoggingArgs):
    """Install the minimal neutral base overlay (never the full opinionated payload)."""

    _parsername_ = "init"

    dest: Path = Path.home() / ".agents"
    "Destination directory for the installed config."
    ("--dest",)

    from_: Optional[str] = None
    "Source directory/URI for the base overlay (default: bundled overlay)."
    ("--from",)

    dry_run: bool = False
    "Show what would be written without touching anything."
    ("--dry-run",)

    force: bool = False
    "Replace AGENTS.md/CLAUDE.md wholesale (with backup) instead of block-merging."
    ("--force",)

    def __run__(self) -> int:
        src = _resolve_from(self.from_, OVERLAY_ROOT)
        dest = Path(self.dest).expanduser().resolve()
        backup_root = timestamped_backup_root(dest) if self.force else None

        agents_src = Path(src) / "AGENTS.md"
        claude_src = Path(src) / "CLAUDE.md"

        branch = merge_block(
            dest / "AGENTS.md",
            agents_src.read_text(encoding="utf-8"),
            force=self.force,
            dry_run=self.dry_run,
            backup_root=backup_root,
        )
        self._logger_.info("%s: AGENTS.md", branch)

        branch = merge_claude_md(
            dest / "CLAUDE.md",
            claude_src.read_text(encoding="utf-8"),
            force=self.force,
            dry_run=self.dry_run,
            backup_root=backup_root,
        )
        self._logger_.info("%s: CLAUDE.md", branch)

        for rel in OVERLAY_PLAIN_FILES:
            source_path = Path(src) / rel
            if not source_path.exists():
                continue
            target_path = dest / rel
            if target_path.exists():
                self._logger_.info("skipped (present): %s", rel)
                continue
            self._logger_.info("created: %s", rel)
            if not self.dry_run:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(source_path), str(target_path))

        if self.dry_run:
            self._logger_.info("dry-run: no files were written")
        return 0


class Install(LoggingArgs):
    """Copy the full opinionated payload into the destination directory."""

    _parsername_ = "install"

    dest: Path = Path.home() / ".agents"
    "Destination directory for the installed config."
    ("--dest",)

    from_: Optional[str] = None
    "Source directory/URI for the payload (default: bundled/repo payload)."
    ("--from",)

    with_examples: bool = False
    "Additionally copy the opt-in examples/ payload (never overwrites)."
    ("--with-examples",)

    bin_dir: Optional[Path] = None
    "Directory to write dotagents/dotagents.cmd wrapper scripts into."
    ("--bin-dir",)

    dry_run: bool = False
    "Show what would be installed/backed up without writing anything."
    ("--dry-run",)

    def __run__(self) -> int:
        default_payload = BUNDLED_PAYLOAD
        if self.from_ is None and default_payload is None:
            raise SystemExit(
                "error: no bundled payload in this install; pass --from <path-to-payload> "
                "(e.g. --from payload, from a dotagents repo checkout)"
            )
        src = _resolve_from(self.from_, default_payload)
        dest = Path(self.dest).expanduser().resolve()

        from dotagents._sync import sync_payload

        counts, lines, backup_root = sync_payload(
            Path(src), dest, PAYLOAD_ENTRIES, dry_run=self.dry_run
        )
        for line in lines:
            self._logger_.info(line)
        self._logger_.info(
            "%d installed, %d backed up (%s), %d unchanged%s",
            counts.installed,
            counts.backed_up,
            backup_root if counts.backed_up and not self.dry_run else "none",
            counts.unchanged,
            " [dry-run]" if self.dry_run else "",
        )

        if self.with_examples:
            ex_src = Path(src) / EXAMPLES_ENTRY
            ex_copied = ex_skipped = 0
            if ex_src.exists():
                for source_path in sorted(ex_src.rglob("*")):
                    if not source_path.is_file() or "__pycache__" in source_path.parts:
                        continue
                    rel = Path(EXAMPLES_ENTRY) / source_path.relative_to(ex_src)
                    target_path = dest / rel
                    if target_path.exists():
                        self._logger_.info("skip (exists): %s", rel.as_posix())
                        ex_skipped += 1
                        continue
                    self._logger_.info("example: %s", rel.as_posix())
                    if not self.dry_run:
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(source_path), str(target_path))
                    ex_copied += 1
            self._logger_.info(
                "%d example(s) copied, %d skipped (already present)%s",
                ex_copied,
                ex_skipped,
                " [dry-run]" if self.dry_run else "",
            )

        if self.bin_dir is not None and not self.dry_run:
            from dotagents._wrappers import check_path_warning, write_wrappers

            pyz_path = Path(sys.argv[0]).resolve()
            if pyz_path.suffix != ".pyz":
                # Running from a plain install (not a pyz): point the wrapper
                # at `python -m dotagents` instead of a nonexistent pyz path.
                pyz_path = None
            if pyz_path is not None:
                written = write_wrappers(Path(self.bin_dir), pyz_path)
                for w in written:
                    self._logger_.info("wrapper: %s", w)
            else:
                self._logger_.info(
                    "skipped wrapper install: not running from a .pyz (use build-pyz first)"
                )
            warning = check_path_warning(Path(self.bin_dir))
            if warning:
                self._logger_.warning(warning)

        if self.dry_run:
            return 0

        auditor = dest / "tools" / "audit_config.py"
        if auditor.exists():
            return subprocess.call([sys.executable, str(auditor), "--root", str(dest)])
        return 0


class Audit(LoggingArgs):
    """Run the config auditor against a payload/install destination."""

    _parsername_ = "audit"

    root: Path = Path.home() / ".agents"
    "Root directory to audit (an installed dest, or a repo payload/ dir)."
    ("--root",)

    check_templates: bool = False
    "Also run --check-templates (needs Python 3.11+)."
    ("--check-templates",)

    repo_hygiene: Optional[Path] = None
    "Also run --repo-hygiene against the given repo root."
    ("--repo-hygiene",)

    def __run__(self) -> int:
        auditor_path = Path(self.root) / "tools" / "audit_config.py"
        if not auditor_path.exists():
            # Fall back to the auditor bundled with this package (e.g. when
            # `root` is a bare payload without its own tools/ copy).
            if BUNDLED_PAYLOAD is not None:
                auditor_path = BUNDLED_PAYLOAD / "tools" / "audit_config.py"
        if not auditor_path.exists():
            raise SystemExit("error: could not find audit_config.py under %s" % self.root)

        rc = 0
        cmd = [sys.executable, str(auditor_path), "--root", str(self.root)]
        rc |= subprocess.call(cmd)
        if self.check_templates:
            rc |= subprocess.call(cmd + ["--check-templates"])
        if self.repo_hygiene:
            rc |= subprocess.call(
                [sys.executable, str(auditor_path), "--repo-hygiene", str(self.repo_hygiene)]
            )
        return rc


class BuildPyz(LoggingArgs):
    """Vendor duho/pathlib_next via pip --target and package a self-contained dotagents.pyz."""

    _parsername_ = "build-pyz"

    out: Path = Path("dist") / "dotagents.pyz"
    "Output path for the built pyz."
    ("--out",)

    python: str = "/usr/bin/env python3"
    "Shebang line to embed in the pyz."
    ("--python",)

    duho_version: str = "0.1.1"
    "Pinned duho version to vendor."
    ("--duho-version",)

    pathlib_next_version: str = "0.8.0"
    "Pinned pathlib_next version to vendor."
    ("--pathlib-next-version",)

    payload_dir: Optional[Path] = None
    "Repo payload/ directory to bundle for offline `install` (default: autodetected)."
    ("--payload-dir",)

    def __run__(self) -> int:
        import zipapp

        repo_root = Path(__file__).resolve().parents[2]
        payload_src = Path(self.payload_dir) if self.payload_dir else (repo_root / "payload")
        if not payload_src.exists():
            raise SystemExit("error: repo payload/ not found at %s (pass --payload-dir)" % payload_src)

        with tempfile.TemporaryDirectory(prefix="dotagents-pyz-") as tmp:
            stage = Path(tmp) / "stage"
            stage.mkdir()

            self._logger_.info(
                "vendoring duho==%s pathlib_next==%s via pip --target",
                self.duho_version,
                self.pathlib_next_version,
            )
            rc = subprocess.call(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--target",
                    str(stage),
                    "duho==%s" % self.duho_version,
                    "pathlib_next==%s" % self.pathlib_next_version,
                ]
            )
            if rc != 0:
                return rc

            dotagents_pkg_src = Path(__file__).resolve().parent
            dotagents_pkg_dest = stage / "dotagents"
            shutil.copytree(
                dotagents_pkg_src,
                dotagents_pkg_dest,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )

            payload_dest = dotagents_pkg_dest / "_payload"
            shutil.copytree(
                payload_src,
                payload_dest,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )

            for path in stage.rglob("*.dist-info"):
                shutil.rmtree(path, ignore_errors=True)
            for path in stage.rglob("__pycache__"):
                shutil.rmtree(path, ignore_errors=True)
            for path in stage.rglob("tests"):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)

            main_py = stage / "__main__.py"
            main_py.write_text(
                "from dotagents.cli import main\n\nraise SystemExit(main())\n",
                encoding="utf-8",
            )

            out_path = Path(self.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            zipapp.create_archive(str(stage), target=str(out_path), interpreter=self.python)
            self._logger_.info("built %s", out_path)

        return 0


class Dotagents(LoggingArgs):
    """Umbrella CLI for installing and building the dotagents config."""

    _version_ = __version__
    _subcommands_ = [Init, Install, Audit, BuildPyz]

    def __run__(self) -> int:
        self._logger_.info("pick a subcommand, e.g. `init`, `install`, `audit`, `build-pyz`")
        return 0


def main(argv=None) -> int:
    return duho.main(Dotagents, argv)


if __name__ == "__main__":
    sys.exit(main())
