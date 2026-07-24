"""`dotagents init` -- lay down the neutral base config (+ optional wrappers)."""

import sys
from pathlib import Path
from typing import Optional

from duho import Cmd, LoggingArgs

from dotagents.cli._common import BASE_ROOT, _apply_base, _resolve_from


class Init(LoggingArgs, Cmd):
    """Lay down the neutral base config -- the `AGENTS.md` scaffolding and design-log
    convention, never the opinionated overlays (those come from `overlays add`).

    Scope: **project** by default (``<cwd>/.agents``), or the **user** store with
    ``-g/--global`` (``~/.agents``). ``--dest`` overrides the resolved location.
    ``--bin-dir`` additionally writes ``dotagents`` wrapper scripts there so the
    command is on your PATH (only meaningful when running from a built ``.pyz``).
    """

    _parsername_ = "init"

    global_scope: bool = False
    "Init the user store (~/.agents) instead of this project's <cwd>/.agents."
    ("--global", "-g")

    agents_dir: Optional[Path] = None
    "User store location for -g (default ~/.agents; configurable via $AGENTS_STORE_DIR)."
    ("--agents-dir",)

    dest: Optional[Path] = None
    "Explicit destination, overriding the resolved scope (project/user)."
    ("--dest",)

    from_: Optional[str] = None
    "Source directory/URI for the base overlay (default: bundled overlay)."
    ("--from",)

    bin_dir: Optional[Path] = None
    "Also write dotagents/dotagents.cmd wrapper scripts here (puts the command on PATH)."
    ("--bin-dir",)

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
