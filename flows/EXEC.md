# Flow: Plan Execution

You are executing a plan from `.agents/plans/`. Follow it precisely — don't re-plan,
don't second-guess recorded decisions. Only execute plans with `Status: ready` or
`executing` (set `executing` when you start); anything else, report and stop.

## Before you start — load these, nothing more
1. `~/.agents/kb/<LANG>.md` for the project's language (your kickoff prompt names
   it) — it holds environment conventions (venv layout, which executables to use,
   install forms) that OVERRIDE generic habits like bare `python`/`pip`.
2. The project's local knowledge — read BOTH, skipping one only if already
   auto-loaded: the **user-managed** `.agents/AGENTS.md` (user directives — wins on
   conflict; never write it unless explicitly told) and the **agent-maintained**
   root `AGENTS.md` (notes/gotchas from previous executions — this is where you
   write yours back; Collateral, below). When a phase's work concentrates under a
   subdirectory, also check the dirs between root and it for scoped `AGENTS.md`
   files — deeper extends/overrides broader for its subtree; a subtree-specific
   gotcha goes to that subtree's `AGENTS.md` if one exists, else root's. Follow root
   `AGENTS.md` routing lines into `<project>/.agents/{kb,flows,references}/` only
   when they match the task.
3. The plan itself, in full — Known Facts before Phases. For a sub-plan: read the
   parent first (shared facts, phase order, status table), then your assigned
   sub-plan only — never siblings unless named as a prereq. Update both parent and
   sub-plan when a phase's status changes.

Other global files (`flows/REPO.md`, `references/` templates) only if the plan
explicitly points at them.

## Progress tracking — live, never batched
- `## Progress` at the top: `[x]` done, `[/]` in progress, `[ ]` pending, `[!]` blocked.
- Update IMMEDIATELY after finishing each item, with a brief inline outcome
  ("done", "skipped — N/A", "deviation: used X not Y because Z").
- **Blockers**: mark `[!]` with a one-line reason, then continue with items that don't
  depend on it. Stop only when nothing executable remains. Never ask the user.
- Never downgrade a required plan item into an "optional enhancement" — if it isn't
  done, it stays `[ ]` or `[!]`.
- Record deviations and key decisions in Progress so the next executor resumes without
  needing any conversation history.

## Collateral — before a phase counts as done
Update alongside the code, in the same commit set: `CHANGELOG.md` (`[Unreleased]`),
`README.md`, the project's root `AGENTS.md` (architecture notes + any gotchas/bugs
discovered — mandatory), `tests/`, `examples/`. Root `AGENTS.md` stays lean: a
topical note that outgrows a few lines moves to `<project>/.agents/kb/<topic>.md`
with a one-line routing entry left in root `AGENTS.md`.

## Verification
Run the plan's literal commands and its observable done-when checks; record actual
results in Progress. Failing checks stay `[/]`/`[!]` with the output noted — never
`[x]` on hope. Checklist:
- **Environment failure** (ImportError, missing tool) = the plan omitted a setup
  command → `[!]` + the exact failing command; never improvise installs.
- **Attempt, don't defer**: a `[!]` on verification cites an actual failed command,
  not "not yet run".
- **Skipped ≠ passed**: report skip counts (`pytest -rs`) and confirm the targeted
  tests actually ran.
- **Performance claims**: recorded baseline + final numbers, never intuition.
- **Wording**: unrun verification = "implemented, unverified" — never "complete";
  no `[x]`/✅ on a test item that wasn't executed.

## Handoff
When every item is `[x]` or `[!]`-with-reason: state explicitly what remains
unverified; report changed files, verification evidence, and any dirty working tree;
set `Status: done` and move the plan to `.agents/plans/completed/` (preserving any
sub-plan folder) only if nothing is `[!]` — otherwise leave it in place with the
blocker report at the top of Progress.
