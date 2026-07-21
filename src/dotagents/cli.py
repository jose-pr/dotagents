"""dotagents CLI: init / install / link / sync / audit / build-pyz subcommands."""

import importlib.resources
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import duho
from duho import Cli, Cmd, LoggingArgs

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


# The base overlay (neutral minimum) is bundled package data at
# `src/dotagents/_overlay`; `init` and `install` both lay it down. Overlays
# beyond the base are opt-in examples applied from an external path via
# `install --overlays <dir>` (the installer bundles none of them).
BASE_ROOT = _package_data_dir("_overlay") or (Path(__file__).resolve().parent / "_overlay")

# Base files that are create-if-absent only (never overwrite), i.e. everything
# except the managed-block files (AGENTS.md/CLAUDE.md).
BASE_PLAIN_FILES = [
    "README.md",
    "dotagents/DECISIONS.md",
]


def _compose_block(base_text: str, overlays: "list[Path]", logger) -> str:
    """Fold each overlay's `rules`/`routing` contributions into the base block.

    Rules append to "Always-on rules" and routing to "Load on demand", in overlay
    order, after the base's own -- the base carries the mechanism (D57) and should
    read first, and overlay order is already the user's stated preference via
    repeated `--overlays`. Returns `base_text` unchanged when nothing contributes,
    so `init` (which takes no overlays) is completely unaffected."""
    from dotagents._overlays import read_manifest, rule_blocks

    rules: "list[str]" = []
    routing: "list[str]" = []
    for overlay_dir in overlays:
        manifest = read_manifest(overlay_dir)
        blocks, warnings = rule_blocks(overlay_dir, manifest["rules"])  # type: ignore[arg-type]
        for warning in warnings:
            logger.warning("overlay %s: %s", manifest["name"], warning)
        rules.extend(blocks)
        routing.extend(manifest["routing"])  # type: ignore[arg-type]

    if not rules and not routing:
        return base_text

    text = base_text
    if rules:
        # Append after the last always-on bullet, i.e. just before the next heading.
        m = re.search(r"(?m)^## Load on demand", text)
        if m is None:
            logger.warning("base AGENTS.md has no 'Load on demand' heading; "
                           "appending overlay rules at the end of the block")
        else:
            text = text[: m.start()] + "\n".join(rules) + "\n\n" + text[m.start():]
    if routing:
        # The base's placeholder sentence only makes sense with no routing lines.
        text = re.sub(
            r"(?m)^Nothing ships here by default[^\n]*\n(?:[^\n#<][^\n]*\n)*",
            "",
            text,
        )
        end = re.search(r"(?m)^<!-- dotagents:end -->", text)
        insert_at = end.start() if end else len(text)
        text = text[:insert_at] + "\n".join(routing) + "\n" + text[insert_at:]
    return text


def _apply_base(
    src: Path, dest: Path, force: bool, dry_run: bool, logger,
    overlays: "list[Path] | None" = None,
) -> None:
    """Lay down the base overlay: managed-block merge AGENTS.md/CLAUDE.md,
    create-if-absent the plain files. Shared by `init` and `install`.

    `overlays` (install only) contribute rules/routing into the managed block."""
    backup_root = timestamped_backup_root(dest) if force else None

    base_agents = (Path(src) / "AGENTS.md").read_text(encoding="utf-8")
    if overlays:
        base_agents = _compose_block(base_agents, overlays, logger)

    branch = merge_block(
        dest / "AGENTS.md",
        base_agents,
        force=force, dry_run=dry_run, backup_root=backup_root,
    )
    logger.info("%s: AGENTS.md", branch)

    branch = merge_claude_md(
        dest / "CLAUDE.md",
        (Path(src) / "CLAUDE.md").read_text(encoding="utf-8"),
        force=force, dry_run=dry_run, backup_root=backup_root,
    )
    logger.info("%s: CLAUDE.md", branch)

    for rel in BASE_PLAIN_FILES:
        source_path = Path(src) / rel
        if not source_path.exists():
            continue
        target_path = dest / rel
        if target_path.exists():
            logger.info("skipped (present): %s", rel)
            continue
        logger.info("created: %s", rel)
        if not dry_run:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source_path), str(target_path))


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


