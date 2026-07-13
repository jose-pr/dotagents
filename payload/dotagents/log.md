# Global Agent Config — Design & Iteration Log

Working notes for the design of this config itself. **Not loaded in normal
sessions** — read this only when iterating on the config (see the core `AGENTS.md`
routing line "Asked to iterate on this global config"). A living LOG, not an
executable plan.

Before adding to this file, triage `~/.agents/dotagents/findings/`: fold settled
notes in as F#/D# entries below, then move each processed file (never delete) to
`~/.agents/dotagents/findings/processed/`.

## Findings

_(none yet)_

## Decisions

_(none yet)_

## How to iterate on this config

1. Triage `~/.agents/dotagents/findings/` first (see above).
2. Read this file, then the current core (`~/.agents/AGENTS.md`) and the flow
   file(s) being changed.
3. Audit against reality: pick 1–2 recent plans in some project's
   `.agents/plans/completed/` and check where executors deviated, re-derived facts,
   or asked questions — that's where the config failed.
4. Record new findings/decisions here (F/D numbering continues), then edit the
   config files directly under `~/.agents/` (or in a `dotagents` source checkout,
   if you keep one, then reinstall). Keep the core `AGENTS.md` small; anything
   conditional goes in a `flows/` or `kb/` file with a routing line.
5. Never let a flow file restate another's content — link it.
6. When updating after an incident, add a provenance note: primary source,
   confirming sources, and superseded rules that should not be restored.
