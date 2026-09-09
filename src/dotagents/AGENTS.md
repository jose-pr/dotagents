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
- `dotagents.cli.DotAgentsArgs` — mix-in base carrying the shared `-g/--global` +
  `--agents-dir` pair and `resolve_scope()`; an overlay-shipped command module
  should inherit it (as the FIRST base) rather than redeclare the flags.
- `dotagents.cli.resolve_user_store(agents_dir=None) -> Path` — the USER store
  root: `agents_dir` (`--agents-dir`) → `$AGENTS_HOME` → legacy
  `$DOTAGENTS_AGENTS_DIR` → `~/.agents`. Use it (not `resolve_scope`) when the
  store is always the user store and the project scope only adds/removes tiers,
  as in `env` / `context`.
- Compiled command classes live in `dotagents.cli.<name>` (`init`, `overlays`
  — `add` (installs each manifest's `requires` first, `--no-requires` to skip;
  every name validated and resolved against the source BEFORE anything is
  touched; skills published from the INSTALLED copy), `remove` (recomposes the
  managed block over what remains — the un-merge), `list`, `sync` (`--copy`
  honoured, `--overwrite` replaces changed files), `show` (manifest, setup,
  skills, files, `--json`) — `context`, `env`, `build_pyz` (a checkout only:
  a clear error elsewhere)); each is a `class X(LoggingArgs, Cmd)` — or
  `class X(DotAgentsArgs)`, which is that pair transitively — with a
  `__call__`. Plus ONE bundled command module,
  `_overlay/dotagents/cmds/findings.py` (`dotagents findings`: a per-scope
  findings queue at `<scope-root>/findings/` — add / list / show / done / reopen /
  remove / index / path; its subcommands are classes NESTED in the `Findings`
  umbrella so discovery does not register them as top-level commands, and `init`
  does not copy it into the store — the bundled dir is always a discovery
  source). `link` / `sync` left the package with their logic (D85): the
  opt-in **private-sync** overlay ships them, renamed `link-project` /
  `sync-project`, from its own `cmds/` + `lib/_link.py`. A personal command module
  dropped into a scope's `dotagents/cmds/` is discovered like any other, so private
  tooling never has to live in the repo (D84). `audit` is repo CI tooling
  (`tools/audit.py`), not a command.
- Command discovery layers sources, later wins: built-ins < bundled `cmds` <
  store overlay `cmds` (`<overlay-root>/cmds`) < system < user < project
  overlay `cmds` < project < `$AGENTS_CMDS_PATH` < `--cmdspath`. The overlay +
  scope tiers come from one Contract-A `get_file_paths` walk (`cli._cmds_dirs`),
  the same resolver that backs `bin`/PATH; an `--agents-dir X` on the command
  line is honoured for the walk. A source that fails to import for ANY reason
  (a `SyntaxError`, an exception at import time) is skipped with a warning
  naming it — discovery runs before every invocation, hooks included, so one
  broken personal module must never take `env`/`context` down. An umbrella
  invoked with no subcommand prints its help and exits 2.

## Helper modules (public surface)

- `_agents` — `Agent` base type + per-agent adapters; `stamp_identity(...)` emits the
  standardized `AGENTS_*` / `AGENT` identity vars.
- `_overlays` — the **`Overlay`** type: one overlay = one directory, `Overlay(path)`.
  Everything that depends on a single overlay is on it. Name rules are static so a
  bare name works: `Overlay.is_valid_name(n)` / `.normalize_name(n)` (THE canonical
  name, lowercase `_`→`-`, = the install dir `overlays/<n>/`) / `.root_var_for(n)`
  (`<NAME>_OVERLAY_ROOT`). Instance: `.path` / `.name` / `.normalized_name` /
  `.root_var` / `.is_valid` / `.manifest_path`, `.read_manifest()` (`name`,
  `description`, `routing`, `rules`, `requires` — normalized names —,
  `priority`; a small quote-aware TOML reader: trailing `#` comments, `'...'`
  and `'''...'''` strings and an indented closing `]` all parse) / `.priority` /
  `.sort_key` (`(priority, manifest name, dir name)`),
  `.find_setup_script()` / `.run_setup(agents_dir=, dry_run=, logger=)`,
  `.files()` / `.rule_blocks(rel_paths)` / `.apply_to(dest, dry_run)` /
  `.install_to(dest_overlay_dir, dry_run, overwrite=False)` (self-describing:
  ships the manifest; `overwrite` replaces files whose content differs) /
  `.merge_rules_into(agents_md, dry_run, logger)`. `Overlay.discover(root)` is the
  ONE discovery rule (valid-named dirs under an `overlays/` root; `_scope`,
  `_resolve` and `env` all call it), `Overlay.sort_by_priority(items)` the one
  merge order. It is `os.PathLike`, so it goes anywhere a path does.
  `recompose_overlay_block(...)` (over a *set* of overlays) is the only module
  function. `DEFAULT_PRIORITY = 500`.
