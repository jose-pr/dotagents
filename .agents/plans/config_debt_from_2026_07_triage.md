# Config debt carried out of the 2026-07 findings triage

Status: draft
Executor: code-executor/implemented

## Goal

Land the four items the 2026-07-21 findings triage deliberately deferred rather
than fixing mid-pass. Each is real, each touches a token-budgeted or
standard-defining surface, and none belonged in a triage commit.

## Known Facts & Context

Opened out of the triage that folded 15 findings into D48–D53. Everything below
was recorded in a decision or an archived finding; nothing here is new analysis.

1. **Core `AGENTS.md` is 1149B over budget** (3649B vs 2500B), `REPO.md` 4012B
   over (7012B vs 3000B) — [D49](../dotagents/decisions/D49.md). The budgets are
   `WARN`-only in `audit_config.py`, so this accumulated unnoticed.
   `.agents/dotagents/NOTES.md` still records the core at 2743B — stale by 906B.
   A compression pass needs the moved-rule mapping discipline the repo
   `AGENTS.md` requires, not a drive-by trim.

2. **`docs.yml` does not exist yet** — [D52](../dotagents/decisions/D52.md)
   decided the three-workflow standard but only recorded it. Still to do: add
   `overlays/python/references/workflows/python/docs.yml` (and node/rust
   equivalents), move `docs-build`/`docs-deploy` out of `release.yml`, keep
   `mkdocs build --strict` there as a gate, add `skip-existing: true` to the
   publish step, and update `flows/REPO.md`'s "two workflow files" wording.

3. **`references/pyproject.toml` template gaps** — from
   `2026-07-14_references_drift_and_pyproject_gaps.md`. Missing `license = "MIT"`
   + `license-files = ["LICENSE"]` (PEP 639), `"Typing :: Typed"`, and
   `"Development Status :: 4 - Beta"`. Related: `kb/PYTHON.md` mandates shipping
   `src/<pkg>/py.typed` but nothing checks it, and duho reached release-prep
   without one — a release-prep checklist item would have caught it.

4. **`summarize_run.py` invocation example** — from `summarize_run_log_flag.md`.
   The core names the tool but not its CLI shape; a session guessed `--log-file`
   when the flag is `--log`. Adding one short example costs bytes on the
   already-over-budget core, so it lands with item 1, not before.

## Phases

### Phase 1 — Compression pass on the core + REPO.md
Files: `src/dotagents/_overlay/AGENTS.md`, `overlays/flows/flows/REPO.md`,
`.agents/dotagents/NOTES.md`.
Follow the moved-rule mapping convention: every deletion gets a mapping row, so
compression is provable as bytes-saved-without-rule-loss. Refresh NOTES.md's size
table from `audit_config.py`'s output rather than hand-editing. Fold item 4's
one-line `summarize_run.py --log` example in during this pass.
Done when: both files under budget (no `WARN` from `audit_config.py --root .`),
every removed rule has a mapping row, NOTES.md sizes match the tool's output.

### Phase 2 — The docs-only workflow
Files: `overlays/python/references/workflows/python/{docs.yml,release.yml}`,
node/rust equivalents, `overlays/flows/flows/REPO.md`, `overlays/python/kb/PYTHON.md`.
Per D52. Also expand the Pages bullet in `kb/PYTHON.md` into the full
precondition list with both literal `gh api` commands, and add the check-run
annotations debugging note.
Done when: `audit_config.py --check-templates` passes with the new workflow, and
REPO.md no longer says "two workflow files".

### Phase 3 — pyproject template + py.typed check
Files: `overlays/python/references/pyproject.toml`, `overlays/python/kb/PYTHON.md`.
Add the three missing fields; add a release-prep checklist item asserting
`py.typed` is present and declared.
Done when: a template render passes `--check-templates`; the checklist item exists.

## Verification

```
$VENV\Scripts\python.exe tools/audit_config.py --root .              # no WARN lines
$VENV\Scripts\python.exe tools/audit_config.py --check-templates --root .
$VENV\Scripts\python.exe tools/audit_config.py --repo-hygiene .
```

## Reporting

Update `## Progress` live. New decisions continue at D54+.

## Progress

- [ ] Phase 1 — compression pass on core `AGENTS.md` + `REPO.md` (+ item 4)
- [ ] Phase 2 — `docs.yml` three-workflow standard + Pages preconditions
- [ ] Phase 3 — pyproject template gaps + `py.typed` release check
