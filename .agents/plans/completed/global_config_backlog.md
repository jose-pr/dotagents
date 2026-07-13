# Plan: global_config_backlog — implement the design log's ideas backlog

Status: done

Executed 2026-07-12 against the live `~/.agents` tree (pre-repo era — the config was
not yet under git; see design log D24 for the current source-of-truth layout).
Review record: `global_config_backlog/reviews/codex.md` (2 rounds, all findings
resolved, per `flows/REVIEW.md`). This archived copy is condensed and sanitized for
public tracking: full phase specs and machine-specific commands removed; decisions,
outcomes, and durable facts kept. Byte tables and the moved-rule mapping live in the
design log.

## Progress
- [x] Phase 0 — baseline byte table recorded in the design log
- [x] Phase 1 — plan-lint "Ready checklist" added to flows/PLAN.md; Contracts 2–3
  deduped in the same edit; PLAN.md net size did not grow
- [x] Phase 2 — reviewer Lenses section added to flows/REVIEW.md (≤6 lines); ≤3KB holds
- [x] Phase 3 — env-facts sentence added to PLAN.md Known Facts bullet
- [x] Phase 4 — tools/audit_config.py written; positive + negative tests proven
  (missing-file exit 1, --probe pattern exit 1, out-of-scope backup ignored)
- [x] Phase 5 — 8 non-workflow templates rewritten as hand-curated skeletons;
  --check-templates all PASS; placeholder hygiene enforced by audit patterns
- [x] Phase 6 — size-discipline pass over the five largest always-reused files;
  results and mapping table recorded in the design log
- [x] Dropped: flows/RELEASE.md split — REPO.md under budget; revisit past ~4.5KB
- [x] Dropped: harness plan-mode auto-persist bridge — needs harness hooks, not
  config text; PLAN.md's manual re-home rule stays the mechanism

## Durable decisions (Design Q&A)

**Q1 — plan-lint checklist location.** A `## Ready checklist` section (≤8 lines) at
the bottom of `flows/PLAN.md`, *replacing* (not duplicating) the repeated "literal
commands / top-to-bottom / no missing setup" phrasing inside Contracts 2–3.

**Q2 — reviewer lenses.** Optional, main-architect-assigned when spawning 2+
reviewers: `correctness`, `executor-cost`, `scope+risk`; reviewers prefix findings
with their lens. Single-reviewer reviews stay lens-free.

**Q3 — audit script shape.** `tools/audit_config.py`, stdlib-only, default mode must
run on the Python 3.9 floor. Closed manifest — never a tree walk (the live tree also
holds backups, harness state, and files that legitimately contain the forbidden
strings): existence + pattern scan + size table for core/flows/kb, extra hygiene
patterns for references/, existence-only entries for files that quote the patterns
by design. `--probe <path>` adds one scratch file for negative tests so live config
files are never mutated; `--check-templates` (3.11+, tomllib) is the template
instantiation pass. Root is resolved as `Path.home()/".agents"` (or `--root`) and
printed first — never cwd-derived, because symlinked access paths exist. Exit 1 only
on missing files or forbidden patterns; size budgets warn.

**Q4 — template fidelity.** Regenerated templates are canonical hand-curated
skeletons, not mechanical copies: section *structure* from the exemplar repo,
project-specific prose dropped, identifiers replaced with `<project_name>`/
`<gh_org>`/`<package_name>` placeholders. Hidden `<!-- EXECUTOR: ... -->` comments:
one imperative line each, instruction-only, stripped when writing real repo files.
`--check-templates` substitutes a demo mapping into a temp dir, tomllib/json-parses
the manifests, and key/section-checks the rest; self-cleaning.

## Durable facts

- Bare `python` on a dev machine may deliberately be an old interpreter (e.g. 3.9
  kept for base-deployment testing) — config tooling never assumes its version:
  default audit mode runs on 3.9; anything needing 3.11+ says so on stderr.
- The live `~/.agents` tree contains non-config content that MUST stay out of audit
  scope: the pre-split core backup (contains forbidden `file:///~` links by design),
  harness state (sessions, projects, file history, caches, sqlite), install backups.
- `~/.agents` can be reachable via symlinks from repos' `.agents/global` — scripts
  resolve `Path.home()/".agents"` themselves, never trust cwd.
- The original pre-loss rich templates are unrecoverable (design log F14); the
  regenerated skeletons are the canonical versions now.
- At execution time `~/.agents` had no git safety net — hence Phase 0's recorded
  baseline, the `--probe` design, and a final changed-file inventory in the report.
  (Superseded by D24: the repo is the source of truth and git covers this.)