- `_scope` — `resolve_scope(global_scope, agents_dir=None, project_root=None)`,
  `resolve_user_store(agents_dir=None)` (the home of the chain `dotagents.cli`
  re-exports) and `resolve_source(...)`; scope = *where installed overlays live*
  (user = the configurable store, project = `<project>/.agents`), source = *where
  an overlay comes from* (bundled by default). `-g` resolves the store through
  `resolve_user_store` (`--agents-dir` → `$AGENTS_HOME` → legacy → `~/.agents`),
  never the literal home dir; `--agents-dir` overrides the store in EITHER scope.
  Installed overlays are **discovered** by presence (`discover_overlays(scope)` →
  names, via `Overlay.discover`), not tracked in a registry. Nothing overlay-only
  lives here any more (the name regex / `is_valid_overlay_name` moved to `Overlay`).
- `_context` — assemble the effective per-agent context (Plan 04):
  `assemble_context(agent, agents_dir, project_root, global_scope=False,
  inline=False)` / `assemble_context_data(...)`. Sources are the contract-A
  walk (overlay `CONTEXT.md`s first, sorted by manifest `priority`, lower
  first; then the store / project `AGENTS.md` + `AGENTS.local.md`), minus what
  the harness loads itself (`Agent.loaded_paths(project_root)` — for Claude,
  whatever its entry files actually `@`-include, recursively). `<PROJECT_ROOT>`
  and one `<NAME_OVERLAY_ROOT>` per installed overlay (user store + project)
  expand. **Inlining the on-demand `.md` files a source mentions is opt-in**
  (`inline=True` / `context --inline`): the base rules say to read them only
  when a task needs them, and inlining every mention made a 100 KB session
  payload. Skills are listed, never inlined.
- `_env` — chained env-file assembly + `env.py` execution (frozen contract B):
  `get_environment` / `get_diff` / `resolve_env_files` / `get_env_from_py` /
  `get_env_from_file`. Bins onto PATH first (`get_bin_paths`, every level's `bin`
  except project-root, missing dirs included — frozen), and each level's EXISTING
  `lib` onto PYTHONPATH the same way (`get_lib_paths`; not part of contract B), so
  an `env.py` and every subprocess can import an overlay's `lib/`; then two tiers
  (`pre.env*` then `env*`), later-overrides-earlier. Identity seeded before the
  chain; proxy vars applied after. **Amended 2026-09-09:** the project-root level
  resolves only `pre.local.env` / `local.env` — a checkout's own top-level
  `env.py` / `env` is never executed or sourced (it ran at every session start,
  i.e. code execution from any cloned repo); only regular files count (a venv
  dir named `env` is not an env file); a plain file whose `source` fails
  contributes nothing (`source F || exit 1`, so the failure is visible); bash's
  own `PWD`/`OLDPWD`/`SHLVL`/`MSYSTEM*` are never reported as a file's changes.
- `_resolve` — `get_file_paths(*names, agents_dir, project_root, global_scope=False,
  include_missing=False)`: the Contract-A precedence walk / filename resolution:
  user-store overlays → system → user → **project overlays** (since 2026-09-09:
  `<project>/.agents/overlays/*`, where `overlays add` installs by default) →
  project → project-root; the last three skipped with `global_scope`. Each
  tuple is `(level, path, root)`; an overlay entry's `level` is the overlay's
  dir name and `root` its dir (`root is None` for every other level — that is
  the overlay test). `LEVEL_NAMES` are reserved as overlay names.
- `_merge` — managed-block merge for `init`'s `AGENTS.md` / `CLAUDE.md`, delimited by
  `<!-- dotagents:begin -->` / `<!-- dotagents:end -->` marker LINES (a prose
  mention of a marker is not a marker; `find_block` returns the span). A begin
  with no end after it is refused; a base without markers is a usage error.
  `begin_marker`/`end_marker` override the pair for other comment syntaxes
  (`#` for TOML), and `append=True` puts a first-time block at the END of the
  file — required for TOML, where a `[table]` header captures every key line
  after it and a prepended block would swallow the user's top-level keys.
  `merge_include_line(target, "@path")` is the harness-entry-file include
  (skipped when the line is already present anywhere), `merge_context_block`
  the `dotagents:context` block `context --write-agent` uses. All writes go
  through `_fs.write_text_lf` (LF-only everywhere; `atomic=True` for JSON
  settings).
