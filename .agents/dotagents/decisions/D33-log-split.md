---
name: D33-log-split
description: design log split onto the memory pattern: DECISIONS.md index + one file per decision
type: decision
supersedes: the 397-line monolithic .agents/dotagents.md
---

The monolithic `.agents/dotagents.md` (397 lines) was read whole every config-iteration session. Split onto the proven memory pattern: `DECISIONS.md` index (one scan-line per decision) + `decisions/D<nn>.md` (one lean file each: frontmatter description=scan key, terse body, `[[links]]`) + `NOTES.md` (non-decision prose). Sessions read the index + only the entries they need. Directory/index share a stem, index all-caps (`decisions/` ↔ `DECISIONS.md`), mirroring `memory/`↔`MEMORY.md`. Skeleton adopts the same shape so `dotagents init` teaches it. Reconciled the D29 numbering collision between the runtime-log fold ([[D29]]–[[D32]]) and the CLI worktree ([[D33]]). Not for human consumption — token-optimized. Relates to [[D27]] (plan-index question, now with a working precedent).
