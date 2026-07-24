"""`dotagents link-project` -- point a project's .agents at a store outside the repo.

Discovered command module shipped BY THIS OVERLAY (D84 per-overlay `cmds/`
discovery, D85): `dotagents.cli._discover` walks each installed overlay's
`<overlay-root>/cmds/` dir, so installing `private-sync` is what makes
`link-project` exist. Plain dotagents ships no private-sync command (D85) -- both
the command and its logic (`../lib/_link.py`) live here.

Renamed from the old bare `link` (D85): the command acts on **a project's
`.agents`**, and the underlying function was already `link_project()`.

Importing the logic: `duho.discover_commands` imports this file as a standalone
module under a synthesized name, not as a package member -- so neither a relative
import nor a plain sibling import is available. `_load_link()` below resolves
`<overlay-root>/lib/_link.py` **by path**, which needs no PYTHONPATH and works
even if this overlay's `setup.py` never ran. A plain `import _link` (what setup.py
enables) is preferred when it resolves to our module.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Optional

from duho import Cmd, LoggingArgs

#: sys.modules key for the by-path-loaded lib -- namespaced so it can never
#: collide with an unrelated top-level `_link` module, and shared with
#: `sync_project.py` so both commands use one instance.
_LIB_KEY = "dotagents_private_sync_link"

#: `<overlay-root>/cmds/link_project.py` -> `<overlay-root>`.
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
        # Only trust it if it is really ours: a stray `_link.py` elsewhere on
        # PYTHONPATH must not be mistaken for the private-sync logic.
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


class LinkProject(LoggingArgs, Cmd):
    """Point a project's .agents at a store outside the project repo.

    Symlinks ``<project>/.agents`` to its store (``--copy`` mirrors it as a real
    dir instead, for Windows / no-symlink environments). An existing real
    ``.agents/`` is adopted into an empty store on the first link. Optional: this
    is one way to keep private agent state out of a public repo, not something
    dotagents requires."""

    _parsername_ = "link-project"

    path: Path = Path(".")
    "Project directory to link (default: current directory)."
    ("path",)

    agents_dir: Path = Path.home() / ".agents"
    "Global agents dir (default: ~/.agents)."
    ("--agents-dir",)

    store_dir: Optional[str] = None
    ("Where stores live: relative to --agents-dir, or absolute to put them "
     "elsewhere entirely (default: projects, or $AGENTS_STORE_DIR).")
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
        _load_link().link_project(
            self.path, self.agents_dir, name=self.name, store_dir=self.store_dir,
            copy=self.copy, force=self.force, dry_run=self.dry_run,
            logger=self._logger_,
        )
        if self.dry_run:
            self._logger_.info("dry-run: no files were written")
        return 0
