# Fold standalone tools into dotagents subcommands

Status: draft
Executor: (unassigned — draft)

## Idea
Distinguish two classes of tool (user clarification 2026-07-14):
- **dotagents-required** — tools that operate on dotagents' *own* structure. Only these
  belong as core `dotagents` subcommands. Candidate: `audit_config.py` → `dotagents
  audit` (validates the config's own layout/templates/hygiene; the CLI already stubs a
  `dotagents audit` per D33 — reconcile to one implementation). Arguably `leak_check.py`
  too, since leakage hygiene is a dotagents concern.
- **user-overlay tools** — `summarize_run.py`, `compare_bench.py` (and skills) are NOT
  dotagents-required; they're part of a *user-provided overlay* (personal instructions +
  tools the user opts into), like the personal `flows/`/`kb/`. These should NOT be baked
  into the CLI's required surface. They stay as overlay-provided scripts/skills the
  user's own config references; if they ever get a unified runner it's a *separate*,
  user-overlay concern, not `dotagents <sub>`.

So the actual scope is narrow: fold only the dotagents-required tool(s) (`audit`, maybe
`leak-check`) into the CLI; leave the overlay tools alone.

## Rough scope
- Reconcile `dotagents audit` (D33 CLI stub) with standalone `audit_config.py` → one
  implementation. Decide: does the standalone script remain (payload installs it, some
  rules reference it by path) or fully move behind `dotagents audit`?
- Consider `leak_check.py` → `dotagents leak-check` on the same required-tool basis.
- Explicitly leave `summarize_run.py`/`compare_bench.py` as user-overlay tools — do NOT
  make them subcommands. Note the token-discipline rule (D31) references
  `~/.agents/tools/summarize_run.py` by path; that's an overlay tool and stays.

## Why (not now)
Adjacent to the CLI-package + design-log-split work but out of scope (D26). Needs the
required-vs-overlay boundary decided per tool (started above) before it's a `ready` plan.
