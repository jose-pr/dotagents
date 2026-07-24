"""`dotagents link` -- point a project's .agents at a store outside the repo."""

from pathlib import Path
from typing import Optional

from duho import Cmd, LoggingArgs


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
