# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- feat: `tools/leak_check.py` now also scans commit messages (current branch history)
  for agent-session trailers/URLs — a `Claude-Session:` trailer or `claude.ai/code/session`
  link — and exits 1 on any hit. The trailer is auto-added by the agent harness and
  exposes a session id in public history if it slips through; the pre-existing tracked-file
  scan didn't cover commit messages. `flows/REPO.md` release discipline documents the check
  and the `git filter-branch --msg-filter` remediation for one that already landed. See D41.
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
  `.agents.bak*` (git state intact) and links the store. See D43.

- fix: `dotagents sync` now authenticates the private repo directly against github.com
  when `DOTAGENTS_AGENTS_TOKEN` is set — and on a hosted runner that rewrites github
  traffic to a scoped in-session proxy, bypasses the rewrite — so a **standalone**
  `dotagents sync` no longer 403s. Previously only the private-sync Stop hook worked
  (it sources `_agents-git-auth.sh`); a direct CLI run had no bypass, so its pull failed
  (`could not read Password`) and its push returned HTTP 403 through the proxy. The CLI
  now ports that logic: a per-command `-c` credential helper in a normal environment, or
  an isolated `GIT_CONFIG_GLOBAL` (identity + CA bundle preserved) that skips the rewrite
  when one is active. The token is still read from the environment at auth time and never
  written to `.git/config`. See D40.
- fix: `tools/cloud-setup.sh` no longer lets a single container-start clone failure
  permanently disable the environment. The clone often loses a race with egress/proxy
  readiness; previously it `exit 0`'d on the first failure, skipping the hook-wiring
  step — so the SessionStart hook (which can itself re-clone) was never registered and
  nothing ever recovered. Now the clone retries with backoff (5 attempts), and if it
  still fails the script persists a copy of itself and wires a SessionStart **recovery
  hook** that re-runs the bootstrap next session (egress is up by then); the first
  successful run merges the private-sync hooks and removes the recovery hook. See D39.
- fix: `tools/cloud-setup.sh` also wires that recovery hook when
  `DOTAGENTS_AGENTS_REMOTE` is **unset at setup time**, not only on clone failure.
  Hosted runners often expose the remote/token secrets to session processes but not
  to the setup-script phase, so the first bootstrap had no remote to clone and its
  no-remote branch just `exit 0`'d, leaving nothing to retry — the environment stayed
  dead every session (observed: setup ran the correct one-liner and emitted only the
  banner + `skipping` line, ~154 bytes, no clone). The branch now persists the recovery
  hook like the exhausted-clone path, so the next session — where the secret is present
  — clones and self-removes the hook. A genuinely remote-less environment just re-skips
  each session (idempotent; the hook never duplicates). See D42. (Durable fix is still
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
