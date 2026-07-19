# Migrate the dotagents CLI to duho >= 0.3.3

Status: done
Executor: self (main thread — held full context from the link/sync work and had already
diagnosed the duho-version mismatch; a cold subagent would re-derive the duho API split
and the zipapp introspection path at higher cost). Decision: D38.

Latest duho is 0.3.3; the repo pinned 0.1.1. duho's Plan-13 `Args`/`Cmd` split is
breaking — a bare `LoggingArgs` command is no longer runnable — so the whole CLI (even
`init`) failed on a box with 0.3.3 until migrated.

## Progress
- [x] Investigate the 0.3.3 API: `LoggingArgs` is a data mixin; runnable commands are
      `(LoggingArgs, Cmd)` with `__call__`; root is `(LoggingArgs, Cli)`; `_subcommands_`
      / `_parsername_` / field syntax unchanged; `main` still applies logging then calls
      `__call__`.
- [x] Rewrite `src/dotagents/cli.py`: 6 leaf commands → `(LoggingArgs, Cmd)`, umbrella →
      `(LoggingArgs, Cli)`, all `__run__` → `__call__`; import `Cmd`/`Cli`.
- [x] Bump `pyproject` `duho>=0.3.3`; `build-pyz` vendored default → `0.3.3`. Confirmed
      duho 0.3.3 is `requires-python >=3.9` (floor holds).
- [x] Fix the zipapp regression: duho reads field flags/help from module source, which
      is unreadable in a `.pyz`, so flags/help degraded (`--from`→`--from-`, `link`
      positional→`--path`). Added `cli._repoint_zipapp_sources()` (extract
      `dotagents.cli`/`duho.presets`, repoint `__file__`) called at the top of `main`.
- [x] Verify: `--version`/`--help`/all subcommands, full link/sync suite, and the built
      `.pyz` (positional, `--from`, `-v`/`--loglevel`, help text, sync copy-back) all
      green on duho 0.3.3; three audit gates PASS.
- [x] CI: add a `build-pyz` + `pyz link <path>` positional smoke so a future duho change
      can't silently re-break the zipapp.
- [x] Docs: CHANGELOG (Changed), AGENTS.md (0.3.3 API + zipapp gotcha), D38 + index.

## Known Facts & Context
- Verify against the duho the project actually targets: a globally-installed newer/older
  duho can mask or fake failures. After the bump, the target IS the installed 0.3.3.
- The repoint shim is a workaround for an upstream duho bug (`getclsdef` swallows the
  `_module_index` `OSError` before its `inspect.getsource` fallback), filed as
  jose-pr/duho#1. Drop it (and the build-pyz CI guard) once that lands; the CI smoke
  will catch a regression either way.
