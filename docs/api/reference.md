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
- **[Context](context.md)** — assembling the effective context for agents.
- **[Environment](env.md)** — chained env-file assembly and `env.py` execution.
- **[Resolution](resolve.md)** — the precedence walk / filename resolution
  (Contract A).
- **[Merge](merge.md)** — the managed-block merge for `init`'s `AGENTS.md` /
  `CLAUDE.md`.
- **[Linking](link.md)** — per-project `.agents` linking and the sync hand-off.
- **[Skills](skills.md)** — publishing an overlay's skills into a scope's shared dir.
- **[Sync](sync.md)** — the `PathSyncer` backup/copy wrapper for `install`.
