# Config Design — reference notes

Non-decision context for the config. Decisions are in [DECISIONS.md](DECISIONS.md) +
`decisions/`. Read a section here only when it's relevant.

## Goals

1. **Mobile**: the whole config is `~/.agents/` — copyable to any machine, usable by any
   agent (Claude Code via `~/.claude/CLAUDE.md`→`@AGENTS.md`; others read `~/.agents/
   AGENTS.md` directly). No agent- or OS-specific paths; plain `~/.agents/...` only.
2. **Records preferences** for repo structure, CI/release, and plan/exec workflows so
   they never need restating per session.
3. **Token-efficient across three cost centers**: C1 instructions read (always-loaded
   core, paid every session — everything else pay-per-use); C2 plan generation (strong
   model, output tokens); C3 plan execution (cheap model re-reads the plan, burns tokens
   on ambiguity). Ambiguity is a C3 multiplier, so C2 spends effort on precision.

`total ≈ core×(all sessions) + flow×(matching) + plan_write(strong) + plan_read×(passes)
+ rediscovery(≈0 if Known Facts is good)`.

## Multi-architect review protocol (design of flows/REVIEW.md; decision: D07)

Flow: main writes plan → each reviewer writes a report → main marks accept/deny + why
in each report + updates plan → peers comment back → iterate. Choices: one file per
reviewer, both append (parallel-safe, thread = audit trail); stable S-ids; severities
give a mechanical termination rule (round with zero new blockers ⇒ done) + let main
bulk-reject minors; 2-round cap; main owns final call; DEFERRED items recorded in the
plan. Reviewers never restate plan content; re-reviews cover changed sections only.

## Size-discipline pass (2026-07-12) — moved-rule mapping

Every fact deleted during the kb/flows compression and where the executor now reaches
it. **Wording in these files is deliberately load-bearing — do not re-expand without
checking this table first.**

| Source (deleted from) | Destination |
|---|---|
| kb/PYTHON.md full badge URL block | references/README.md badge row + kb one-line shield paths |
| kb/PYTHON.md pyproject literals | references/pyproject.toml |
| kb/PYTHON.md mkdocs config prose | references/mkdocs.yml (docs_dir gotcha kept as kb one-liner) |
| kb/PYTHON.md README optional/dev prose | references/README.md EXECUTOR comments |
| kb/PYTHON.md Known Difficulties paragraphs | kept in kb, compressed to symptom→fix one-liners |
| kb/NODE.md package.json spec prose | references/package.json |
| kb/NODE.md + kb/RUST.md badge URL blocks | references/README.md badge row + kb one-liners |
| kb/NODE.md + kb/RUST.md changelog-scraper rationale | flows/REPO.md; kb keeps pointer |
| kb/NODE.md + kb/RUST.md version-tag prose | kb one-liners |
| kb/RUST.md Cargo.toml spec prose | references/Cargo.toml |
| flows/EXEC.md verification incident rationale | decisions D15/D16; EXEC.md keeps the checklist |

## Byte tables (baseline → after 2026-07-12 passes)

Non-listed references/ files were regenerated stubs at both points; current sizes come
from `audit_config.py`'s size table on demand.

| File | Baseline | Final | Saved |
|---|---|---|---|
| AGENTS.md (core) | 2743 | 2743 | 0 (budget ≤ ~2.5KB aspiration) |
| flows/PLAN.md | 5477 | 5475 | 2 |
| flows/EXEC.md | 4062 | 3725 | 337 |
| flows/REVIEW.md | 2309 | 2693 | −384 (Lenses added) |
| flows/REPO.md | 3561 | 3561 | 0 |
| kb/PYTHON.md | 6953 | 4848 | 2105 |
| kb/NODE.md | 4772 | 3025 | 1747 |
| kb/RUST.md | 4839 | 2744 | 2095 |

Compression = **bytes saved without rule loss** (each deletion has a mapping row) so
future agents know terse wording is intentional.

## Template regeneration (2026-07-12)

All 8 non-workflow templates rewritten as hand-curated skeletons (structure generalized
from the freshest standardized repo; package.json/Cargo.toml synthesized from kb specs).
`audit_config.py --check-templates`: all 8 PASS. Placeholder hygiene enforced by the
audit's references patterns.

## Ideas backlog

Closed items live in their decisions; open ones:
- Plan index/dependency convention (D27) — OPEN; this design-log split (D33-log-split)
  is a working precedent for the index-per-dir shape, so reconsider D27 in that light.
- Tools-as-subcommands — see draft plan `.agents/plans/tools_as_dotagents_subcommands.md`.
- Leak check earlier than the tagging gate (D31) — OPEN; EXEC.md collateral or hook.
