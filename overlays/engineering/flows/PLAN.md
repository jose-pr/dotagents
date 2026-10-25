# Flow: Plan Generation

A plan is read on every executor pass: spend tokens on precision, not prose. Plans
are autonomous: resolve choices while planning, state defaults and observable
deviation triggers, and never defer a question to execution.

## Ready Contract

1. **No user input**: resolve open questions by research or an explicit default.
   Number simultaneous choices as `Design Q1`, `Q2`, with a bold `Decision:`.
2. **No implementation**: paths, signatures, hints, a one-line *Why*, and code
   blocks of at most five lines. Commands are complete literals, environment setup
   included; verify undocumented third-party behaviour against the real system.
3. **Required shape**: title + `Status:`; exact `Executor: family/subrole` from
   `MODELS.md` plus why; `## Progress`; `## Known Facts & Context`; `## Phases` with
   files, guidance, Why and observable done-when; `## Verification`; `## Reporting`.
4. **Right-sized**: split only above ~150 lines or for independently executable
   phases. A parent keeps facts, progress and a sub-plan status table; each sub-plan
   holds one phase. Executors read only the parent and their assigned sub-plan.
5. **Evidence**: performance baselines before claims, registry metadata before new
   dependencies, per-item consent for bundled approvals. A plan that breaks a
   documented API names its known consumers (dependents, sibling repos), and its
   Verification runs their suites against the new tree.

## Writing one

Start with `dotagents plans add <name> -t "<title>" -p "<phase>" ...`: it writes
`$ENGINEERING_OVERLAY_ROOT/references/plan_template.md` filled with those phases
(shapes drift when imitated from memory or an old plan). `dotagents plans validate
<name>` must pass before the plan is set `ready`.
`$ENGINEERING_OVERLAY_ROOT/references/master_refactoring_plan.md` illustrates tone
and detail only; the template wins any disagreement.

Executor kickoff, given with the plan:

> Read `$ENGINEERING_OVERLAY_ROOT/flows/EXEC.md` and, if present,
> `$<LANG>_OVERLAY_ROOT/kb/<LANG>.md`. Execute `<path>` precisely; `EXEC.md` resolves
> its `Executor:` role and provider. Keep `## Progress` live (`[/]` when started,
> `[x]` when done); record blockers and continue independent work without asking the
> user.

## Persistence

Resolve the project root before the first write (where plans live: the Plans rule
in `$ENGINEERING_OVERLAY_ROOT/rules/ENGINEERING.md`). Move an approved harness draft
there with `Status: ready` and delete the scratch copy. Peer review uses
`Status: review` and `REVIEW.md`.

The `plans/` top level holds only live plans (`draft`/`ready`/`review`/`executing`);
`completed/` holds finished ones, `on-hold/` parked ones. `dotagents plans status
<name> <status>` sets the status and moves the file together: `done` needs every
phase checked and `-m` with the evidence, `on-hold` needs `-m` with the reason. Every
change regenerates `plans/INDEX.md`. A `done` plan at top level is drift: whoever
sees one moves it.

## One tool for every plan

| Need | Command |
| --- | --- |
| what exists | `plans list`: one line per plan (status, progress, current phase) |
| what to do now | `plans next <name>`: the head, the progress list, one phase |
| one part | `plans show <name> --section facts` / `--phase 3` |
| a step | `plans start`, `check -m`, `block -m`, `uncheck`: one line changes |
| a fact learned | `plans note <name> -m "..."` |
| the shape | `plans validate <name>` |

Do not read a whole plan to learn one thing, or rewrite one to change a line.

## Ready Checklist

- No user questions, guesses, incomplete commands, or non-observable done-when items.
- Known Facts holds discovered context; Verification runs setup before checks.
- Optional-test skip counts and expected outcomes are stated.
- No code block exceeds five lines.
- `Executor:` names a role present in `MODELS.md`; no raw provider IDs.
- A rule cited to skip, stub or shrink work is quoted verbatim with `file:line`,
  never paraphrased.