class Init(LoggingArgs, Cmd):
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

    def __call__(self) -> int:
        src = _resolve_from(self.from_, BASE_ROOT)
        dest = Path(self.dest).expanduser().resolve()
        _apply_base(Path(src), dest, self.force, self.dry_run, self._logger_)
        if self.dry_run:
            self._logger_.info("dry-run: no files were written")
        return 0


class Install(LoggingArgs, Cmd):
    """Install the base config, plus any opt-in overlays given with --overlays.

    The installer bundles only the neutral base overlay. Overlays beyond the
    base (flows, language kbs, tools, ...) are opt-in examples: point --overlays
    at one or more overlay directories (e.g. this repo's overlays/flows) to layer
    them in. A future `dotagents overlays` subcommand will manage them by name."""

    _parsername_ = "install"

    dest: Path = Path.home() / ".agents"
    "Destination directory for the installed config."
    ("--dest",)

    from_: Optional[str] = None
    "Source directory/URI for the base overlay (default: bundled base)."
    ("--from",)

    overlays: "list[str]" = []
    "Overlay directory to apply on top of the base (repeatable); an opt-in example path."
    ("--overlays",)

    bin_dir: Optional[Path] = None
    "Directory to write dotagents/dotagents.cmd wrapper scripts into."
    ("--bin-dir",)

    dry_run: bool = False
    "Show what would be installed without touching anything."
    ("--dry-run",)

    force: bool = False
    "Replace AGENTS.md/CLAUDE.md wholesale (with backup) instead of block-merging."
    ("--force",)

    def __call__(self) -> int:
        src = _resolve_from(self.from_, BASE_ROOT)
        dest = Path(self.dest).expanduser().resolve()

        _apply_base(
            Path(src), dest, self.force, self.dry_run, self._logger_,
            overlays=[Path(o) for o in (self.overlays or [])],
        )

        if self.overlays:
            from dotagents._overlays import apply_overlay

            total_copied = total_skipped = 0
            for ov in self.overlays:
                ov_dir = Path(ov).expanduser()
                if not ov_dir.is_dir():
                    raise SystemExit("error: overlay path is not a directory: %s" % ov)
                copied, skipped, lines = apply_overlay(ov_dir, dest, self.dry_run)
                for line in lines:
                    self._logger_.info(line)
                total_copied += copied
                total_skipped += skipped
            self._logger_.info(
                "%d overlay file(s) copied, %d skipped (already present)%s",
                total_copied, total_skipped, " [dry-run]" if self.dry_run else "",
            )

        if self.bin_dir is not None and not self.dry_run:
            from dotagents._wrappers import check_path_warning, write_wrappers

            pyz_path = Path(sys.argv[0]).resolve()
            if pyz_path.suffix != ".pyz":
                # Running from a plain install (not a pyz): the wrappers point at
                # `python -m dotagents` instead of a nonexistent pyz path.
                pyz_path = None
            if pyz_path is not None:
                for w in write_wrappers(Path(self.bin_dir), pyz_path):
                    self._logger_.info("wrapper: %s", w)
            else:
                self._logger_.info(
                    "skipped wrapper install: not running from a .pyz (use build-pyz first)"
                )
            warning = check_path_warning(Path(self.bin_dir))
            if warning:
                self._logger_.warning(warning)

        if self.dry_run:
            self._logger_.info("dry-run: no files were written")
        return 0


