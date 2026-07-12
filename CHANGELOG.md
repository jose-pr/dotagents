# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Initial public release of the agent config: always-loaded core (`AGENTS.md`) with
  load-on-demand routing; task flows (PLAN/EXEC/REVIEW/REPO); language kb files
  (PYTHON/NODE/RUST) plus a config-recovery playbook; repo file templates including
  per-language CI workflows; `tools/audit_config.py` (integrity/size/hygiene audit),
  `tools/leak_check.py` (private-plan leak scan for repos), and `install.py`.
