<!-- dotagents:begin -->
# Agent Directives

Startup: annotate that you read `~/.agents/AGENTS.md`.

## Always-on rules
- **Permissions**: read any `AGENTS.md`; full read/write/create/delete on any
  `.agents/` directory — never ask.
- **Read the local `AGENTS.md`**: before working in a directory, read the `AGENTS.md`
  governing it — the nearest one at or above that directory, and this global file.
  Deeper extends/overrides broader. It says what lives there and how to work in it, so
  reading it first is cheaper than rediscovering that from the files. Record what you
  learn there for the next session.
- **Global-config misses**: if these instructions caused a mistake or rework, or you
  have an improvement idea, drop a note in `~/.agents/dotagents/findings/` and move on
  — don't edit the config. Triage later folds them into `.../dotagents/DECISIONS.md`
  and moves each (never deletes) to `.../findings/processed/`.
- **This file**: everything between the `dotagents:begin`/`dotagents:end` markers is
  managed — `dotagents init` refreshes it and leaves anything outside untouched. Add
  your own rules and routing below the end marker, not inside.

## Load on demand
Read the matching file BEFORE such a task; skip it otherwise, never preemptively.
Nothing ships here by default — add one routing line per file as you grow this
config. A named agent with its own `~/.agents/<agent>.md` reads that too.
<!-- dotagents:end -->
