# Private-agents git sync: one private repo, per-project .agents symlinks

Status: done
Executor: self (main thread — holds full context from reading the CLI, sync/overlay
internals, and the audit/hygiene constraints this session; a cold subagent would
re-derive the duho arg surface, the install/overlay flow, and the leakage/hygiene rules
at higher total cost). Sonnet-tier judgment, already loaded.

Give a user one **private git repo** that is their global `~/.agents` *and* the home for
every project's private `.agents` (plans, kb, findings) — synced across machines and
cloud — while the public project repos track none of it. Basename-keyed per-project
stores under `~/.agents/projects/<name>`, symlinked into each checkout. Decision: D37.

## Progress
- [x] Phase 1 — `src/dotagents/_link.py`: `link_project` (symlink default, `--copy`
      fallback + auto-fallback on symlink failure, adoption of an existing `.agents/`,
      `--force` conflict/backup, idempotent re-link, `.gitignore` leak warning) and
      `sync_agents` (copy-back for copy mode, `git init`+origin bootstrap via `--remote`,
      pull --rebase / commit / push, all git non-fatal). Pure stdlib — no pathlib_next.
- [x] Phase 2 — CLI: `Link`/`Sync` subcommands wired into `Dotagents._subcommands_`
      (positional `path`/optional flags via duho), registered in the umbrella help line.
- [x] Phase 3 — `overlays/private-sync/`: `kb/PRIVATE_SYNC.md` (model, commands,
      first-time + cloud setup, auth, gotchas) + `hooks/private-sync-{start,stop}.sh`
      + `settings.snippet.json`; `overlay.toml` with a routing line.
- [x] Phase 4 — docs + manifest: README (Layout rows + "Private sync" section),
      CHANGELOG, AGENTS.md architecture note, D37 + index line, audit `EXAMPLES`.
- [x] Phase 5 — verify: end-to-end smoke (adoption, idempotent, seed+link, copy mode,
      conflict/--force, sync bootstrap+commit on `main`, copy-back, dry-run) on the
      pinned `duho==0.1.1`; `audit --root .` + `--repo-hygiene .` PASS; CI steps added.

## Known Facts & Context

Repo directives (`AGENTS.md`): edit source then reinstall to test; public repo — keep
tracked files sanitized (no user accounts / machine paths / private names — the docs use
a `<you>` placeholder, never a real handle); before commit all three `audit_config.py`
invocations must PASS; decisions live in `.agents/dotagents/` (one file per decision,
continue the D-numbering).

Gotchas hit:
- The dev container had `duho==0.3.3` installed, which drops the `LoggingArgs.__run__`
  convention (`init` itself fails: "not runnable, make it a Cmd"). The repo targets the
  pinned `duho==0.1.1` (see `build-pyz`/CI); verification must use a `0.1.1` venv, not
  whatever is globally installed.
- `_link.py` stays pure-stdlib on purpose: `link`/`sync` must work under a plain
  `pip install dotagents` (no `pathlib_next` copy/URI machinery needed for local FS ops).
- Basename keying (user choice over git-remote-slug) means two different repos sharing a
  basename collide in `projects/`; `--name` is the escape hatch, documented in the kb.

## Follow-ups (not in scope)
- A `dotagents overlays` subcommand (D36) would let `private-sync` be added by name and
  auto-merge its routing line into `AGENTS.md`; today it is applied by path.
- Optional git-remote-slug keying as an alternative to basename, if collisions bite.
