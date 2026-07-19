# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
  `~/.claude/settings.json`, so cloud sessions link and sync automatically.
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
