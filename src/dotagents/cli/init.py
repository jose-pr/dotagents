"""`dotagents init` -- install the minimal neutral base overlay."""

from pathlib import Path
from typing import Optional

from duho import Cmd, LoggingArgs

from dotagents.cli._common import BASE_ROOT, _apply_base, _resolve_from


class Init(LoggingArgs, Cmd):
    """Install the minimal neutral base overlay (never the full opinionated payload).

    Scope: **project** by default (``<cwd>/.agents``), or the **user** store with
    ``-g/--global`` (``~/.agents``). ``--dest`` overrides the resolved location.
    """

    _parsername_ = "init"

    global_scope: bool = False
    "Init the user store (~/.agents) instead of this project's <cwd>/.agents."
    ("--global", "-g")

    agents_dir: Optional[Path] = None
    "User store location for -g (default ~/.agents; configurable via $DOTAGENTS_STORE_DIR)."
    ("--agents-dir",)

    dest: Optional[Path] = None
    "Explicit destination, overriding the resolved scope (project/user)."
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
        if self.dest is not None:
            dest = Path(self.dest).expanduser().resolve()
        else:
            from dotagents import _scope

            scope = _scope.resolve_scope(self.global_scope, agents_dir=self.agents_dir)
            dest = Path(scope.agents_root).expanduser().resolve()
            self._logger_.info("scope: %s (%s)", scope.level, dest)

        agent_names = []
        if self.agents:
            for a in self.agents:
                agent_names.extend([x.strip() for x in a.split(",") if x.strip()])

        _apply_base(Path(src), dest, self.force, self.dry_run, self._logger_, agents=agent_names if agent_names else None)
        if self.dry_run:
            self._logger_.info("dry-run: no files were written")
        return 0
