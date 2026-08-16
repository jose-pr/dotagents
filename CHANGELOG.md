# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Removed

- **`dotagents._sync`** — a `pathlib_next.PathSyncer` wrapper that existed only
  to back the `install` subcommand's backup/copy report. `install` was removed
  in 0.3.x; the module has had no callers since, no tests, and a return
  annotation that disagreed with what it returned. `pathlib_next` remains a
  dependency (duho, and `--from` URI support). Its API-reference page went with
  it.

### Fixed

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
- feat: `env` and `context` gained **`--agents-dir`** (from the shared
  `DotAgentsArgs` base) to override the store for one run. Their `-g/--global`
  keeps its existing, narrower meaning here — *skip the project-level files* —
  and now says so in `--help`.

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

- feat: `tools/leak_check.py` now also scans commit messages (current branch history)
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
  tooling in top-level `tools/` (`audit_config.py`, `leak_check.py`); repo root holds
  the installer, CI, repo-development directives, and the tracked, sanitized
  `.agents/` design log (index + per-decision files) + plans. `audit_config.py` has
  `--repo-hygiene` (scans tracked files for personal/machine-specific leftovers).