- `_agents.Agent` — per adapter: `harness_loads` (static: what the harness
  reads by itself, relative = project root), `loaded_paths(project_root)`
  (resolved; Claude adds its real `@` includes), `context_target` (the
  harness's instruction file under the project root that `write_context`
  merges a managed context block into — Claude `.claude/CLAUDE.md`, Codex
  `AGENTS.md`, Gemini `GEMINI.md`, Cursor `.cursorrules`, Copilot
  `.github/copilot-instructions.md`, Antigravity `.agents/rules/dotagents.md`),
  `write_base_config(dest, ...)` (Claude also writes the `@` include into
  `~/.claude/CLAUDE.md` or `<project>/.claude/CLAUDE.md` — THE last mile; the
  store's own `CLAUDE.md` is kept for the skeleton but no harness reads it).
  The user scope is whatever `resolve_user_store()` returns, never the literal
  `~/.agents`.
- `_skills` — publish an overlay's `skills/<name>/` into a scope's shared skills dir
  (symlink-preferred, copy fallback); unpublish removes only what the overlay
  published — a copy counts as the overlay's only when its file set AND bytes
  match the source, so a user-edited copy is kept — then sweeps broken
  symlinks. Pure stdlib. `ClaudeAgent.wire_hooks` links the scope's skills
  into `<config>/skills/<name>` PER SKILL (a same-named skill the user placed
  there is a conflict and stays), never the whole directory.
- `_hooks` — additive, idempotent merge of our hooks into an agent's `settings.json`.
  `hooks.<Event>` is a **list of matcher-objects** each holding its own `hooks` list,
  not a flat command list. Foreign hooks are preserved verbatim (a foreign hook
  sharing a matcher-object with an older shape of ours keeps its object; ours
  moves to its own), an entry that is exactly what we would write is left
  alone and any other shape of ours (revised command, `shell`, `matcher`,
  `commandWindows`, status) is replaced in place, malformed entries are
  dropped rather than raising, and invalid JSON raises `SystemExit` instead of
  silently overwriting the user's file. Writes are LF-only and atomic, with
  non-ASCII kept as-is. `shell` (Claude: `"bash"`/`"powershell"`,
  picks the interpreter for the hook's own command) and `command_windows`
  (Codex: emitted as `commandWindows`, a separate Windows-only command OVERRIDE,
  not an interpreter choice) are both supported. Pure stdlib. Consumed by
  `ClaudeAgent.wire_hooks` (`~/.claude/settings.json`: env via `$CLAUDE_ENV_FILE`
  + context via stdout, plus `CwdChanged`, plus a `PreToolUse` env-loader for the
  PowerShell tool) and `CodexAgent.wire_hooks` (`<CODEX_HOME|~/.codex>/hooks.json`,
  never `config.toml`: `SessionStart` context-only, plus a `PreToolUse` env-loader
  matched on `matcher: "Bash"`). Codex's hook JSON is structurally identical to
  Claude's, including the SAME `updatedInput.command` rewrite mechanism on
  `PreToolUse` — confirmed directly against Codex's own docs
  (learn.chatgpt.com/docs/hooks), not assumed from Claude parity.
  Gemini/Cursor/Copilot keep the base no-op — Gemini CLI proper has no hook
  mechanism documented at all (checked directly). **Antigravity is a separate
  product** from Gemini CLI (Antigravity CLI/IDE/SDK family, shares only the
  `~/.gemini/` namespace for some files) and DOES wire a hook: see
  `AntigravityAgent.wire_hooks` below. `PreToolUse` there is still allow/deny/ask
  only — no `updatedInput`, so no command-rewrite/env-loader path exists to hang
  on it, unlike Claude/Codex. Revisit both conclusions if either framework changes.
- **`AntigravityAgent.wire_hooks`** (`<AGENTS_HOME_ANTIGRAVITY|~/.gemini/config>/hooks.json`,
  keyed under a `"dotagents"` name per the docs' own example shape — a named-entry
  object, not Claude/Codex's flat `hooks.<Event>`): wires a single `PreInvocation`
  entry, context-only, no env mechanism. Antigravity's hooks
  (antigravity.google/docs/hooks) have exactly five events — `PreToolUse`,
  `PostToolUse`, `PreInvocation`, `PostInvocation`, `Stop` — no SessionStart
  equivalent. `PreInvocation` fires every model turn (`invocationNum`, 0-indexed),
  so the deployed script (`preinvocation_antigravity_context.py`,
  `_overlay/dotagents/hooks/`) gates on `invocationNum == 0` to behave like a
  one-shot SessionStart rather than resending context every turn. Output shape is
  a bare `{"injectSteps": [{"ephemeralMessage": "..."}]}`, no `hookSpecificOutput`
  wrapper — confirmed against the primary docs after an earlier pass here wrongly
  concluded no useful injection was possible; `ephemeralMessage` is the one of the
  three step types meant for free text (`toolCall` executes a tool,
  `userMessage` impersonates the user). No detection marker exists anywhere in
  Antigravity's docs, so `detect_env_vars = []` — explicit `--agents antigravity`
  only, same posture as Codex's env-block precedent (writes touching an agent's
  own live config are opt-in, never inferred).
- **`SessionStart`/`CwdChanged` register TWO handlers each**, bash-syntax
  (default shell) and a PowerShell-native equivalent (`shell: "powershell"`).
  hooks.md: `shell` "Defaults to bash, or to powershell on Windows when Git Bash
  isn't installed" — verified directly that bash syntax fed to `powershell
  -Command` on such a machine is a hard parse error, not a soft failure, so
  every session there would silently get neither env nor context. Every handler
  in a matched group fires unconditionally (hooks.md), so both always run —
  and on a box with BOTH interpreters both used to succeed, injecting the same
  context twice per session (two identical 100 KB payloads, measured
  2026-09-09). The PowerShell variants therefore **select themselves: they run
  only when `bash` is not on PATH** (`Get-Command bash`). The PowerShell
  `SessionStart` variant is context-only (`dotagents context`), not
  env+context: `$CLAUDE_ENV_FILE`'s documented effect is "subsequent BASH
  commands" regardless of which shell wrote it, so writing to it from a
  PowerShell-shelled hook would feed nothing. Every hook command resolves the
  store as `$AGENTS_HOME`, else `~/.agents` (bash: `${AGENTS_HOME:-$HOME/.agents}`),
  and the bash `CwdChanged` handler re-pins `AGENTS_PROJECT_ROOT` into
  `$CLAUDE_ENV_FILE` when the new cwd carries a `.agents/` (the SessionStart pin
  is only-if-unset, so without this a `cd` into another project kept the first
  project's root for the rest of the session).
- **Windows only**: `ClaudeAgent._wire_powershell_pretooluse` additionally wires
  a no-matcher `PreToolUse` hook (fires on every tool call), `shell:
  "powershell"`, running `PRETOOLUSE_POWERSHELL_COMMAND` INLINE — deliberately
  not a `.ps1` file, since a script file is subject to PowerShell's execution
  policy (RemoteSigned/AllSigned/Restricted) and dotagents has no code-signing
  certificate; verified directly that the inline form runs successfully even
  under `Restricted`, which blocks every `.ps1` file outright. Closes a real
  gap: `$CLAUDE_ENV_FILE` is Bash-tool-only (confirmed empirically that
  `$env:CLAUDE_ENV_FILE` is empty inside a live PowerShell tool call), so the
  SessionStart env half never reaches the PowerShell tool. Uses `PreToolUse`'s
  `updatedInput` to prepend a guarded env-loader (`AGENTS_RUNTIME_SET`,
  matching the precursor's convention) to a `PowerShell` tool call's own
  command — not by trying to persist state across hook invocations, which are
  each their own fresh process and cannot. Each PowerShell TOOL call is a fresh
  process too, so the guard never carries over and the loader runs on every
  call: it therefore runs `env --diff` (the change set), not the whole
  environment through `Invoke-Expression`. Every literal `\` in the
  command constant must be a raw string — a bare `\b` in a normal Python string
  literal silently becomes a backspace character, corrupting the emitted path;
  caught once by testing a draft through a real PowerShell spawn.
- **`CodexAgent._deploy_pretooluse_script`** covers the same env gap for Codex,
  which has NO env-persistence mechanism at any hook event (not Bash-only like
  Claude — none). Ships `pretooluse_codex_env.py` (`_overlay/dotagents/hooks/`),
  deployed to `<codex-home>/hooks/` (create-or-refresh), wired as `PreToolUse`
  with `matcher: "Bash"` — Codex's one shell tool, so (unlike Claude's
  no-matcher hook) filtering happens at the settings level, no runtime
  `tool_name` check needed in the script. A FILE, not inlined like Claude's:
  Codex's docs show every hook example as `python3 <path>`, and a `.py` file
  has no execution-policy/signing concern (PowerShell-specific). Sets
  `commandWindows` to `python "<path>"` (not `python3`) — verified directly
  that `python3` resolves to the Microsoft Store app-execution-alias stub and
  fails outright on this dev machine (exit 49), the same trap noted elsewhere
  for `py`/venv creation.

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
`AGENTS_HARNESS`, `AGENTS_VENDOR`, `AGENTS_MODEL`, `AGENT`, `AGENTS_PROXY`,
`AGENTS_WEBFETCH_PROXY_URL`, plus the two
scope roots — `AGENTS_HOME` (the user store, `agents_dir`/`~/.agents`) and
`AGENTS_PROJECT_ROOT` (this project's root) — and one **`<NAME>_OVERLAY_ROOT`**
per installed overlay under `<store>/overlays/` (`_env.get_overlay_roots`, the
same presence-by-directory rule as the contract-A walk). `NAME` is the overlay's
directory name upper-cased with every non-alphanumeric character turned into `_`
(`my-ov.v2` → `MY_OV_V2_OVERLAY_ROOT`). `Overlay.normalize_name(name)` is THE
canonical overlay name (lowercase, `_`→`-`): it names the install dir
`overlays/<normalize_name(name)>/` and the source lookup, and
`Overlay.root_var_for(name)` (`Overlay(dir).root_var`) derives the env var from
it (upper-case, `-`/`.`→`_`, suffix), so the var for `overlays/<n>/` is always
`root_var_for(n)`. `context`'s `<NAME_OVERLAY_ROOT>` placeholder goes through the
same property, so an env file and a context file name an overlay's install dir
identically. All of these
roots are seeded before the file chain and only if unset, so a harness/env can
pin them. `PATH` is emitted with every level's `bin` prepended, and `PYTHONPATH`
with every level's existing `lib` prepended (both except project-root; the
formatter treats any `*PATH` var as a path list for Windows/POSIX conversion).
Deliberately NOT emitted, despite looking like they
would be: `AGENTS_AGENT` (a named persona — nothing derives one, so there is
nothing to emit) and `AGENTS_CODE_SESSION_ID` (dropped with the precursor's
blanket `CLAUDE_*`→`AGENTS_*` rewrite). Do not branch on either. `resolve_scope` READS `AGENTS_PROJECT_ROOT` (then the
agent-native `CLAUDE_PROJECT_DIR`, then cwd) for the project scope's root.

Every command READS both back, so the pin actually holds: `env` and `context`
resolve their user store through `cli.resolve_user_store()` (`--agents-dir` →
`$AGENTS_HOME` → legacy `$DOTAGENTS_AGENTS_DIR` → `~/.agents`) and their project
root through `_scope.project_root_default()` — neither is taken from the cwd or a
hardcoded home. This matters for hook-invoked runs: a `SessionStart` hook runs
`dotagents context` / `dotagents env` from wherever the session started, and only
the pinned root makes that cwd-independent. `-g/--global` on these two means
*skip the project tier*, not *use a different store*.

## Gotchas

- **Python 3.9 floor.** Files using bare `X | Y` unions in runtime-evaluated positions
  need `from __future__ import annotations`. `Path.write_text(..., newline=...)` is
  3.10+, so wrapper-script writers use `open(path, "w", newline="")`.
- **Zipapp source shim.** Inside a `.pyz`, `Path(__file__)` is not a real file, so
  duho's AST flag/help introspection degrades (`--from` → `--from-`, positionals lost).
  `cli.main()` calls `_repoint_zipapp_sources()` first, extracting the built-in command
  modules to real temp files. Discovered `cmds` modules are extracted by
  `_package_data_dir` before import, so they need no repoint.
- **Package data in a `.pyz`.** `_package_data_dir(name)` resolves a package-data dir
  by name — `_overlay` (the base overlay `init` writes) and `_overlays_src` (bundled
  example overlays, when a build includes them) — via `importlib.resources` (a
  zip-backed `Traversable` is extracted once), never `Path(__file__).exists()`
  (always False in a zipapp). Everything a `.pyz` run extracts — package data
  and the repointed module sources — lives under ONE per-process scratch dir
  (`_common._scratch_dir()`), removed at interpreter exit; a `mkdtemp` per item
  with no cleanup had littered a dev box's `%TEMP%` with 632 directories.
- **`pathlib_next` needs `typing_extensions` on Python < 3.10** (an upstream gap); a
  3.9 environment must `pip install typing_extensions`.
