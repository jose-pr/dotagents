# Plan: leak_guard_and_agents_md_retrofit

Status: done

Executed 2026-07-12. Two goals: (1) the mechanical guard against private-plan
leakage in committed files (design log F15/D22), and (2) the D23 rollout —
retrofitting oversized agent-maintained root `AGENTS.md` files across the user's
repos to the core+routing split. This archived copy is condensed and sanitized for
public tracking: repos are identified by role, not name.

## Progress
- [x] Phase 1 — `tools/leak_check.py` written; `flows/REPO.md` release step now
  names it; true-positive proven on the exemplar repo's changelog (41 hits)
- [x] Phase 2 — 50 findings fixed across three repos (41 + 7 + 2); all five
  standardized repos then PASS; edited files compile-checked; fixes left uncommitted
  in each working tree for user review
- [x] Phase 3 — design-log backlog bullets all annotated CLOSED/DROPPED
- [x] Phase 4 — smallest retrofit: root 5158→3217B + one kb topic file
- [x] Phase 5 — exemplar repo: root 21809→3311B + kb/{architecture, gotchas,
  plans_history, testing}
- [x] Phase 6 — largest retrofit (a proxy library): root 54718→3808B +
  kb/{architecture 39KB, ci_release, gotchas, testing}
- [x] No-op: two repos had no agent-maintained root `AGENTS.md`; their
  `.agents/AGENTS.md` is user-managed and out of scope (D19)

## Durable decisions (Design Q&A)

**Q1 — leak_check.py shape.** `tools/leak_check.py <repo_path>`, stdlib-only. Scan
set = `git ls-files` (tracked files only), skipping `.gitignore` (legitimately names
agent artifacts) and undecodable files. Patterns: `.agents/` references, `AGENTS.md`
references, `Phase [0-9]` phrasing, plus every plan basename harvested live from
`<repo>/.agents/plans/**/*.md` (including `completed/`). Findings are human-judged —
fix or consciously accept; no allowlist until false positives prove common.

**Q2 — retrofit split criteria (D23).** Root `AGENTS.md` keeps: one-paragraph
architecture map, durable environment facts (venv paths, test commands), always-on
gotchas (≤1 line each), and a routing list. Everything topical moves verbatim
(cut+paste, not rewrite) to `<repo>/.agents/kb/<topic>.md`, one topic per file, each
with a routing line. Target root ≤ ~5KB. Done-when per repo: every routing target
exists; bytes(root + kb) ≥ original root bytes (proves nothing dropped); no
tracked-file changes from the re-homing itself.

**Q3 — fixes to tracked files.** Minimal in-place edits replacing the private
reference with public phrasing (e.g. a plan filename → "planned as separate
follow-up work"). No commits unless the user asks — leave edits in the working tree
and report them.

## Durable facts

- The proven-real leak class: a changelog `[Unreleased]` section naming a private
  plan file — changelogs get copied to package registries, so they are the
  highest-value scan target.
- Repos may expose `.agents/global` symlinks into `~/.agents` — never traverse them
  when globbing/copying.
