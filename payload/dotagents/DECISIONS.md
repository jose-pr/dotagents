# Config Design Decisions — index

Working notes for the design of *your* config itself. **Not loaded in normal
sessions** — read this only when iterating on the config (routing line "Asked to
iterate on this global config"). A living log, not an executable plan.

Ships empty on purpose: it's your log to grow, not the author's.

**Structure** (mirrors the memory pattern — a lean index plus one file per entry, so a
session reads this short index and opens only the entries it needs): each decision is
its own file `decisions/D<nn>.md` with a one-line `description:` scan key; add one index
line here per decision. Findings fold into the decision they prompt (no separate files).

**How findings become config**: as you use the config, notes accumulate under
`~/.agents/dotagents/findings/`. On request, triage them: fold each settled note into a
decision below (+ the payload/flow change it implies), then **move** (never delete) the
processed file to `~/.agents/dotagents/findings/processed/`.

## How to iterate

1. Triage `~/.agents/dotagents/findings/`: read each note, decide keep/drop.
2. Read this index + the current core (`~/.agents/AGENTS.md`) + the flow file(s) you're
   changing.
3. Audit vs reality: pick a recent plan you ran, look for where an executor deviated,
   re-derived facts, or had to ask — that's where the config failed.
4. New decision → new `decisions/D<nn>.md` + an index line here; edit the config (edit a
   source checkout and reinstall, or `~/.agents/` directly). Keep the core `AGENTS.md`
   small; conditional detail goes behind a `flows/` or `kb/` routing line.
5. Never let a flow file restate another's content — link it. Add provenance on
   incidents (primary source, confirming sources, superseded rules).

## Decisions

_(none yet)_
