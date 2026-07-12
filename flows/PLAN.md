# Flow: Plan Generation

Roles: a strong "architect" model writes plans; a cheaper "executor" model runs them.
A plan is written once but read on every executor pass — ambiguity is paid for in
executor trial-and-error. Spend effort on precision, not length.

## Contract — a plan is not `ready` unless all of these hold
1. **Autonomous**: executable start-to-finish with zero user input. Never "ask the
   user" inside a plan. Pre-decide every choice; where real uncertainty remains, state
   the chosen default plus the *observable* condition under which to deviate. Open
   questions get resolved at planning time (research or ask the user now) — never
   deferred to execution.
   - 2+ simultaneous choices → number them (`Design Q1`, `Q2`...) with a bolded
     `Decision:` each; reference by number later.
   - A choice rests on a third party's undocumented behavior → verify against the
     *real* system, not just the plan's own fake, if one is reachable and free;
     record what was checked in Known Facts (else: say so, make it a done-when item).
   - New dependency → check the registry (last release, version) first; recalling
     that it "exists and is good" isn't enough.
   - Bundled independent approvals → record go-ahead per item; a blanket approval
     must enumerate what it covers.
   - Performance claims → recorded baseline first, final comparison after; numbers
     go in the plan before any speedup is claimed.
2. **No implementations**: hints, signatures, `file:line` refs, one-line *Why*
   rationales (past bugs, platform limits — so executors don't undo intent). Code
   snippets ≤5 lines, only when the exact form matters. **Commands are the exception**:
   always literal and complete (exact paths, flags, venv) — an executor must never
   guess a command, and environment setup counts as a command (a new dependency,
   extra, or tool means its literal install command is part of the plan).
3. **Structure**, in order:
   - Title + `Status: draft | review | ready | executing | done` (single line).
   - `## Progress` — one line per phase, all `[ ]` initially (update rules: EXEC.md).
   - `## Known Facts & Context` — everything already discovered/verified: exact paths,
     confirmed behaviors, root causes, absolute dates, environment/test commands.
     Anything not written here will be re-derived at executor prices. Durable
     environment facts (venv paths, test commands, platform gotchas) live in the
     project's root `AGENTS.md` — reference them; restate only what the plan overrides.
   - `## Phases` — per phase: goal, files touched, guidance/hints, *Why* lines, and an
     observable done-when check.
   - `## Verification` — literal commands + expected observable outcomes for the
     whole plan, setup lines before the checks that need them.
   - `## Reporting` — restate: live-update Progress; gotchas discovered during
     execution go to the project's root (or relevant subtree's) `AGENTS.md`.
4. **Right-sized**: split into sub-plans (`plans/<name>/<sub>.md`) only when the plan
   exceeds ~150 lines or phases are independently executable. Parent keeps
   Progress + Known Facts + a sub-plan status table (file / status / prereqs); each
   sub holds one phase, so an executor loads only the parent header + its own phase.
   Say so explicitly in the parent: executors read the parent plus their assigned
   sub-plan only — never sibling sub-plans unless named as a prereq.
   Sub-plan shape: `# Phase N: <name>`, `Status:`, `## Goal`,
   `## Files to Create/Modify`, `## Guidance`, `## Done When`, `## Verification`,
   `## Why This Phase`.

## Executor kickoff prompt (use verbatim, insert the plan path and language)
> You are an expert execution assistant. Read `~/.agents/flows/EXEC.md` and
> `~/.agents/kb/<LANG>.md` (the project's language: PYTHON, NODE, or RUST), then
> execute the plan at `<path>` precisely. Do not deviate, re-evaluate, or make
> unguided assumptions. If an item is blocked, record it in Progress and continue
> with independent items; never ask the user anything.

Format model (shape only, not content): `~/.agents/references/master_refactoring_plan.md`.

## Naming & persistence
Descriptive snake_case (`http_verify_and_fix.md`) — never a harness-generated codename.
Plans live in `<project>/.agents/plans/` — the project the plan is *about*, never the
global `~/.agents/plans/`. Before the first Write: resolve the project root explicitly
(the checkout containing `.git`/the manifest), verify or create its `.agents/plans/`,
and compose the absolute path from that root — never from `~`. If a plan was drafted
in a harness plan mode (which scratches into `~/.agents/plans/` with a codename),
persist it to the project on approval with a `## Progress` section and
`Status: ready`, and delete the global scratch copy.

If peer-architect review is requested, set `Status: review` and follow
`~/.agents/flows/REVIEW.md` before execution.

## Ready checklist (final gate — run before setting `ready`)
1. Zero "ask the user" steps; every choice pre-decided.
2. Every command literal and complete, environment setup included.
3. Every done-when observable — no judgment calls.
4. No code block over 5 lines.
5. Known Facts holds all discovered context — an executor re-derives nothing.
6. Verification runs top-to-bottom on a standard checkout, setup lines first.
7. Expected skip counts stated wherever optional-dependency tests exist.
