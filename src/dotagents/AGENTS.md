# dotagents — package API header

Public API of the installed `dotagents` package: the CLI umbrella plus the `_*.py`
helper modules that back it. This file ships inside the package so a consuming agent
can read it without the source. Full docs: https://jose-pr.github.io/dotagents/

## Entry points

- `dotagents.cli.main(argv=None) -> int` — the `install.py` shim and
  `python -m dotagents` entry point. Repoints zipapp sources, then dispatches through
  `duho.app` with the discovered command set. Also the `dotagents` console script
  (`[project.scripts]`).
- `dotagents.cli.Dotagents(LoggingArgs, Cli)` — the umbrella CLI class.
- Compiled command classes live in `dotagents.cli.<name>` (`init`, `audit`,
  `overlays`, `context`, `env`, `build_pyz`); each is a `class X(LoggingArgs, Cmd)`
  with a `__call__`. `audit` stays a built-in — a STRUCTURAL config validator (no
  personal data, D84). `link` / `sync` are **discovered** command modules (D76),
  shipped in the bundled `_overlay/dotagents/cmds/` dir. `leak-check` is not in the
  repo at all — it is a personal command module the user keeps in their private
  `<scope>/dotagents/cmds/` (D84).
- Command discovery layers sources, later wins: built-ins < bundled `cmds` <
  overlay `cmds` (`<overlay-root>/cmds`) < scope `cmds` dirs (user + project) <
  `$AGENTS_CMDS_PATH` < `--cmdspath`. The overlay + scope tiers come from one
  Contract-A `get_file_paths` walk (`cli._cmds_dirs`), the same resolver that
  backs `bin`/PATH.

## Helper modules (public surface)

- `_agents` — `Agent` base type + per-agent adapters; `stamp_identity(...)` emits the
  standardized `AGENTS_*` / `AGENT` identity vars.
- `_overlays` — `install_overlay` / `read_manifest` / `find_setup_script` /
  `run_setup_script`; installs an overlay's files and collects its `routing` / `rules`
  contributions to the managed `AGENTS.md` block. `DEFAULT_PRIORITY = 500`.
- `_scope` — `resolve_scope(global_scope, agents_dir=None)` and `resolve_source(...)`;
  scope = *where installed overlays live* (user = the configurable store, project =
  `<project>/.agents`), source = *where an overlay comes from* (bundled by default).
  Installed overlays are **discovered** by presence, not tracked in a registry.
- `_context` — assemble the effective per-agent context (Plan 04); reads overlay
  `priority` from the manifest (lower sorts earlier).
- `_env` — chained env-file assembly + `env.py` execution (frozen contract B):
  `get_environment` / `get_diff` / `resolve_env_files` / `get_env_from_py` /
  `get_env_from_file`. Bins onto PATH first, then two tiers (`pre.env*` then `env*`),
  later-overrides-earlier. Identity seeded before the chain; proxy vars applied after.
- `_resolve` — `get_file_paths(*names, agents_dir, project_root, global_scope=False,
  include_missing=False)`: the Contract-A precedence walk / filename resolution.
- `_merge` — managed-block merge for `init`'s `AGENTS.md` / `CLAUDE.md`, delimited by
  `<!-- dotagents:begin -->` / `<!-- dotagents:end -->`. Detection is by marker
  presence only, so it survives user reformatting.
- `_link` — `link_project(...)`, `sync_agents(...)`, `store_root(agents_dir,
  store_dir=None)`, `project_store(...)`, `resolve_name(project_dir, name)`. Pure
  stdlib (no `pathlib_next`) so it works in a plain `pip install` and in the `.pyz`.
- `_skills` — publish an overlay's `skills/<name>/` into a scope's shared skills dir
  (symlink-preferred, copy fallback); unpublish removes only what the overlay
  published, then sweeps broken symlinks. Pure stdlib.
- `_sync` — `PathSyncer` wrapper reproducing `install`'s backup/copy/report; requires
  `pathlib_next.Path` instances (not plain `pathlib.Path`) and a pre-created parent dir.

