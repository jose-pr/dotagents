# Flow: Plan Generation

A plan is read on every executor pass, so spend tokens on precision, not prose.
Plans are autonomous: resolve choices while planning, state defaults and observable
deviation triggers, and never defer questions to execution.

## Ready Contract

1. **No user input**: resolve open questions by research or an explicit default.
   Number simultaneous choices as `Design Q1`, `Q2`, with a bold `Decision:`.
2. **No implementation**: use paths, signatures, hints, one-line *Why* rationale,
   and code blocks of at most five lines. Commands are complete literals, including
   environment setup; verify undocumented third-party behavior against the real system.
3. **Required shape**: title + `Status:`; exact `Executor: family/subrole` from
   `MODELS.md` plus why; `## Progress`; `## Known Facts & Context`; `## Phases` with
   files, guidance, Why, and observable done-when; `## Verification`; `## Reporting`.
4. **Right-sized**: split only above ~150 lines or for independently executable
   phases. A parent keeps facts/progress and a sub-plan status table; each sub-plan
   contains one phase. Executors read only the parent and assigned sub-plan.
5. **Evidence**: record performance baselines before claims, registry metadata before
   new dependencies, and per-item consent for bundled approvals.

## Executor Kickoff

> Read `$FLOWS_OVERLAY_ROOT/flows/EXEC.md` and, if present,
> `$<LANG>_OVERLAY_ROOT/kb/<LANG>.md`. Execute `<path>` precisely; `EXEC.md` resolves
> its `Executor:` role and provider. Keep `## Progress` live (`[/]` when started,
> `[x]` when done); record blockers and continue independent work without asking the
> user.

Start every plan by **copying** `$REFERENCES_OVERLAY_ROOT/references/plan_template.md`
— imitating from memory or an old plan is how shapes drift. The shape reference
`$REFERENCES_OVERLAY_ROOT/references/master_refactoring_plan.md` illustrates tone and
detail only; the template wins any disagreement.

## Persistence

Use descriptive snake_case filenames. Plans live under the project checkout at
`<project>/.agents/plans/`, never `~/.agents/plans/`. Resolve the project root before
the first write; move approved harness drafts there with `Status: ready` and delete
the scratch copy. Peer review uses `Status: review` and `REVIEW.md`.

The `plans/` top level holds only live plans (`draft`/`ready`/`review`/`executing`);
finished ones move to `plans/completed/` in the same edit that sets `Status: done`.
A `done` plan at top level is drift — whoever sees one moves it.

## Ready Checklist

- No user questions, guesses, incomplete commands, or non-observable done-when items.
- Known Facts contains discovered context; verification runs setup before checks.
- Optional-test skip counts and expected outcomes are stated.
- No code block exceeds five lines.
- `Executor:` names a role present in `MODELS.md`; raw provider IDs do not appear.
