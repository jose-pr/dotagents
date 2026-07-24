"""`dotagents sync` -- reconcile a copy-mode project, then hand off transport."""

from pathlib import Path
from typing import Optional

from duho import Cmd, LoggingArgs


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
