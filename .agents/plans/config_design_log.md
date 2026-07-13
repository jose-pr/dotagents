# Global Agent Config — Design & Iteration Log

Working notes for the design of the config itself. **Not loaded in normal sessions** —
read this only when iterating on the config. This is a living LOG, not an executable
plan — Status/Progress rules don't apply; executable work lives beside it in
`plans/`. Tracked publicly since the repo restructure (D24): entries are sanitized —
findings from private repos are described by role ("the exemplar repo", "a proxy
library"), never by name; no user accounts or machine paths.

## Goals

1. **Mobile**: the whole config is `~/.agents/` — copyable to any machine, usable by
   any agent (Claude Code loads `AGENTS.md` via `~/.claude/CLAUDE.md` → `@AGENTS.md`;
   Antigravity and others read `~/.agents/AGENTS.md` directly). No agent- or
   OS-specific paths; plain `~/.agents/...` notation only.
2. **Records preferences** for repo structure, CI/release flows, and plan/execution
   workflows so they never need restating per session.
3. **Token-efficient across three cost centers**:
   - **C1 — instructions read**: the always-loaded core is paid in *every* session;
     everything else must be pay-per-use.
   - **C2 — plan generation**: written once by a strong ("architect") model — output
     tokens are the expensive kind.
   - **C3 — plan execution**: a cheaper model reads the plan repeatedly and burns
     tokens on any ambiguity via trial-and-error. Ambiguity is a C3 multiplier, so
     C2 spends effort on *precision*, not length.

Cost model in one line:
`total ≈ core×(all sessions) + flow_file×(matching sessions) + plan_write(strong) + plan_read×(executor passes) + rediscovery(≈0 if Known Facts is good)`.

## Findings (2026-07-12 audits)

- F1: A compressed core had lost load-bearing content vs its 9.5KB predecessor:
  `ci-*` tag naming + cleanup discipline, the changelog-scraper rationale, the
  agent-artifact `.gitignore` list, the PEP440-vs-SemVer "don't fix one to match the
  other" warning, the "never descend into `completed/`" nuance. Compression that
  drops the *why* pushes cost into C3 (executors re-derive or violate).
- F2: All three `kb/*.md` files linked `~/.agents/examples/...` — a directory that no
  longer existed (templates live in `references/`). Broken since a rename.
- F3: `file:///~/...` markdown links resolve nowhere (tilde is invalid in file URLs)
  and cost more tokens than bare paths.
- F4: Everything was always-loaded — repo/CI/release rules were paid for in sessions
  that never touch a repo's structure.
- F5: Harness plan mode persists plans with random codenames into `~/.agents/plans/`
  — naming + re-home rule needed.
- F6: Old executor prompt said "Flag blockers and stop" — a first-blocker full stop
  wastes an entire execution session and conflicts with fully-autonomous executors.
- F7: a live session's in-context copy of the global config silently diverges from
  disk after a restructure — a same-day session kept planning with the old structure
  until explicitly told to reload.
- F8: real plans validated a numbered **"Design Q# … Decision:"** block per
  genuinely-undecided choice, cross-referenced by number — needed whenever a plan
  has 2+ simultaneous open choices.
- F9: recall is not verification — two real instances where a stated assumption about
  a third-party API / package ecosystem was wrong or incomplete until checked against
  the real system. Plans resolving external-behavior or dependency questions must say
  *how* they checked, not just assert.
- F10: a cheap-model executor probed a quoted Windows path ending in `\"` — the
  trailing backslash-quote mangled it in Git Bash and the executor confidently
  reported "No plans directory found". Cheap model + unstated shell gotcha ⇒ false
  conclusion, not an error. Plans use forward-slash paths; "not found" probes need
  one corroborating check.
- F11: an executor never ran pytest — and couldn't have: the plan added optional
  extras but omitted the install command; unguarded SDK imports in new test modules
  then broke *collection for the whole suite*, transitively blocking two sibling
  plans' verification. One missing install line ⇒ zero verification across three
  plans. Report said "Implementation complete ✅" over unrun tests.
- F12 (retry of F11): executor improvised the missing install with bare
  `python -m pip` → resolved to an old **global** interpreter, polluting system
  site-packages while both venvs stayed SDK-less. The venv rule existed in
  `kb/PYTHON.md` but was never on the executor's load path. Rules not reachable from
  the kickoff prompt don't exist for the executor.
- F13: a session in another project wrote four plans into global `~/.agents/plans/`.
  Three config defects invited it: Permissions said "(global or project)"; PLAN.md's
  persistence rule used a relative `.agents/plans/` string; harness codename
  artifacts already there provided precedent-by-example. Agents pattern-match on what
  wording *permits* and what the filesystem already *shows*.
- F14 (data loss + recovery, 2026-07-12): `~/.agents` was lost and recreated.
  Recovery (see `kb/RECOVERY.md`) rebuilt paraphrases from transcripts/IDE caches;
  the live session that had authored the config then restored originals from context
  and **verified them byte-for-byte against exact content-hash snapshots** found in
  IDE chat-editing storage and agent conversation DBs (core backup: identical;
  NODE/RUST: identical after re-applying the session's deterministic transforms;
  PYTHON: identical + two deliberate additions). Reference templates survived only as
  rebuilt stubs — originals were never read into any transcript; they were
  regenerated from the user's newest standardized repos (newest file wins). Workflow
  templates were restored from one repo's CI-validated workflows, generalized.
- F15 (private-plan leakage, 2026-07-12): public/committed files must not reference
  private agent plan names, `.agents/plans` paths, or "Phase N" labels from those
  plans. Release notes/changelogs are especially sensitive — they get copied to
  package registries and GitHub releases. A sweep of the user's repos found ~50 repair
  candidates: a changelog naming three private plan files, code/test/benchmark
  comments citing plan names or phase labels. All rewritten to public rationale or
  issue-neutral wording (mechanism, not provenance).
- F16 (repo restructure, 2026-07-12): with the payload at the repo root, every agent
  session *developing* the config repo auto-loaded the payload core as its project
  instructions — telling it to gitignore the very files the repo exists to track, and
  routing it to `~/.agents/...` instead of the checkout being edited. The product
  must not govern its own workshop.
- F17 (execution-state misread, 2026-07-12): an agent reviewed a repo after a
  follow-up plan had already been executed and deleted, but still reasoned from an
  earlier in-session draft summary and from stale plan references inside another
  plan. Result: it incorrectly told the user the compliance plan was still pending.
  The fix came only after re-checking the live plan tree and current source/tests.
  The config had strong plan-location rules, but no explicit rule to record when the
  global config itself contributed to a miss, so the lesson could easily have been
  lost instead of fed back into the config. Independently reconfirmed as a private
  finding the same day (see F18) — corrective actions match.
- F18 (findings-mechanism dogfooding, 2026-07-12): once D25's original "record
  findings in the source repo" rule shipped, the very next uses of it surfaced
  three more issues (F19–F21) — including a self-correction (F19) of D25 itself.
  Signal that a first-draft feedback-loop rule needs a live trial before it's
  considered settled.
- F19 (findings forced unwanted repo edits, 2026-07-12): the original rule told
  agents to write findings into the tracked dotagents repo and update the design log
  immediately. The user's actual intent was a lightweight *personal* backlog kept
  under `~/.agents/dotagents/findings/`, reconciled into the public repo only during
  a deliberate, explicitly-requested triage pass. The rule conflated "capture a
  lesson for later" with "update the source of truth now" — two different jobs with
  different urgency and different audiences (private vs. public).
- F20 (no draft-plan escape hatch, 2026-07-12): mid-execution, agents that noticed
  adjacent work or improvements had only two options — silently drop the idea, or
  scope-creep the current execution to cover it. No sanctioned way to capture the
  idea without expanding scope.
- F21 (plan discoverability, 2026-07-12): as a project's `.agents/plans/` grows,
  nothing records which plans are active, which supersede others, or what order
  dependent plans should run in — raising the same "stale plan state" risk as F17 at
  the plan-directory level instead of the single-plan level. Flagged as a structural
  idea worth prototyping, not yet a settled design (no chosen index format).

## Decisions

- **D1 — Core + flows split.** `AGENTS.md` keeps only (a) rules that apply to every
  session and (b) a load-on-demand routing table. Task-scoped rules live in
  `flows/{PLAN,EXEC,REVIEW,REPO}.md`. Target: core ≤ ~60 lines.
- **D2 — Release-tag consent stays in the core**: the one irreversible action must
  not depend on the agent having loaded `REPO.md` first.
- **D3 — Plain paths, no `file:///` links** anywhere in the config (F3).
- **D4 — kb files point at `flows/REPO.md`** for the generic repo standard; their
  stale `examples/` links fixed to `references/` (F2).
- **D5 — Plans carry a `Status:` line**: `draft | review | ready | executing | done`.
  Executors refuse anything not `ready`/`executing`.
- **D6 — Blocker semantics** (F6): `[!]` + one-line reason, continue independent
  items, stop only when nothing executable remains. Never ask the user.
- **D7 — Multi-architect review is file-threaded**: one review file per reviewer
  under `plans/<name>/reviews/`, S-ids, severities, main's resolutions appended in
  the same file, 2-round default cap, main is final authority (`flows/REVIEW.md`).
- **D8 — Sub-plan threshold**: split only when >~150 lines or phases independently
  executable; parent keeps Progress + Known Facts + a sub-plan status table; each sub
  holds one phase; executors read parent + assigned sub only, never siblings.
- **D9 — Standard executor kickoff prompt** lives in `flows/PLAN.md` and routes the
  executor to `flows/EXEC.md`.
- **D10 — the pre-split monolithic core is superseded** by `flows/` + this file; its
  backup is kept in `~/.agents` only, as historical reference (byte-verified
  post-recovery, never tracked).
- **D11 — numbered Design-Q/Decision sub-structure** (F8), in PLAN.md Contract 1.
- **D12 — external-behavior verification requirement** (F9): decisions resting on a
  third party's undocumented behavior get checked against the real system when
  reachable/free; what was checked goes in Known Facts, else it's a done-when item.
- **D13 — dependency-maturity check** (F9): new third-party deps need registry
  metadata (last release, version) checked and recorded before recommending.
- **D14 — per-item go-ahead**: bundled independently-approvable items record consent
  per item; blanket approvals enumerate what they cover.
- **D15 — verification must be environment-complete** (F11): environment setup
  counts as a command (PLAN.md); Verification puts setup before checks with a
  "runs top-to-bottom on a standard checkout" self-test; EXEC.md: environment
  failures are `[!]` with the exact failing command, never improvised installs;
  unrun verification is "implemented, unverified", never "complete"; `kb/PYTHON.md`:
  optional-extra test modules start with `pytest.importorskip`.
- **D16 — kickoff prompt routes to the language kb** (F12). Principle: any rule an
  *executor* must obey has to be reachable from the kickoff prompt (EXEC.md or the
  language kb), not only from the core routing table. EXEC.md opens with a
  "Before you start" load list (language kb, local AGENTS.md files, the plan).
- **D17 — local-AGENTS read rule**: read both `.agents/AGENTS.md` and root
  `AGENTS.md` when present (skip whichever was auto-loaded).
- **D18 — nested/scoped AGENTS.md**: subfolders may carry their own; read
  root→subtree; deeper extends/overrides broader — same chain as global → project.
  Matches the ecosystem's nested AGENTS.md/CLAUDE.md convention.
- **D19 — ownership flip (supersedes D17's write/precedence)**: agents write
  directory-level `AGENTS.md` (root or subtree); **`.agents/AGENTS.md` is
  user-managed** — read always, write never unless told; it wins on conflict.
- **D20 — global plans dir is scratch-only** (F13): plans are ALWAYS
  `<project>/.agents/plans/`; Permissions dropped "(global or project)"; PLAN.md
  mandates resolving the project root before the first Write and deleting the global
  scratch copy after persisting.
- **D21 — post-recovery merges** (F14): rules from the rebuilt files worth keeping
  were folded into the restored originals — skipped ≠ passed (report skip counts,
  `pytest -rs`); benchmark claims need recorded baseline + final numbers; never
  downgrade required plan items to "optional enhancements"; sub-plan parent/child
  status updated together; final executor report includes changed files + dirty
  tree; templates may carry hidden `<!-- EXECUTOR: ... -->` comments that must be
  stripped when writing real repo files; a generic routing row for other named
  agents (`~/.agents/<agent>.md`).
- **D22 — no private-plan leakage in committed artifacts** (F15): changelog entries,
  release notes, README/docs, committed source comments, tests, workflows, and
  benchmarks describe user-visible behavior, bug rationale, or technical mechanisms
  directly. Do not mention private plan filenames, `.agents/plans` paths, internal
  phase numbers, reviewer IDs, or agent workflow history. If provenance matters,
  record it in the private plan/design log, not in committed project files.
- **D23 — project AGENTS.md mirrors the global core+routing split (2026-07-12).**
  A project's root `AGENTS.md` is that project's always-loaded core, so the same
  size discipline applies: always-on facts + a load-on-demand routing list only.
  Topical detail lives in `<project>/.agents/{kb,flows,references}/` mirroring
  `~/.agents`'s layout, loaded only when a routing line matches. Executors move any
  topical note that outgrows a few lines out of root `AGENTS.md` into
  `.agents/kb/<topic>.md`, leaving a one-line routing entry. `.agents/AGENTS.md`
  stays user-managed and unaffected. Recorded in: core Local-knowledge bullet,
  `flows/EXEC.md`.
- **D24 — config source of truth is this repo; payload isolated under `payload/`
  (2026-07-12, supersedes the initial root-mirror layout).** The public repo holds
  the sanitized payload under `payload/` (core, flows, kb, references, tools —
  installs 1:1 into `~/.agents` via `install.py`); `~/.agents` is an *install
  target*, never edited directly. Repo root carries its own dev-facing `AGENTS.md`/
  `CLAUDE.md` so sessions working on the repo are not governed by the payload (F16).
  This repo — alone — tracks `.agents/` (this log + plans) as public documentation;
  everything tracked is sanitized: no user accounts, machine paths, or private
  repo/plan names (`audit_config.py --repo-hygiene` enforces the mechanical part;
  D22 wording judgment covers the rest). Machine-specific facts stay out of the
  tracked payload or are phrased generically. The pre-split core backup and harness
  state remain only in `~/.agents`.
- **D25 — global-config misses get first-class writeups, captured privately by
  default** (F17, revised 2026-07-12 by F19 — supersedes the original same-day
  version that wrote into the tracked repo). When an agent determines the global
  config contributed to a mistake, misread, or avoidable rework — or has an
  improvement idea — it writes a short note under `~/.agents/dotagents/findings/`
  (create if absent) and stops there. This is deliberately decoupled from the
  source of truth: no edit to the dotagents repo, its design log, or the installed
  payload, unless the user explicitly asks for a triage/reconciliation pass. Purpose:
  keep "we fixed it later" incidents from disappearing into chat history, without
  forcing every capture to become a repo change.
- **D26 — executors may leave `Status: draft` follow-up plans** (F20). Mid-execution
  discoveries worth capturing but out of current scope become a new top-level plan
  (`.agents/plans/<name>.md`) with just idea + rough scope + why, `Status: draft`.
  Never expanded or executed in the same pass — an architect turns it into a `ready`
  plan later. Recorded in core `AGENTS.md`.
- **D27 — plan indexing left open** (F21). No index/README convention adopted yet;
  candidate shape (`plans/<plan>/index.md` + sibling subplan files, replacing the
  current `plan.md` + `plan/<sub>.md` split) stays a backlog idea until it's tried on
  a real plan family and the trade-off against D8's existing sub-plan shape is clear.
- **D28 — `~/.agents/dotagents/` is the private working area for the config itself**
  (2026-07-12, formalizes the location F19 already established for findings).
  Distinct from this repo's tracked `.agents/` (public, sanitized, reconciled only):
  `~/.agents/dotagents/` is personal scratch, gitignored, never sanitized on write.
  Layout, matching the same `plans/`/`findings/` shape used per-project elsewhere so
  nothing already written there needs to move:
  - `findings/YYYY-MM-DD_<slug>.md` — D25's private capture target; flat, no
    subdirectory, one file per incident/idea. Existing files keep their names.
  - `plans/<name>.md`, `plans/completed/` — for a plan *about the config* that the
    user wants scratched privately before it's ready for the public repo (mirrors
    D20's per-project rule, applied to this one meta-project). Most config work still
    plans directly in the repo's tracked `.agents/plans/`; this is the private-draft
    escape hatch, not the default.
  - No other subdirectories added speculatively — extend only when a concrete need
    appears (matches the "never load/create preemptively" principle already applied
    elsewhere in this config).
  Triage flow: a user-requested pass reads `findings/`, folds settled ones into this
  design log (as F#/D# rows, referencing the source finding date) and the repo's
  `flows/`/`kb/` files, then **moves** (never deletes) each reconciled file to
  `findings/processed/`, prefixed with an HTML comment naming which F#/D# it became.
  `findings/` (top level) holds only *unprocessed* backlog; `findings/processed/` is
  a disposable receipt trail — safe to prune later since the log is the permanent
  record, but not auto-deleted at triage time.

## Multi-architect review protocol — design notes

Flow: main architect writes the plan → each peer reviewer writes a report → main
marks accept/deny + why in each report and updates the plan → peers comment back →
iterate. Choices in `flows/REVIEW.md`:
- One file per reviewer, both parties append — parallel-safe, thread = audit trail.
- Stable S-ids so resolutions/follow-ups are one-liners.
- Severities give a mechanical termination rule (round with zero new blockers ⇒
  done) and let main bulk-reject minors.
- Round cap (2) prevents ping-pong; main owns the final call; DEFERRED items are
  recorded in the plan so nothing silently disappears.
- Reviewers never restate plan content; re-reviews cover changed sections only.

## Ideas backlog

> All items executed or dropped 2026-07-12 via `plans/completed/global_config_backlog.md`
> (2-round review record beside it) and `plans/completed/leak_guard_and_agents_md_retrofit.md`.

- ~~Plan lint checklist~~ — CLOSED: `flows/PLAN.md` Ready checklist.
- ~~Reviewer specialization~~ — CLOSED: `flows/REVIEW.md` Lenses.
- ~~Env facts consolidated in root `AGENTS.md`~~ — CLOSED: PLAN.md Known Facts rule.
- ~~Token measurement~~ — CLOSED: `tools/audit_config.py` size table + budget warns.
- ~~RELEASE.md split~~ — DROPPED: REPO.md under budget; revisit past ~4.5KB.
- ~~Harness plan-mode bridge~~ — DROPPED: needs harness hooks; manual rule stays.
- ~~kb compression pass~~ — CLOSED: see mapping table below.
- ~~Regenerate non-workflow templates~~ — CLOSED: --check-templates green.
- ~~Audit script~~ — CLOSED: `tools/audit_config.py`.
- ~~Private-plan leakage guard~~ — CLOSED: `tools/leak_check.py` + REPO.md release
  step + repo cleanup (F15).
- Plan index/dependency convention (D27/F21) — OPEN: no format chosen; prototype on
  a real multi-plan project before deciding.

## How to iterate on this config

1. First, triage `~/.agents/dotagents/findings/` (D28): read each note, decide
   whether it becomes a design-log F/D entry (and, if so, a payload/flow-file
   change), then delete the file once folded in — it's unprocessed backlog, not an
   archive.
2. Read this file, then the current core + the flow file(s) being changed.
3. Audit against reality: pick 1–2 recent plans in some project's
   `.agents/plans/completed/` and check where executors deviated, re-derived facts,
   or asked questions — that's where the config failed.
4. Record new findings/decisions here (F/D numbering continues), then edit the flow
   files under `payload/`. Keep the core ≤ ~60 lines; anything conditional goes in a
   flow file. Reinstall (`python install.py`) after editing.
5. Never let a flow file restate another's content — link it.
6. When updating after an incident, add a provenance note: primary source,
   confirming sources, and superseded rules that should not be restored.
7. This log is public: describe private repos/plans by role, never by name.

## Size-discipline pass (2026-07-12) — moved-rule mapping

Every fact deleted during the kb/flows compression and where the executor now
reaches it. **Wording in these files is deliberately load-bearing — do not
re-expand without checking this table first.**

| Source (deleted from) | Destination |
|---|---|
| kb/PYTHON.md full badge URL block | references/README.md badge row + kb one-line shield paths |
| kb/PYTHON.md pyproject literals (backend, extras, pythonpath) | references/pyproject.toml |
| kb/PYTHON.md mkdocs config prose | references/mkdocs.yml (docs_dir gotcha kept as kb one-liner) |
| kb/PYTHON.md README optional-features/dev-section prose | references/README.md EXECUTOR comments |
| kb/PYTHON.md Known Difficulties paragraphs | kept in kb, compressed to symptom→fix one-liners |
| kb/NODE.md package.json spec prose (exports/files/scripts) | references/package.json |
| kb/NODE.md + kb/RUST.md badge URL blocks | references/README.md badge row + kb one-liners |
| kb/NODE.md + kb/RUST.md changelog-scraper rationale | flows/REPO.md (already there); kb keeps pointer |
| kb/NODE.md + kb/RUST.md version-tag convention prose | kb one-liners (rule retained verbatim in spirit) |
| kb/RUST.md Cargo.toml spec prose (features, docs.rs metadata) | references/Cargo.toml |
| flows/EXEC.md verification incident rationale | this log (F11/F12); EXEC.md keeps the action checklist |

## Byte tables (baseline → after the 2026-07-12 passes)

Non-listed references/ files were regenerated stubs at both points; full current
sizes come from `audit_config.py`'s size table on demand.

| File | Baseline | Final | Saved |
|---|---|---|---|
| AGENTS.md (core) | 2743 | 2743 | 0 (budget ≤ ~2.5KB aspiration) |
| flows/PLAN.md | 5477 | 5475 | 2 (absorbed Ready checklist + env-facts rule at net-zero) |
| flows/EXEC.md | 4062 | 3725 | 337 (below the 0.8KB go-gate — kept: checklist form, zero rule loss) |
| flows/REVIEW.md | 2309 | 2693 | −384 (Lenses section added; ≤3KB budget holds) |
| flows/REPO.md | 3561 | 3561 | 0 |
| kb/PYTHON.md | 6953 | 4848 | 2105 |
| kb/NODE.md | 4772 | 3025 | 1747 |
| kb/RUST.md | 4839 | 2744 | 2095 |

Compression is recorded as **bytes saved without rule loss** (each deletion has a
mapping row above) so future agents know the terse wording is intentional.

## Template regeneration (2026-07-12)

All 8 non-workflow templates rewritten as hand-curated skeletons (structure
generalized from the freshest standardized repo; package.json/Cargo.toml synthesized
from kb specs). `audit_config.py --check-templates`: all 8 PASS. Placeholder hygiene
(no source-project names, no machine paths) enforced by the audit's references
patterns.
