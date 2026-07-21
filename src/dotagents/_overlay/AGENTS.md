<!-- dotagents:begin -->
# Agent Directives

Startup: annotate that you read `~/.agents/AGENTS.md`.

## Always-on rules
- **Permissions**: read any `AGENTS.md`; full read/write/create/delete inside this
  config directory (`~/.agents` unless installed elsewhere) — never ask.
- **Read the local `AGENTS.md`**: before working in a directory, read the ones
  governing it — at each level from this global file down to that directory, both
  `AGENTS.md` and `AGENTS.local.md`. Deeper extends/overrides broader, and `.local`
  wins at its own level: it is the unshared override (machine- or user-specific —
  either way, never committed or copied into a repo, plan, or shared config). These
  files say what lives there and how to work in it, so reading them first is cheaper
  than rediscovering it from the source.
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
