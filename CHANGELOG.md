# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- feat: installable `dotagents` CLI package (`src/dotagents/`, built on `duho` for
  the argument surface and `pathlib_next` for copy/sync/URI handling) exposing
  `init` (minimal neutral base overlay — no author-opinionated flows),
  `install` (this repo's full `payload/`, preserving the legacy backup/report
  behavior via `pathlib_next`'s `PathSyncer`), `audit` (wraps
  `tools/audit_config.py`), and `build-pyz` (vendors pinned `duho`/`pathlib_next`
  via `pip install --target` + `zipapp` into a self-contained, downloadable
  `dotagents.pyz` that needs no `pip install` to run). `init`'s `AGENTS.md`/
  `CLAUDE.md` are merged as a marker-delimited managed block so re-running it
  never clobbers user customizations outside the block. `install --bin-dir`
  writes `dotagents`/`dotagents.cmd` wrapper scripts. `install.py` at the repo
  root is now a thin shim over `dotagents.cli.main()`.

- Initial public release of the agent config: always-loaded core (`AGENTS.md`) with
  load-on-demand routing; task flows (PLAN/EXEC/REVIEW/REPO); language kb files
  (PYTHON/NODE/RUST) plus a config-recovery playbook; repo file templates including
  per-language CI workflows; `tools/audit_config.py` (integrity/size/hygiene audit),
  `tools/leak_check.py` (private-plan leak scan for repos), and `install.py`.
- Repo layout: the installable config lives under `payload/` (installs 1:1 into
  `~/.agents`); repo root holds the installer, CI, repo-development directives, and
  the tracked, sanitized `.agents/` design log + plans. `audit_config.py` gains
  `--repo-hygiene` (scans tracked files for personal/machine-specific leftovers).
