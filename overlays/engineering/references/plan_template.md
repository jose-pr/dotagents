<!-- COPY this file to <project>/.agents/plans/<snake_case_name>.md and fill every
     <placeholder>; delete these comments. This skeleton IS the required shape from
     $ENGINEERING_OVERLAY_ROOT/flows/PLAN.md — do not add, drop, or reorder sections. If a filled
     example disagrees with this shape, this file wins. -->

# <Plan Title>

Status: draft
Executor: <family/subrole from MODELS.md> — <one line: why this role>

## Progress

<!-- [ ] pending · [/] active (set BEFORE writing code) · [x] done + short outcome ·
     [!] blocked + reason. The box update is part of the step itself, made in the
     same edit set as the work it reflects — never batched at the end. -->

- [ ] Phase 1: <name>
- [ ] Phase 2: <name>

## Known Facts & Context

<!-- Discovered context and evidence, not guesses: baselines before perf claims,
     registry metadata before new deps, verified third-party behavior. -->

- <fact>

## Phases

<!-- No implementation: paths, signatures, hints, code blocks ≤5 lines.
     Done-when must be observable. Split per PLAN.md rule 4 above ~150 lines. -->

### Phase 1: <name>

- Files: <paths>
- Guidance: <signatures, hints, defaults; Design Q1 … **Decision:** … if choices exist>
- Why: <one line>
- Done-when: <observable check>

## Verification

<!-- Complete literal commands including environment setup; expected outcomes and
     skip counts. Unrun checks are "implemented, unverified", never done. -->

- <command> → <expected>

## Reporting

- Handoff includes: changed files, evidence per done-when, unverified items,
  skip counts, dirty state, resolved provider/model/settings.