## Environment variables

The prefix split (D80): **`AGENTS_*`** names everything about the `.agents` / agent
world (paths, scope, overlays, sync) — non-secret, safe to emit; **`DOTAGENTS_*`** is
reserved for genuinely tool-internal config and secrets, so the "never print
`DOTAGENTS_*` values" leak guard (D48) stays a simple blanket ban over exactly the
sensitive set.

Config / path / sync vars (`AGENTS_*`, non-secret — read, and some emitted):

- `AGENTS_HOME` — the configurable user-scope store path (default `~/.agents`). Also
  **emitted** by `dotagents env` (D79) and set for overlay setup scripts / sync hooks.
- `AGENTS_STORE_DIR` — per-project store location (absolute paths allowed).
- `AGENTS_OVERLAYS_SRC` — default overlay source dir for `overlays`.
- `AGENTS_CMDS_PATH` — extra command-module search paths (os.pathsep-split).
- `AGENTS_OVERLAY_DIR` — set for an overlay's setup script (its own installed dir).
- `AGENTS_REMOTE` / `AGENTS_SYNC_MESSAGE` — private-store sync (tokenless remote URL /
  commit message).

Every reader above prefers the `AGENTS_*` name and falls back to the old
`DOTAGENTS_*` name (`DOTAGENTS_AGENTS_DIR`, `DOTAGENTS_STORE_DIR`,
`DOTAGENTS_OVERLAYS_SRC`, `DOTAGENTS_CMDS_PATH`, `DOTAGENTS_OVERLAY_DIR`,
`DOTAGENTS_AGENTS_REMOTE`, `DOTAGENTS_SYNC_MESSAGE`) for one release — deprecated,
removable next. Setters emit both names this release.

Tool-internal / secret vars (`DOTAGENTS_*` — kept; read, **never printed**):

- `DOTAGENTS_AGENTS_TOKEN` — **secret** (fine-grained PAT) for private-store auth.
- `DOTAGENTS_CLI_INSTALL` — pip spec to install the CLI itself (tool-specific).
- `DOTAGENTS_AUDIT_PATTERNS` — path to the machine-local audit-pattern file (tooling).

Emitted by the identity/env layer (safe to branch on in env files):
`AGENTS_HARNESS`, `AGENTS_VENDOR`, `AGENTS_MODEL`, `AGENTS_AGENT` / `AGENT`,
`AGENTS_CODE_SESSION_ID`, `AGENTS_PROXY`, `AGENTS_WEBFETCH_PROXY_URL`, plus the two
scope roots — `AGENTS_HOME` (the user store, `agents_dir`/`~/.agents`) and
`AGENTS_PROJECT_ROOT` (this project's root). Both are seeded only if unset, so a
harness/env can pin them. `resolve_scope` READS `AGENTS_PROJECT_ROOT` (then the
agent-native `CLAUDE_PROJECT_DIR`, then cwd) for the project scope's root.

## Gotchas

- **Python 3.9 floor.** Files using bare `X | Y` unions in runtime-evaluated positions
  need `from __future__ import annotations`. `Path.write_text(..., newline=...)` is
  3.10+, so wrapper-script writers use `open(path, "w", newline="")`.
- **Zipapp source shim.** Inside a `.pyz`, `Path(__file__)` is not a real file, so
  duho's AST flag/help introspection degrades (`--from` → `--from-`, positionals lost).
  `cli.main()` calls `_repoint_zipapp_sources()` first, extracting the built-in command
  modules to real temp files. Discovered `cmds` modules are extracted by
  `_package_data_dir` before import, so they need no repoint.
- **Package data in a `.pyz`.** `_package_data_dir()` resolves `skeleton/` and the
  bundled payload via `importlib.resources` (a zip-backed `Traversable` is extracted to
  a temp dir once), never `Path(__file__).exists()` (always False in a zipapp).
- **`pathlib_next` needs `typing_extensions` on Python < 3.10** (an upstream gap); a
  3.9 environment must `pip install typing_extensions`.
