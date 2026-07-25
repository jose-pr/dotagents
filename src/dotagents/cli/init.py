"""`dotagents init` -- lay down the neutral base config (+ optional wrappers)."""

import sys
from pathlib import Path
from typing import Optional

from dotagents.cli._common import BASE_ROOT, DotAgentsArgs, _apply_base, _resolve_from


class Init(DotAgentsArgs):
    """Lay down the neutral base config -- the `AGENTS.md` scaffolding and design-log
    convention, never the opinionated overlays (those come from `overlays add`).

    Scope: **project** by default (``<cwd>/.agents``), or the **user** store with
    ``-g/--global`` (``~/.agents``). ``--dest`` overrides the resolved location.
    ``--bin-dir`` additionally writes ``dotagents`` wrapper scripts there so the
    command is on your PATH (only meaningful when running from a built ``.pyz``).
    """

    _parsername_ = "init"

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

    no_hooks: bool = False
    "Skip wiring agent hooks and the shared skills link into the agent's config dir."
    ("--no-hooks",)

    def __call__(self) -> int:
        src = _resolve_from(self.from_, BASE_ROOT)
        if self.dest is not None:
            dest = Path(self.dest).expanduser().resolve()
        else:
            scope = self.resolve_scope()
            dest = Path(scope.agents_root).expanduser().resolve()
            self._logger_.info("scope: %s (%s)", scope.level, dest)

        agent_names = []
        if self.agents:
            for a in self.agents:
                agent_names.extend([x.strip() for x in a.split(",") if x.strip()])

        _apply_base(
            Path(src), dest, self.force, self.dry_run, self._logger_,
            agents=agent_names if agent_names else None,
            wire_hooks=not self.no_hooks,
        )

        if not self.dry_run:
            from dotagents._wrappers import check_path_warning, write_wrappers

            pyz_path = Path(sys.argv[0]).resolve()
            if pyz_path.suffix != ".pyz":
                # Running from a plain install (not a pyz): the wrappers point at
                # `python -m dotagents` instead of a nonexistent pyz path.
                pyz_path = None

            # `<scope>/bin/` is always populated, with a path relative to the scope
            # so the store stays relocatable. Everything downstream of `init` --
            # the SessionStart hook, overlay `bin/` PATH entries, an overlay setup
            # script calling a sibling -- shells out to `dotagents` by name, so a
            # scope without it is a scope where those silently fail.
            targets = [(dest / "bin", True)]
            if self.bin_dir is not None:
                targets.append((Path(self.bin_dir), False))

            for bin_dir, relative in targets:
                if pyz_path is not None:
                    for w in write_wrappers(bin_dir, pyz_path, relative=relative):
                        self._logger_.info("wrapper: %s", w)
                else:
                    self._logger_.info(
                        "skipped wrapper install in %s: not running from a .pyz "
                        "(use build-pyz first)", bin_dir,
                    )

            if self.bin_dir is not None:
                warning = check_path_warning(Path(self.bin_dir))
                if warning:
                    self._logger_.warning(warning)

        if self.dry_run:
            self._logger_.info("dry-run: no files were written")
        return 0
