# Flow: Plan Execution

Execute only plans with `Status: ready` or `executing`; set `executing` at start.
Do not re-plan, second-guess recorded decisions, or silently downgrade required work.

## Load Once

1. Read `$ENGINEERING_OVERLAY_ROOT/kb/MODELS.md`; resolve the exact `Executor: family/subrole`
   using the calling host's native lane (Codex/OpenAI, Claude/Anthropic, Gemini/Google).
   Verify callability, apply settings, and record host/provider/model before edits.
   A permitted fallback and its settings are a recorded deviation.
2. Read the matching `$<LANG>_OVERLAY_ROOT/kb/<LANG>.md` (the language overlay's kb,
   e.g. `$PYTHON_OVERLAY_ROOT/kb/PYTHON.md`), if present; its environment commands
   override generic habits.
3. Read the project's working knowledge — `.agents/AGENTS.md` (repo-root and any
   intervening subtree; deeper scope wins). Committed `AGENTS.md` files are a
   different artifact (REPO.md meta-files): the shipped API header when consuming
   the library, an optional repo-root orientation file for a checkout. Follow
   matching routing lines only.
4. Read the plan in full. For a sub-plan, read its parent first and the assigned
   sub-plan only; update both when phase status changes. **Reconcile before
   continuing**: fix Progress that disagrees with the working tree or git history,
   and move any `Status: done` plan still at `plans/` top level into `completed/`.

## Progress

Use `[ ]` pending, `[/]` active, `[x]` done, `[!]` blocked. Mark a phase `[/]` before
writing code and `[x]` immediately after, with a short outcome. The box update is part
of the step, not bookkeeping about it — code whose box is stale is an incomplete step
the next reader will treat as drift. Blockers keep their reason; continue independent
items and never ask the user. Record key decisions and deviations in Progress.

## Collateral

Before a phase counts as done, update the same commit set as applicable: changelog,
README, `.agents/AGENTS.md` working notes (repo-root or subtree), tests, and examples.
Keep those notes lean; move topical detail to `.agents/{kb,flows,references}/` with a
routing line. A public API change also updates the shipped `AGENTS.md` header in the
same commit. Any run that commits or edits public-facing text runs
your personal leak-scanning command against `<repo>` before handoff — routine hygiene, not only
release discipline; leaks are cheap to fix the day they appear and tedious by the
hundreds.

## Verification

Run literal plan commands and done-when checks; record actual results. Missing tools
or imports mean `[!]` and the exact failed command, never an improvised install.
Skipped tests are not passed: report skip counts and confirm targeted tests ran.
Performance claims require baseline and final evidence; unrun checks are
“implemented, unverified”, never complete.

## Handoff

Reconcile Progress with the working tree. Existing code cannot remain `[ ]`; mark it
done, active with `uncommitted: <files>`, or blocked. Report changed files, evidence,
unverified items, dirty state, and resolved provider/model/settings. Only when no
blocker remains: set `Status: done` and move the plan to `.agents/plans/completed/`
in the same edit — never leave a `done` plan at the `plans/` top level.