class Link(LoggingArgs, Cmd):
    """Point a project's .agents at a store outside the project repo.

    Symlinks ``<project>/.agents`` to its store (``--copy`` mirrors it as a real
    dir instead, for Windows / no-symlink environments). An existing real
    ``.agents/`` is adopted into an empty store on the first link. Optional: this
    is one way to keep private agent state out of a public repo, not something
    dotagents requires."""

    _parsername_ = "link"

    path: Path = Path(".")
    "Project directory to link (default: current directory)."
    ("path",)

    agents_dir: Path = Path.home() / ".agents"
    "Global agents dir (default: ~/.agents)."
    ("--agents-dir",)

    store_dir: Optional[str] = None
    ("Where stores live: relative to --agents-dir, or absolute to put them "
     "elsewhere entirely (default: projects, or $DOTAGENTS_STORE_DIR).")
    ("--store-dir",)

    name: Optional[str] = None
    "Store name (default: the project directory's basename)."
    ("--name",)

    copy: bool = False
    "Copy the store into the project instead of symlinking (no-symlink fallback)."
    ("--copy",)

    force: bool = False
    "Replace an existing .agents symlink, or back up a conflicting real .agents dir."
    ("--force",)

    dry_run: bool = False
    "Show what would happen without touching anything."
    ("--dry-run",)

    def __call__(self) -> int:
        from dotagents._link import link_project

        link_project(
            self.path, self.agents_dir, name=self.name, store_dir=self.store_dir,
            copy=self.copy, force=self.force, dry_run=self.dry_run,
            logger=self._logger_,
        )
        if self.dry_run:
            self._logger_.info("dry-run: no files were written")
        return 0


class Sync(LoggingArgs, Cmd):
    """Reconcile a copy-mode project, then hand off to whatever moves the store.

    Pass ``--project`` so a copy-mode project's .agents is copied back into its
    store first (symlinked projects need no copy-back -- their .agents *is* the
    store).

    Transport is not dotagents' concern. If ``<agents-dir>/hooks/sync`` exists it
    owns that step entirely and its exit code is returned; use it for rsync, a
    cloud drive, or anything else. Otherwise a built-in git path runs as a
    convenient default (``pull --rebase`` / commit / push), with ``--remote``
    bootstrapping a fresh repo (``git init`` + set ``origin``) in one command. A
    store that never leaves the machine is a valid setup -- neither is required.

    In that git path, when ``DOTAGENTS_AGENTS_TOKEN`` is set the pull/push
    authenticate directly against github.com with that PAT -- and on a hosted
    runner that rewrites github traffic to a scoped in-session proxy, they bypass
    the rewrite -- so a standalone ``dotagents sync`` works without being run
    through the private-sync Stop hook."""

    _parsername_ = "sync"

    agents_dir: Path = Path.home() / ".agents"
    "Global agents dir (default: ~/.agents)."
    ("--agents-dir",)

    store_dir: Optional[str] = None
    ("Where stores live: relative to --agents-dir, or absolute to put them "
     "elsewhere entirely (default: projects, or $DOTAGENTS_STORE_DIR).")
    ("--store-dir",)

    message: str = "dotagents: sync"
    "Commit message for the sync (also passed to a hooks/sync script)."
    ("--message", "-m")

    project: Optional[Path] = None
    "A project whose (copy-mode) .agents should be copied back into the store first."
    ("--project",)

    name: Optional[str] = None
    "Store name for --project (default: that project's basename)."
    ("--name",)

    remote: Optional[str] = None
    "Set origin to this URL (git init first if needed) before syncing."
    ("--remote",)

    no_pull: bool = False
    "Skip the git pull --rebase step (built-in git path only)."
    ("--no-pull",)

    no_push: bool = False
    "Skip the git push step (built-in git path only)."
    ("--no-push",)

    dry_run: bool = False
    "Show what would happen without touching anything."
    ("--dry-run",)

    def __call__(self) -> int:
        from dotagents._link import sync_agents

        return sync_agents(
            self.agents_dir, message=self.message, project_dir=self.project,
            name=self.name, store_dir=self.store_dir, remote=self.remote,
            pull=not self.no_pull, push=not self.no_push, dry_run=self.dry_run,
            logger=self._logger_,
        )


class Audit(LoggingArgs, Cmd):
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

    def __call__(self) -> int:
        auditor_path = Path(self.root) / "tools" / "audit_config.py"
        if not auditor_path.exists():
            # Fall back to the auditor bundled with this package (required
            # tooling, shipped as package data `_tools`), then to a repo
            # checkout's top-level tools/ (dev use).
            bundled_tools = _package_data_dir("_tools")
            if bundled_tools is not None and (bundled_tools / "audit_config.py").exists():
                auditor_path = bundled_tools / "audit_config.py"
            else:
                repo_tool = Path(__file__).resolve().parents[2] / "tools" / "audit_config.py"
                if repo_tool.exists():
                    auditor_path = repo_tool
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


