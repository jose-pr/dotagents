# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `env` exports `AGENTS_PYTHON`: the interpreter `dotagents` itself is running
  under (`sys.executable`), seeded only if unset like the scope roots. Shims
  and helper scripts now have a default Python they can trust, where a bare
  `python`/`python3` on PATH may be a Store alias stub, an emulated build, a
  venv's, or missing.

### Fixed

- `init` no longer registers the PowerShell hook variants on POSIX hosts. A
  `shell: powershell` entry where no PowerShell exists is not harmless: the
  text is run as bash and fails with a syntax error on every session (measured
  in WSL). Linux and macOS get the bash handler only; Windows keeps both, and
  the PowerShell one still selects itself only when `bash` is absent.
- Command discovery also survives a module that calls `sys.exit()` at import.
  `SystemExit` is not an `Exception`, so such a module (a project-scope
  override refusing to load without its user-scope base) escaped the
  resilience guard and killed every invocation, `init -g` for a store that
  did not exist yet included. It is skipped with a warning like any other
  broken source.

## [0.4.0] - 2026-09-09

### Security

- `env --format export` (the form the SessionStart hook writes into
  `$CLAUDE_ENV_FILE`) now single-quotes every value (`'` → `'\''`, control
  characters via bash's `$'...'`). Values were JSON-quoted, i.e. inside DOUBLE
  quotes, so `$(...)`, backticks and `$VAR` in any env value were executed or
  expanded when the file was sourced, while `\n` and non-ASCII (`\u00e9`)
  arrived as literal escape text.
- The env chain no longer executes or sources a project's own top-level
  `env.py` / `env`: only `<project>/.agents/*` and the user-local
  `local.env` / `pre.local.env` run at the project levels. Every session start
  ran the chain, so opening a session in a cloned repository with a top-level
  `env.py` was code execution from that checkout.

### Added

- feat: **`dotagents findings`** — a per-scope findings queue, shipped as the
  one bundled command module (`_overlay/dotagents/cmds/findings.py`). A
  finding is one markdown file shaped like an agent memory (frontmatter
  `name`/`description`/`status`/`created` for the index line, details in the
  body) under `<scope-root>/findings/`: the project's `.agents/findings/` by
  default, the user store's with `-g`, anywhere with `--dir`. `add` records
  one (`--body`/`--body-file`, `-` = stdin), `list` prints the active ones
  (`--all`, `--processed`, `--json`), `show` prints one (`--json`), `done`
  appends a required `## Resolution` and MOVES the file to `processed/`
  (never deletes), `reopen` moves it back, `remove` deletes a mistaken one,
  `index` regenerates `INDEX.md` (active first, then processed — every
  mutating command rewrites it), `path` prints the queue's location.
  Hand-written notes without a frontmatter are listed too and gain one when
  first rewritten. Files are written LF-only on every platform.
- chore: `init` no longer copies bundled command modules (`*.py`) from the
  package's `dotagents/cmds/` into the store — only the README. The bundled
  dir is always a discovery source, so a copy added nothing, and being
  create-if-absent it would have pinned the first-installed version of
  `findings` and shadowed every later one. A same-named module dropped into a
  scope's `dotagents/cmds/` still overrides the bundled one.
- feat: `env` prepends every level's existing **`lib/`** dir to `PYTHONPATH`,
  the way it already prepends every level's `bin/` to `PATH` — overlays first,
  then the store, then the project's `.agents/`, never the project root — and
  does so before the env-file chain, so an `env.py` (and every subprocess that
  inherits the env) can `import` an overlay's `lib/` module. Only dirs that
  exist are added, and `PYTHONPATH` is untouched when there are none. The
  formatter already converts any `*PATH` variable between Windows and POSIX
  forms, so `PYTHONPATH` gets the same treatment as `PATH`.
- feat: `env` emits one **`<NAME>_OVERLAY_ROOT`** per installed overlay (the
  overlay's install dir), seeded before the env-file chain like the two scope
  roots and, like them, only if unset. `NAME` is the overlay's directory name
  upper-cased with every non-alphanumeric character turned into `_`
  (`my-ov.v2` → `MY_OV_V2_OVERLAY_ROOT`). This is the same name `context`
  already expanded as a `<NAME_OVERLAY_ROOT>` placeholder; the two now share
  one naming function, so an env file and a context file name an overlay's
  install dir identically. The variable is derived from the same normalized
  name `overlays add` installs under (`Overlay.normalize_name`), so the var
  for `overlays/<n>/` is always `Overlay.root_var_for(n)`.

### Changed

- The overlay-root note `overlays add` writes above routing lines cites
  `$ENGINEERING_OVERLAY_ROOT` as its example; the docs and CI install the
  `engineering` overlay, which now carries the flows, references, and tools that
  were separate overlays (each is a category dir under it, never `<name>/<name>/`).
- The contract-A walk is `Scope.paths(*names, include_missing=False)`; the
  `_resolve` module (and its API docs page) is gone, its `LEVEL_NAMES` live in
  `_scope`.
- The system store is a store like the others. `Scope.system_root`
  (`/etc/agents`, or `$AGENTS_SYSTEM_ROOT`) heads `Scope.stores`, and the
  contract-A walk goes store by store -- system, user, project -- each store's
  overlays first, then the store itself, then the project root; the hardcoded
  `/etc/agents` tier and its odd position (after the user store's overlays,
  before the user store) are gone. A system overlay is shadowed by a same-named
  user or project one. `overlays list` prints one `installed (<level>)` block
  per store that has anything, most specific first.
- The library walk takes one object. `Scope` now carries the whole picture
  (`user_root`, `project_root`, `stores`, `overlays`, `files(*names)`), and
  `get_file_paths`, `get_environment`, `get_diff`, `resolve_env_files`,
  `get_bin_paths`, `get_lib_paths`, `get_overlay_roots`, `assemble_context` and
  `assemble_context_data` take a `Scope` instead of the
  `(agents_dir, project_root, global_scope)` keyword triple ported from the
  precursor. `Scope.of(agents_dir=, project_root=, global_scope=)` builds one
  from resolved parts. No compatibility form: the CLI is the public surface.
- **One overlay name, two scopes = one overlay, the project's.** A project
  session uses the user store's overlays AND the project's (`<project>/.agents/
  overlays/`); an overlay installed in both under the same name is the
  project's copy only, which shadows the store's for bin, lib, env files,
  cmds, `CONTEXT.md` and the `<NAME>_OVERLAY_ROOT` var (a store root the
  session had already pinned is re-pointed). `-g` sees the user store alone.
  `Overlay.installed(*stores)` is the one discovery behind the contract-A
  walk, `env`, `context` and `overlays list` / `show`; the `_scope`
  `discover_overlays` pass-through is gone (`Overlay.discover(root)` is the
  single-root form). `overlays list` in a project scope prints both
  `installed (project)` and `installed (user)`, marking shadowed store copies;
  `overlays show` falls back to the user store's copy before the source.
- Overlay routing lines refer to overlay files through `$<NAME>_OVERLAY_ROOT`
  (the variable `env` exports per installed overlay), not a hard `~/.agents/`
  path; when a merged block carries such lines, `AGENTS.md` gets one line
  above them saying what the token is and how to resolve it. The example
  overlays on the `overlays` branch follow this convention and ship no setup
  scripts any more (PATH, PYTHONPATH and the root var are `env`'s job; an
  overlay's own env belongs in its `env.py`). `tools/cloud-setup.sh` finds
  the private-sync settings snippet inside the installed overlay.
- The base overlay's findings workflow now goes through the command. The
  managed `AGENTS.md` rule ("Global-config misses"), `dotagents/DECISIONS.md`
  ("How findings become config") and the skeleton README say
  `dotagents findings add -g ...` to record a miss, `list -g` / `show -g` to
  triage and `done -g <name> -r ...` to close one, instead of "drop a note in
  `~/.agents/dotagents/findings/`". With that, the user store's queue is
  **`~/.agents/findings/`** (the command's `-g` default), no longer
  `~/.agents/dotagents/findings/`. An existing queue at the old path keeps
  working with `--dir ~/.agents/dotagents/findings`, or move it once:
  `mv ~/.agents/dotagents/findings ~/.agents/findings`. Re-run `dotagents init`
  to refresh the managed block.
- refactor: `dotagents._overlays` is now built around an **`Overlay`** class —
  one overlay is one directory, and everything that depends on a single
  overlay is a method or property on it: `.path` / `.name` / `.normalized_name`
  / `.root_var` / `.is_valid` / `.manifest_path`, `.read_manifest()` /
  `.priority` / `.sort_key`, `.find_setup_script()` / `.run_setup(...)`,
  `.files()` / `.rule_blocks(...)` / `.apply_to(...)` / `.install_to(...)` /
  `.merge_rules_into(...)`. The name rules are static methods
  (`Overlay.is_valid_name` / `.normalize_name` / `.root_var_for`), so a caller
  holding only a name uses the same rule as one holding a directory;
  `Overlay.discover(root)` is the single discovery routine that `_scope`,
  the contract-A resolver and `env` all share, and `Overlay.sort_by_priority`
  the single merge order. The module-level functions they replace
  (`read_manifest`, `find_setup_script`, `run_overlay_setup`, `overlay_files`,
  `rule_blocks`, `apply_overlay`, `install_overlay_dir`, `merge_overlay_rules`,
  `overlay_sort_key`, `sort_overlays_by_priority`, `normalize_name`,
  `overlay_root_var`) are gone, as are `_scope.is_valid_overlay_name` and
  `_scope.normalize_overlay_name`. `recompose_overlay_block` stays a module
  function (it works over a set of overlays) and now accepts `Overlay`
  instances or directories. `_overlays` is a private module, so no public
  version signal.

- fix: an `env.py` may now print its changes as one JSON object **per line**,
  merged in order (a later line wins), as well as a single object. Each
  overlay's `setup.py` appends its own managed block to the store's `env.py`
  and each block prints its own object, so a store with two such overlays
  (e.g. `net` + `private-sync`) emitted two lines — and the single-object
  reader rejected the whole output, silently dropping both overlays' vars. A
  line that is not a JSON object still voids the whole script's contribution,
  so a half-applied change set is impossible.
- fix: `init --agents codex` run from a session whose environment already
  carried dotagents' identity vars (which its own env-loader hook exports —
  `AGENT=claude-code` and friends in every command a Claude session runs)
  wrote a Codex env block with no identity at all: the identity stamp never
  overrides a value already present, so every key counted as "already set",
  to Claude's values. When the agent is named explicitly the identity is now
  that agent's, overriding the base env; without an explicit agent a pinned
  value is respected as before.
- fix: the built `.pyz` lost the help text of the umbrella's own `--cmdspath`
  flag (every subcommand's help survived). The zipapp source-repoint shim
  covered each `dotagents.cli.<x>` command module but not the `dotagents.cli`
  package itself, and it resolved a package's source as `cli.py` instead of
  `cli/__init__.py`, so the umbrella kept its zip-internal `__file__` and duho
  fell back to a bare flag. Both fixed; the top-level help in a `.pyz` now
  matches a plain install.
- fix: `context`'s `<NAME_OVERLAY_ROOT>` placeholder now also maps `.` (and any
  other non-alphanumeric character) in an overlay name to `_`, not only `-` —
  the name has to be a legal shell variable to be emitted by `env`, and the
  placeholder follows the same rule. Overlays whose names contain only letters,
  digits, `_` and `-` are unaffected.

### Fixed

- **One broken command module no longer breaks every `dotagents` call.**
  Discovery skips a source that fails to import for any reason (a
  `SyntaxError`, an exception at import time) with a warning naming it. It
  caught `ImportError` only, and duho propagates the rest on purpose, so a
  typo in `~/.agents/dotagents/cmds/foo.py` made `env`, `context`, `init` and
  even `--version` traceback, i.e. the hooks delivered nothing.
- **No more temp-directory litter from the `.pyz`.** Everything a zipapp run
  extracts (package data, the repointed module sources) lives under one
  per-process scratch directory removed at exit; a `mkdtemp` per item with no
  cleanup had left 632 `dotagents-*` directories in one machine's `%TEMP%`.
- `overlays add` installs a source dir spelled `my_overlay` (it normalized to
  `my-overlay` and looked only that up), validates and resolves EVERY name
  against the source before touching anything (`add good bad` used to install
  `good`, run its setup, then fail), installs each manifest's `requires`
  first (transitively; `--no-requires` to skip; a missing requirement warns, a
  cycle errors), publishes skills from the INSTALLED copy (a symlink into the
  source dies with a temporary checkout and could never be matched by
  `remove`), and reports the setup script on a dry run.
- `overlays remove` normalizes names (`add My_Ov` + `remove My_Ov` said "not
  installed") and recomposes the managed block over the overlays that remain,
  so an overlay's rules and routing leave `AGENTS.md` with it; the warning
  that pointed at the removed `dotagents install` is gone.
- `overlays sync` honours `--copy` (declared and ignored) and gains
  `--overwrite`, which replaces installed files whose content differs from the
  source; without it a sync never updated an upstream change to an existing
  file.
- New `overlays show <name>` describes an overlay (installed copy first, else
  the source): description, priority, requires, routing, rules, setup script,
  skills, file count, root var; `--json`.
- The manifest reader strips trailing `#` comments quote-aware and finds an
  array's closing `]` by scanning, so `routing = ["a"] # note` no longer
  swallows the NEXT array (a rules path became a routing line), an indented
  `]` no longer yields `[]`, `'single-quoted'` strings parse, and
  `priority = 5 # low` is 5, not 500. `description` and `requires` are read.
- `_compose_block` with a base that has no `## Load on demand` heading appends
  the overlay rules at the end of the block, as its warning always claimed;
  they were dropped.
- `build-pyz` outside a source checkout is a clear error instead of a
  `FileNotFoundError`.
- `dotagents`, `overlays` and `findings` invoked with no subcommand print
  their help and exit 2 instead of logging a hint and exiting 0.
- `--agents-dir X` on the command line is honoured by command discovery, so
  the store the command is about to use is the one whose `cmds/` are found.
- `findings add` rejects a name already carried by another note's frontmatter
  (`get` matches frontmatter names first, so `done` would have processed the
  wrong file).
- Two overlay dirs with the same manifest `name` sort deterministically (dir
  name is the final tiebreaker).
- **SessionStart context is no longer injected twice.** The PowerShell
  variants of Claude's `SessionStart` / `CwdChanged` handlers run only when
  `bash` is not on PATH. Both handlers fire on every session, and on a Windows
  box that has both Git Bash and PowerShell both succeeded, so the same
  payload landed twice at every session start (two identical 100 KB
  payloads, measured).
- The Codex `PreToolUse` env-loader prefix is quoted correctly: inside
  `"$(...)"` its `\"` were literal quote characters, so `PATH` became
  `".agents/bin:...:<last>"` with the quotes, the project `.agents/bin` was
  never found and the last original entry was broken. The rewritten command
  is now executed in bash by a test.
- Hook merging keeps a user's hook that shares a matcher-object with an older
  shape of ours (the whole object used to be dropped), and a revised `shell`
  / `matcher` / `commandWindows` / status on an unchanged command text now
  reaches existing users instead of being kept as-is.
- Claude's skills link is per skill into `<config>/skills/<name>`: linking the
  whole directory failed with "conflict" for anyone who already had their own
  `~/.claude/skills`, so overlay skills never reached them. A same-named skill
  the user placed there stays.
- Every hook command resolves the store as `$AGENTS_HOME` when set (bash:
  `${AGENTS_HOME:-$HOME/.agents}`), so a custom store gets hooks that can find
  `dotagents`; the PowerShell SessionStart variant prefers the project's own
  `.agents\bin` like the bash one. The PowerShell tool env-loader runs
  `env --diff` instead of re-assigning the whole environment on every call.
- The bash `CwdChanged` handler re-pins `AGENTS_PROJECT_ROOT` into
  `$CLAUDE_ENV_FILE` when the new directory carries a `.agents/`; the
  SessionStart pin is only-if-unset, so a `cd` into another project used to
  keep the first project's root for the rest of the session.
- The Antigravity hook pins the project root from `workspacePaths` (cwd and
  `AGENTS_PROJECT_ROOT` of the `dotagents context` spawn), as its docstring
  already claimed, honours `$AGENTS_HOME` when locating `dotagents`, and
  injects nothing when the assembly exits non-zero. Its constant is
  `PREINVOCATION_HOOK_SCRIPT` (it wires a `PreInvocation` hook).
- Unpublishing an overlay's skills compares file CONTENT, not just names: a
  copy the user had edited (same file set, different bytes) was deleted as
  the overlay's.
- **The store's rules now reach Claude on a fresh install.** `init` writes the
  `@` include where Claude Code reads it -- `~/.claude/CLAUDE.md` for the user
  store, `<project>/.claude/CLAUDE.md` for a project -- as an appended managed
  block, skipped when the include line is already there by hand. Before, it
  wrote `<store>/CLAUDE.md` (which Claude never reads) while `context`
  subtracted `~/.agents/AGENTS.md` as "already loaded" on a static assumption,
  so nothing delivered the base rules. `context` now subtracts exactly what
  the harness's entry files actually `@`-include (`Agent.loaded_paths`),
  recursively, on this machine.
- `context` no longer inlines every `.md` file the sources mention. Inlining is
  opt-in (`--inline` / `inline=True`): the base rules say to read those files
  only when a task needs them, and inlining every mention made a 100 KB
  SessionStart payload, most of it a changelog and an API header that
  happened to be named in prose. With `--inline`, sources and harness-loaded
  files are never inlined a second time, and placeholders inside inlined
  files expand.
- Overlay priority ordering and `<NAME_OVERLAY_ROOT>` placeholder expansion in
  `context` never worked: the resolver labels an overlay entry with the
  overlay's name and the code compared it to the literal `"overlay"`. Every
  installed overlay now gets a placeholder (matching `env`), not only one that
  ships a `CONTEXT.md`.
- `context --write-agent` merges the context into the harness's own
  instruction file under the PROJECT root -- Claude `.claude/CLAUDE.md`, Codex
  `AGENTS.md`, Gemini `GEMINI.md`, Cursor `.cursorrules`, Copilot
  `.github/copilot-instructions.md`, Antigravity `.agents/rules/dotagents.md`
  -- as a managed `dotagents:context` block that is refreshed in place. It
  used to overwrite a file under the user STORE; for Codex that was
  `<store>/AGENTS.md`, a context source, so every run re-inlined the previous
  run's output. `--format` is validated, and `--write-agent` refuses `--format
  json` or an output path instead of silently ignoring one.
- Project-scope overlays (`<project>/.agents/overlays/`, where `overlays add`
  installs by default) are now part of the contract-A walk: their `bin`,
  `lib`, env files, `cmds` and `CONTEXT.md` resolve, after the user store's,
  and each gets a `<NAME>_OVERLAY_ROOT` (a project overlay wins a name clash).
  Nothing consumed them before except the `AGENTS.md` recompose.
- Managed-block markers must be a line of their own: a prose MENTION of the
  markers (the base `AGENTS.md` carries one) was matched as the block, so the
  sentence around it was replaced and a second block appeared. A begin marker
  with no end after it is refused with a clear error instead of gaining a
  second block; a `--from` base without markers is a usage error, not a
  traceback.
- Every managed file (`AGENTS.md`, the includes, `settings.json`, `hooks.json`,
  context targets) is written LF-only on every platform; `settings.json` /
  `hooks.json` are written atomically and keep non-ASCII values as-is.
- The user scope is recognized by the configurable store (`$AGENTS_HOME`), not
  the literal `~/.agents`: `init -g` with a custom store used to wire Claude's
  hooks into `<store-parent>/.claude/settings.local.json`, which nothing reads.
- `CODEX_HOME` no longer marks a running Codex session (it is the user's
  persistent state-dir override, exported from a shell profile); the
  `CODEX_SANDBOX*` vars still do, and `CODEX_HOME` still locates the config.
- An unknown `--agents` name no longer overrides a pinned identity in `env`.
- An overlay cannot be named like a contract-A level (`user`, `project`,
  `system`, `project-root`, `default`, `overlay`); its dir name is its level
  label in the walk and would collide with the per-level filename keys.
- A directory named `env` (a common virtualenv name) is no longer "sourced" as
  an env file; only regular files resolve.
- Sourcing a plain env file that fails (missing, a directory, a syntax error)
  now contributes nothing and warns, as documented. The bash command was a
  `;` list, so `env -0` ran regardless and reported rc 0 with whatever had
  been assigned before the error.
- Bash's own `PWD` (in `/c/...` form), `OLDPWD`, `SHLVL` and `MSYSTEM*` are no
  longer reported as a sourced file's changes; a non-UTF-8 byte in a sourced
  value no longer aborts the whole assembly.
- `env --format auto` returned `cmd` when invoked through the `dotagents.cmd`
  wrapper from PowerShell (the wrapper cannot exec, so `cmd.exe` sits between
  Python and the shell); a `cmd` whose own parent is a shell is now skipped.
- `env` formats: `fish` escapes backslashes; `cmd` doubles `%` and flattens
  newlines; `yaml` quotes values a reader would type (`true`, `123`, `null`,
  `1e3`, leading indicators); `powershell`/`cmd` no longer mangle a
  forward-slash native path (`C:/a/tools` became `C;A:\tools`); output is
  written as UTF-8 bytes so a non-Latin-1 value cannot crash on a cp1252
  console.
- `init -g`, `overlays ... -g` and every other `resolve_scope` caller honour
  `$AGENTS_HOME` (the var `env` itself emits) instead of the literal `~/.agents`,
  and `--agents-dir` overrides the store in the project scope too (it was
  silently ignored without `-g`). `resolve_user_store` moved to `_scope`
  (still re-exported from `dotagents.cli`); the `findings` command's local
  workaround is gone.
- `overlays list` / `overlays add` apply the one overlay-name rule when listing
  a source (`__pycache__`, `2fast`, dotdirs are no longer "available"), and a
  bad source path names whether it came from `--source` or the env var.
- `findings list` / `show` write UTF-8 bytes, so a description with a
  non-Latin-1 character no longer raises on a cp1252 console.

## [0.3.4] - 2026-08-16

### Changed

- chore: raise the dependency floors and scope them to a minor series —
  `duho>=0.5.0,<0.6` (was `>=0.4.0`) and `pathlib_next>=0.9.0,<0.10` (was
  `>=0.8.0`). Both are pre-1.0, where a minor bump is the signal that the
  documented API broke, so the ceiling is what keeps the next one from arriving
  unannounced. Neither floor sits above its `.0` patch: the suite and a CLI
  smoke pass at exactly duho 0.5.0 and pathlib_next 0.9.0, so nothing here needs
  an API added later in either series. The one 0.5.0 behavior change that
  reaches this CLI's surface is list-typed *option* fields taking one value per
  occurrence instead of `nargs="*"` — `--agents` and `--cmdspath` are unaffected,
  since the documented forms are the comma list (`--agents a,b`, split by the
  command itself) and the repeated flag, both of which behave the same either
  way. Exercised end to end against duho 0.5.4 / pathlib_next 0.9.2 on Python
  3.9 and 3.14. The `[uri]`/`[http]`/`[sftp]`/`[s3]` extras stay unversioned
  passthroughs to `pathlib_next`'s own extras.
- chore: `build-pyz`'s vendored pins moved with those floors — the `.pyz` now
  bundles duho 0.5.0 and pathlib_next 0.9.0 (was 0.4.0 / 0.8.0), the minimum the
  package claims to support rather than the latest patch. These pins are a
  second copy of the dependency versions and had drifted a full minor series
  behind, so the shipped zipapp bundled versions `pip install dotagents-cli`
  would have refused. The rebuilt `.pyz` keeps full flag/help/positional
  fidelity through the zipapp shim.

## [0.3.3] - 2026-08-16

### Added

- feat: `env` and `context` gained **`--agents-dir`** (from the shared
  `DotAgentsArgs` base) to override the store for one run. Their `-g/--global`
  keeps its existing, narrower meaning here — *skip the project-level files* —
  and now says so in `--help`.

### Removed

- **`build-pyz` no longer bundles the repo's `tools/`** as `dotagents/_tools`
  inside the built `.pyz`, and the `--tools-dir` flag is gone with it. Nothing
  read `_tools`: the compiled `audit` wrapper and a personal scanner's wrapper that
  shelled out to it no longer exist. Every shipped artifact carried the dead weight while
  `tools/audit.py` claimed it was "not shipped in the `.pyz`" — now true.
- **`dotagents._sync`** — a `pathlib_next.PathSyncer` wrapper that existed only
  to back the `install` subcommand's backup/copy report. `install` was removed
  in 0.3.x; the module has had no callers since, no tests, and a return
  annotation that disagreed with what it returned. `pathlib_next` remains a
  dependency (duho, and `--from` URI support). Its API-reference page went with
  it.

### Fixed

- docs: a cluster of "docs say X, tree does Y" corrections — `tools/audit.py`
  no longer claims to ship as a bundled `audit` command module (it is repo CI
  tooling and there is no `dotagents audit`) and its `--root` help names the
  real default; `install.py`'s usage line drops the removed `install` and the
  never-existing `audit`; the README's `tools/` row says where a personal
  command module actually lives; the API header names the real `_overlays` exports
  (`install_overlay_dir` / `apply_overlay` / `run_overlay_setup`) and the real
  package-data dirs (`_overlay` / `_overlays_src`, never a `skeleton/`); and
  `_merge._extract_block`'s docstring now says it returns the block *including*
  its marker lines, which is what it has always done and what callers rely on.
- fix: **`stamp_identity` no longer pretends to emit `AGENTS_AGENT`.** The line
  sourced the value from `$AGENTS_AGENT` and only assigned when that same key
  was unset, so it could never emit anything — while the docstring and the
  shipped API header both advertised the var. Line removed; the API header's
  "emitted by the identity/env layer" list now drops both `AGENTS_AGENT` and
  `AGENTS_CODE_SESSION_ID` (the latter was deliberately never emitted) and says
  so explicitly, so nothing branches on a var that never arrives.
- fix: **`dotagents env` and `dotagents context` now resolve their roots instead
  of hardcoding them.** Both took `Path.cwd()` as the project root and
  `~/.agents` as the user store, so `$AGENTS_PROJECT_ROOT` (or the agent-native
  `$CLAUDE_PROJECT_DIR`) and `$AGENTS_HOME` were ignored by the two commands
  that most needed them — a `SessionStart` hook runs `dotagents context` from
  wherever the session happens to start, so a pinned project root was silently
  dropped and a relocated store was never read. They now use
  `_scope.project_root_default()` and the new
  `dotagents.cli.resolve_user_store()` (`--agents-dir` → `$AGENTS_HOME` →
  legacy `$DOTAGENTS_AGENTS_DIR` → `~/.agents`), matching what the package's own
  `resolve_scope` docstring and command discovery already promised. Behavior is
  unchanged when none of the vars are set.

## [0.3.2] - 2026-07-25

### Added

- feat: **`AntigravityAgent`** — context injection for Google's Antigravity
  CLI/IDE (a separate product from Gemini CLI, despite sharing the `~/.gemini/`
  namespace for some files). Antigravity's hooks have no `SessionStart`
  equivalent — only `PreToolUse`/`PostToolUse`/`PreInvocation`/`PostInvocation`/
  `Stop` — so a `PreInvocation` hook gated on `invocationNum == 0` behaves like a
  one-shot context load instead of resending it every model turn. Wires into
  `~/.gemini/config/hooks.json`. No detection marker exists for Antigravity, so
  it's explicit-`--agents antigravity`-only, never auto-detected.
- feat: **Codex gets a `PreToolUse` env hook**, closing the one gap Codex had
  versus Claude: Codex has no per-session env-persistence mechanism at any hook
  event, so a deployed script prepends a guarded env-loader to every `Bash` tool
  call via `updatedInput.command` — the same rewrite mechanism Claude's own
  `PreToolUse` hook uses, confirmed directly against Codex's docs.
- feat: **`init` wires a PowerShell `PreToolUse` env hook on Windows**, closing
  a real gap: `$CLAUDE_ENV_FILE` only reaches Claude's *Bash* tool
  (`$env:CLAUDE_ENV_FILE` is empty inside a live PowerShell tool call, confirmed
  directly) — a fresh PowerShell tool call gets none of the SessionStart env.
  The hook prepends a guarded env-loader to a PowerShell tool call's own command,
  shipped as an inline command string (never a `.ps1` file — a script file is
  subject to PowerShell's execution policy, and dotagents has no code-signing
  certificate; the inline form runs even under `Restricted`).
- feat: **`SessionStart`/`CwdChanged` now register two handlers each** — a
  bash-syntax one and an explicit `shell: "powershell"` one — because Claude's
  own hooks.md says the shell "defaults to bash, or to powershell on Windows
  when Git Bash isn't installed": bash syntax fed to `powershell -Command` on
  such a machine is a hard parse error, silently losing both env and context for
  the whole session.
- feat: **`DotAgentsArgs`** (`dotagents.cli`, re-exported for overlay-shipped
  commands) — one shared `-g/--global` + `--agents-dir` base class. `init` and
  all four `overlays` subcommands now inherit it instead of independently
  redeclaring the same fields, so scope resolution can't silently drift between
  commands (one previously defaulted `agents_dir` eagerly to `Path.home() /
  ".agents"`; harmless in practice, but needless).

### Fixed

- fix: **`dotagents env --format powershell`/`cmd` left an already-POSIX `PATH`
  unconverted**, the mirror of the export/dotenv/fish fix below going the other
  direction. Found live: run from a genuine Windows PowerShell terminal whose
  own inherited `PATH` already held WSL/MSYS-mount-style entries
  (`/mnt/c/Program Files/...`), the emitted `${env:PATH} = '...'` was
  syntactically valid PowerShell but a single opaque colon-joined string, not
  the `;`-split list PowerShell's own PATH lookup needs — every subsequent
  bare-command lookup broke for that session. Also handles the MIXED case
  (dotagents' own native bin dirs prepended onto an already-POSIX inherited
  PATH, one string with both separators at once), caught by sourcing real
  output into a live PowerShell session and watching `git.exe` fail to
  resolve before the second fix. A WSL-only segment with no Windows equivalent
  (`/usr/bin`) is dropped rather than mangled into a broken relative path.
- fix: **the built `.pyz` degraded `-g` on any discovered command inheriting a
  dotagents-defined base class** (first hit by `DotAgentsArgs` above) — duho's
  AST introspection walks the full MRO for a command's flags, but the zipapp
  source-repoint shim only ever covered built-in command modules, not a base
  class's own module (`dotagents.cli._common`). `--global` degraded to the
  name-derived `--global-scope` and `-g` vanished silently. Fixed, with new CI
  coverage asserting the exact short flag survives a real built pyz.
- fix: **`harness_loads` relative entries matched by bare filename, not full
  path** — a relative entry like Codex's `"AGENTS.md"` wrongly suppressed ANY
  file sharing that basename anywhere on disk, including the unrelated
  `~/.agents/AGENTS.md` user-store file Codex's harness never reads.
  `dotagents context --agents codex` emitted an empty `sources: []` even with
  real content present. Now resolved against `project_root` and compared by
  full path, like the absolute (`~/`, `/`) forms already were.
- fix: `dotagents context --format json` crashed with `UnicodeEncodeError` on
  any character outside Latin-1 (a bare `print()` encoding with the console's
  codepage) — the same class of bug already fixed for the markdown path, just
  never covered for JSON.
- fix: PowerShell format uses `${env:NAME}` (curly-brace form), not the bare
  `$env:NAME` sigil — a handful of real Windows env vars have parens in their
  names (`ProgramFiles(x86)`), and `$env:FOO(X86) = ...` is a PowerShell parse
  error; the curly-brace form is valid for every name.

## [0.3.1] - 2026-07-24

### Fixed

- fix: **`dotagents env --format export`/`dotenv`/`fish` now emit a POSIX PATH on
  Windows**, instead of the OS-native `C:\...;C:\...` form. This is the exact
  command the Claude `SessionStart` hook appends into `$CLAUDE_ENV_FILE`, which
  Claude sources before *every* subsequent Bash tool call in the session — an
  unconverted PATH broke command lookup (`git`, `grep`, `head`, `python`, ...) for
  the rest of the session once poisoned, not just once. PATH-shaped values are
  now converted per segment: backslash to forward-slash, `;` to `:`, and a drive
  letter to its MSYS mount point (`C:/...` -> `/c/...` — required for PATH
  *lookups* specifically; slash direction alone does not work in MSYS2/Cygwin
  bash). `PATHEXT` is dropped (no POSIX meaning). A handful of real Windows env
  vars with parentheses in their names (`ProgramFiles(x86)`) are also dropped for
  these formats — `export FOO(X86)=...` is a bash syntax error, not a bad value,
  and aborts sourcing the rest of the file. `powershell`/`cmd`/`json`/`ini`/`yaml`
  are unaffected.
- fix: a UNC PATH segment (`\\server\share\...`) no longer collapses to a single
  leading slash (`/server/share/...`) during the POSIX conversion above — MSYS
  requires the double-slash UNC root (`//server/share/...`) to resolve it.

Patch release: the PATH/POSIX-conversion fix above (the only change since 0.3.0).

## [0.3.0] - 2026-07-24

### Changed

- **BREAKING** — `dotagents link` / `dotagents sync` are gone from the CLI. They are
  the private-sync workflow's commands, not dotagents' core, so they moved — together
  with the logic behind them (`src/dotagents/_link.py`) — into the opt-in
  **`private-sync` overlay**, and were renamed to say what they act on:

      dotagents link-project .            # was: dotagents link .
      dotagents sync-project -m "msg"     # was: dotagents sync -m "msg"

  Install the overlay to get them back: `dotagents overlays add private-sync --source
  <overlays-checkout>`. A plain dotagents now ships no private-sync workflow at all;
  its whole command surface is `init` / `build-pyz` / `context` / `env` / `overlays`,
  and everything else is discovered from an overlay or from your own `cmds/` modules.
  `tools/cloud-setup.sh` installs the overlay before linking, so the cloud bootstrap
  is unaffected.
- The bundled `dotagents/cmds/` directory now ships no command module of its own, but
  `init` still creates it: it is the documented drop-in point for your own commands
  (a `README.md` beside it explains the shape and the precedence rules).

### Added

- feat: `init` wires agent hooks and links the shared skills dir, for each active
  agent with a published hook schema (today: Claude and Codex). `--no-hooks` skips it.

  **Claude** (`~/.claude/settings.json`) gets `SessionStart`, which appends
  `dotagents env --diff --format export` to `$CLAUDE_ENV_FILE` and then runs
  `dotagents context` — Claude sources that file before each Bash command and injects
  the hook's stdout into the session context, so both the env layers and the assembled
  context reach the session automatically. It also gets `CwdChanged`, surfacing a
  directory's `AGENTS.md`. The env redirect **appends** and is guarded against an
  unset variable, both as the hooks docs require.

  **Codex** (`~/.codex/hooks.json`, or `$CODEX_HOME`) gets `SessionStart` running
  `dotagents context`; its hook JSON is structurally identical to Claude's. We write
  `hooks.json` rather than touching your `config.toml` for hooks.

  Codex's env arrives differently: it has no `$CLAUDE_ENV_FILE` equivalent, reads no
  `.env` files, and has no event that fires before config load, so
  `dotagents init --agents codex` writes a `# dotagents:begin/end` managed block
  containing `[shell_environment_policy].set` into `config.toml`. **Only on an
  explicit `--agents`** — this edits your main config with values that go stale, so
  auto-detection never triggers it. The block is appended and refreshed in place
  (everything outside the markers is untouched) and `set` merges rather than
  replaces. **The values are a static snapshot: re-run `init` after changing your env
  layers.** Identity vars describe the target agent, so initializing from Claude still
  writes `AGENT = "codex"`; `PATH` is excluded, since `set` overrides per subprocess
  and a baked-in `PATH` would replace the inherited one.

  The merge is additive and idempotent: unrelated keys and hooks you wrote yourself
  survive verbatim, malformed entries are dropped rather than raising, and re-running
  writes nothing.

  `init` also links `<scope>/skills/` into the agent's config dir, closing the last
  mile for overlay-published skills — publishing only helps if the agent reads that
  directory. Symlink where the OS permits, copy otherwise (a copy is a snapshot;
  re-run `init` to refresh).

- feat: the personal leak scanner (then in `tools/`) now also scans commit messages (current branch history)
  for agent-session trailers/URLs — a `Claude-Session:` trailer or `claude.ai/code/session`
  link — and exits 1 on any hit. The trailer is auto-added by the agent harness and
  exposes a session id in public history if it slips through; the pre-existing tracked-file
  scan didn't cover commit messages. `flows/REPO.md` release discipline documents the check
  and the `git filter-branch --msg-filter` remediation for one that already landed.
- feat: `tools/cloud-setup.sh` step 5 wires `hooks/settings.snippet.json` into the
  user-level `~/.claude/settings.json` (idempotent JSON merge, preserves existing
  settings/hooks). A fresh cloud container has no settings file and nothing else
  created one, so the SessionStart pull/link and Stop sync-back hooks never ran —
  the private repo went stale and session changes were silently never pushed back.
  `kb/PRIVATE_SYNC.md` documents the auto-wiring (manual merge still applies on
  local machines).

### Fixed

- fix: `dotagents link`/`sync` never adopt or copy back a `<project>/.agents` that is
  itself a git checkout (`.git` present — dir, or file for worktrees). A hosted-runner
  session that lists the agents repo as a *source* gets it cloned to
  `<project>/.agents` by the harness; first-link adoption then moved that entire
  checkout — `.git`, foreign proxy remote, session branch — into
  `~/.agents/projects/<name>/`, nesting a repo inside the private repo, which a later
  sync's `git add -A` would push as a bare gitlink (and `sync`'s copy-back had the same
  swallow, with `overwrite=True`). Both paths now log a skip and leave the checkout in
  place; `link --force` keeps an escape hatch that backs the checkout up to
  `.agents.bak*` (git state intact) and links the store.

- fix: `dotagents sync` now authenticates the private repo directly against github.com
  when `DOTAGENTS_AGENTS_TOKEN` is set — and on a hosted runner that rewrites github
  traffic to a scoped in-session proxy, bypasses the rewrite — so a **standalone**
  `dotagents sync` no longer 403s. Previously only the private-sync Stop hook worked
  (it sources `_agents-git-auth.sh`); a direct CLI run had no bypass, so its pull failed
  (`could not read Password`) and its push returned HTTP 403 through the proxy. The CLI
  now ports that logic: a per-command `-c` credential helper in a normal environment, or
  an isolated `GIT_CONFIG_GLOBAL` (identity + CA bundle preserved) that skips the rewrite
  when one is active. The token is still read from the environment at auth time and never
  written to `.git/config`.
- fix: `tools/cloud-setup.sh` no longer lets a single container-start clone failure
  permanently disable the environment. The clone often loses a race with egress/proxy
  readiness; previously it `exit 0`'d on the first failure, skipping the hook-wiring
  step — so the SessionStart hook (which can itself re-clone) was never registered and
  nothing ever recovered. Now the clone retries with backoff (5 attempts), and if it
  still fails the script persists a copy of itself and wires a SessionStart **recovery
  hook** that re-runs the bootstrap next session (egress is up by then); the first
  successful run merges the private-sync hooks and removes the recovery hook.
- fix: `tools/cloud-setup.sh` also wires that recovery hook when
  `DOTAGENTS_AGENTS_REMOTE` is **unset at setup time**, not only on clone failure.
  Hosted runners often expose the remote/token secrets to session processes but not
  to the setup-script phase, so the first bootstrap had no remote to clone and its
  no-remote branch just `exit 0`'d, leaving nothing to retry — the environment stayed
  dead every session (observed: setup ran the correct one-liner and emitted only the
  banner + `skipping` line, ~154 bytes, no clone). The branch now persists the recovery
  hook like the exhausted-clone path, so the next session — where the secret is present
  — clones and self-removes the hook. A genuinely remote-less environment just re-skips
  each session (idempotent; the hook never duplicates). (Durable fix is still
  to expose the secrets to the Setup Script phase so the first container succeeds.)
- fix: `.gitignore` templates and `dotagents link` now use a slashless `.agents`
  instead of `.agents/`. `link` creates `.agents` as a *symlink*, which git treats
  as a file, so the directory-only `.agents/` pattern never actually ignored it —
  the link showed up as untracked in every project. `_gitignore_excludes_agents`
  is now symlink-aware (a bare `.agents/` no longer counts as excluding a symlinked
  link, so the WARN fires), and the reference template, REPO.md guidance, and the
  starter `_overlay/AGENTS.md` Leakage rule all recommend `.agents`.
- docs: recommend `curl … -o file && sh file` over `curl … | sh` for the setup-script
  field (README, `kb/PRIVATE_SYNC.md`, `tools/cloud-setup.sh` header). With a pipe the
  field's exit code is `sh`'s (0 on empty stdin), so a failed fetch at container start
  is silently reported as success; `&&` propagates the fetch failure to the setup log.

### Changed

- refactor: move the cloud bootstrap from `overlays/private-sync/hooks/cloud-setup.sh` to
  top-level `tools/cloud-setup.sh` (public, required tooling) so a fresh cloud container
  can fetch-and-run it from the public repo instead of pasting its contents — the web
  environment setup-script field becomes a one-liner
  (`curl -fsSL …/tools/cloud-setup.sh | sh`) that stays current on every container start.
  Docs (README, `kb/PRIVATE_SYNC.md`) updated to the download bootstrap.
- fix: `tools/cloud-setup.sh` prints `starting`/`done` banners (so a setup-script log
  proves whether it executed — a blank log means the field never invoked it, a config
  issue) and `mkdir -p "$HOME"` before `git config --global` (which fails if HOME isn't
  created yet in some setup contexts).

## [0.2.0] - 2026-07-19

### Changed

- chore: migrate the CLI to `duho>=0.3.3` (was `>=0.1.1`). duho's Plan-13 `Args`/`Cmd`
  split means commands are now `class X(LoggingArgs, Cmd)` with a `__call__` entrypoint
  (was a bare `LoggingArgs` with `__run__`) and the umbrella root is
  `class Dotagents(LoggingArgs, Cli)`. Field declarations (annotation + help string +
  flags tuple) are unchanged. Bumped the `build-pyz` vendored `duho` default to 0.3.3.
- fix: restore full flag/help fidelity in the built `dotagents.pyz` under duho 0.3.3.
  duho discovers each field's flags + help by AST-parsing its module source, and inside
  a zipapp the zip-internal `__file__` isn't readable — degrading `--from` to `--from-`,
  the `link` positional to `--path`, and dropping help text. `cli.main` now repoints the
  affected module sources (`dotagents.cli`, `duho.presets`) to extracted temp files
  before dispatch; a no-op for a plain install.

### Added

- feat: private-agents git sync — `dotagents link` symlinks a project's `.agents` to a
  per-project store under the global `~/.agents/projects/<name>` (basename-keyed, so a
  local and a cloud checkout converge on the same store), adopting an existing real
  `.agents/` into an empty store on the first link; `--copy` mirrors it as a real dir
  for no-symlink environments (with automatic fallback), `--force` handles conflicts.
  `dotagents sync` runs `git pull --rebase`/commit/push on the private repo, copies a
  copy-mode project's `.agents` back into its store first (`--project`), and bootstraps
  a fresh repo in one command (`--remote`). Logic in `src/dotagents/_link.py`; the model
  keeps per-user config and every project's private `.agents` in one private repo while
  the public project repos track none of it (the Leakage rule already `.gitignore`s
  `.agents/`).
- feat: `overlays/private-sync/` overlay — `kb/PRIVATE_SYNC.md` (the model, commands,
  first-time + cloud setup, auth, gotchas) plus `hooks/private-sync-{start,stop}.sh`
  (SessionStart clone/pull + link, Stop sync-back) and a `settings.snippet.json` for
  `~/.claude/settings.json`, so cloud sessions link and sync automatically. Cloud auth is
  a fine-grained PAT via `DOTAGENTS_AGENTS_TOKEN`, wired through a git credential helper
  that reads it from the environment (never persisted to `.git/config`);
  `hooks/_agents-git-auth.sh` auto-detects a hosted-runner `github.com`→in-session-proxy
  `insteadOf` rewrite and bypasses it (isolated git config) so token auth reaches the
  real github.com for a private repo outside the session's scope. `hooks/cloud-setup.sh`
  is a self-contained container-start bootstrap (inlines auth + bypass, so it runs before
  `~/.agents` exists) that clones/pulls the repo, installs the CLI, and links the project
  — for the web environment's setup-script field, solving the first-clone chicken-and-egg
  the SessionStart hook can't.
- feat: installable `dotagents` CLI package (`src/dotagents/`, built on `duho` for
  the argument surface and `pathlib_next` for copy/URI handling) exposing `init`
  (lay down the neutral base overlay), `install` (base plus opt-in overlays via
  repeatable `--overlays <path>`, copied additively), `audit` (wraps
  `tools/audit_config.py`), and `build-pyz` (vendors pinned `duho`/`pathlib_next`
  via `pip install --target` + `zipapp` into a self-contained, downloadable
  `dotagents.pyz`). `init`'s `AGENTS.md`/`CLAUDE.md` are merged as a
  marker-delimited managed block so re-running never clobbers customizations
  outside the block. `install --bin-dir` writes `dotagents`/`dotagents.cmd`
  wrappers. `install.py` is a thin shim over `dotagents.cli.main()`.
- Config content is a **neutral base overlay** (`src/dotagents/_overlay/` — the
  `AGENTS.md` scaffolding + design-log convention `init` writes) plus **opt-in
  overlays** (`overlays/<name>/`): `flows` (PLAN/EXEC/REVIEW/REPO + MODELS),
  `recovery`, `references`, `python`/`node`/`rust`, `agents`, `tools`. Each carries
  an `overlay.toml` manifest for a future `dotagents overlays` subcommand.
- Repo layout: the CLI in `src/dotagents/`, config overlays in `overlays/`, required
  tooling in top-level `tools/` (`audit_config.py` and a personal leak scanner); repo root holds
  the installer, CI, repo-development directives, and the tracked, sanitized
  `.agents/` design log (index + per-decision files) + plans. `audit_config.py` has
  `--repo-hygiene` (scans tracked files for personal/machine-specific leftovers).
