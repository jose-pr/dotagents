# Config Design Decisions — index

Not loaded in normal sessions. Read only when deliberately iterating on
`~/.agents/AGENTS.md` or its flows.

**How findings become config**: when an agent hits a mistake or gap caused by this
config, it records a finding instead of editing the config mid-task —
`dotagents findings add -g "<one line>" -b "<details>"` (one file per finding under
`~/.agents/findings/`, a lean `INDEX.md` beside them). On request, triage the queue:
`dotagents findings list -g`, `show -g <name>` for the details, fold each settled one
into a decision below, then close it with `dotagents findings done -g <name> -r
"<the decision, or why no action was needed>"` — that appends the resolution and
**moves** (never deletes) the file to `~/.agents/findings/processed/`. This keeps the
config's evolution auditable without letting routine work rewrite always-on rules
under time pressure.

**Structure** (mirrors the memory pattern — a lean index plus one file per entry, so a
session reads this short index and opens only what it needs): each decision is its own
file `decisions/D<nn>.md` with a one-line `description:` scan key; add one index line
here per decision. Ships empty — this is your log to grow.

## Decisions

_(none yet)_
