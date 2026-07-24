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
    agents: "list[str] | None" = None,
) -> None:
    """Lay down the base overlay: managed-block merge AGENTS.md/CLAUDE.md,
    create-if-absent the plain files. Shared by `init` and `install`."""
    from dotagents import _agents
    import os

    base_agents = (Path(src) / "AGENTS.md").read_text(encoding="utf-8")

    active_agents = []
    if agents:
        for name in agents:
            agent = _agents.get_agent(name)
            if agent:
                active_agents.append(agent)
            else:
                logger.warning(f"Unknown agent: {name}")
    else:
        # Default: all detected + claude
        all_agents = _agents.get_all_agents()
        active_agents = [a for a in all_agents if a.detect_env(os.environ)]
        if not any(a.name == "claude" for a in active_agents):
            active_agents.append(_agents.ClaudeAgent())
            
    for agent in active_agents:
        agent.write_base_config(
            dest, src, base_agents, force=force, dry_run=dry_run, logger=logger
        )


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
    
    agents: "list[str]" = []
    "List of agents to install for (e.g. claude,gemini). Default: auto-detect + claude."
    ("--agents",)

    def __call__(self) -> int:
        src = _resolve_from(self.from_, BASE_ROOT)
        dest = Path(self.dest).expanduser().resolve()
        
        agent_names = []
        if self.agents:
            for a in self.agents:
                agent_names.extend([x.strip() for x in a.split(",") if x.strip()])
                
        _apply_base(Path(src), dest, self.force, self.dry_run, self._logger_, agents=agent_names if agent_names else None)
        if self.dry_run:
            self._logger_.info("dry-run: no files were written")
        return 0


