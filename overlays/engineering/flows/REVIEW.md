# Flow: Multi-Architect Plan Review

Peer architect models cross-review a plan before execution. One **main** architect
owns the plan; each **reviewer** owns one report file. All communication is through
files, never a shared conversation. The plan is `Status: review` while this runs.

## Files
- Plan: `.agents/plans/<name>.md`; only main edits it.
- Reports: `.agents/plans/<name>/reviews/<reviewer-id>.md` (e.g. `gemini.md`). The
  reviewer creates it; main and reviewer both append; nothing is rewritten or
  deleted, so the thread is the audit trail.

## Protocol (default cap: 2 rounds; main is final authority)
1. **Reviewer, round N**: append `## R<N> — findings`, one line per finding:
   `S<id> [blocker|major|minor] (lens?) <plan section/phase>: <suggestion> — <why>`.
   S-ids are sequential per report, never reused. Reference plan sections, never
   restate them. Focus on correctness, missing context, ambiguous executor
   instructions, verification gaps, risk and token waste; don't rewrite the plan.
2. **Main, resolution**: edit the plan, then append `## R<N> — resolutions` to each
   report, one line per S-id:
   `S<id>: ACCEPTED — <what changed>` | `REJECTED — <why>` | `DEFERRED — <where recorded>`
3. **Reviewer, round N+1**: deltas only (`S<id>: ok` or `S<id>: still-open — <why>`),
   plus new findings *only on sections that changed*.
4. **Termination**: when a round adds no new `blocker`, or at the round cap. Main
   resolves anything still open, records DEFERRED items in the plan (Known Facts or
   a `## Deferred` section) so nothing silently disappears, and sets `Status: ready`.

## Severity
- `blocker`: executing as written produces wrong or broken results.
- `major`: correct, but costs the executor significant tokens or time (ambiguity, a
  missing command, a re-derivable but unstated fact).
- `minor`: style or polish. Main may bulk-reject minors without justification.

## Lenses (only with 2+ reviewers)
Main assigns each reviewer one lens, which prefixes its findings: `correctness`
(blocker-class defects), `executor-cost` (major-class costs), `scope+risk` (creep,
irreversibility, missing approvals). A single reviewer works lens-free.

## Token rules
- One line per finding and per resolution; rationale is a clause, not a paragraph.
- Reviewers read the plan once, then only changed sections in later rounds.
- No pleasantries, summaries or restated context in report files.
