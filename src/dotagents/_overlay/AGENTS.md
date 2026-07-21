<!-- dotagents:begin -->
# Agent Directives

Startup: annotate that you read `~/.agents/AGENTS.md`.

## Always-on rules
- **Permissions**: full read/write/create/delete on any `.agents/` directory — never ask.
- **Global-config misses**: if these instructions caused a mistake or rework, or you
  have an improvement idea, drop a note in `~/.agents/dotagents/findings/` and move on
  — don't edit the config. Triage later folds them into `.../dotagents/DECISIONS.md`
  and moves each (never deletes) to `.../findings/processed/`.
- **This file**: everything between the `dotagents:begin`/`dotagents:end` markers is
  managed — `dotagents init` refreshes it and leaves anything outside untouched. Add
  your own rules and routing below the end marker, not inside.

## Load on demand
Read the matching file BEFORE such a task; skip it otherwise, never preemptively.
Nothing ships here by default — add one routing line per file as you grow
`~/.agents/flows/`, `~/.agents/kb/<language>.md`, or named-agent `~/.agents/<agent>.md`.
<!-- dotagents:end -->
