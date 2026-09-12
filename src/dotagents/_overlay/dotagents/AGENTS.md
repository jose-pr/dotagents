# `dotagents/` — working notes for this directory

Read this when you work IN this directory (adding a command, touching a hook,
editing the design log). Nothing here is loaded by a normal session.

- `templates/AGENTS.md` — the base `AGENTS.md` block `dotagents init`
  renders into the store root, and every `overlays add` / `remove` / `sync`
  recomposes over the installed overlays' rules and routing. `{{AGENTS_MD}}`
  becomes the actual path of the file being written. The copy that matters is
  the one inside the installed `dotagents` package; a copy here (from a
  `--from` base) is a reference, not what `init` reads.
- `cmds/` — command modules: any `*.py` here defining a `duho` command class is
  a `dotagents <name>` subcommand, discovered by presence (see `cmds/README.md`).
  A module whose name matches a bundled command (`findings`, `launch`)
  overrides it. Files starting with `_` are helpers, never commands.
- `hooks/` — the scripts the agent hooks `init` wires call: keep them
  dependency-free (they run under whatever Python the harness finds) and quiet
  on stdout unless the hook protocol expects output there.
- `DECISIONS.md` + `decisions/` — the design log for this config: one file per
  decision, `D<nn>`, indexed. Record a decision here when a rule changes;
  record a *problem* with `dotagents findings add -g …` instead.
