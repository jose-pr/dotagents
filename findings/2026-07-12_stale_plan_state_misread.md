# Stale plan-state misread (2026-07-12)

**What happened:** an agent reviewed a repo after a follow-up plan had already been
executed and deleted, but reasoned from an earlier in-session draft summary and from
stale plan references inside another plan, and incorrectly told the user the work
was still pending. Corrected only after re-checking the live plan tree and current
source/tests.

**Config contribution:** the config had strong plan-location rules but no rule to
verify plan state against the live tree before reporting it, and no channel to
record config-contributed misses — the lesson would have been lost.

**Fix:** core `AGENTS.md` gained the "Global-config misses" always-on rule (record a
sanitized note here + the design log before moving on). Logged as F17/D25 in
`.agents/plans/config_design_log.md`.
