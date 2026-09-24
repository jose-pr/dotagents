# API Reference

The `dotagents` package is a CLI umbrella plus a set of `_*.py` helper modules that
form its API. Generated from docstrings, organized by module:

- **[CLI](cli.md)** — the `dotagents.cli` package: the `Dotagents` umbrella, `main()`,
  and the extension API a command module imports (`DotAgentsArgs`,
  `resolve_user_store`).
- **[Commands](commands.md)** — the built-in command classes (`init`, `overlays`,
  `context`, `env`, `about`, `build-pyz`).
- **[Agents](agents.md)** — the agent registry: the `Agent` base type, the per-harness
  adapters, active-agent detection and identity stamping.
- **[Overlays](overlays.md)** — the `Overlay` type: name rules, discovery, the
  manifest, installing an overlay's files, its setup script, and recomposing the
  managed block over a set of overlays.
- **[Scope](scope.md)** — the `Scope` every walk takes (the stores and their
  precedence), the user-store and project-root resolution, and the overlay-source
  entry point.
- **[Sources](sources.md)** — overlay repos: directories of overlays, registry
  files, git specs, and the precedence the `overlays` commands resolve names through.
- **[Context](context.md)** — assembling the effective context for agents.
- **[Environment](env.md)** — chained env-file assembly and `env.py` execution.
- **[Merge](merge.md)** — the managed-block merge for `init`'s `AGENTS.md`, the
  harness-entry `@` include, the `context --write-agent` block, and other comment
  syntaxes (e.g. TOML) via `begin_marker`/`end_marker`/`append`.
- **[Filesystem](fs.md)** — the one text-file writer every module uses:
  LF-only on every platform, optionally atomic.
- **[Hooks](hooks.md)** — additive, idempotent merge of dotagents' hooks into an
  agent's own settings/config file.
- **[Skills](skills.md)** — publishing an overlay's skills into a scope's shared dir.
- **[Wrappers](wrappers.md)** — the `dotagents` / `dotagents.cmd` wrapper scripts
  `init` writes into a `bin/` dir.
- **[Resources](resources.md)** — the package's own data: the bundled base overlay
  and the base `AGENTS.md` block rendered for a store, reachable from a plain install
  and from a `.pyz`.

The package also ships `AGENTS.md`, a header-file-style summary of this API for an
agent reading an installed copy.