class Install(LoggingArgs, Cmd):
    """Install the neutral base config.

    The installer lays down only the base overlay. Overlays beyond the base
    (flows, language kbs, tools, ...) are managed by name with the
    `dotagents overlays add <name>` command."""

    _parsername_ = "install"

    dest: Path = Path.home() / ".agents"
    "Destination directory for the installed config."
    ("--dest",)

    from_: Optional[str] = None
    "Source directory/URI for the base overlay (default: bundled base)."
    ("--from",)

    bin_dir: Optional[Path] = None
    "Directory to write dotagents/dotagents.cmd wrapper scripts into."
    ("--bin-dir",)

    dry_run: bool = False
    "Show what would be installed without touching anything."
    ("--dry-run",)

    force: bool = False
    "Replace AGENTS.md/CLAUDE.md wholesale (with backup) instead of block-merging."
    ("--force",)

    agents: "list[str]" = []
    "List of agents to install for (e.g. claude,gemini). Default: auto-detect + claude."
    ("--agents",)

    def __call__(self) -> int:
        src = _resolve_from(self.from_, BASE_ROOT)
        dest = Path(self.dest).expanduser().resolve()
        
        agent_names = []
        if self.agents:
            for a in self.agents:
                agent_names.extend([x.strip() for x in a.split(",") if x.strip()])

        _apply_base(
            Path(src), dest, self.force, self.dry_run, self._logger_,
            agents=agent_names if agent_names else None,
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


class Context(LoggingArgs, Cmd):
    """Assemble the effective context for agents (Plan 04)."""

    _parsername_ = "context"

    format: str = "markdown"
    "Output format: markdown, system-reminder, or json."
    ("--format",)

    global_scope: bool = False
    "Use global scope."
    ("--global", "-g")

    agents: "list[str]" = []
    "List of agents to generate context for (e.g. claude,gemini). Default: active agent."
    ("--agents",)

    out: Optional[str] = None
    "Output path, or '-' for stdout. Default: agent native config file."
    ("--out", "-o")

    def __call__(self) -> int:
        from dotagents import _agents
        from dotagents import _context
        import json
        import os

        project_root = Path.cwd()
        agents_dir = Path.home() / ".agents"

        agent_names = []
        if self.agents:
            for a in self.agents:
                agent_names.extend([x.strip() for x in a.split(",") if x.strip()])

        if agent_names:
            active_agents = []
            for name in agent_names:
                a = _agents.get_agent(name)
                if a:
                    active_agents.append(a)
                else:
                    self._logger_.warning("Unknown agent: %s", name)
        else:
            # Default target = the active agent (env-var detection / $AGENTS_HARNESS
            # stamp / config-file detect), not "all detected".
            active_agents = [
                _agents.resolve_active_agent(os.environ, root=project_root)
            ]

        # --- JSON: emit structured data (object for one agent, array for many);
        #     never writes native config files. ---
        if self.format == "json":
            payloads = [
                _context.assemble_context_data(
                    agent, agents_dir, project_root, global_scope=self.global_scope
                )
                for agent in active_agents
            ]
            out_obj = payloads[0] if len(payloads) == 1 else payloads
            blob = json.dumps(out_obj, indent=2, ensure_ascii=False)
            if self.out and self.out != "-":
                Path(self.out).write_text(blob, encoding="utf-8")
                self._logger_.info("Wrote JSON context to %s", self.out)
            else:
                print(blob)
            return 0

        # --- markdown / system-reminder text paths ---
        for agent in active_agents:
            text = _context.assemble_context(
                agent, agents_dir, project_root, global_scope=self.global_scope
            )

            if self.format == "system-reminder":
                text = (
                    "<!-- system-reminder: begin -->\n"
                    + text
                    + "\n<!-- system-reminder: end -->"
                )

            if self.out == "-":
                print(f"--- Context for {agent.name} ---\n{text}\n")
            elif self.out:
                Path(self.out).write_text(text, encoding="utf-8")
                self._logger_.info(f"Wrote {agent.name} context to {self.out}")
            else:
                agent.write_context(agents_dir, text, force=False, dry_run=False, logger=self._logger_)

        return 0


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


# --------------------------------------------------------------------------- #
# `dotagents overlays` -- add / remove / list / sync, with skills sync.
#
# Self-contained block (own module deps in `_scope.py` / `_skills.py` /
# `_overlays.py`); the only umbrella touch is registering `Overlays` below.
# Discover-not-track: installed overlays are the dirs under `<scope>/overlays/`.
# --------------------------------------------------------------------------- #


class OverlayAdd(LoggingArgs, Cmd):
    """Install overlay(s) by name into a scope, and publish their skills.

    Resolves each ``<name>`` against the source (``--source`` /
    ``$DOTAGENTS_OVERLAYS_SRC``, default the bundled ``overlays/``), copies it into
    ``<scope>/.agents/overlays/<name>/`` (discoverable), merges its D59
    routing/rules into the installed ``AGENTS.md`` managed block (additive), and
    publishes its ``skills/`` into the shared ``<scope>/.agents/skills/`` so every
    agent sees them. ``--copy`` mirrors skills as real dirs instead of symlinks
    (Windows / no-symlink)."""

    _parsername_ = "add"

    name: "list[str]" = []
    "Overlay name(s) to install (resolved against the source)."
    ("name",)

    source: Optional[str] = None
    "Overlay source directory (default: $DOTAGENTS_OVERLAYS_SRC or the bundled overlays/)."
    ("--source",)

    global_scope: bool = False
    "Install into the user scope (~/.agents) instead of the project scope."
    ("--global", "-g")

    agents_dir: Path = Path.home() / ".agents"
    "User-scope agents dir (default: ~/.agents)."
    ("--agents-dir",)

    copy: bool = False
    "Copy skills into the shared dir instead of symlinking (no-symlink fallback)."
    ("--copy",)

    dry_run: bool = False
    "Show what would happen without touching anything."
    ("--dry-run",)

    def __call__(self) -> int:
        from dotagents import _overlays, _scope, _skills

        if not self.name:
            self._logger_.warning("no overlay name given; nothing to add")
            return 0
        source = _scope.resolve_source(self.source)
        scope = _scope.resolve_scope(self.global_scope, agents_dir=self.agents_dir)
        self._logger_.info("scope: %s (%s)", scope.level, scope.agents_root)
        agents_md = scope.agents_root / "AGENTS.md"

        for name in self.name:
            overlay_src = source.overlay_dir(name)
            dest_dir = scope.overlay_dir(name)
            copied, skipped, lines = _overlays.install_overlay_dir(
                overlay_src, dest_dir, self.dry_run
            )
            for line in lines:
                self._logger_.info(line)
            self._logger_.info(
                "overlay %s: %d file(s) installed, %d skipped%s",
                name, copied, skipped, " [dry-run]" if self.dry_run else "",
            )
            if _overlays.merge_overlay_rules(agents_md, overlay_src, self.dry_run, self._logger_):
                self._logger_.info("merged %s rules/routing into AGENTS.md", name)
            if not self.dry_run:
                published = _skills.publish_overlay_skills(
                    overlay_src, scope.shared_skills_dir, copy=self.copy,
                    logger=self._logger_,
                )
                if published:
                    self._logger_.info("published %d skill(s) from %s", published, name)

        if self.dry_run:
            self._logger_.info("dry-run: no files were written")
        return 0


class OverlayRemove(LoggingArgs, Cmd):
    """Remove installed overlay(s): delete the overlay dir + unpublish its skills.

    Deletes only ``<scope>/.agents/overlays/<name>/`` and unpublishes only the
    skills that overlay published (matched to its own ``skills/`` source) -- never a
    file outside the overlay dir, never another overlay's skill. Broken skill
    symlinks are then swept. The overlay's D59 rules/routing in ``AGENTS.md`` are
    **not** auto-unmerged (a clean managed-block un-merge is deferred, see the
    decision); a warning points at the manual prune when the overlay carried any."""

    _parsername_ = "remove"

    name: "list[str]" = []
    "Overlay name(s) to remove."
    ("name",)

    global_scope: bool = False
    "Operate on the user scope (~/.agents) instead of the project scope."
    ("--global", "-g")

    agents_dir: Path = Path.home() / ".agents"
    "User-scope agents dir (default: ~/.agents)."
    ("--agents-dir",)

    dry_run: bool = False
    "Show what would happen without touching anything."
    ("--dry-run",)

    def __call__(self) -> int:
        from dotagents import _overlays, _scope, _skills

        if not self.name:
            self._logger_.warning("no overlay name given; nothing to remove")
            return 0
        scope = _scope.resolve_scope(self.global_scope, agents_dir=self.agents_dir)
        self._logger_.info("scope: %s (%s)", scope.level, scope.agents_root)

        for name in self.name:
            overlay_dir = scope.overlay_dir(name)
            if not overlay_dir.is_dir():
                self._logger_.warning("overlay %r not installed at %s", name, overlay_dir)
                continue
            manifest = _overlays.read_manifest(overlay_dir)
            has_rules = bool(manifest["routing"] or manifest["rules"])
            if not self.dry_run:
                removed = _skills.remove_overlay_skills(
                    overlay_dir, scope.shared_skills_dir, logger=self._logger_
                )
                if removed:
                    self._logger_.info("unpublished %d skill(s) from %s", removed, name)
                shutil.rmtree(str(overlay_dir))
            self._logger_.info(
                "removed overlay %s (%s)%s",
                name, overlay_dir, " [dry-run]" if self.dry_run else "",
            )
            if has_rules:
                self._logger_.warning(
                    "overlay %s contributed rules/routing to AGENTS.md; those are "
                    "NOT auto-removed -- prune its lines from the managed block by "
                    "hand (or re-run `dotagents install` to regenerate it).", name,
                )

        if self.dry_run:
            self._logger_.info("dry-run: no files were written")
        return 0


class OverlayList(LoggingArgs, Cmd):
    """List overlays: those installed in the scope, and those available from source.

    ``installed`` is discovered by presence under ``<scope>/.agents/overlays/``; no
    registry file. ``available`` is what the source offers. ``--json`` emits both as
    a machine-readable object."""

    _parsername_ = "list"

    source: Optional[str] = None
    "Overlay source directory (default: $DOTAGENTS_OVERLAYS_SRC or the bundled overlays/)."
    ("--source",)

    global_scope: bool = False
    "List the user scope (~/.agents) instead of the project scope."
    ("--global", "-g")

    agents_dir: Path = Path.home() / ".agents"
    "User-scope agents dir (default: ~/.agents)."
    ("--agents-dir",)

    json: bool = False
    "Emit JSON instead of plain text."
    ("--json",)

    def __call__(self) -> int:
        import json as _json

        from dotagents import _scope

        scope = _scope.resolve_scope(self.global_scope, agents_dir=self.agents_dir)
        installed = _scope.discover_overlays(scope)
        try:
            available = _scope.resolve_source(self.source).available()
        except SystemExit:
            available = []

        if self.json:
            print(_json.dumps({
                "scope": scope.level,
                "root": str(scope.overlay_root),
                "installed": installed,
                "available": available,
            }, indent=2))
            return 0

        self._logger_.info("scope: %s (%s)", scope.level, scope.overlay_root)
        print("installed (%s):" % scope.level)
        if installed:
            for n in installed:
                print("  %s" % n)
        else:
            print("  (none)")
        print("available (source):")
        if available:
            for n in available:
                mark = " *" if n in installed else ""
                print("  %s%s" % (n, mark))
        else:
            print("  (none)")
        return 0


class OverlaySync(LoggingArgs, Cmd):
    """Refresh installed overlays from source, and resync their skills.

    Re-applies each installed overlay (additive: new files land, hand-edits stay),
    re-merges its rules/routing, and refreshes its published skills. An optional
    ``<glob>`` filters which installed overlays to sync (``sync 'py*'``)."""

    _parsername_ = "sync"

    pattern: Optional[str] = None
    "Glob over installed overlay names to sync (default: all)."
    ("pattern",)

    source: Optional[str] = None
    "Overlay source directory (default: $DOTAGENTS_OVERLAYS_SRC or the bundled overlays/)."
    ("--source",)

    global_scope: bool = False
    "Sync the user scope (~/.agents) instead of the project scope."
    ("--global", "-g")

    agents_dir: Path = Path.home() / ".agents"
    "User-scope agents dir (default: ~/.agents)."
    ("--agents-dir",)

    copy: bool = False
    "Copy skills into the shared dir instead of symlinking (no-symlink fallback)."
    ("--copy",)

    dry_run: bool = False
    "Show what would happen without touching anything."
    ("--dry-run",)

    def __call__(self) -> int:
        from dotagents import _overlays, _scope, _skills

        scope = _scope.resolve_scope(self.global_scope, agents_dir=self.agents_dir)
        source = _scope.resolve_source(self.source)
        installed = _scope.discover_overlays(scope)
        names = _scope.filter_names(installed, self.pattern)
        if not names:
            self._logger_.info(
                "no installed overlays%s to sync",
                "" if self.pattern in (None, "*") else " matching %r" % self.pattern,
            )
            return 0
        self._logger_.info("scope: %s (%s)", scope.level, scope.agents_root)
        agents_md = scope.agents_root / "AGENTS.md"

        for name in names:
            try:
                overlay_src = source.overlay_dir(name)
            except SystemExit:
                self._logger_.warning("overlay %r not in source; skipping", name)
                continue
            dest_dir = scope.overlay_dir(name)
            copied, skipped, lines = _overlays.install_overlay_dir(
                overlay_src, dest_dir, self.dry_run
            )
            self._logger_.info(
                "synced %s: %d new file(s), %d unchanged%s",
                name, copied, skipped, " [dry-run]" if self.dry_run else "",
            )
            _overlays.merge_overlay_rules(agents_md, overlay_src, self.dry_run, self._logger_)
            if not self.dry_run:
                _skills.resync_overlay_skills(
                    overlay_src, scope.shared_skills_dir, logger=self._logger_
                )

        if self.dry_run:
            self._logger_.info("dry-run: no files were written")
        return 0


class Overlays(LoggingArgs, Cli):
    """Manage opt-in overlays by name: add / remove / list / sync (+ skills sync)."""

    _parsername_ = "overlays"
    _subcommands_ = [OverlayAdd, OverlayRemove, OverlayList, OverlaySync]

    def __call__(self) -> int:
        self._logger_.info("pick an overlays subcommand: add, remove, list, sync")
        return 0


class Dotagents(LoggingArgs, Cli):
    """Umbrella CLI for installing and building the dotagents config."""

    _version_ = __version__
    _subcommands_ = [Init, Install, Link, Sync, Audit, BuildPyz, Context, Overlays]

    def __call__(self) -> int:
        self._logger_.info(
            "pick a subcommand, e.g. `init`, `install`, `overlays`, `link`, `sync`, "
            "`audit`, `build-pyz`"
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
