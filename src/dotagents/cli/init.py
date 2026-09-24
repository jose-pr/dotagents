"""`dotagents init` -- lay down the neutral base config (+ optional wrappers)."""

import sys
from pathlib import Path
from typing import Optional

from dotagents.cli._common import (
    BASE_ROOT,
    DotAgentsArgs,
    _apply_base,
    _resolve_from,
    read_store_config,
    write_store_config,
)


class Init(DotAgentsArgs):
    """Lay down the neutral base config -- the store's `AGENTS.md` managed block
    plus each harness's include and hooks -- never the opinionated overlays
    (those come from `overlays add`).

    Scope: **project** by default (``<cwd>/.agents``), or the **user** store with
    ``-g/--global`` (``~/.agents``). ``--dest`` overrides the resolved location.
    ``--bin-dir`` additionally writes ``dotagents`` wrapper scripts there so the
    command is on your PATH: at the ``.pyz`` when run from one, else running
    ``"<this python>" -m dotagents``. ``<scope>/bin/`` always gets them.
    """

    _parsername_ = "init"

    dest: Optional[Path] = None
    "Explicit destination, overriding the resolved scope (project/user)."
    ("--dest",)

    from_: Optional[str] = None
    "Source directory/URI for the base overlay (default: the one recorded by an earlier --from, else bundled)."
    ("--from",)

    bin_dir: Optional[Path] = None
    "Also write dotagents/dotagents.cmd wrapper scripts here (puts the command on PATH)."
    ("--bin-dir",)

    dry_run: bool = False
    "Show what would be written without touching anything."
    ("--dry-run",)

    force: bool = False
    "Replace AGENTS.md wholesale (with backup) instead of block-merging."
    ("--force",)

    agents: "list[str]" = []
    "List of agents to install for (e.g. claude,gemini). Default: auto-detect + claude."
    ("--agents",)

    no_hooks: bool = False
    "Skip wiring agent hooks and the shared skills link into the agent's config dir."
    ("--no-hooks",)

    powershell_env_hook: bool = False
    (
        "Windows, Claude Code: also wire the PowerShell-tool env loader. It "
        "AUTO-APPROVES every PowerShell tool call (skips the permission prompt)."
    )
    ("--powershell-env-hook",)

    def __call__(self) -> int:
        project = False
        scope_level, project_root = None, None
        if self.dest is not None:
            dest = Path(self.dest).expanduser().resolve()
        else:
            scope = self.resolve_scope()
            dest = Path(scope.agents_root).expanduser().resolve()
            project = not scope.global_scope
            scope_level, project_root = scope.level, scope.project_root
            self._logger_.info("scope: %s (%s)", scope.level, dest)

        # An explicit --from is recorded in the store's config, so a later plain
        # `init` and every `overlays add/remove/sync` compose over the same base.
        config = read_store_config(dest)
        base_arg = self.from_ if self.from_ is not None else config.get("base")
        src = _resolve_from(base_arg, BASE_ROOT)

        agent_names = []
        if self.agents:
            for a in self.agents:
                agent_names.extend([x.strip() for x in a.split(",") if x.strip()])

        _apply_base(
            Path(src), dest, self.force, self.dry_run, self._logger_,
            agents=agent_names if agent_names else None,
            wire_hooks=not self.no_hooks,
            powershell_env_hook=self.powershell_env_hook,
            project=project,
            scope_level=scope_level,
            project_root=project_root,
        )

        if self.from_ is not None and not self.dry_run:
            local = Path(self.from_).expanduser()
            recorded = str(local.resolve()) if local.exists() else self.from_
            if config.get("base") != recorded:
                config["base"] = recorded
                self._logger_.info("recorded base: %s", write_store_config(dest, config))

        if not self.dry_run:
            from dotagents._wrappers import (
                check_path_warning,
                wrapper_points_at_pyz,
                write_module_wrappers,
                write_wrappers,
            )

            pyz_path = Path(sys.argv[0]).resolve()
            if pyz_path.suffix != ".pyz":
                # Running from a plain install (not a pyz): the wrappers run
                # `"<this python>" -m dotagents` instead of a pyz path.
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
                    written = write_wrappers(bin_dir, pyz_path, relative=relative)
                else:
                    if wrapper_points_at_pyz(bin_dir):
                        self._logger_.warning(
                            "replacing the .pyz wrappers in %s with ones running "
                            "this install (%s -m dotagents)", bin_dir, sys.executable,
                        )
                    written = write_module_wrappers(bin_dir)
                for w in written:
                    self._logger_.info("wrapper: %s", w)

            if self.bin_dir is not None:
                warning = check_path_warning(Path(self.bin_dir))
                if warning:
                    self._logger_.warning(warning)

        if self.dry_run:
            self._logger_.info("dry-run: no files were written")
        return 0
