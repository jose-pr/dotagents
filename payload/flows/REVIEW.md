# Flow: Multi-Architect Plan Review

Peer architect models cross-review a plan before execution. One **main** architect owns
the plan; each **reviewer** owns one report file. All communication is through files —
no shared conversation. Plan `Status: review` while this runs.

## Files
- Plan: `.agents/plans/<name>.md` — only main edits it.
- Reports: `.agents/plans/<name>/reviews/<reviewer-id>.md` (e.g. `gemini.md`,
  `gpt5.md`) — the reviewer creates it; main and reviewer both append; nothing is ever
  rewritten or deleted, so the thread is the audit trail.

## Protocol (default cap: 2 rounds; main is final authority)
1. **Reviewer, round N** — append `## R<N> — findings`, one line per finding:
   `S<id> [blocker|major|minor] (lens?) <plan section/phase>: <suggestion> — <why>`
   S-ids are sequential per report and never reused. Reference plan sections; never
   restate their content. Focus on correctness, missing context, ambiguous executor
   instructions, verification gaps, risk, and token waste — don't rewrite the plan.
2. **Main, resolution** — edit the plan, then append `## R<N> — resolutions` to each
   report, one line per S-id:
   `S<id>: ACCEPTED — <what changed>` | `REJECTED — <why>` | `DEFERRED — <where recorded>`
3. **Reviewer, round N+1** — deltas only: `S<id>: ok` or `S<id>: still-open — <why>`,
   plus new findings *only on sections that changed*. No re-review of untouched
   sections.
4. **Termination** — review ends when a round adds zero new `blocker` findings, or the
   round cap is hit. Main resolves anything still open unilaterally, records DEFERRED
   items in the plan (Known Facts or a `## Deferred` section) so nothing silently
   disappears, and sets `Status: ready`.

## Severity semantics
- `blocker` — executing as written produces wrong/broken results.
- `major` — correct but costs the executor significant tokens/time (ambiguity, missing
  command, re-derivable-but-unstated fact).
- `minor` — style/polish. Main may bulk-reject minors without individual justification.

## Lenses (optional, only with 2+ reviewers)
Main assigns each reviewer one lens; reviewers prefix findings with it:
- `correctness` — executing as written produces wrong/broken results.
- `executor-cost` — ambiguity, missing commands, re-derivable-but-unstated facts.
- `scope+risk` — creep, irreversibility, missing approvals.
Single-reviewer reviews stay lens-free.

## Token rules
- One line per finding and per resolution; rationale is a clause, not a paragraph.
- Reviewers read the plan once, then only its diffs/changed sections in later rounds.
- No pleasantries, summaries, or restated context in report files.
