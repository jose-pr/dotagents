# Review thread: global_config_backlog — reviewer "codex"

Condensed archive (finding + resolution merged per S-id; sanitized for public
tracking — exemplar-repo names generalized). Original was a two-round
file-threaded review per `flows/REVIEW.md`.

## R1 (14 findings, all resolved)

- S1 [blocker] Unscoped forbidden-pattern grep can't exit 0 on a tree whose backups
  legitimately contain the patterns. → ACCEPTED: audit scope became a closed
  manifest; backups/harness state excluded; done-when proves the backup doesn't fail
  the run.
- S2 [major] Verification used `grep`, absent on the Windows/PowerShell host. →
  ACCEPTED: verification rewritten as shell-agnostic `python -c` one-liners.
- S3 [major] Template rewrite lacked an explicit source-read list (kb/REPO promises
  would be rediscovered). → ACCEPTED: Phase 5 opens with a "Read first" list: REPO.md
  sections, three kb specs, six exemplar-repo source files.
- S4 [major] Audit needed a scope table naming included/excluded roots. → ACCEPTED:
  four-row manifest table (exist+scan / references-extra / size-warn / excluded).
- S5 [major] Audit root must be `Path.home()/".agents"`, printed, never cwd/argv
  -derived (symlinked access paths). → ACCEPTED.
- S6 [major] Negative test only covered missing files, not a broken pattern scanner.
  → ACCEPTED, then redesigned in R2: `--probe <path>` adds a scratch file so no live
  config file is ever mutated.
- S7 [major] Required-file list must be literal (LICENSE, master plan, each workflow
  with real .yml/.yaml spelling). → ACCEPTED: manifest enumerates every file.
- S8 [major] Templates must be hand-curated skeletons, not mechanical copies of the
  exemplar repo (bloat/leak risk). → ACCEPTED: defined in Q4; README ≲2KB.
- S9 [major] Placeholder hygiene must be checked (no exemplar names, no local
  Windows paths). → ACCEPTED: enforced mechanically in the audit's references
  patterns.
- S10 [major] `<!-- EXECUTOR: -->` comments need bounds or they recreate flow
  content in templates. → ACCEPTED: one imperative line each, instruction-only;
  presence + bound checked.
- S11 [major] Dry-run instantiation for one package per language. → MODIFIED, merged
  with S17: became `--check-templates` in audit_config.py (tempdir substitution,
  tomllib/json parses, key/section checks, self-cleaning, one literal command).
- S12 [major] Before/after byte tables required in the design log. → ACCEPTED:
  Phase 0 baseline + closing table; audit's size table is the instrument.
- S13 [major] "Zero orphaned rules" must be traceable via source→destination mapping
  rows before any deletion. → ACCEPTED: mapping table is the definition of done.
- S14 [minor] No git safety net ⇒ final changed-file inventory required. → ACCEPTED.

## R2 (4 still-opens + 10 new, all resolved)

- S2 still-open: plan now depended on 3.11+ `tomllib` while bare `python` is
  deliberately an old interpreter on the host. → ACCEPTED: all commands name the
  newest installed interpreter explicitly; audit default mode required 3.9-safe;
  `--check-templates` says its requirement on stderr.
- S6 still-open: accepted version appended to a live kb file. → ACCEPTED
  (redesigned): `--probe` scratch-file mechanism.
- S11 still-open: instantiation pass had no literal command/cleanup. → ACCEPTED:
  folded into `--check-templates`.
- S15 [major] Phase 0 needed the exact inventory one-liner runnable before the audit
  script exists. → ACCEPTED.
- S16 [major] Design log + audit script belong in the manifest at least as
  existence-only entries (they quote forbidden strings by design). → ACCEPTED.
- S17 [major] Template parse checks must be in audit_config.py, not hand-waved. →
  ACCEPTED (see S11).
- S18 [major] Compression pass must target the five largest always-reused files
  explicitly with go/no-go per file, not just badge blocks. → ACCEPTED.
- S19 [major] PLAN.md: Ready checklist must replace Contract 2–3 prose, net-zero
  growth. → ACCEPTED, moved into Phase 1.
- S20 [major] EXEC.md: compress Verification to an action checklist; incident
  rationale to the design log. → ACCEPTED.
- S21 [major] PYTHON.md: move badge/README/pyproject/mkdocs/.gitignore boilerplate
  into templates; keep decisions + commands. → ACCEPTED.
- S22 [major] NODE/RUST: shared concepts live once (template or REPO.md); kb keeps
  language deltas. → ACCEPTED.
- S23 [minor] Known Difficulties paragraphs → symptom→fix one-liners. → ACCEPTED.
- S24 [minor] Record compression as "bytes saved without rule loss" so terse wording
  reads as intentional. → ACCEPTED.

## Termination

R2 added zero new blockers; 2-round default cap reached. Plan set to Status: ready.
Main architect closing per flows/REVIEW.md.
