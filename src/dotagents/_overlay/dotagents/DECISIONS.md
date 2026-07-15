# Config Design Decisions — index

Not loaded in normal sessions. Read only when deliberately iterating on
`~/.agents/AGENTS.md` or its flows.

**How findings become config**: when an agent hits a mistake or gap caused by this
config, it drops a short note in `~/.agents/dotagents/findings/` instead of editing the
config mid-task. On request, triage that directory: fold each settled note into a
decision below, then **move** (never delete) the processed file to
`~/.agents/dotagents/findings/processed/`. This keeps the config's evolution auditable
without letting routine work rewrite always-on rules under time pressure.

**Structure** (mirrors the memory pattern — a lean index plus one file per entry, so a
session reads this short index and opens only what it needs): each decision is its own
file `decisions/D<nn>.md` with a one-line `description:` scan key; add one index line
here per decision. Ships empty — this is your log to grow.

## Decisions

_(none yet)_