class BuildPyz(LoggingArgs, Cmd):
    """Vendor duho/pathlib_next via pip --target and package a self-contained dotagents.pyz."""

    _parsername_ = "build-pyz"

    out: Path = Path("dist") / "dotagents.pyz"
    "Output path for the built pyz."
    ("--out",)

    python: str = "/usr/bin/env python3"
    "Shebang line to embed in the pyz."
    ("--python",)

    duho_version: str = "0.3.3"
    "Pinned duho version to vendor."
    ("--duho-version",)

    pathlib_next_version: str = "0.8.0"
    "Pinned pathlib_next version to vendor."
    ("--pathlib-next-version",)

    tools_dir: Optional[Path] = None
    "Repo tools/ dir (required tooling) to bundle as _tools (default: autodetected)."
    ("--tools-dir",)

    def __call__(self) -> int:
        import zipapp

        repo_root = Path(__file__).resolve().parents[2]
        tools_src = Path(self.tools_dir) if self.tools_dir else (repo_root / "tools")
        if not tools_src.exists():
            raise SystemExit("error: repo tools/ not found at %s (pass --tools-dir)" % tools_src)

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

            tools_dest = dotagents_pkg_dest / "_tools"
            shutil.copytree(
                tools_src,
                tools_dest,
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


class Dotagents(LoggingArgs, Cli):
    """Umbrella CLI for installing and building the dotagents config."""

    _version_ = __version__
    _subcommands_ = [Init, Install, Link, Sync, Audit, BuildPyz]

    def __call__(self) -> int:
        self._logger_.info(
            "pick a subcommand, e.g. `init`, `install`, `link`, `sync`, `audit`, `build-pyz`"
        )
        return 0


def _repoint_zipapp_sources() -> None:
    """Make duho's AST field-introspection work when running from a zipapp.

    duho 0.3.3 discovers each command's flags + help by parsing its module
    source (`_introspect.getclsdef` -> `Path(module.__file__).read_text()`).
    Inside a `.pyz` the module `__file__` is a zip-internal path `read_text()`
    can't open, and duho catches that `OSError` *before* its `inspect.getsource`
    fallback -- so every field silently loses its declared flags and help (the
    positional `path` degrades to `--path`, `--from` to `--from-`, help text
    vanishes). Extract the affected module sources to real temp files and
    repoint `__file__` so the read succeeds. A no-op for a plain install, where
    `__file__` already exists on disk. Covers this package's command module and
    duho's `LoggingArgs` preset (the only field-bearing sources dispatched
    here).

    Tracked upstream: jose-pr/duho#1 — drop this shim (and the build-pyz CI
    guard) once duho's getclsdef falls through to inspect.getsource when
    _module_index raises."""
    import importlib.resources as _ir

    for modname in ("dotagents.cli", "duho.presets"):
        mod = sys.modules.get(modname)
        if mod is None:
            continue
        current = getattr(mod, "__file__", None)
        if current and Path(current).exists():
            continue  # plain install: source already readable
        top, _sep, rest = modname.partition(".")
        rel = (rest.replace(".", "/") or "__init__") + ".py"
        try:
            resource = _ir.files(top).joinpath(rel)
            if not resource.is_file():
                continue
            text = resource.read_text(encoding="utf-8")
        except (FileNotFoundError, ModuleNotFoundError, OSError, TypeError):
            continue
        tmp = Path(tempfile.mkdtemp(prefix="dotagents-src-")) / (
            modname.replace(".", "_") + ".py"
        )
        tmp.write_text(text, encoding="utf-8")
        mod.__file__ = str(tmp)


def main(argv=None) -> int:
    _repoint_zipapp_sources()
    return duho.main(Dotagents, argv)


if __name__ == "__main__":
    sys.exit(main())
