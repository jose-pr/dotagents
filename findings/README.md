# findings/ — global-config miss reports

When the global config itself materially contributes to a mistake, misread, or
avoidable rework, the agent records it here **before moving on** (core rule
"Global-config misses"; design-log D25) — so the lesson feeds back into the config
instead of disappearing into chat history.

Convention:

- One file per incident: `YYYY-MM-DD_<short_snake_case_slug>.md`.
- Short and sanitized (this directory is public): what happened, which instruction
  or gap contributed, and the proposed config fix. No user accounts, machine paths,
  or private repo/plan names — `payload/tools/audit_config.py --repo-hygiene .`
  must stay green.
- Cross-reference the design log: each finding should end up as (or link to) an
  F#/D# entry in `.agents/plans/config_design_log.md` once triaged; note the number
  in the finding file when assigned.
