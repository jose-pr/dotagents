# Flow: Plan Execution

Execute only plans with `Status: ready` or `executing`; set `executing` at start.
Do not re-plan, second-guess recorded decisions, or silently downgrade required work.

## Load Once

1. Read `~/.agents/MODELS.md`; resolve the exact `Executor: family/subrole` using
   the calling host's native lane (Codex/OpenAI, Claude/Anthropic, Gemini/Google).
   Verify callability, apply settings, and record host/provider/model before edits.
   A permitted fallback and its settings are a recorded deviation.
2. Read the matching `~/.agents/kb/<LANG>.md`, if present; its environment commands
   override generic habits.
3. Read the project's user-managed `.agents/AGENTS.md`, agent-maintained root
   `AGENTS.md`, and intervening subtree files; deeper scope wins. Follow matching
   routing lines only.
4. Read the plan in full. For a sub-plan, read its parent first and the assigned
   sub-plan only; update both when phase status changes.

## Progress

Use `[ ]` pending, `[/]` active, `[x]` done, `[!]` blocked. Mark a phase `[/]` before
writing code and `[x]` immediately after it finishes with a short outcome. Blockers
keep their reason; continue independent items and never ask the user. Record key
decisions and deviations in Progress.

## Collateral

Before a phase counts as done, update the same commit set as applicable: changelog,
README, root or subtree `AGENTS.md` notes, tests, and examples. Keep AGENTS notes
lean; move topical detail to `.agents/{kb,flows,references}/` with a routing line.

## Verification

Run literal plan commands and done-when checks; record actual results. Missing tools
or imports mean `[!]` and the exact failed command, never an improvised install.
Skipped tests are not passed: report skip counts and confirm targeted tests ran.
Performance claims require baseline and final evidence; unrun checks are
“implemented, unverified”, never complete.

## Handoff

Reconcile Progress with the working tree. Existing code cannot remain `[ ]`; mark it
done, active with `uncommitted: <files>`, or blocked. Report changed files, evidence,
unverified items, dirty state, and resolved provider/model/settings. Set `Status: done`
and move the plan to `.agents/plans/completed/` only when no blocker remains.
