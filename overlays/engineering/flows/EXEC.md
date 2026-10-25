# Flow: Plan Execution

Execute only plans with `Status: ready` or `executing`; set `executing` at start.
Do not re-plan, second-guess recorded decisions, or silently downgrade required work.

## Load Once

1. Read `$ENGINEERING_OVERLAY_ROOT/kb/MODELS.md`; resolve the exact
   `Executor: family/subrole` on the calling host's native lane (Codex/OpenAI,
   Claude/Anthropic, Gemini/Google). Verify callability, apply settings, and record
   host/provider/model before edits. A permitted fallback is a recorded deviation.
2. Read `$<LANG>_OVERLAY_ROOT/kb/<LANG>.md` if present; its environment commands
   override generic habits.
3. Read the project's `.agents/AGENTS.md` at the root and each intervening subtree
   (deeper wins); follow matching routing lines only.
4. Read the plan in full once (`dotagents plans show <name>`); after that,
   `dotagents plans next <name>` gives what the current step needs. For a sub-plan,
   read its parent first and the assigned sub-plan only; update both when a phase
   changes. **Reconcile before continuing**: fix Progress that disagrees with the
   working tree or git history, and move any `done` plan at the `plans/` top level
   into `completed/`.
5. **Check for another executor in this tree** before editing, and check your base
   if you are in a worktree: `$ENGINEERING_OVERLAY_ROOT/flows/PARALLEL.md`, which
   also covers fanning out, integrating and removing worktrees.

## Progress

`[ ]` pending, `[/]` active, `[x]` done, `[!]` blocked. Mark a phase `[/]` before
writing code and `[x]` right after, with a short outcome, one call each as the step
happens, never batched at the end:

```
dotagents plans start <name> <phase>
dotagents plans check <name> <phase> -m "<outcome>"
dotagents plans block <name> <phase> -m "<reason and the failed command>"
dotagents plans note  <name> -m "<fact learned, with its evidence>"
```

Where the harness has `dotagents` as an MCP server (`DOTAGENTS_MCP=stdio`), use the
tools `dotagents.plans.start`, `.check`, `.block` and `.note` instead of a process
each. The box update is part of the step: code whose box is stale reads as drift.
Blockers keep their reason; continue independent items and never ask the user.
Record key decisions and deviations in Progress.

## Collateral

Before a phase counts as done, update in the same commit set as applicable: the
changelog, README, `.agents/AGENTS.md` notes, tests and examples; a public API
change also updates the shipped `AGENTS.md` header. Any run that commits or edits
public-facing text runs `dotagents leak-check <repo>`
(`$ENGINEERING_OVERLAY_ROOT/kb/LEAKS.md`) before handoff, not only at release.

## Verification

- Run the plan's literal commands and done-when checks; record actual results.
- A missing tool or import is `[!]` with the exact failed command, never an
  improvised install.
- Skipped tests are not passed: report skip counts and confirm targeted tests ran.
- A step whose output feeds something else reads that output back the way the
  consumer will (its parser, its type) before reporting success.
- Never hand-write the serialised form of a type the codebase defines.
- Every image to be published is looked at, not sampled.
- A check that reports PASS is trusted only after it has been seen to FAIL on a
  planted bad case (a positive control).
- Performance claims need baseline and final evidence. Unrun checks are
  "implemented, unverified", never complete.

## Handoff

Reconcile Progress with the working tree: existing code cannot remain `[ ]`; mark it
done, active with `uncommitted: <files>`, or blocked. Report changed files,
evidence, unverified items, dirty state and the resolved provider/model/settings.
Only when no blocker remains: `dotagents plans status <name> done -m "<evidence>"`,
which sets `Status: done` and moves the plan to `.agents/plans/completed/`.
