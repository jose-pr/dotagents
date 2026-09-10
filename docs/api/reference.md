# API Reference

The `dotagents` package is a CLI umbrella plus a set of `_*.py` helper modules that
form its API. Generated from docstrings, organized by module:

- **[CLI](cli.md)** — the `dotagents.cli` package: the `Dotagents` umbrella,
  `main()`, and command discovery.
- **[Agents](agents.md)** — the agent registry: the `Agent` base type and identity
  stamping.
- **[Overlays](overlays.md)** — installing an overlay's files and collecting its
  `AGENTS.md` contributions; setup-script discovery and the manifest reader.
- **[Scope](scope.md)** — scope and overlay-source resolution for the `overlays`
  command.
- **[Sources](sources.md)** — overlay repos: directories of overlays, registry
  files, git specs, and the precedence the `overlays` commands resolve names through.
- **[Context](context.md)** — assembling the effective context for agents.
- **[Environment](env.md)** — chained env-file assembly and `env.py` execution.
- **[Merge](merge.md)** — the managed-block merge for `init`'s `AGENTS.md` /
  `CLAUDE.md`, the harness-entry `@` include, the `context --write-agent`
  block, and other comment syntaxes (e.g. TOML) via `begin_marker`/
  `end_marker`/`append`.
- **[Filesystem](fs.md)** — the one text-file writer every module uses:
  LF-only on every platform, optionally atomic.
- **[Hooks](hooks.md)** — additive, idempotent merge of dotagents' hooks into an
  agent's own settings/config file.
- **[Skills](skills.md)** — publishing an overlay's skills into a scope's shared dir.
