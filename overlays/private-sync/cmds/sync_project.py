"""`dotagents sync-project` -- reconcile a copy-mode project, then hand off transport.

Discovered command module shipped BY THIS OVERLAY (D84 per-overlay `cmds/`
discovery, D85): `dotagents.cli._discover` walks each installed overlay's
`<overlay-root>/cmds/` dir, so installing `private-sync` is what makes
`sync-project` exist. Plain dotagents ships no private-sync command (D85) -- both
the command and its logic (`../lib/_link.py`) live here.

Renamed from the old bare `sync` (D85): the command reconciles and pushes **a
project's `.agents` store** (and the bare name collided conceptually with
`overlays sync`, which refreshes overlays -- a different thing entirely).

See `link_project.py` for why the logic is loaded by path rather than imported:
discovered command modules are imported standalone, so `_load_link()` resolves
`<overlay-root>/lib/_link.py` directly and needs no PYTHONPATH or setup run.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Optional

from duho import Cmd, LoggingArgs

#: Shared with `link_project.py` -- one loaded lib instance per process.
_LIB_KEY = "dotagents_private_sync_link"

#: `<overlay-root>/cmds/sync_project.py` -> `<overlay-root>`.
OVERLAY_ROOT = Path(__file__).resolve().parent.parent


def _load_link():
    """Return this overlay's `_link` module (PYTHONPATH import, else by path)."""
    cached = sys.modules.get(_LIB_KEY)
    if cached is not None:
        return cached
    try:
        import _link as module  # type: ignore[import-not-found]
    except ImportError:
        module = None
    else:
        if not (hasattr(module, "link_project") and hasattr(module, "sync_agents")):
            module = None
    if module is None:
        source = OVERLAY_ROOT / "lib" / "_link.py"
        if not source.is_file():
            raise ImportError("private-sync overlay is incomplete: no %s" % source)
        spec = importlib.util.spec_from_file_location(_LIB_KEY, str(source))
        if spec is None or spec.loader is None:
            raise ImportError("could not load the private-sync lib from %s" % source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[_LIB_KEY] = module  # register before exec (self-reference safe)
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(_LIB_KEY, None)
            raise
        return module
    sys.modules[_LIB_KEY] = module
    return module


class SyncProject(LoggingArgs, Cmd):
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
    the rewrite -- so a standalone ``dotagents sync-project`` works without being
    run through the private-sync Stop hook."""

    _parsername_ = "sync-project"

    agents_dir: Path = Path.home() / ".agents"
    "Global agents dir (default: ~/.agents)."
    ("--agents-dir",)

    store_dir: Optional[str] = None
    ("Where stores live: relative to --agents-dir, or absolute to put them "
     "elsewhere entirely (default: projects, or $AGENTS_STORE_DIR).")
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
        return _load_link().sync_agents(
            self.agents_dir, message=self.message, project_dir=self.project,
            name=self.name, store_dir=self.store_dir, remote=self.remote,
            pull=not self.no_pull, push=not self.no_push, dry_run=self.dry_run,
            logger=self._logger_,
        )
